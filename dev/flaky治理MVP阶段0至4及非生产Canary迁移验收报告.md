# Flaky 治理 MVP 阶段0至4及非生产 Canary 迁移验收报告

## 1. 报告信息

- 验收日期：2026-09-02
- 验收范围：Flaky 治理 MVP 阶段0至阶段4及阶段4非生产 Enforce canary 补充
- 代码分支：GitLab `dev3`
- 非生产 canary Jenkins 验证代码：`5ae6ec2`
- 运行环境：容器化 GitLab/Jenkins、Windows `probe-controller`、受限 `probe-target-restricted`
- 总体结论：`COMPONENTS_INTEGRATED / FINAL_MVP_ACCEPTANCE_PENDING`

本报告验收的是已经完成开发的 Flaky 治理数据契约、状态机、基础只读看板、Shadow、Probe 恢复证据、限定范围 Enforce 机制和非生产自动回退组件，并确认这些组件可安全迁入主体框架。它不代表阶段5“看板、报告、运维与完成验收”已经实现，不代表 `module/smoke/` 业务用例集已全部通过，也不授权生产数据库迁移、生产 Probe 或生产 Enforce。

## 2. 总体验收结论

阶段0至阶段4及非生产 canary 补充的约定交付物和验证已经完成，可迁入主体框架：

- 数据契约、显式 Schema 迁移、跨进程单写者和 NORMAL/Probe 隔离已实现并通过测试。
- Shadow 决策、不可变快照、精确身份匹配、fail-open 和核对产物已验证。
- Probe 的创建、投递、认领、证据签名、异常对账、取消和人工关闭链路已完成容器化真实演练。
- Enforce 只在固定的 6 个离线 canary 用例上完成计划、执行和 kill-switch 回退验证。
- 正式开关保持关闭，Probe 正式最小间隔保持 30 分钟，未对真实 API 或正式 Smoke Job 执行 Enforce。
- 最新全量 `tests/quality` 结果为 `472 passed, 44 warnings`。

因此，本次可以验收“已开发组件迁入主体框架”，但完整阶段5和最终 MVP 验收仍未完成，生产启用继续为 No-Go。

## 3. 分阶段结论

| 阶段 | 验收状态 | 关键结论 |
| --- | --- | --- |
| 阶段0：基线与契约 | `CONTRACT_READY / PRODUCTION_AUDIT_COMPLETE` | 完成冻结契约、回放 fixture 和当前 v2 SQLite 的一致性副本只读脱敏审计；不授权生产变更 |
| 阶段1：Schema 与状态机 | `LOCAL_V3_READY / PRODUCTION_MIGRATION_BLOCKED` | 完成显式 `0003`、单写者锁、NORMAL/Probe 隔离、attempt/evidence 和人工关闭门禁；未执行生产迁移 |
| 阶段2：看板与 Shadow | `SHADOW_VALIDATED / ENFORCE_NOT_AUTHORIZED` | 完成只读看板、快照、决策与核对；阶段3后续真实演练确认 Shadow 身份和范围边界有效 |
| 阶段3：Probe 闭环 | `PROBE_VALIDATED / ENFORCE_NOT_AUTHORIZED` | 成功、可信失败、响应不确定和运行中取消四类真实演练完成；最终只生成关闭资格，仍需人工 close |
| 阶段4：限定 Enforce | `CODE_VALIDATED / ENFORCE_NOT_ENABLED` | 双开关、精确匹配、fail-open、业务 Skip 隔离、并发只读计划和即时回退通过代码级验证 |
| 阶段4补充：非生产灰度 | `CANARY_VALIDATED / PRODUCTION_ENFORCE_NOT_AUTHORIZED` | 固定 6 用例在 Jenkins 完成 plan-only、Enforce 和 rollback 三段闭环，独占状态已清理；不替代完整阶段5 |

