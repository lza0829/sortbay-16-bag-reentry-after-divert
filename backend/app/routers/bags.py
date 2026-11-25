from __future__ import annotations
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.db import db, rows_to_list
from app.services import bagging
from app.services.common import current_shift, require_unsealed

router = APIRouter(tags=["封袋"])


class BagOpenIn(BaseModel):
    口门编号: int
    备注: str = ""


class BagAddIn(BaseModel):
    扫描编号: int


@router.get("/封袋")
def list_bags():
    with db() as conn:
        return rows_to_list(conn.execute(
            """
            SELECT b.*, c.code AS 口门码,
              (SELECT COUNT(*) FROM scans s WHERE s.bag_id=b.id) AS 件数,
              b.parent_bag_id AS 原袋编号,
              (SELECT code FROM bags p WHERE p.id=b.parent_bag_id) AS 原袋码,
              (SELECT code FROM divert_batches d WHERE d.id=b.divert_batch_id) AS 回流批次码
            FROM bags b JOIN chutes c ON c.id=b.chute_id
            ORDER BY b.id DESC LIMIT 200
            """
        ).fetchall())


@router.post("/封袋/开袋")
def open_bag(body: BagOpenIn):
    with db(immediate=True) as conn:
        require_unsealed(conn)
        shift = current_shift(conn)
        chute = conn.execute("SELECT * FROM chutes WHERE id=?", (body.口门编号,)).fetchone()
        if not chute:
            raise HTTPException(404, "口门不存在")
        return bagging.open_bag(
            conn, chute=dict(chute), shift_id=shift["id"], note=body.备注,
        )


@router.post("/封袋/{bag_id}/入袋")
def add_to_bag(bag_id: int, body: BagAddIn):
    with db(immediate=True) as conn:
        require_unsealed(conn)
        scan = conn.execute("SELECT * FROM scans WHERE id=?", (body.扫描编号,)).fetchone()
        if scan and scan["divert_batch_id"] is not None:
            raise HTTPException(
                400,
                "回流后再裁决件禁止写入原袋/常规袋，请使用「回流承接」写口",
            )
        return bagging.attach_regular(conn, bag_id, body.扫描编号)


@router.post("/封袋/{bag_id}/封口")
def seal_bag(bag_id: int):
    with db(immediate=True) as conn:
        require_unsealed(conn)
        return bagging.seal_bag(conn, bag_id)


@router.post("/回流承接/入袋")
def settle_divert(body: BagAddIn):
    """回流后再裁决 -> 自动新开/复用回流承接袋承接（Arm B 唯一入袋口）。"""
    with db(immediate=True) as conn:
        require_unsealed(conn)
        return bagging.settle_divert(conn, body.扫描编号)


@router.get("/扫描/{scan_id}/承接重放")
def replay_scan_route(scan_id: int):
    with db() as conn:
        return bagging.replay_scan(conn, scan_id)
