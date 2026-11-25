# 分拣过机台（Sortbay）

骨架：过机扫描装载 → 口门路由裁决 → 错分回流批次 → 封袋班次对账

- 前端 http://127.0.0.1:6600
- API http://127.0.0.1:11600

## 回流后再入袋承接的架构口径（全局唯一）

**选定：Arm B — 「强制新开袋承接」。**

回流后再裁决的件**禁止写入原袋**，必须落入新开的「回流承接」袋（`bags.kind='回流承接'`），
并保留与回流批次、原袋的关联。原袋件数由 `scans.bag_id` 派生，回流移出时 `bag_id` 置空、
`origin_bag_id` 留痕，因此原袋件数只会回落、不会被偷增；已封袋的件数另冻结在
`bags.sealed_count`，对账与总览都以它为准校验，任何路径都不得改写。

要点：

| 环节 | 落点 | 约束 |
| --- | --- | --- |
| 回流写口 | `POST /api/回流批次`（可带 `批次编号` 并入现有批次） | 已封原袋禁止移出；`BEGIN IMMEDIATE` 串行化 |
| 再裁决写口 | `POST /api/扫描/{id}/路由` | 回流件走「回流再裁决」，清空 `bag_id`、保留批次关联 |
| 入袋写口 | 常规 `POST /api/封袋/{id}/入袋` / 承接 `POST /api/回流承接/入袋` | 常规口拒绝回流件；承接口自动新开/复用同批次同口门开口承接袋，绝不写回原袋 |
| 封口写口 | `POST /api/封袋/{id}/封口` | 冻结 `sealed_count`；袋内有未闭合回流件则拒绝 |
| 总览对账 | `GET /api/总览`、`GET /api/班次/对账` | 同口径输出违规（已封袋件数漂移、回流件写回原袋） |
| 审计/重放 | `GET /api/审计`、`GET /api/回流批次/{id}/重放`、`GET /api/扫描/{id}/承接重放` | append-only `bag_events` + `audit_events` 可重建承接结局 |

并发：所有写口在 `BEGIN IMMEDIATE` 事务内复读状态，回流与封口并发只会产生一个终态；
半截回滚后袋关联与已封袋件数不变。

## 测试

```bash
python3 -m venv .venv && .venv/bin/pip install -r backend/requirements.txt pytest httpx
cd backend && ../.venv/bin/python -m pytest tests -q
```

- `tests/test_redivert_rebag.py`：原袋仍集包再入、原袋已封误入、强制新开袋/复用、
  半截回滚、重放、总览对账、审计联动。
- `tests/test_concurrency.py`：真实文件库多线程并发，回流 vs 封口、并发承接对已封袋不偷增。
