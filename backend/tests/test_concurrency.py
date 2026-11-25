"""并发 / 半截夹具：回流与封口并发、分拣线装载路由并发，均不得双终态或偷增已封袋。

这些用真实文件库 + 线程直接驱动服务层，绕过 TestClient，专测 BEGIN IMMEDIATE 串行化。
"""
import os
import tempfile
import threading
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.db import init_db
from app.services import bagging


@pytest.fixture()
def conn_env():
    d = tempfile.mkdtemp(prefix="sortbay-conc-")
    db_file = Path(d) / "conc.db"
    os.environ["SORTBAY_DB"] = str(db_file)
    init_db()
    from app.db import db as dbctx
    with dbctx() as c:
        c.execute("INSERT INTO lines(name, active) VALUES ('L', 1)")
        lid = c.execute("SELECT id FROM lines WHERE name='L'").fetchone()["id"]
        c.execute("INSERT INTO shifts(business_day, name, sealed) VALUES ('d','早',0)")
        sid = c.execute("SELECT id FROM shifts WHERE sealed=0").fetchone()["id"]
        c.execute("INSERT INTO chutes(line_id, code, title) VALUES (?, 'A01', 't')", (lid,))
        cid = c.execute("SELECT id FROM chutes WHERE code='A01'").fetchone()["id"]
        c.execute("INSERT INTO route_rules(dest_code, chute_id) VALUES ('110', ?)", (cid,))
        c.execute("""INSERT INTO scans(barcode, dest_code, shift_id, line_id, status, scanned_at)
                     VALUES ('B1','110',?,?,'待路由','t')""", (sid, lid))
    return db_file


def _col(conn, sql, args=()):
    return conn.execute(sql, args).fetchone()[0]


def test_concurrent_divert_and_seal_single_terminal(conn_env):
    """一件已入袋件，同时触发「标回流」与「封原袋」：二者只允许一个成功。"""
    from app.db import db as dbctx
    with dbctx() as c:
        sid = _col(c, "SELECT id FROM shifts")
        cid = _col(c, "SELECT id FROM chutes")
        sc = _col(c, "SELECT id FROM scans")
        c.execute("""INSERT INTO bags(shift_id, chute_id, code, status)
                     VALUES (?,?, '袋-1', '集包中')""", (sid, cid))
        bag = _col(c, "SELECT id FROM bags")
        c.execute("UPDATE scans SET status='已入袋', bag_id=?, chute_id=? WHERE id=?", (bag, cid, sc))

    results = {}

    def do_divert():
        try:
            with dbctx(immediate=True) as c:
                s = c.execute("SELECT * FROM scans WHERE id=?", (sc,)).fetchone()
                # 复读：若已被别的写口改变则丢弃
                if s["status"] != "已入袋":
                    raise HTTPException(400, "状态已变")
                r = bagging.divert_scan(c, s, "并发")
            results["divert"] = ("ok", r)
        except Exception as e:  # noqa: BLE001
            results["divert"] = ("err", str(detail_of(e)))

    def do_seal():
        try:
            with dbctx(immediate=True) as c:
                r = bagging.seal_bag(c, bag)
            results["seal"] = ("ok", r)
        except Exception as e:  # noqa: BLE001
            results["seal"] = ("err", str(detail_of(e)))

    t1 = threading.Thread(target=do_divert)
    t2 = threading.Thread(target=do_seal)
    t1.start(); t2.start(); t1.join(); t2.join()

    with dbctx() as c:
        s = c.execute("SELECT * FROM scans WHERE id=?", (sc,)).fetchone()
        b = c.execute("SELECT * FROM bags WHERE id=?", (bag,)).fetchone()
        actual = c.execute("SELECT COUNT(*) FROM scans WHERE bag_id=?", (bag,)).fetchone()[0]

    # 一致性硬断言：已封袋冻结件数 == 实际件数；件不得同时“在已封袋里且已回流”
    assert b["sealed_count"] == actual
    if b["status"] == "已封袋":
        assert s["status"] == "已封袋"
        assert s["bag_id"] == bag
        results["divert"][0] == "err"
    else:
        assert s["status"] == "错分回流"
        assert s["bag_id"] is None
    # 恰好一个写口成功
    assert sum(1 for v in results.values() if v[0] == "ok") == 1, results


