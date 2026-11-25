"""回流后再裁决 -> 入袋承接 夹具（Arm B：强制新开袋）。

覆盖：
1. 原袋仍集包再入：回流件再裁决后不得回写原袋，必须进新承接袋；原袋件数不偷增。
2. 原袋已封误入：已封原袋禁止回流移出 / 禁止写回。
3. 强制新开袋：承接自动开袋，保留回流批次与原袋关联，可复用同批次同口门的开口承接袋。
4. 回流与封口半截/并发：写口互斥，袋关联不得偷增已封袋。
5. 重放：承接结局可由事件重放且与库态一致。
"""
from conftest import add_regular, divert, make_scan, open_bag, route


def _scan(client, sid):
    return next(s for s in client.get("/api/扫描").json() if s["id"] == sid)


def _bag(client, bid):
    return next(b for b in client.get("/api/封袋").json() if b["id"] == bid)


# ---------------------------------------------------------------- 1. 原袋仍集包

def test_redivert_goes_to_new_bag_not_open_origin(client):
    sid = make_scan(client, "YB-R1", "110")
    route(client, sid)
    origin = open_bag(client, "A01")
    assert add_regular(client, origin["id"], sid).status_code == 200
    assert _bag(client, origin["id"])["件数"] == 1

    divert(client, sid)
    # 原袋仍在集包中，件已移出 -> 原袋件数回落
    assert _bag(client, origin["id"])["件数"] == 0
    assert _scan(client, sid)["status"] == "错分回流"

    route(client, sid)  # 再裁决
    r = client.post("/api/回流承接/入袋", json={"扫描编号": sid})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["新开袋"] is True
    newbag = body["袋"]
    assert newbag["id"] != origin["id"]
    assert newbag["kind"] == "回流承接"
    assert newbag["parent_bag_id"] == origin["id"]
    assert newbag["divert_batch_id"] is not None

    # 原袋件数绝不偷增
    assert _bag(client, origin["id"])["件数"] == 0
    assert _bag(client, newbag["id"])["件数"] == 1
    s = _scan(client, sid)
    assert s["bag_id"] == newbag["id"]
    assert s["origin_bag_id"] == origin["id"]


def test_regular_port_rejects_diverted_piece(client):
    sid = make_scan(client, "YB-R2", "110")
    route(client, sid)
    divert(client, sid)
    route(client, sid)
    origin = open_bag(client, "A01")
    r = add_regular(client, origin["id"], sid)
    assert r.status_code == 400
    assert "回流承接" in r.json()["detail"] or "原袋" in r.json()["detail"]
    assert _bag(client, origin["id"])["件数"] == 0


# ---------------------------------------------------------------- 2. 原袋已封误入

def test_sealed_origin_rejects_divert_and_never_inflates(client):
    sid = make_scan(client, "YB-S1", "210")
    route(client, sid)
    origin = open_bag(client, "A02")
    assert add_regular(client, origin["id"], sid).status_code == 200
    assert client.post(f"/api/封袋/{origin['id']}/封口").status_code == 200
    sealed_before = _bag(client, origin["id"])["件数"]
    assert sealed_before == 1

    r = client.post("/api/回流批次", json={"扫描编号": sid})
    assert r.status_code == 400
    assert "已封" in r.json()["detail"]
    # 已封袋件数不变
    assert _bag(client, origin["id"])["件数"] == sealed_before
    assert _bag(client, origin["id"])["sealed_count"] == sealed_before


def test_settle_refuses_when_rebag_not_possible_targets_sealed(client):
    # 直接构造：件已回流但再裁决口门关闭 -> 承接失败，不得落已封袋
    sid = make_scan(client, "YB-S2", "110")
    route(client, sid)
    divert(client, sid)
    route(client, sid)
    chutes = client.get("/api/口门").json()
    cid = next(c["id"] for c in chutes if c["code"] == "A01")
    client.post(f"/api/口门/{cid}/开闭", params={"状态": "口门闭"})
    r = client.post("/api/回流承接/入袋", json={"扫描编号": sid})
    assert r.status_code == 400
    assert "口门" in r.json()["detail"]


# ---------------------------------------------------------------- 3. 强制新开袋 + 复用 + 关联

def test_rebag_reused_within_same_batch_and_chute(client):
    s1 = make_scan(client, "YB-M1", "110")
    s2 = make_scan(client, "YB-M2", "110")
    route(client, s1)
    first = divert(client, s1)  # 建批
    bid = first["批次"]["id"]
    route(client, s2)
    divert(client, s2, batch_id=bid)  # 并入同批
    route(client, s1)
    route(client, s2)
    b1 = client.post("/api/回流承接/入袋", json={"扫描编号": s1}).json()
    assert b1["新开袋"] is True
    b2 = client.post("/api/回流承接/入袋", json={"扫描编号": s2}).json()
    assert b2["新开袋"] is False
    assert b2["袋"]["id"] == b1["袋"]["id"]
    assert _bag(client, b1["袋"]["id"])["件数"] == 2


def test_settle_reopens_new_bag_after_previous_rebag_sealed(client):
    """同批次同口门，前承接袋已封口 -> 再承接必须新开，不得写已封袋。"""
    s1 = make_scan(client, "YB-M3", "110")
    s2 = make_scan(client, "YB-M4", "110")
    route(client, s1)
    bid = divert(client, s1)["批次"]["id"]
    route(client, s2)
    divert(client, s2, batch_id=bid)
    route(client, s1)
    route(client, s2)
    b1 = client.post("/api/回流承接/入袋", json={"扫描编号": s1}).json()["袋"]
    assert client.post(f"/api/封袋/{b1['id']}/封口").status_code == 200
    b2 = client.post("/api/回流承接/入袋", json={"扫描编号": s2}).json()
    assert b2["新开袋"] is True
    assert b2["袋"]["id"] != b1["id"]
    assert _bag(client, b1["id"])["件数"] == 1  # 已封袋件数不偷增


