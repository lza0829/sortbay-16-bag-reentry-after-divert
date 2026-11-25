from __future__ import annotations
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.db import db, rows_to_list
from app.services import bagging
from app.services.common import current_shift, require_unsealed

router = APIRouter(tags=["错分回流"])


class DivertIn(BaseModel):
    扫描编号: int
    批次编号: int | None = None
    备注: str = ""


@router.get("/回流批次")
def list_batches():
    with db() as conn:
        return rows_to_list(conn.execute(
            """
            SELECT b.*,
              (SELECT COUNT(*) FROM scans s WHERE s.divert_batch_id=b.id) AS 件数,
              (SELECT COUNT(*) FROM scans s WHERE s.divert_batch_id=b.id
                 AND s.status='错分回流') AS 待再裁决,
              (SELECT COUNT(*) FROM scans s WHERE s.divert_batch_id=b.id
                 AND s.status='已入袋') AS 已承接件数,
              (SELECT code FROM bags g WHERE g.id=b.rebag_bag_id) AS 承接袋码,
              (SELECT parent_bag_id FROM bags g WHERE g.id=b.rebag_bag_id) AS 原袋编号
            FROM divert_batches b ORDER BY b.id DESC LIMIT 100
            """
        ).fetchall())


@router.post("/回流批次")
def create_divert(body: DivertIn):
    with db(immediate=True) as conn:
        require_unsealed(conn)
        shift = current_shift(conn)
        scan = conn.execute("SELECT * FROM scans WHERE id=?", (body.扫描编号,)).fetchone()
        if not scan:
            raise HTTPException(404, "扫描不存在")
        if scan["shift_id"] != shift["id"]:
            raise HTTPException(400, "扫描不属于当前未封班班次")
        return bagging.divert_scan(conn, scan, body.备注, batch_id=body.批次编号)


@router.get("/回流批次/{batch_id}/重放")
def replay(batch_id: int):
    with db() as conn:
        return bagging.replay_batch(conn, batch_id)
