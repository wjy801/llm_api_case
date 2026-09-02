# Flaky 治理 MVP 阶段4补充：非生产 Enforce 灰度与自动回退

## 1. 阶段目标

本补充阶段不直接授权正式 Smoke Enforce，而是建立固定、离线、可重复的非生产灰度链路，证明阶段4代码在真实 Runner 与 Jenkins 环境中可以完成“计划预检 → 实际治理 Skip → kill switch 回退执行”闭环。该 canary 是阶段4的运行验收补充，不替代后续“看板、报告、运维与完成验收”阶段5。

当前状态：`CANARY_VALIDATED / PRODUCTION_ENFORCE_NOT_AUTHORIZED`。

2026-09-02 容器化 Jenkins 验收证据：

- 专用 Job `flaky-enforce-stage5` 构建 `#7` 为 `SUCCESS`。
- `enforce-plan-gate.json` 为 `READY`，固定 6 项全部计划 `SKIP`。
- `enforce-execution-gate.json` 为 `PASSED`，实际治理 Skip 为 6、RUN 为 0。
- `rollback-execution-gate.json` 为 `PASSED`，RUN 为 6、实际治理 Skip 为 0。
- 构建结束后独占 canary 状态目录已清理，正式 Enforce/Probe 开关仍保持关闭。

## 2. 冻结边界

- 使用独立 `Jenkinsfile.enforce`，无 cron、无任意测试路径参数、无真实 API 调用。
- 固定目标为 `module/smoke/test_flaky_enforce_canary.py` 的 6 个离线参数化用例。
- 灰度数据库必须是 Workspace 外的新建绝对路径；每次构建独占，禁止复用生产或阶段3数据库。
- `prepare` 必须显式传入 `--acknowledge-nonproduction`，只生成固定 canary 身份的 ACTIVE 治理记录。
- 计划门禁要求分支为 `dev3`、Schema 4、模式为 `enforce`、完整性为 `OK`、无 fail-open、无业务 Skip，且 1～6 个收集项全部计划 `SKIP`。
- 执行门禁要求 6 个计划 Skip 全部核对为实际治理 Skip；任一缺失、额外 RUN、产物损坏或核对降级均返回 `ROLLBACK_REQUIRED`。
- 回退阶段只关闭 `QUALITY_FLAKY_AUTO_SKIP_ENABLE`，保持请求模式为 `enforce`，要求同 6 个用例全部 `RUN`、实际治理 Skip 为 0，并留下 6 个执行 marker。
- Enforce 执行即使失败，Jenkins 也必须继续尝试 kill-switch 回退，再统一给出构建结论。
- 机器产物可归档；SQLite、WAL、SHM、锁文件、凭据和绝对数据库路径不得归档。

## 3. 机器门禁

`python -m quality.flaky_enforce_gate` 提供两个操作：

- `prepare`：只对固定 6 用例创建一次性非生产 Schema 4 数据库。
- `verify`：校验 snapshot、decision、reconciliation 三类不可变产物的引用、checksum、模式、预算和实际 Skip 数。

门禁结果版本为 `flaky-enforce-gate.v1`：

| 状态 | 含义 |
| --- | --- |
| `READY` | plan-only 检查通过，可以进入受限 Enforce 执行 |
| `PASSED` | Enforce 或 kill-switch 回退的执行核对通过 |
| `BLOCKED` | 执行前条件不满足，禁止进入 Enforce |
| `ROLLBACK_REQUIRED` | Enforce 已执行但结果不可信，必须保持/恢复 kill switch 关闭 |

## 4. Jenkins 三段验收

1. Plan-only：双开关开启，收集 6 项但不执行，门禁必须为 `READY`。
2. Enforce：2 个 xdist worker 消费同一不可变计划，6 项全部治理 Skip，测试体 marker 必须不存在，门禁必须为 `PASSED`。
3. Rollback：仅把自动 Skip 开关设为 `0`，6 项全部执行并产生 marker，门禁必须为 `PASSED`。

Jenkins 最终清理本轮独占状态目录。正式 Smoke Job、阶段3 Probe Job、正式数据库和真实 API 凭据均不参与本阶段演练。

## 5. 验收门槛

- prepare 的路径、重复数据库、固定用例数量和身份校验测试通过。
- plan、execution、异常核对与 off 回退门禁测试通过。
- 本地真实 Runner 的 6 用例 xdist Enforce/rollback 双态测试通过。
- 容器 Jenkins 固定 Job 三段构建成功，归档门禁状态与本地结论一致。
- 全量 `tests/quality` 通过，运行后正式 Enforce/Probe 开关保持关闭。
