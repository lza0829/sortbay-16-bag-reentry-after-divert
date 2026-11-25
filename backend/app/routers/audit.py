from __future__ import annotations
from fastapi import APIRouter

from app.db import db, rows_to_list

router = APIRouter(tags=["审计"])


@router.get("/审计")
def list_audit(种类: str | None = None, 引用: int | None = None, 关键字: str | None = None):
    with db() as conn:
        sql = "SELECT * FROM audit_events"
        where, args = [], []
        if 种类:
            where.append("kind=?"); args.append(种类)
        if 引用 is not None:
            where.append("ref_id=?"); args.append(引用)
        if 关键字:
            where.append("(action LIKE ? OR note LIKE ?)")
            args += [f"%{关键字}%", f"%{关键字}%"]
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY id DESC LIMIT 200"
        return rows_to_list(conn.execute(sql, args).fetchall())
