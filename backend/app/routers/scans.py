from __future__ import annotations
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.db import db, rows_to_list, row_to_dict
from app.services.common import audit, current_shift, now_str, require_unsealed

router = APIRouter(tags=["过机扫描"])


class ScanIn(BaseModel):
    条码: str = Field(min_length=1)
    目的地码: str = ""
    分拣线编号: int | None = None
    备注: str = ""


@router.get("/扫描")
def list_scans(状态: str | None = None):
    with db() as conn:
        sql = """
            SELECT s.*,
              (SELECT code FROM bags b WHERE b.id=s.bag_id) AS 当前袋码,
              (SELECT code FROM bags b WHERE b.id=s.origin_bag_id) AS 原袋码,
              (SELECT code FROM divert_batches d WHERE d.id=s.divert_batch_id) AS 回流批次码
            FROM scans s
        """
        if 状态:
            rows = conn.execute(
                sql + " WHERE s.status=? ORDER BY s.id DESC LIMIT 200", (状态,)
            ).fetchall()
        else:
            rows = conn.execute(sql + " ORDER BY s.id DESC LIMIT 200").fetchall()
        return rows_to_list(rows)


@router.post("/扫描")
def create_scan(body: ScanIn):
    with db(immediate=True) as conn:
        require_unsealed(conn)
        shift = current_shift(conn)
        line = None
        if body.分拣线编号:
            line = conn.execute("SELECT * FROM lines WHERE id=?", (body.分拣线编号,)).fetchone()
        else:
            line = conn.execute("SELECT * FROM lines WHERE active=1 ORDER BY id LIMIT 1").fetchone()
        if not line:
            raise HTTPException(400, "无可用分拣线")
        if not line["active"]:
            raise HTTPException(400, "分拣线已停用")
        dup = conn.execute(
            "SELECT id FROM scans WHERE barcode=? AND shift_id=?",
            (body.条码, shift["id"]),
        ).fetchone()
        if dup:
            raise HTTPException(400, "本班次条码已过机")
        conn.execute(
            """
            INSERT INTO scans(barcode, dest_code, shift_id, line_id, status, scanned_at, note)
            VALUES (?, ?, ?, ?, '待路由', ?, ?)
            """,
            (body.条码, body.目的地码, shift["id"], line["id"], now_str(), body.备注),
        )
        row = conn.execute(
            "SELECT * FROM scans WHERE barcode=? AND shift_id=?",
            (body.条码, shift["id"]),
        ).fetchone()
        audit(conn, "扫描", row["id"], "过机装载", body.条码)
        return row_to_dict(row)


@router.post("/扫描/{scan_id}/路由")
def route_scan(scan_id: int):
    with db(immediate=True) as conn:
        require_unsealed(conn)
        scan = conn.execute("SELECT * FROM scans WHERE id=?", (scan_id,)).fetchone()
        if not scan:
            raise HTTPException(404, "扫描不存在")
        if scan["status"] not in ("待路由", "错分回流"):
            raise HTTPException(400, f"当前状态不可路由: {scan['status']}")
        if not scan["dest_code"]:
            raise HTTPException(400, "缺少目的地码")
        rule = conn.execute(
            "SELECT * FROM route_rules WHERE dest_code=?", (scan["dest_code"],)
        ).fetchone()
        if not rule:
            raise HTTPException(400, "无匹配路由")
        chute = conn.execute("SELECT * FROM chutes WHERE id=?", (rule["chute_id"],)).fetchone()
        if not chute:
            raise HTTPException(400, "口门不存在")
        if chute["status"] != "口门开":
            raise HTTPException(400, "口门已闭，禁止落入")
        redivert = scan["divert_batch_id"] is not None
        conn.execute(
            "UPDATE scans SET status='已路由', chute_id=?, bag_id=NULL WHERE id=?",
            (chute["id"], scan_id),
        )
        action = "回流再裁决" if redivert else "路由落入"
        conn.execute(
            "INSERT INTO route_events(scan_id, chute_id, action, created_at, note) VALUES (?, ?, ?, ?, ?)",
            (scan_id, chute["id"], action, now_str(),
             chute["code"] + (f";回流批次{scan['divert_batch_id']}" if redivert else "")),
        )
        audit(conn, "扫描", scan_id, action, chute["code"])
        return row_to_dict(conn.execute("SELECT * FROM scans WHERE id=?", (scan_id,)).fetchone())