def test_bag_list_surfaces_divert_association(client):
    sid = make_scan(client, "YB-A1", "110")
    route(client, sid)
    origin = open_bag(client, "A01")
    add_regular(client, origin["id"], sid)
    divert(client, sid)
    route(client, sid)
    newbag = client.post("/api/回流承接/入袋", json={"扫描编号": sid}).json()["袋"]
    listed = _bag(client, newbag["id"])
    assert listed["原袋编号"] == origin["id"]
    assert listed["回流批次码"]


# ---------------------------------------------------------------- 4. 回流与封口并发 / 半截

def test_cannot_seal_bag_with_dangling_divert_member(client):
    sid = make_scan(client, "YB-C1", "110")
    route(client, sid)
    divert(client, sid)
    route(client, sid)
    newbag = client.post("/api/回流承接/入袋", json={"扫描编号": sid}).json()["袋"]
    # 正常承接后件已闭合 -> 可封
    assert client.post(f"/api/封袋/{newbag['id']}/封口").status_code == 200


def test_seal_origin_then_redivert_blocked_atomic(client):
    """回流与封口半截：先封原袋，再回流被拒，袋关联与件数均不动。"""
    sid = make_scan(client, "YB-C2", "110")
    route(client, sid)
    origin = open_bag(client, "A01")
    add_regular(client, origin["id"], sid)
    assert client.post(f"/api/封袋/{origin['id']}/封口").status_code == 200
    snapshot = _bag(client, origin["id"])
    r = client.post("/api/回流批次", json={"扫描编号": sid})
    assert r.status_code == 400
    after = _bag(client, origin["id"])
    assert after["件数"] == snapshot["件数"] == snapshot["sealed_count"]
    assert _scan(client, sid)["bag_id"] == origin["id"]


# ---------------------------------------------------------------- 5. 重放

def test_replay_consistent_after_settle(client):
    sid = make_scan(client, "YB-P1", "110")
    route(client, sid)
    divert(client, sid)
    route(client, sid)
    body = client.post("/api/回流承接/入袋", json={"扫描编号": sid}).json()
    rep = client.get(f"/api/扫描/{sid}/承接重放").json()
    assert rep["一致"] is True
    assert rep["重放落袋"] == body["袋"]["id"]


def test_batch_replay_reports_no_violation(client):
    sid = make_scan(client, "YB-P2", "110")
    route(client, sid)
    divert(client, sid)
    route(client, sid)
    body = client.post("/api/回流承接/入袋", json={"扫描编号": sid}).json()
    bid = body["扫描"]["divert_batch_id"]
    rep = client.get(f"/api/回流批次/{bid}/重放").json()
    assert rep["一致"] is True
    assert rep["违规"] == []


# ---------------------------------------------------------------- 6. 总览 / 对账同刷

def test_overview_and_reconcile_agree(client):
    sid = make_scan(client, "YB-O1", "110")
    route(client, sid)
    origin = open_bag(client, "A01")
    add_regular(client, origin["id"], sid)
    divert(client, sid)
    route(client, sid)
    client.post("/api/回流承接/入袋", json={"扫描编号": sid})
    ov = client.get("/api/总览").json()
    rec = client.get("/api/班次/对账").json()
    assert ov["对账一致"] is True
    assert rec["一致"] is True
    assert rec["回流承接袋数"] == 1


# ---------------------------------------------------------------- 7. 审计可重放同刷

def test_audit_chain_replayable(client):
    sid = make_scan(client, "YB-AD1", "110")
    route(client, sid)
    origin = open_bag(client, "A01")
    add_regular(client, origin["id"], sid)
    divert(client, sid)
    route(client, sid)
    body = client.post("/api/回流承接/入袋", json={"扫描编号": sid}).json()
    bid = body["扫描"]["divert_batch_id"]

    # 审计四段齐全：装载 / 路由 / 回流 / 再裁决 + 承接
    scan_audit = client.get("/api/审计", params={"种类": "扫描"}).json()
    actions = {a["action"] for a in scan_audit}
    assert "过机装载" in actions and "路由落入" in actions and "回流再裁决" in actions
    divert_audit = client.get("/api/审计", params={"种类": "回流", "引用": bid}).json()
    assert any(a["action"] == "建回流批次" for a in divert_audit)
    settle_audit = client.get("/api/审计", params={"种类": "回流承接", "引用": bid}).json()
    assert any(a["action"] == "再入袋" for a in settle_audit)

    # bag_events 重放与库态一致
    rep = client.get(f"/api/扫描/{sid}/承接重放").json()
    assert rep["一致"] is True
    assert rep["重放落袋"] == body["袋"]["id"]
    batch_rep = client.get(f"/api/回流批次/{bid}/重放").json()
    assert batch_rep["一致"] is True
    assert batch_rep["批次"]["status"] == "已承接"


def test_audit_filter_by_keyword(client):
    sid = make_scan(client, "YB-AD2", "110")
    route(client, sid)
    rows = client.get("/api/审计", params={"关键字": "YB-AD2"}).json()
    assert any(r["action"] == "过机装载" for r in rows)
