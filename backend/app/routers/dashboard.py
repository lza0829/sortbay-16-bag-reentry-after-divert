from __future__ import annotations
from fastapi import APIRouter

from app.db import db, rows_to_list, row_to_dict

router = APIRouter(tags=["总览"])


@router.get("/总览")
def overview():
    with db() as conn:
        shift = conn.execute(
            "SELECT * FROM shifts ORDER BY id DESC LIMIT 1"
        ).fetchone()
        sid = shift["id"] if shift else None
        counts = {
            "待路由": conn.execute("SELECT COUNT(*) c FROM scans WHERE status='待路由'").fetchone()["c"],
            "已路由": conn.execute("SELECT COUNT(*) c FROM scans WHERE status='已路由'").fetchone()["c"],
            "错分回流": conn.execute("SELECT COUNT(*) c FROM scans WHERE status='错分回流'").fetchone()["c"],
            "已入袋": conn.execute("SELECT COUNT(*) c FROM scans WHERE status='已入袋'").fetchone()["c"],
            "已封袋": conn.execute("SELECT COUNT(*) c FROM scans WHERE status='已封袋'").fetchone()["c"],
        }
        chutes = rows_to_list(conn.execute(
            """
            SELECT c.*,
              (SELECT COUNT(*) FROM scans s WHERE s.chute_id=c.id
                 AND s.status IN ('已路由','已入袋')) AS 在口件数,
              (SELECT COUNT(*) FROM bags b WHERE b.chute_id=c.id
                 AND b.status='集包中') AS 开袋数,
              (SELECT COUNT(*) FROM bags b WHERE b.chute_id=c.id
                 AND b.status='集包中' AND b.kind='回流承接') AS 承接开袋数
            FROM chutes c ORDER BY c.code
            """
        ).fetchall())
        bag_status = {
            "集包中": conn.execute(
                "SELECT COUNT(*) c FROM bags WHERE status='集包中'").fetchone()["c"],
            "已封袋": conn.execute(
                "SELECT COUNT(*) c FROM bags WHERE status='已封袋'").fetchone()["c"],
            "回流承接袋": conn.execute(
                "SELECT COUNT(*) c FROM bags WHERE kind='回流承接'").fetchone()["c"],
        }
        divert = {
            "进行中": conn.execute(
                "SELECT COUNT(*) c FROM divert_batches WHERE status='回流中'").fetchone()["c"],
            "已承接": conn.execute(
                "SELECT COUNT(*) c FROM divert_batches WHERE status='已承接'").fetchone()["c"],
        }
        # 总览对账：与班次对账同一口径的违规汇总
        violations = []
        if sid is not None:
            violations = [dict(r) for r in conn.execute(
                """SELECT b.id AS bag_id, b.sealed_count,
                          (SELECT COUNT(*) FROM scans s WHERE s.bag_id=b.id) AS actual
                   FROM bags b WHERE b.shift_id=? AND b.status='已封袋'
                     AND b.sealed_count != (SELECT COUNT(*) FROM scans s WHERE s.bag_id=b.id)""",
                (sid,),
            ).fetchall()]
            violations += [{"扫描": r["id"], "问题": "回流件写回原袋"} for r in conn.execute(
                """SELECT id FROM scans WHERE shift_id=? AND divert_batch_id IS NOT NULL
                     AND bag_id IS NOT NULL AND origin_bag_id IS NOT NULL
                     AND bag_id=origin_bag_id""",
                (sid,),
            ).fetchall()]
        return {
            "班次": row_to_dict(shift),
            "分布": counts,
            "袋状态": bag_status,
            "回流批次": divert,
            "口门": chutes,
            "违规": violations,
            "对账一致": not violations,
        }
