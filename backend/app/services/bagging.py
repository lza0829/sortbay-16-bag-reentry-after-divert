"""回流后再入袋承接（Arm B：强制新开袋）。

全局唯一直贯的承接口径：
- 一旦 scanned 件进入「错分回流」流转（scans.divert_batch_id 非空），
  其再裁决后的入袋 **禁止** 写回原袋；必须落入新开「回流承接」袋，
  且该新袋通过 bags.divert_batch_id / bags.parent_bag_id 保留回流批次与原袋关联。
- 原袋件数以 scans.bag_id 派生，回流移出时 bag_id 置空 -> 原袋件数自然回落，
  不产生「偷增」；已封袋的件数另冻结在 bags.sealed_count，任何路径都不得改动。
- 所有写口在 BEGIN IMMEDIATE 内完成并复读状态，回流与封口并发只有一个终态。
- 每一步写入 bag_events（append-only），承接结局由事件可重放。
"""
from __future__ import annotations

from fastapi import HTTPException

from app.db import row_to_dict
from app.services.common import audit, now_str

# 状态常量
ST_PENDING = "待路由"
ST_DIVERTED = "错分回流"
ST_ROUTED = "已路由"
ST_BAGGED = "已入袋"
ST_SEALED = "已封袋"

BAG_OPEN = "集包中"
BAG_SEALED = "已封袋"
BAG_KIND_REGULAR = "常规"
BAG_KIND_DIVERT = "回流承接"


# ---------------------------------------------------------------- 事件 / 审计

