# Flaky 治理 MVP 阶段4：限定范围 Enforce 与回退

## 1. 阶段目标

阶段4把阶段2已验证的 Shadow 决策链扩展为可执行的限定范围 Enforce，同时保留确定性的 fail-open 和即时回退能力。本阶段只交付代码、测试与审计产物，不授权生产或 Jenkins 正式任务开启 Enforce。

## 2. 冻结边界

- 默认模式仍为 `off`。
- 仅当 `QUALITY_FLAKY_AUTO_SKIP_ENABLE=1` 且 `QUALITY_FLAKY_SKIP_MODE=enforce` 时，配置的有效模式才是 `enforce`；任一条件撤销立即回退为 `off`。
- 只允许控制器确认的 `dev3` 分支，以及策略包含的 `module/smoke/` 目录前缀。
- 快照必须绑定当前 run、当前策略 revision、有效时间窗和数据库 Schema 4，并通过内容 checksum 校验。
- 仅对 ACTIVE/RECOVERING 治理记录与收集身份精确匹配的用例计划 `SKIP`；同 case_id 的参数、环境、执行画像或 epoch 不同均不匹配。
- pytest worker 只读取不可变决策文件，禁止访问治理数据库。
- 决策文件、checksum 或任一已收集身份校验失败时，当前 worker 在添加任何治理 Skip 标记之前整批 fail-open，并记录 `flaky_decision_plan_invalid`。
- 已有 `skip`、`skipif`、`xfail` 属于业务语义，不追加治理标记，也不计入实际治理 Skip。

## 3. 决策与核对

| 场景 | 决策/行为 |
| --- | --- |
| kill switch 关闭或模式 `off` | `RUN` |
| 快照、分支、run、策略、时间、Schema、checksum 或身份异常 | fail-open `RUN` |
| 精确命中且模式 `shadow` | `WOULD_SKIP`，继续执行 |
| 精确命中且模式 `enforce` | 计划 `SKIP` |
| pytest 完整校验后执行计划 SKIP | 添加 `flaky-governance:<governance_id>` Skip 原因 |
| 计划 SKIP 未在执行结果中出现 | 核对 `DEGRADED`，诊断 `governance_skip_not_observed` |

决策产物分别统计 `RUN`、`WOULD_SKIP`、计划 `SKIP` 和 fail-open。核对产物独立统计实际治理 Skip；业务 Skip 不进入该计数。

## 4. 回退

紧急回退只需把 `QUALITY_FLAKY_AUTO_SKIP_ENABLE` 设为 `0`，无需改库、删治理记录或重建历史证据。下一轮生成 DISABLED 快照并全部 `RUN`。已经开始执行的单轮测试使用绑定该 run 的不可变决策，不接受执行中途替换。

## 5. 验收门槛

- 双开关与 kill switch 配置测试通过。
- 精确匹配、越界/损坏 fail-open、业务 Skip 隔离和实际 Skip 核对测试通过。
- 6 个并发用例在 xdist worker 中只读同一决策产物并全部按计划治理 Skip。
- 阶段2 Shadow 回归及全部 `tests/quality` 通过。
- 测试结束后环境保持 Enforce 未启用；推送仅面向 GitLab `dev3`。

## 6. 验证记录

- 阶段4聚焦测试：59 passed。
- 受影响的只读查询与 Dashboard 回归：39 passed，22 warnings。
- 全量 `tests/quality`：466 passed，44 warnings。
- 6 个 xdist 并发治理用例均按计划 Skip。
- 测试完成后 `QUALITY_FLAKY_AUTO_SKIP_ENABLE`、`QUALITY_FLAKY_SKIP_MODE`、`QUALITY_FLAKY_PROBE_TRIGGER_ENABLE` 均未设置。

当前状态：`CODE_VALIDATED / ENFORCE_NOT_ENABLED`。