阶段2文档中的 `SHADOW_READY / OBSERVATION_PENDING` 是观察窗口完成前的历史快照；阶段3验收记录中的 `SHADOW_VALIDATED / ENFORCE_NOT_AUTHORIZED` 是后续生效结论。

## 4. 核心验收矩阵

| 验收域 | 结果 | 证据摘要 |
| --- | --- | --- |
| v2 审计与契约冻结 | 通过 | 一致性副本只读审计：44 Run、1717 observation、132 state、281 transition、0 governance；敏感字段脱敏 |
| Schema 与一致性 | 通过 | 显式迁移、过新/待迁移拒绝、双进程争锁和回滚测试通过；阶段3收尾 `flaky-db-check` 为 Schema 4、`issue_codes=[]` |
| NORMAL/Probe 隔离 | 通过 | Probe evidence 不进入 NORMAL history，不触发 detection reprojection；达到门槛不自动关闭 |
| Dashboard 与 Shadow | 通过 | 只读 DTO、分页、转义、健康状态、不可变 snapshot/decision/reconciliation 和 checksum 校验通过 |
| Probe 投递协议 | 通过 | 固定 Job、参数 allowlist、claim-before-checkout、token hash、幂等投递和单 build claim 通过 |
| Probe 异常语义 | 通过 | `TRUSTED_FAIL`、`NON_COUNTING`、`DISPATCH_UNKNOWN`、重复 queue 和 kill-switch 取消均按冻结状态机收敛 |
| 限定 Enforce | 通过 | 固定分支与目录、精确身份、整批 fail-open、业务 Skip 隔离和实际治理 Skip 核对通过 |
| 并发执行 | 通过 | 6 个参数化用例由 2 个 xdist worker 只读同一不可变计划，未访问治理数据库 |
| 自动回退 | 通过 | 关闭 `QUALITY_FLAKY_AUTO_SKIP_ENABLE` 后，同 6 用例全部 RUN，治理 Skip 为 0 |
| 数据与凭据清理 | 通过 | canary SQLite/WAL/SHM/锁文件未归档；Jenkins `#7` 独占状态目录已删除，状态根目录为空 |
| 生产启用 | 未授权 | 正式 Enforce、正式 Probe trigger 和生产数据库迁移均不在本次验收授权内 |

## 5. 自动化与运行证据

### 5.1 测试演进

以下数字是各阶段当时的累计测试快照，不应相加：

| 检查点 | 结果 |
| --- | --- |
| 阶段0受控 Quality | 379 passed |
| 阶段3收尾 Quality | 461 passed，44 warnings |
| 阶段4全量 Quality | 466 passed，44 warnings |
| 非生产 canary 收口时全量 Quality | 472 passed，44 warnings |
| 非生产 canary 聚焦测试 | 6 passed |

44 条 warning 来自 FastAPI 对 `asyncio.iscoroutinefunction` 的弃用提示，未导致测试失败；后续升级 Python/FastAPI 时应消除。

### 5.2 阶段3真实 Probe 演练

| 场景 | 结果 |
| --- | --- |
| 5 次合格 PASS | 进入 `READY_TO_CLOSE`；人工 close 后 governance 为 `CLOSED`，generation 增加 |
| 已有 PASS 后可信失败 | 新证据为 `TRUSTED_FAIL/APPLIED`；attempt 为 `FAILED`，governance 回到 `ACTIVE` |
| Jenkins 响应丢失与重复 queue | 先进入 `DISPATCH_UNKNOWN`；仅一个 build claim，重复 build 被拒绝，未重复导入 evidence |
| kill-switch 运行中取消 | 先进入 `CANCEL_REQUESTED`；Jenkins 终态后收敛为 `CANCELLED` 并释放容量 |

演练期间经授权临时使用 5 分钟间隔；验收后代码和 Job 均恢复固定 30 分钟。Probe trigger 已关闭，治理 Skip 在阶段3演练期间始终未启用。

### 5.3 阶段4补充 Jenkins Enforce canary

专用 Job `flaky-enforce-stage5` 构建 `#7` 为 `SUCCESS`，执行代码为 `5ae6ec2`：