def bag_event(
    conn,
    bag_id: int,
    action: str,
    *,
    scan_id: int | None = None,
    from_bag_id: int | None = None,
    to_bag_id: int | None = None,
    divert_batch_id: int | None = None,
    note: str = "",
) -> None:
    conn.execute(
        """INSERT INTO bag_events(bag_id, scan_id, action, from_bag_id, to_bag_id,
                                  divert_batch_id, created_at, note)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (bag_id, scan_id, action, from_bag_id, to_bag_id, divert_batch_id, now_str(), note),
    )


def bag_code(conn, prefix: str) -> str:
    """生成袋码，带重试避免并发撞车。"""
    base = f"{prefix}-{now_str().replace(' ', '').replace(':', '').replace('-', '')}"
    for i in range(100):
        code = base if i == 0 else f"{base}-{i}"
        if not conn.execute("SELECT 1 FROM bags WHERE code=?", (code,)).fetchone():
            return code
    raise HTTPException(500, "袋码冲突，请重试")


def bag_count(conn, bag_id: int) -> int:
    return conn.execute(
        "SELECT COUNT(*) c FROM scans WHERE bag_id=?", (bag_id,)
    ).fetchone()["c"]


# ---------------------------------------------------------------- 开袋 / 入袋

def open_bag(
    conn,
    *,
    chute: dict,
    shift_id: int,
    kind: str = BAG_KIND_REGULAR,
    divert_batch_id: int | None = None,
    parent_bag_id: int | None = None,
    note: str = "",
) -> dict:
    prefix = "袋" if kind == BAG_KIND_REGULAR else "回流袋"
    code = bag_code(conn, f"{prefix}-{chute['code']}")
    conn.execute(
        """INSERT INTO bags(shift_id, chute_id, code, status, kind, divert_batch_id,
                            parent_bag_id, sealed_count, note)
           VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)""",
        (shift_id, chute["id"], code, BAG_OPEN, kind, divert_batch_id, parent_bag_id, note),
    )
    row = conn.execute("SELECT * FROM bags WHERE code=?", (code,)).fetchone()
    bag_event(
        conn, row["id"], "开袋", divert_batch_id=divert_batch_id,
        note=f"{kind}:{code}" + (f";原袋={parent_bag_id}" if parent_bag_id else ""),
    )
    audit(conn, "封袋", row["id"], "开袋", f"{kind}:{code}")
    return row_to_dict(row)


def _load_bag(conn, bag_id: int):
    bag = conn.execute("SELECT * FROM bags WHERE id=?", (bag_id,)).fetchone()
    if not bag:
        raise HTTPException(404, "袋不存在")
    return bag


def _load_scan(conn, scan_id: int):
    scan = conn.execute("SELECT * FROM scans WHERE id=?", (scan_id,)).fetchone()
    if not scan:
        raise HTTPException(404, "扫描不存在")
    return scan


def attach_regular(conn, bag_id: int, scan_id: int) -> dict:
    """常规入袋。拒绝：已封袋、口门不符、以及任何带回流批次的件（必须走承接口）。"""
    bag = _load_bag(conn, bag_id)
    if bag["status"] != BAG_OPEN:
        raise HTTPException(400, "袋已封，禁止再入")
    if bag["kind"] == BAG_KIND_DIVERT:
        raise HTTPException(400, "回流承接袋只能经承接口写入")
    scan = _load_scan(conn, scan_id)
    if scan["divert_batch_id"] is not None:
        raise HTTPException(
            400,
            "回流后再裁决件禁止写入原袋/常规袋，必须落入新开的回流承接袋",
        )
    if scan["status"] != ST_ROUTED:
        raise HTTPException(400, "仅已路由件可入袋")
    if scan["chute_id"] != bag["chute_id"]:
        raise HTTPException(400, "扫描口门与袋口门不一致")
    conn.execute(
        "UPDATE scans SET status=?, bag_id=? WHERE id=?",
        (ST_BAGGED, bag_id, scan_id),
    )
    bag_event(conn, bag_id, "入袋", scan_id=scan_id, to_bag_id=bag_id)
    audit(conn, "封袋", bag_id, "入袋", str(scan_id))
    return row_to_dict(conn.execute("SELECT * FROM scans WHERE id=?", (scan_id,)).fetchone())


# ---------------------------------------------------------------- 回流后再入袋承接

def settle_divert(conn, scan_id: int) -> dict:
    """回流后再裁决 -> 强制新开袋承接（Arm B）。

    前置：扫描处于错分回流流转（divert_batch_id 非空）且已重新裁决为已路由。
    行为：为该（回流批次, 口门）自动开/复用一只「回流承接」袋，把件落入其中。
    原袋（origin_bag_id）绝不被写入或增计。
    """
    scan = _load_scan(conn, scan_id)
    if scan["divert_batch_id"] is None:
        raise HTTPException(400, "该件不在回流流转中，请走常规入袋")
    if scan["status"] != ST_ROUTED:
        raise HTTPException(400, f"回流件须先再裁决为已路由，当前状态: {scan['status']}")
    if not scan["chute_id"]:
        raise HTTPException(400, "回流件缺少再裁决口门")

    batch = conn.execute(
        "SELECT * FROM divert_batches WHERE id=?", (scan["divert_batch_id"],)
    ).fetchone()
    if not batch:
        raise HTTPException(404, "回流批次不存在")
    if batch["status"] == "已撤销":
        raise HTTPException(400, "回流批次已撤销")

    chute = conn.execute("SELECT * FROM chutes WHERE id=?", (scan["chute_id"],)).fetchone()
    if not chute:
        raise HTTPException(400, "再裁决口门不存在")
    if chute["status"] != "口门开":
        raise HTTPException(400, "口门已闭，禁止承接")
    if not shift_open(conn, scan["shift_id"]):
        raise HTTPException(400, "班次已封班，禁止写口")

    origin_bag_id = scan["origin_bag_id"]

    # 复用本批次本口门尚未封口的承接袋，否则自动新开一只。
    # 说明：一只承接袋按 (批次, 口门) 归集，可容纳来自多只原袋的件；每件自身的
    # 原袋关联保留在 scans.origin_bag_id 与 bag_events.from_bag_id 上，
    # 袋级 parent_bag_id 仅作首次开袋的主原袋展示，不承担逐件溯源。
    rebag = conn.execute(
        """SELECT * FROM bags
           WHERE kind=? AND divert_batch_id=? AND chute_id=? AND status=?
           ORDER BY id DESC LIMIT 1""",
        (BAG_KIND_DIVERT, batch["id"], chute["id"], BAG_OPEN),
    ).fetchone()
    created = False
    if rebag is None:
        rebag = open_bag(
            conn,
            chute=chute,
            shift_id=scan["shift_id"],
            kind=BAG_KIND_DIVERT,
            divert_batch_id=batch["id"],
            parent_bag_id=origin_bag_id,
            note=f"回流批次{batch['code']}自动承接",
        )
        created = True
        bag_event(
            conn, rebag["id"], "回流承接开袋", scan_id=scan_id,
            from_bag_id=origin_bag_id, to_bag_id=rebag["id"],
            divert_batch_id=batch["id"], note=rebag["code"],
        )

    conn.execute(
        "UPDATE scans SET status=?, bag_id=? WHERE id=?",
        (ST_BAGGED, rebag["id"], scan_id),
    )
    bag_event(
        conn, rebag["id"], "回流承接入袋", scan_id=scan_id,
        from_bag_id=origin_bag_id, to_bag_id=rebag["id"],
        divert_batch_id=batch["id"], note=rebag["code"],
    )
    conn.execute(
        "UPDATE divert_batches SET rebag_bag_id=? WHERE id=?",
        (rebag["id"], batch["id"]),
    )

    remaining = conn.execute(
        "SELECT COUNT(*) c FROM scans WHERE divert_batch_id=? AND status IN (?,?)",
        (batch["id"], ST_DIVERTED, ST_ROUTED),
    ).fetchone()["c"]
    if remaining == 0:
        conn.execute(
            "UPDATE divert_batches SET status='已承接', settled_at=? WHERE id=?",
            (now_str(), batch["id"]),
        )

    audit(
        conn, "回流承接", batch["id"], "再入袋",
        f"件{scan_id}->袋{rebag['id']}" + (f"(原袋{origin_bag_id})" if origin_bag_id else ""),
    )
    return {
        "扫描": row_to_dict(conn.execute("SELECT * FROM scans WHERE id=?", (scan_id,)).fetchone()),
        "袋": row_to_dict(conn.execute("SELECT * FROM bags WHERE id=?", (rebag["id"],)).fetchone()),
        "新开袋": created,
        "原袋编号": origin_bag_id,
    }


def shift_open(conn, shift_id: int) -> bool:
    row = conn.execute("SELECT sealed FROM shifts WHERE id=?", (shift_id,)).fetchone()
    return bool(row) and not row["sealed"]


def divert_scan(conn, scan, note: str = "", batch_id: int | None = None) -> dict:
    """把已路由/已入袋件转入错分回流。并发安全由 BEGIN IMMEDIATE 保证。

    batch_id 非空时并入既有「回流中」批次（同班次），便于一批多件统一承接。
    """
    origin_bag_id = scan["bag_id"]
    if origin_bag_id is not None:
        bag = conn.execute("SELECT * FROM bags WHERE id=?", (origin_bag_id,)).fetchone()
        if bag and bag["status"] == BAG_SEALED:
            # 袋已封：不得把件从已封袋里"抽"出来，避免关联与件数被拧动。
            raise HTTPException(400, "原袋已封袋，禁止回流移出，请显式隔离处理")

    if scan["status"] not in (ST_ROUTED, ST_BAGGED):
        raise HTTPException(400, "仅已路由或已入袋可标错分回流")

    shift_id = scan["shift_id"]
    if not shift_open(conn, shift_id):
        raise HTTPException(400, "班次已封班，禁止写口")

    if batch_id is not None:
        batch = conn.execute("SELECT * FROM divert_batches WHERE id=?", (batch_id,)).fetchone()
        if not batch:
            raise HTTPException(404, "回流批次不存在")
        if batch["shift_id"] != shift_id:
            raise HTTPException(400, "回流批次不属于该扫描班次")
        if batch["status"] != "回流中":
            raise HTTPException(400, "回流批次已承接，禁止并入")
        bid = batch["id"]
        code = batch["code"]
    else:
        code = f"回流-{shift_id}-{now_str().replace(' ', '').replace(':', '')[-8:]}"
        if conn.execute("SELECT 1 FROM divert_batches WHERE code=?", (code,)).fetchone():
            code = f"{code}-{scan['id']}"
        conn.execute(
            "INSERT INTO divert_batches(shift_id, code, status, created_at, note) VALUES (?, ?, '回流中', ?, ?)",
            (shift_id, code, now_str(), note),
        )
        bid = conn.execute("SELECT id FROM divert_batches WHERE code=?", (code,)).fetchone()["id"]

    conn.execute(
        "UPDATE scans SET status=?, divert_batch_id=?, origin_bag_id=?, bag_id=NULL WHERE id=?",
        (ST_DIVERTED, bid, origin_bag_id, scan["id"]),
    )
    if origin_bag_id is not None:
        bag_event(
            conn, origin_bag_id, "回流移出", scan_id=scan["id"],
            from_bag_id=origin_bag_id, divert_batch_id=bid,
            note=f"原袋件数回落; 批次{code}",
        )
    if scan["chute_id"]:
        conn.execute(
            "INSERT INTO route_events(scan_id, chute_id, action, created_at, note) VALUES (?, ?, ?, ?, ?)",
            (scan["id"], scan["chute_id"], "错分回流", now_str(), code),
        )
    audit(conn, "回流", bid, "并入回流批次" if batch_id is not None else "建回流批次", f"件{scan['id']}")
    return {
        "批次": row_to_dict(conn.execute("SELECT * FROM divert_batches WHERE id=?", (bid,)).fetchone()),
        "扫描": row_to_dict(conn.execute("SELECT * FROM scans WHERE id=?", (scan["id"],)).fetchone()),
    }


# ---------------------------------------------------------------- 封口

def seal_bag(conn, bag_id: int) -> dict:
    bag = _load_bag(conn, bag_id)
    if bag["status"] != BAG_OPEN:
        raise HTTPException(400, "袋状态不可封口")
    n = bag_count(conn, bag_id)
    if n == 0:
        raise HTTPException(400, "空袋不可封口")

    # 承接袋内不得残留未完成回流件的悬空关联
    dangling = conn.execute(
        """SELECT COUNT(*) c FROM scans
           WHERE bag_id=? AND divert_batch_id IS NOT NULL
             AND status NOT IN (?, ?)""",
        (bag_id, ST_BAGGED, ST_SEALED),
    ).fetchone()["c"]
    if dangling:
        raise HTTPException(400, f"袋内存在未闭合回流件 {dangling}，禁止封口")

    conn.execute(
        "UPDATE bags SET status=?, sealed_at=?, sealed_count=? WHERE id=?",
        (BAG_SEALED, now_str(), n, bag_id),
    )
    conn.execute(
        "UPDATE scans SET status=? WHERE bag_id=? AND status=?",
        (ST_SEALED, bag_id, ST_BAGGED),
    )
    bag_event(conn, bag_id, "封口", to_bag_id=bag_id, note=f"件数{n}")
    audit(conn, "封袋", bag_id, "封口", f"件数{n}")
    return row_to_dict(conn.execute("SELECT * FROM bags WHERE id=?", (bag_id,)).fetchone())


# ---------------------------------------------------------------- 事件重放

def replay_scan(conn, scan_id: int) -> dict:
    """从 append-only 事件重建单件承接结局，并与当前库态对账。

    落袋只由 bag_events 派生（其 id 单序列且带 from/to，顺序可靠）；
    route_events 仅作展示，避免双表 id 交错的排序歧义。
    """
    scan = _load_scan(conn, scan_id)
    route_events = [dict(r) for r in conn.execute(
        """SELECT id, action, created_at, note, chute_id AS ref
           FROM route_events WHERE scan_id=? ORDER BY id""",
        (scan_id,),
    ).fetchall()]
    bag_events = [dict(r) for r in conn.execute(
        """SELECT id, action, created_at, note, from_bag_id, to_bag_id
           FROM bag_events WHERE scan_id=? ORDER BY id""",
        (scan_id,),
    ).fetchall()]
    derived_bag = None
    for e in bag_events:
        if e["action"] in ("入袋", "回流承接入袋"):
            derived_bag = e["to_bag_id"]
        elif e["action"] == "回流移出":
            derived_bag = None
    events = (
        [{"src": "route", **r} for r in route_events]
        + [{"src": "bag", **r} for r in bag_events]
    )
    ok = derived_bag == scan["bag_id"]
    # 承接硬约束：回流件的落袋不得等于原袋
    if scan["divert_batch_id"] is not None and scan["bag_id"] is not None \
            and scan["origin_bag_id"] is not None and scan["bag_id"] == scan["origin_bag_id"]:
        ok = False
    return {
        "扫描": row_to_dict(scan),
        "事件": events,
        "重放落袋": derived_bag,
        "当前袋": scan["bag_id"],
        "一致": ok,
    }


def replay_batch(conn, batch_id: int) -> dict:
    batch = conn.execute("SELECT * FROM divert_batches WHERE id=?", (batch_id,)).fetchone()
    if not batch:
        raise HTTPException(404, "回流批次不存在")
    members = [dict(r) for r in conn.execute(
        "SELECT * FROM scans WHERE divert_batch_id=? ORDER BY id", (batch_id,)
    ).fetchall()]
    bags = [dict(r) for r in conn.execute(
        "SELECT * FROM bags WHERE divert_batch_id=? ORDER BY id", (batch_id,)
    ).fetchall()]
    events = [dict(r) for r in conn.execute(
        """SELECT * FROM bag_events WHERE divert_batch_id=? ORDER BY created_at, id""",
        (batch_id,),
    ).fetchall()]
    violations = []
    for s in members:
        if s["bag_id"] is not None and s["origin_bag_id"] is not None \
                and s["bag_id"] == s["origin_bag_id"]:
            violations.append({"扫描": s["id"], "问题": "回流件写回原袋"})
    for b in bags:
        if b["status"] == BAG_SEALED and bag_count(conn, b["id"]) != b["sealed_count"]:
            violations.append({"袋": b["id"], "问题": "已封袋件数与冻结值不符"})
    return {
        "批次": row_to_dict(batch),
        "成员": members,
        "承接袋": bags,
        "事件": events,
        "违规": violations,
        "一致": not violations,
    }