def test_concurrent_settle_never_inflates_sealed_bag(conn_env):
    """两件同批次回流件并发再裁决承接，已封原袋件数不得被偷增。"""
    from app.db import db as dbctx
    with dbctx() as c:
        lid = _col(c, "SELECT id FROM lines")
        sid = _col(c, "SELECT id FROM shifts")
        cid = _col(c, "SELECT id FROM chutes")
        c.execute("INSERT INTO bags(shift_id, chute_id, code, status, sealed_count) VALUES (?,?, '原袋', '已封袋', 2)", (sid, cid))
        origin = _col(c, "SELECT id FROM bags WHERE code='原袋'")
        c.execute("INSERT INTO divert_batches(shift_id, code, status, created_at) VALUES (?,?, '回流中','t')", (sid, "回流-X"))
        bid = _col(c, "SELECT id FROM divert_batches")
        scan_ids = []
        for i in range(2):
            c.execute("""INSERT INTO scans(barcode, dest_code, shift_id, line_id, status, chute_id,
                                           divert_batch_id, origin_bag_id, scanned_at)
                         VALUES (?, '110', ?, ?, '已路由', ?, ?, ?, 't')""",
                      (f"SETTLE{i}", sid, lid, cid, bid, origin))
            scan_ids.append(c.execute("SELECT id FROM scans WHERE barcode=?", (f"SETTLE{i}",)).fetchone()[0])

    errs = []

    def settle(sid_scan):
        try:
            with dbctx(immediate=True) as c:
                bagging.settle_divert(c, sid_scan)
        except Exception as e:  # noqa: BLE001
            errs.append(repr(detail_of(e)))

    ts = [threading.Thread(target=settle, args=(s,)) for s in scan_ids]
    for t in ts: t.start()
    for t in ts: t.join()

    with dbctx() as c:
        orig = c.execute("SELECT * FROM bags WHERE id=?", (origin,)).fetchone()
        orig_actual = c.execute("SELECT COUNT(*) FROM scans WHERE bag_id=?", (origin,)).fetchone()[0]
        rebags = c.execute("SELECT * FROM bags WHERE kind='回流承接'").fetchall()
        settled = c.execute("SELECT COUNT(*) FROM scans WHERE status='已入袋'").fetchone()[0]
    assert orig["sealed_count"] == 2
    assert orig_actual == 0  # 已封原袋实际件数仍为 0（无偷增）
    assert settled == 2, errs
    assert len(rebags) >= 1
    for rb in rebags:
        assert rb["id"] != origin


def test_route_into_sealed_bag_never_inflates(conn_env):
    """分拣线装载路由落入并发：已封袋件数不得被路由/承接口偷增。"""
    from app.db import db as dbctx
    with dbctx() as c:
        lid = _col(c, "SELECT id FROM lines")
        sid = _col(c, "SELECT id FROM shifts")
        cid = _col(c, "SELECT id FROM chutes")
        c.execute("INSERT INTO bags(shift_id, chute_id, code, status, sealed_count) VALUES (?,?, '封袋-1','已封袋', 1)",
                  (sid, cid))
        sealed_bag = _col(c, "SELECT id FROM bags WHERE code='封袋-1'")
        scan_ids = []
        for i in range(4):
            c.execute("""INSERT INTO scans(barcode, dest_code, shift_id, line_id, status, scanned_at)
                         VALUES (?, '110', ?, ?, '已路由', 't')""",
                      (f"ROUTE{i}", sid, lid))
            scan_ids.append(_col(c, "SELECT id FROM scans WHERE barcode=?", (f"ROUTE{i}",)))

    def attach(s):
        try:
            with dbctx(immediate=True) as c:
                bagging.attach_regular(c, sealed_bag, s)
        except Exception:  # noqa: BLE001
            pass

    ts = [threading.Thread(target=attach, args=(s,)) for s in scan_ids]
    for t in ts: t.start()
    for t in ts: t.join()

    with dbctx() as c:
        b = c.execute("SELECT * FROM bags WHERE id=?", (sealed_bag,)).fetchone()
        actual = c.execute("SELECT COUNT(*) FROM scans WHERE bag_id=?", (sealed_bag,)).fetchone()[0]
        still_routed = c.execute("SELECT COUNT(*) FROM scans WHERE status='已路由'").fetchone()[0]
    assert b["status"] == "已封袋"
    assert b["sealed_count"] == 1
    assert actual == 0, "已封袋被偷增"
    assert still_routed == 4


def detail_of(e):
    return getattr(e, "detail", e)