| 门禁 | 状态 | 关键计数 |
| --- | --- | --- |
| `enforce-plan-gate.json` | `READY` | planned 6，planned SKIP 6 |
| `enforce-execution-gate.json` | `PASSED` | RUN 0，实际治理 Skip 6 |
| `rollback-execution-gate.json` | `PASSED` | RUN 6，实际治理 Skip 0 |

流水线无 cron、无任意测试路径参数、无真实 API 调用。构建前清理专用 `reports`，避免历史 marker 或产物造成假阳性；构建后清理 Workspace 外的本轮独占 canary 状态目录。

## 6. 安全与运维检查

- Probe controller 与受限 target 使用独立节点和身份；target 无宿主机目录挂载，不能读取 controller checkout、数据库、HMAC key 或 Jenkins service credential。
- 阶段3对数据库、工作区、Jenkins 控制台和归档产物执行明文敏感值扫描，结果为 0 命中。
- 非生产 canary 只归档机器门禁与运行产物，不归档数据库、锁、凭据或绝对数据库路径。
- Jenkins SCM 在控制器容器内使用容器网络地址，在 Windows agent 上使用宿主机映射地址；两次检出均固定校验 `dev3` HEAD。
- 正式默认状态仍为 `QUALITY_FLAKY_AUTO_SKIP_ENABLE=0`、`QUALITY_FLAKY_SKIP_MODE=off`、`QUALITY_FLAKY_TRIGGER_ENABLE=0`。
- 本轮代码只推送 GitLab `dev3` 和阶段分支；未访问或推送 GitHub。

## 7. 未完成项、已知问题与接受风险

1. `module/smoke/` 用例集中仍存在已知未修复失败；本次没有把业务 Smoke 全量通过作为 Flaky 治理验收证据。非生产 canary 只执行固定的 6 个离线用例。
2. 未对生产数据库执行 Schema 迁移；任何生产迁移都需要独立备份、恢复演练、变更窗口和审批。
3. 未授权正式 Enforce，也未授权正式 Probe trigger。canary 成功只证明受限非生产链路有效。
4. 阶段3保留两项已接受风险：管理网匿名调用者可能占用 Probe 配额；极端的 queue 响应丢失且外部删除可能需要人工处置 `DISPATCH_UNKNOWN`。
5. SQLite 方案只满足当前单机单写者边界，不等同于跨主机高可用存储。
6. 现有 44 条依赖弃用 warning 不阻塞本次验收，但应纳入依赖升级计划。

## 8. Go/No-Go 决策

### Go

- 接受阶段0至阶段4及非生产 canary 已开发组件迁入主体框架。
- 允许继续开展下一阶段的生产启用方案设计、权限加固、生产迁移预演和业务 Smoke 基线修复。
- 允许继续在固定非生产环境重复 Shadow、Probe 和 Enforce canary 演练。

### No-Go

- 不得因本报告直接开启正式 Smoke Enforce。
- 不得因本报告直接开启正式 Probe trigger。
- 不得把 6 个离线 canary 结果解释为业务 Smoke 用例集通过。
- 不得把本迁移报告解释为阶段5“看板、报告、运维与完成验收”已经完成。
- 不得未经独立审批迁移或写入生产 Flaky 数据库。

## 9. 最终签署结论

阶段0至阶段4及非生产 canary 已开发组件在冻结范围内完成迁移验收，当前状态为：

`COMPONENTS_INTEGRATED / FINAL_MVP_ACCEPTANCE_PENDING`

后续必须继续依据《flaky治理MVP阶段5看板报告运维与完成验收.md》完成统一查询/报告、完整性工具、备份恢复、运行手册和最终异常链验收。若要进入生产启用，还需要完成业务 Smoke 失败基线处置、生产数据库迁移与恢复演练、权限模型确认、正式开关变更审批以及分批灰度和回退值守；这些事项均不因本报告签署而自动获得授权。
