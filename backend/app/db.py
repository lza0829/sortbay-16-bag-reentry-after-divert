from __future__ import annotations
import sqlite3
from contextlib import contextmanager

SCHEMA = """
CREATE TABLE IF NOT EXISTS lines (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS shifts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  business_day TEXT NOT NULL,
  name TEXT NOT NULL,
  sealed INTEGER NOT NULL DEFAULT 0,
  sealed_at TEXT,
  UNIQUE(business_day, name)
);
CREATE TABLE IF NOT EXISTS chutes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  line_id INTEGER NOT NULL,
  code TEXT NOT NULL,
  title TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT '口门开',
  soft_cap INTEGER NOT NULL DEFAULT 200,
  UNIQUE(line_id, code),
  FOREIGN KEY(line_id) REFERENCES lines(id)
);
CREATE TABLE IF NOT EXISTS route_rules (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  dest_code TEXT NOT NULL UNIQUE,
  chute_id INTEGER NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  FOREIGN KEY(chute_id) REFERENCES chutes(id)
);
CREATE TABLE IF NOT EXISTS scans (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  barcode TEXT NOT NULL,
  dest_code TEXT NOT NULL DEFAULT '',
  shift_id INTEGER NOT NULL,
  line_id INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT '待路由',
  chute_id INTEGER,
  bag_id INTEGER,
  divert_batch_id INTEGER,
  origin_bag_id INTEGER,
  scanned_at TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  FOREIGN KEY(shift_id) REFERENCES shifts(id),
  FOREIGN KEY(line_id) REFERENCES lines(id),
  FOREIGN KEY(chute_id) REFERENCES chutes(id)
);
CREATE TABLE IF NOT EXISTS route_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  scan_id INTEGER NOT NULL,
  chute_id INTEGER NOT NULL,
  action TEXT NOT NULL,
  created_at TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  FOREIGN KEY(scan_id) REFERENCES scans(id),
  FOREIGN KEY(chute_id) REFERENCES chutes(id)
);
CREATE TABLE IF NOT EXISTS divert_batches (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  shift_id INTEGER NOT NULL,
  code TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL DEFAULT '回流中',
  rebag_bag_id INTEGER,
  settled_at TEXT,
  created_at TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  FOREIGN KEY(shift_id) REFERENCES shifts(id)
);
CREATE TABLE IF NOT EXISTS bags (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  shift_id INTEGER NOT NULL,
  chute_id INTEGER NOT NULL,
  code TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL DEFAULT '集包中',
  kind TEXT NOT NULL DEFAULT '常规',
  divert_batch_id INTEGER,
  parent_bag_id INTEGER,
  sealed_count INTEGER NOT NULL DEFAULT 0,
  sealed_at TEXT,
  note TEXT NOT NULL DEFAULT '',
  FOREIGN KEY(shift_id) REFERENCES shifts(id),
  FOREIGN KEY(chute_id) REFERENCES chutes(id)
);
CREATE TABLE IF NOT EXISTS bag_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  bag_id INTEGER NOT NULL,
  scan_id INTEGER,
  action TEXT NOT NULL,
  from_bag_id INTEGER,
  to_bag_id INTEGER,
  divert_batch_id INTEGER,
  created_at TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS audit_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,
  ref_id INTEGER,
  action TEXT NOT NULL,
  created_at TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
"""

# 老库补列（只增不改）。承接口径固定为「强制新开袋」。
MIGRATIONS: dict[str, dict[str, str]] = {
    "scans": {"origin_bag_id": "INTEGER"},
    "divert_batches": {"rebag_bag_id": "INTEGER", "settled_at": "TEXT"},
    "bags": {
        "kind": "TEXT NOT NULL DEFAULT '常规'",
        "divert_batch_id": "INTEGER",
        "parent_bag_id": "INTEGER",
        "sealed_count": "INTEGER NOT NULL DEFAULT 0",
    },
}


def connect() -> sqlite3.Connection:
    from app.config import db_path

    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # isolation_level=None => 自动提交，事务由 db(immediate=True) 显式 BEGIN IMMEDIATE 控制
    conn = sqlite3.connect(path, check_same_thread=False, timeout=15, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=15000")
    return conn


@contextmanager
def db(immediate: bool = False):
    """immediate=True 时用 BEGIN IMMEDIATE 串行化写口，杜绝回流/封口并发双终态。"""
    conn = connect()
    try:
        if immediate:
            conn.execute("BEGIN IMMEDIATE")
        yield conn
        if immediate:
            conn.execute("COMMIT")
    except Exception:
        if immediate:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def init_db() -> None:
    # executescript 会隐式提交，故不复用 BEGIN IMMEDIATE；DDL 均为幂等。
    with db() as conn:
        conn.executescript(SCHEMA)
        for table, cols in MIGRATIONS.items():
            existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
            for name, ddl in cols.items():
                if name not in existing:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")


def row_to_dict(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row is not None else None


def rows_to_list(rows) -> list[dict]:
    return [dict(r) for r in rows]
