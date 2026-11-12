# 01-feedstitch（信汇）

Feedstitch — 多源目录/价目 Feed 归并落库：解析 → 业务键去重/冲突隔离 → 按源水位线增量或全量落库。

## 启动

```bash
docker compose up --build
```

| 入口 | 地址 |
| --- | --- |
| 前端 | http://localhost:5100 |
| API | http://localhost:10100 |

## 主链

配置源与夹具 → 触发 Run → 解析 / 去重 / 冲突隔离 → Canonical + 运行报告对账

## 种子说明

- **Alpha Catalog（clean）**：JSON，三件干净货号，增量可落库。
- **Beta Offers（dirty）**：JSONL，含与 Alpha 同键但价格不同的冲突行，以及迟到低 revision 行（底座增量会因水位线跳过）。
- **Gamma Shelf（xml）**：简易 XML，两件独立货号。

打开“运行台”选源跑一次增量，再看 Staging / 冲突 / Canonical 三页计数是否与本次 Run 报告一致。

## 死信重放（dead_letter_replay）

同键非键字段分歧的行会进冲突队列且不写 Canonical，随后在“冲突队列”页完成裁决与重放：

1. 勾选冲突并裁决（结果、时间立即落库，刷新/重启仍在）：
   - **接受传入（overwrite）**：重放时以传入侧覆盖写入主数据；
   - **保留主数据（keep）**：维持主数据不变并关闭冲突；
   - **作废（discard）**：忽略该冲突且不改主数据。
2. 对“已裁决”的冲突发起一次重放批次；仅 `overwrite` 会真正写 Canonical，
   `keep` / `discard` 绝不触碰主数据。冲突状态区分 `pending / adjudicated / replayed`。
3. 批次报告满足对账恒等式：`处理总数 = 写入主数据 + 关闭 + 作废`；
   报告同时记录 Canonical 批次前后实测条数（覆盖更新不改条数、新增才 +1），
   与 Canonical 列表页、总览页数字同源（均来自同一组 SQLite 计数查询）。
   明细见“重放批次”页。

相关接口：`GET /api/conflicts(/counts)`、`POST /api/conflicts/adjudicate`、
`POST /api/replay-batches`、`GET /api/replay-batches(/{{id}})`。

## 技术栈

Python 3.12 + FastAPI + SQLite；Vue 3 + Vite + Nginx。

## 0-1 切口（未实现）

`lookback_backfill` / `schema_drift_guard` / `webhook_push_source`
