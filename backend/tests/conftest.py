import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

# 在导入 app 之前钉死独立数据库，避免污染 dev 库
_TMPDIR = tempfile.mkdtemp(prefix="sortbay-test-")
os.environ["SORTBAY_DB"] = str(Path(_TMPDIR) / "test.db")

from fastapi.testclient import TestClient  # noqa: E402
import app.main as main_module  # noqa: E402
from app.db import db, init_db  # noqa: E402


@pytest.fixture()
def client():
    # 每个用例用全新库
    db_file = Path(_TMPDIR) / f"test-{os.urandom(4).hex()}.db"
    os.environ["SORTBAY_DB"] = str(db_file)
    init_db()
    with db() as conn:
        conn.execute("INSERT INTO lines(name, active) VALUES ('一号皮带', 1)")
        line_id = conn.execute("SELECT id FROM lines WHERE name='一号皮带'").fetchone()["id"]
        conn.execute(
            "INSERT INTO shifts(business_day, name, sealed) VALUES ('2026-11-25', '早班', 0)"
        )
        for code, title in [("A01", "华北格"), ("A02", "华东格"), ("B01", "西南格")]:
            conn.execute(
                "INSERT INTO chutes(line_id, code, title, status, soft_cap) VALUES (?,?,?, '口门开', 120)",
                (line_id, code, title),
            )
        chute = {r["code"]: r["id"] for r in conn.execute("SELECT id, code FROM chutes")}
        for dest, cc in [("110", "A01"), ("210", "A02"), ("510", "B01")]:
            conn.execute(
                "INSERT INTO route_rules(dest_code, chute_id, note) VALUES (?,?, 't')",
                (dest, chute[cc]),
            )
        conn.execute(
            "INSERT INTO settings(key, value) VALUES ('回流承接策略', '强制新开袋')"
        )
    yield TestClient(main_module.app)


def make_scan(client, barcode="YB001", dest="110"):
    r = client.post("/api/扫描", json={"条码": barcode, "目的地码": dest})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def route(client, scan_id):
    r = client.post(f"/api/扫描/{scan_id}/路由")
    assert r.status_code == 200, r.text
    return r.json()


def open_bag(client, chute_code="A01"):
    chutes = client.get("/api/口门").json()
    cid = next(c["id"] for c in chutes if c["code"] == chute_code)
    r = client.post("/api/封袋/开袋", json={"口门编号": cid})
    assert r.status_code == 200, r.text
    return r.json()


def add_regular(client, bag_id, scan_id):
    return client.post(f"/api/封袋/{bag_id}/入袋", json={"扫描编号": scan_id})


def divert(client, scan_id, batch_id=None):
    payload = {"扫描编号": scan_id}
    if batch_id is not None:
        payload["批次编号"] = batch_id
    r = client.post("/api/回流批次", json=payload)
    assert r.status_code == 200, r.text
    return r.json()
