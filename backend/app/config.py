from __future__ import annotations
import os
from pathlib import Path

PROJECT_NAME = "分拣过机台"
ROOT = Path(__file__).resolve().parents[2]


def db_path() -> Path:
    """数据库路径在每次连接时解析，便于测试用环境变量隔离。"""
    return Path(os.environ.get("SORTBAY_DB", str(ROOT / "data" / "sortbay.db")))


# 兼容旧引用；实际连接请使用 db_path()
DB_PATH = db_path()
