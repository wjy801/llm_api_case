# Flaky 治理 MVP 阶段 2 Shadow 观察记录

## 1. 结论

2026-09-01 在 `dev3` 分支完成 10 个连续真实 Smoke Run。每轮权威收集并以 6 个 xdist worker 并发执行相同的 6 个用例，共执行 60 次，60 次通过；10 轮原始 pytest 退出码均为 0。

Shadow 证据逐轮核对结果：

- snapshot 均为 `READY`，`mode_requested=shadow`，`mode_effective=shadow`，分支为 `dev3`。
- 每轮 6 个决策均为 `RUN`，`WOULD_SKIP=0`、`SKIP=0`、`fail_open=0`。
- 每轮 reconciliation 均为 `OK`，计划与观测均为 6，无缺失、重复、意外用例或意外 skipped。
- 6 个 nodeid、case_id、param_hash、规范路径和 `parallel` execution profile 在 10 轮中保持稳定。
- 所有规范路径均位于 `module/smoke/`，未发现身份扩大或范围外候选。
- 10 个 run_id 互不相同，snapshot、decision plan、reconciliation 的 run_id 与 checksum 引用逐轮一致。

因此阶段 2 观察门禁达到 `SHADOW_VALIDATED / ENFORCE_NOT_AUTHORIZED`。本记录不授权 Enforce。

## 2. 观察边界

- 环境：`china`。
- 数据库：外置独占 SQLite，观察前后 `flaky-db-check` 均为 Schema v3、状态 `OK`。
- 观察前后治理列表均为空，没有 ACTIVE/RECOVERING 候选；所以 snapshot entry 为 0，所有决策理由均为 `governance_not_matched`。
- 本轮真实观察验证无幽灵候选、无范围扩大和无实际 Skip，但没有真实覆盖治理候选命中后的 `WOULD_SKIP`；该分支由阶段 2 自动化测试覆盖。
- 观察时 HEAD 为 `92c3bf5`。工作区已有用户未提交改动，本次观察未清理或覆盖这些改动。
- 用户将执行范围明确收窄为以下 6 个并发用例。此前启动的 87 项全量运行在串行阶段被中止，保留为部分证据，但不计入本次 10 轮窗口。

## 3. 固定用例与身份

| 用例参数 ID | param_hash | 路径 | profile |
| --- | --- | --- | --- |
| `images_generations_openai_seedream_block` | `4c81c865cec6a87f` | `module/smoke/protocol_testing/image_model/test_protocol_interception.py` | parallel |
| `images_edits_openai_seedream_block` | `cad59bcef0787e26` | `module/smoke/protocol_testing/image_model/test_protocol_interception.py` | parallel |
| `chat_completions_openai_seedream_block` | `2dc03463210e1fba` | `module/smoke/protocol_testing/image_model/test_protocol_interception.py` | parallel |
| `responses_openai_seedream_block` | `079274ec4e0d27b6` | `module/smoke/protocol_testing/image_model/test_protocol_interception.py` | parallel |
| `images_generations_openai_seedance_oversea_block` | `be6f4244e2d49056` | `module/smoke/protocol_testing/video_model/test_protocol_interception.py` | parallel |
| `images_generations_openai_doubao_seedance_block` | `3d12a31f238cee27` | `module/smoke/protocol_testing/video_model/test_protocol_interception.py` | parallel |

## 4. 逐轮结果

时间均为 UTC；本地时区为 Asia/Shanghai。

| 轮次 | run_id | 开始时间 | pytest | RUN | WOULD_SKIP | SKIP | fail-open | reconciliation |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| 01 | `local-20260901T083702Z-1457cad5` | `2026-09-01T08:37:02Z` | 0 | 6 | 0 | 0 | 0 | OK 6/6 |
| 02 | `local-20260901T083803Z-75ea794a` | `2026-09-01T08:38:03Z` | 0 | 6 | 0 | 0 | 0 | OK 6/6 |
| 03 | `local-20260901T083822Z-4b1f1fcd` | `2026-09-01T08:38:22Z` | 0 | 6 | 0 | 0 | 0 | OK 6/6 |
| 04 | `local-20260901T083842Z-3e86514c` | `2026-09-01T08:38:42Z` | 0 | 6 | 0 | 0 | 0 | OK 6/6 |
| 05 | `local-20260901T083900Z-d264c9aa` | `2026-09-01T08:39:00Z` | 0 | 6 | 0 | 0 | 0 | OK 6/6 |
| 06 | `local-20260901T083919Z-c829d291` | `2026-09-01T08:39:19Z` | 0 | 6 | 0 | 0 | 0 | OK 6/6 |
| 07 | `local-20260901T083940Z-2d3d873c` | `2026-09-01T08:39:40Z` | 0 | 6 | 0 | 0 | 0 | OK 6/6 |
| 08 | `local-20260901T083957Z-4023b90c` | `2026-09-01T08:39:57Z` | 0 | 6 | 0 | 0 | 0 | OK 6/6 |
| 09 | `local-20260901T084014Z-dccbb124` | `2026-09-01T08:40:14Z` | 0 | 6 | 0 | 0 | 0 | OK 6/6 |
| 10 | `local-20260901T084033Z-cd3bed54` | `2026-09-01T08:40:33Z` | 0 | 6 | 0 | 0 | 0 | OK 6/6 |

## 5. 证据位置

每轮证据位于 `reports/quality-shadow-observation/six-case-run-NN/`，至少包含：

- `run.json`
- `execution-result.json`
- `flaky-skip-snapshot.json`
- `flaky-skip-decisions.json`
- `flaky-skip-reconciliation.json`
- `merged/case-results.jsonl`
- `junit/quality-parallel.xml`

不计入窗口的中止全量运行保留在 `reports/quality-shadow-observation/run-01/`。
