# Flaky 治理 MVP 分阶段开发计划

## 1. 文档定位

本计划不是从零建设 Flaky 平台，而是在仓库现有能力上补齐一条安全、可回滚的治理闭环：

```text
可信 NORMAL Run
  -> 历史导入与自动检测
  -> 人工确认隔离（owner / reason / expires_at）
  -> 下一轮 Smoke 精确 Skip
  -> 看板一键发起固定 Commit 的 Probe
  -> 可信恢复证据
  -> 人工关闭隔离
  -> 下一轮 Smoke 恢复执行
```

仓库当前已经具备以下基础，不在 MVP 中重复实现：

- P0 质量事实、完整性校验和原子产物写入。
- Flaky SQLite v2、历史导入、幂等 Run 导入和数据库检查。
- `case_id + param_hash + environment + execution_profile + state_epoch` 身份模型。
- `OBSERVING / STABLE / SUSPECTED / CONFIRMED` 自动检测。
- `QUARANTINED / RECOVERING` 人工治理投影及 owner、原因、到期时间。
- Flaky CLI、`flaky-import.json`、`flaky-evaluation.json` 和 Pipeline Summary。

MVP 只新增：治理驱动的精确 Skip、独立 Probe 证据、人工关闭门禁、动态看板与一键验证，以及必要的审计与运维能力。

## 2. 本次修订的关键决策

### 2.1 检测不直接改变执行

`CONFIRMED` 只表示“历史证据符合 Flaky 规则”，不能自动 Skip。只有人工创建的、仍未关闭的治理记录才有 Skip 资格，而且必须包含 owner、reason 和 expires_at。

因此：

```text
自动检测结果 != 人工治理决定 != pytest 执行决定
```

这条边界可避免把真实回归、环境故障或规则误判自动隐藏起来。

### 2.2 复用现有治理记录，不新建平行的 work item 模型

现有 `flaky_governance` 已经是一次治理 occurrence。MVP 不再引入 `OPEN / VERIFYING / READY_TO_CLOSE / CLOSED` 第二套 work item 状态，而是在现有治理记录下增加 verification attempt。

### 2.3 Probe 证据与 NORMAL 检测历史严格隔离

Probe 只回答“指定修复 Commit 上，这个精确用例是否满足恢复门槛”，不得写入 `case_observation`，不得进入自动检测窗口，不得改变 `detected_state`，也不能作为普通 Smoke PASS 参与稳定性统计。所有 history、projection、rebuild 和 db-check 只能从合格 NORMAL observation 构造检测结论。

### 2.4 恢复失败不解除隔离

可信 Probe FAIL 只使当前 attempt 失败，并把治理状态从 `RECOVERING` 回到 `ACTIVE`。治理记录仍然打开、下一轮仍然 Skip。只有可信 PASS 门槛满足并经人工关闭后，才停止 Skip。

### 2.5 MVP 看板包含一键验证，不做用户权限校验

本期交付 FastAPI 查询接口、动态看板和“一键验证”按钮。按钮是唯一的 Web 写入口：创建 verification attempt，并触发固定的 Jenkins Probe Job。隔离、取消隔离、取消 attempt 和关闭治理等操作仍通过 CLI 完成。

按本期约束不实现登录、用户身份识别或角色授权；所有按钮操作统一记录 `actor=dashboard-anonymous`，来源 IP 和 User-Agent 只作非可信审计信息。由于任何能访问页面的人都能触发 Probe，看板必须只绑定 loopback 或隔离的管理网段，禁止暴露到公网。网络隔离是部署前提，不等同于应用权限校验。

不做权限校验意味着仍然接受一项无法由代码消除的风险：管理网内任意调用者都可匿名占用 Probe 配额，审计记录也不能证明真实操作者身份。页面和报告必须明确显示该限制，不能把 IP、User-Agent 或自填姓名标为可信 actor。

### 2.6 SQLite 坚持单活动写者

SQLite 文件位于 workspace 外的宿主机本地持久路径。MVP 采用数据库同目录的跨进程 OS 文件锁：`FlakyStore` 的所有 public write 在打开写连接前获取同一把锁，再使用 `BEGIN IMMEDIATE`；超时则整项写操作失败，不在锁外重试部分步骤。操作系统负责在进程崩溃时释放锁，禁止使用“创建锁文件后凭文件是否存在判断”的脆弱实现。

NORMAL 导入、状态评估、治理 CLI、看板触发、dispatcher/reconciler 和 Probe 导入都必须经过该入口。Uvicorn 默认单 worker，OS 锁用于阻止误启动的第二实例或并发 CLI；目标测试工作区永远不直接访问数据库。WAL 和 busy timeout 只提高读写健壮性，不替代此互斥边界。外部 Jenkins HTTP 调用必须发生在数据库事务提交之后。

### 2.7 一键触发保证 exactly-once effect，不承诺 exactly-once delivery

Jenkins HTTP 接口无法原子地同时提交 SQLite 和 Jenkins queue。MVP 接受 dispatcher 可能至少投递一次，但必须通过 `dispatch_token`、Jenkins 构建开始时的原子 claim，以及 `(attempt_id, round_no)` 唯一约束保证最多一次有效 Probe 执行与计数。

若 Jenkins 已接收请求但响应丢失，trigger 进入 `DISPATCH_UNKNOWN`：reconciler 先按 token 查询 queue/build；无法证明“未接收”时禁止盲目重试。重复 queue item 可以存在，但未取得 claim 的构建必须在 checkout 和测试前退出。

## 3. MVP 目标与非目标

### 3.1 目标

- 只从可比、完整、可信的 NORMAL Run 形成自动检测结论。
- 只有人工隔离的精确参数化用例才进入 Skip 候选。
- Runner 在一次运行开始时读取不可变快照，所有进程和 xdist worker 使用同一决策。
- Probe 在固定 Commit、环境和执行画像上生成可审计证据。
- 默认连续 5 次合格 PASS 后进入“可关闭”，最终仍由人工关闭。
- 提供动态看板，支持列表、筛选、详情、证据时间线、本轮 Skip 查询和一键启动验证。
- 所有状态变化、排除原因、Skip 决策和证据均可追溯。
- 任何观测或治理组件异常都不覆盖 pytest 已形成的原始退出码。
- 可以通过一个最高优先级开关停止新增 Skip，并在下一轮恢复正常执行。

### 3.2 非目标

- 根据 `CONFIRMED` 自动隔离或自动关闭。
- 用“失败后重跑通过率”替代跨 Run 的 Flaky 检测。
- 自动修复、根因分类或 AI 判责。
- 通过 Web 看板执行隔离、取消、关闭、配置修改等其他写操作。
- 用户登录、身份认证、角色授权和审批流。
- 从看板选择任意仓库、分支、历史 SHA 或未合入受保护分支的提交执行代码。
- 保证 Jenkins queue item 物理上恰好创建一次；MVP 保证的是重复投递下最多一次有效执行。
- 多宿主机共享 SQLite、高可用数据库或跨 Jenkins Controller 调度。
- 自动创建 Jira/飞书工单、自动通知和复杂 SLA 引擎。
- 一开始覆盖全部测试目录；首批只灰度 `module/smoke`。

## 4. 领域模型和唯一事实源

| 领域 | 唯一事实源 | 含义 | 是否直接决定 Skip |
| --- | --- | --- | --- |
| 执行事实 | P0 CaseResult / Failure / IntegrityIssue | 本轮真实发生了什么 | 否 |
| 自动检测 | `flaky_state.detected_state` | 可比历史是否表现出波动 | 否 |
| 人工治理 | `flaky_governance` | 是否决定隔离、由谁负责、何时到期 | 是 |
| 恢复验证 | verification attempt + probe evidence | 固定修复版本是否达到恢复门槛 | 否，只提供关闭资格 |
| 执行决策 | 本轮 Skip 快照与决策事实 | 本轮最终 RUN 或 SKIP | 是 |

不得用报告文本、pytest skip reason 或 Jenkins Console 反向推导数据库状态。

CLI、看板、Pipeline Summary 必须复用同一只读查询服务和统计口径；数据不可用时显示 `UNKNOWN/DEGRADED`，不能用 0 冒充无问题。一键验证必须调用独立的应用服务，不能让路由直接拼 SQL 或 Jenkins 参数。

### 4.1 自动检测状态

沿用现有规则：

```text
OBSERVING -> STABLE | SUSPECTED
STABLE -> SUSPECTED
SUSPECTED -> STABLE | CONFIRMED
CONFIRMED -> CONFIRMED（同一 detection generation 内保持粘性）
```

- 单次失败不等于 Flaky。
- 全部稳定失败不等于 Flaky；它更可能是确定性缺陷。
- 规则阈值和版本必须入库、可重放，不能散落在 Jenkinsfile 或页面代码里。
- 手工纠正和 epoch reset 必须保留审计记录。
- 恢复关闭时只为该 `flaky_key` 开启新的 detection generation：锚点之后的第一条合格 NORMAL 才成为新窗口首样本，初始显示为 `UNOBSERVED`（派生展示状态）。旧 NORMAL 和 Probe PASS 都不能充当新窗口基线。
- `state_epoch` 只表达测试语义/身份边界变化；detection generation 只表达一次治理关闭后的重新观察，两者不能混用。

### 4.2 治理状态

沿用现有 `flaky_governance`：

```text
无记录 --人工隔离--> ACTIVE
ACTIVE --人工开始恢复--> RECOVERING
RECOVERING --可信 Probe FAIL--> ACTIVE
RECOVERING --超时/证据不足/构建丢失--> ACTIVE
RECOVERING --PASS 门槛满足--> RECOVERING（attempt=READY_TO_CLOSE）
RECOVERING --人工关闭--> CLOSED
ACTIVE | RECOVERING --人工取消隔离--> CLOSED(resolution=CANCELLED)
```

补充规则：

- `expires_at` 到期只产生 `OVERDUE` 告警，不自动关闭、不自动恢复执行。
- 任何关闭都要记录 actor、reason、时间和证据引用。
- CLOSED 后的迟到 Probe 结果只审计，不重新打开记录。
- 一个 `flaky_key` 最多存在一条 `ACTIVE/RECOVERING` 治理记录。

### 4.3 Verification attempt 状态

```text
ACTIVE -> READY_TO_CLOSE
ACTIVE -> FAILED
ACTIVE -> INCONCLUSIVE
ACTIVE -> EXPIRED
ACTIVE -> CANCELLED
READY_TO_CLOSE -> FAILED       # 人工关闭提交前到达可信 FAIL
READY_TO_CLOSE -> CANCELLED
READY_TO_CLOSE -> CLOSED       # 治理记录成功关闭
```

- 一个治理记录最多有一个活动 attempt。
- 失败后的再次验证必须显式创建新 attempt，计数从 0 开始。
- 超时、非计数结果达到上限或构建丢失分别进入 EXPIRED/INCONCLUSIVE，治理回到 ACTIVE，并释放创建新 attempt 的资格。
- attempt 的状态与治理状态在同一事务内更新。

### 4.4 Skip 判定

某条用例只有同时满足以下条件才可 Skip：

```text
skip_mode == enforce
AND 全局 kill switch 未关闭
AND 存在 ACTIVE 或 RECOVERING 治理记录
AND case_id、param_hash、environment、execution_profile、state_epoch 全部匹配
AND 用例路径位于 include_paths 且不在 exclude_paths
AND 快照 Schema、配置 revision 和内容校验均有效
```

`CONFIRMED`、`SUSPECTED`、`OVERDUE`、模糊 nodeid 或仅 case_id 匹配都不能单独触发 Skip。

## 5. 证据准入规则

### 5.1 Run 类型

新增显式 `run_kind`：

- `NORMAL`：允许进入自动检测历史。
- `FLAKY_PROBE`：只允许进入对应 attempt 的 Probe 证据。
- `LEGACY_UNKNOWN`：仅用于无法证明来源的 v2 历史，只读保留，不参与新状态推进。

本地调试、历史回放、数据补录和未知类型不得参与任何自动状态推进。

`run_kind` 不能从现有 `source_kind=jenkins/local` 推断。P0 RunRecord/manifest 必须升级版本并显式携带 run_kind；Probe 还必须携带 attempt_id、trigger_id、plan_digest、round_no、target/controller SHA 和 Jenkins Job/build 身份。读取旧 `quality.v1` 产物时采用双版本 reader，但不能静默赋值为 NORMAL。

### 5.2 NORMAL 资格

进入自动检测前必须同时满足：

- `run_kind=NORMAL`，且来源 Job 在 allowlist 中。
- 目标分支、环境和执行画像符合当前策略。
- Run 已完成，必要产物 hash 复验通过，且 `flaky_input_status=ELIGIBLE`。
- identity、environment、execution profile、observation 和 fingerprint 规则版本兼容。
- comparability fingerprint 一致；至少覆盖测试定义 digest、SUT/模型版本、影响结果的配置 revision、环境和执行画像。跨 cohort 变化只能形成“版本相关变化”告警，不能共同满足 CONFIRMED。
- Case 最终结果可折叠为明确 PASS 或 FAIL。
- 不是框架自动 Skip、xfail、xpass、收集失败或基础设施失败。
- 如未来引入 pytest 用例级 rerun，必须保存每次 attempt；“重跑后通过”不能伪装成一次普通 PASS。

`flaky_input_status` 由版本化 integrity reason-code 矩阵计算：COMPLETE 默认合格；DEGRADED 只有当全部 issue 都属于明确 allowlist 的非 Case/身份/结果影响项时才可合格；FAILED、未知 issue 或关键事实缺失一律不合格。范围外或不合格事实可以保留审计信息，但不能进入检测窗口。

### 5.3 Probe 资格与计数

默认策略由版本化配置提供，而非硬编码：

```yaml
required_consecutive_passes: 5
min_interval_minutes: 30
max_attempt_age_hours: 72
max_non_counting_runs: 3
```

一条 Probe PASS 只有同时满足以下条件才计数：

- attempt 仍为当前 ACTIVE attempt。
- `run_kind=FLAKY_PROBE`，run_id 在全部 Probe evidence 中从未使用。
- flaky 身份、attempt_id、trigger_id、plan_digest、round_no、target commit、environment、execution profile 与计划完全一致。
- P0 完整、产物 hash 正确、插件和 Schema 版本兼容。
- 测试没有被 skip/xfail，也没有使用用例级 rerun 掩盖首次失败。
- 与上一次计数 PASS 的控制端可信开始时间满足最小间隔；不得信任目标工作区自报时间。

结果处理矩阵：

| 结果 | 写入审计 | 推进 PASS | 清零/失败 attempt | 改变自动检测 |
| --- | --- | --- | --- | --- |
| 合格 PASS | 是 | 是 | 否 | 否 |
| 可信测试 FAIL | 是 | 否 | 是，治理回到 ACTIVE | 否 |
| 间隔不足 PASS | 是 | 否 | 否 | 否 |
| skip / xfail / xpass / NO_DATA | 是 | 否 | 否 | 否 |
| P0 不完整或基础设施失败 | 是 | 否 | 否 | 否 |
| Commit、身份或版本不匹配 | 是 | 否 | 否 | 否 |
| 重复 run_id | 记录幂等命中 | 否 | 否 | 否 |

“连续”是指相邻的合格证据之间没有可信测试 FAIL；不合格或基础设施结果既不推进也不清零。

`TRUSTED_FAIL / COUNT_PASS / NON_COUNTING` 必须由版本化机器规则根据 pytest phase、final_status、failure_category、confidence 和 integrity code 计算，禁止解析错误文本或依赖 AI 分类。UNKNOWN 默认 NON_COUNTING。PASS 进度每次由全部 evidence 按 `round_no、可信时间、run_id` 规范排序后确定性重算，不能按导入到达顺序直接 `+1`。

可信 FAIL 至少要求目标 Case 有完整且唯一的 decisive FAILED/ERROR 事实、关联 Failure 可验证、Run/P0 完整，并且失败不属于收集、Agent、凭据、环境准备或框架完整性故障。failure_category 只能参与版本化规则，不能单独覆盖原始事实。

“连续 5 次 PASS”是本期治理策略，不是测试已经绝对稳定的统计证明；看板必须同时展示样本数、时间跨度和策略版本。

## 6. 技术边界

### 6.1 数据流

```text
NORMAL Job -> P0 产物 -> 单写者导入/评估 -> detected_state
                                             |
                                      人工 quarantine
                                             |
                                  Skip 快照（shadow/enforce）
                                             |
                                  Runner/pytest 精确匹配

SQLite / 结构化决策产物 -> 统一只读查询服务 -> 看板 / CLI / Pipeline Summary

看板一键验证 -> attempt + trigger ledger -> Jenkins Probe -> 目标工作区执行 -> P0 产物
                                              |
                                  控制工作区校验/导入
                                              |
                                     verification attempt
                                              |
                                 人工 close -> 下轮不再 Skip
```

### 6.2 SQLite 约束

- 数据库必须是宿主机本地绝对路径，禁止放在 workspace、SMB、NFS 或 UNC 路径。
- 所有写操作通过统一协调入口串行化；读操作使用短事务。
- 开启 foreign keys、合理的 busy timeout，并明确 WAL/备份策略。
- 生产运行路径不得自动执行待处理 migration；迁移时停止所有访问者，先备份和 quick check，再由显式单一迁移命令执行。
- 在线备份只能由写入口调用 SQLite online backup API；离线备份必须先停写并 checkpoint。恢复时不得混用旧 WAL/SHM，恢复后执行 quick_check、foreign_key_check 和领域一致性检查。
- 数据库忙、损坏或版本不兼容时：读侧执行决策 fail-open，写侧 fail-closed 且不产生部分状态。
- 若未来需要多宿主机、多 Job 并发写或高可用，直接迁移到中心数据库/存储服务，不继续堆叠 SQLite 锁技巧。

### 6.3 快照边界

- 数据库只由 Runner 主进程在本轮收集前读取一次；pytest worker 不访问数据库。
- 快照包含 schema version、生成时间、run_id、配置 revision、数据库 schema version、完整身份键、governance_id 和内容校验和。
- 快照写入采用临时文件加原子替换，并在 serial/parallel pool 与所有 xdist worker 间只读共享。
- 一轮执行中的快照不可变；治理状态或 kill switch 的中途变化从下一轮生效。
- 快照缺失、过期、损坏或版本不兼容时执行全部测试，并生成显式告警与决策事实。

## 7. 分阶段实施

### 阶段 0：基线验证与契约冻结

#### 开发内容

- 用现有 `quality/flaky_*`、SQLite v2、CLI 和测试确认真实基线，形成“已有/缺失”清单。
- 仅在数据库副本上离线回放现有历史；新准入门禁完成前，不把当前未标记 run_kind/allowlist 的正式 Smoke 数据写入新治理库。
- 固定 Run 类型、NORMAL/Probe reason-code 决策矩阵、comparability fingerprint、Skip 公式、Probe 计数矩阵和失败语义。
- 将 5 次 PASS、30 分钟间隔、72 小时 attempt 有效期定义为可版本化默认策略。
- 明确一个可复用的 flaky 身份构造函数，Runner、快照、Probe 禁止各自重新实现。
- 确认当前没有用例级 pytest rerun；若将来引入，先升级事实 Schema。

#### 交付物

- 本文档中的状态转换、资格矩阵和配置 Schema 定稿。
- 代表性历史数据回放样本及预期结果。
- v2 历史中 LEGACY_UNKNOWN、RECOVERING、CLOSED(REGRESSED/RECOVERED) 的审计清单及人工处置规则。
- SQLite 单写者运行方式和迁移/恢复步骤草案。

#### 验收门槛

- 同一批历史事实重复回放得到完全相同的检测状态和 transition ID。
- 单次失败、稳定失败、交替结果和基础设施失败均符合预期分类。
- 不同 comparability fingerprint 的 P/F 变化不会共同确认 Flaky。
- 所有不合格样本都有机器可读 reason code，不使用“其他”吞掉未知情况。
- 本阶段不会新增任何 Skip。

### 阶段 1：最小 v3 Schema、纯状态机与 CLI

#### 数据库改造

新增不可变的 `0003` 迁移，保留 `0001/0002` 内容与 checksum。只增加闭环必需字段和表：

- 发布新的 P0 RunRecord/manifest Schema；Run 元数据增加 `run_kind`、`attempt_id`、`trigger_id`、`plan_digest`、`round_no`、`target_commit_sha`、`controller_commit_sha`、Jenkins Job/build 身份和事实/插件版本，并提供旧 `quality.v1` 只读兼容 reader。
- `flaky_governance` 增加 `row_version`；已有 governance_id 直接代表一次 occurrence。
- `flaky_state` 增加 detection generation/排他锚点，并允许新 generation 在首条 NORMAL 前表示 0 样本 UNOBSERVED。
- `flaky_governance` 补充 closed_by、close_reason 和 close_attempt_id，不能只保存 closed_at/resolution。
- `flaky_verification_attempt`：attempt_no、状态、固定 target SHA、策略版本、PASS 进度、最后计数时间及结束原因。
- `flaky_probe_evidence`：全局唯一 run_id、`(attempt_id, round_no)` 唯一、结果分类、可信控制端时间、证据 envelope/产物引用、SHA、资格与排除原因；不得外键到 NORMAL `case_observation`。
- `flaky_probe_trigger`：request_id、attempt_id、plan_digest、dispatch_token、fencing token、PENDING/DISPATCHING/QUEUED/RUNNING/COMPLETED/FAILED/DISPATCH_UNKNOWN/CANCELLED 状态、Jenkins queue/build 数字 ID、重试次数和安全错误码。
- `flaky_governance_event`：只追加记录隔离、attempt 创建/终结、恢复就绪、关闭、取消、迁移修正和迟到结果。现有 `flaky_transition` 继续只表示检测/兼容投影转换，`flaky_override` 继续表示人工检测纠正，三者不得混写。

至少具备以下约束：

- 每个 flaky_key 最多一个未关闭 governance。
- 每个 governance 最多一个 ACTIVE/READY_TO_CLOSE attempt。
- `(governance_id, attempt_no)`、全局 Probe run_id 和 `(attempt_id, round_no)` 唯一。
- request_id 唯一；同 request_id、同 payload 重放原响应，不同 payload 返回冲突。
- governance=RECOVERING 当且仅当存在该治理的 ACTIVE/READY_TO_CLOSE attempt；跨表约束由同一事务和 db-check 双重保证。
- row_version 通过条件 UPDATE 实现 compare-and-swap。
- 状态、结束时间和结束原因组合受 CHECK 约束。

#### 兼容与迁移

- v2 ACTIVE 原样保留。
- v2 RECOVERING 保留原始审计字段，但治理状态回到 ACTIVE、兼容投影回到 QUARANTINED；旧 NORMAL 恢复进度不得转换成 Probe PASS。
- v2 CLOSED(CANCELLED) 原样保留；CLOSED(RECOVERED) 标记 `LEGACY_NORMAL_RECOVERY`，不得冒充 Probe 恢复。
- v2 CLOSED(REGRESSED) 不可静默保留为已解除隔离：迁移报告列为阻断项，由人工重新隔离或显式豁免后才允许开启 Enforce。
- 无法由不可变 job/branch/integrity 元数据证明来源的旧 Run 标记为 LEGACY_UNKNOWN，不进入新检测窗口；迁移不得把现有 source_kind 当作 run_kind。
- 不因迁移、CONFIRMED 状态或 allowlist 自动创建治理记录。
- 重复迁移不重复写事件或创建 attempt。
- 回滚旧代码若不能识别 v3，必须恢复迁移前备份，不能让旧代码尝试降级写库。
- 将当前 `initialize_store()` 的运行时自动迁移拆分为显式 `flaky-db-migrate`；正常读写进程遇到 pending/too-new Schema 必须拒绝写入。

#### CLI

复用现有隔离、查询和取消命令，补充：

- `flaky-recovery-start`：调用与看板相同的应用服务创建 attempt，固定完整 target SHA 和策略版本。
- `flaky-recovery-status`：显示计数、排除证据和当前关闭资格。
- `flaky-recovery-close`：校验 READY_TO_CLOSE、row_version、证据和 Commit 归属后人工关闭。
- `flaky-recovery-cancel`：取消 attempt，治理回到 ACTIVE。

现有 `flaky-start-recovery` 必须删除，或保留为强制委托给新 attempt 服务的兼容别名；不得继续存在“只把状态改为 RECOVERING、但不创建 attempt”的路径。手工 transition ID 必须加入 command/governance/attempt ID，避免同一 observation 上重复隔离发生主键冲突。

NORMAL evaluator 中现有 `evaluate_recovery/close_governance` 路径必须删除：NORMAL 只更新自动检测；任何数量的 NORMAL PASS/FAIL 都不能推进 attempt、关闭 governance 或解除 Skip。

#### 验收门槛

- 空库初始化、v2→v3、重复迁移和备份恢复测试通过。
- 并发创建 attempt、导入证据或关闭时只有一个事务成功。
- 写入口跨 FastAPI、CLI、NORMAL/Probe importer 的并发测试证明同一时刻只有一个活动 writer；锁超时不留下部分写入。
- 运行期检测到 pending migration 时拒绝写入，只有显式迁移命令能够改变 Schema。
- Probe FAIL、取消和迟到结果不会关闭治理记录。
- 旧“5 次 NORMAL PASS 自动恢复”和“NORMAL 波动以 REGRESSED 关闭”测试必须反转为禁止该行为。
- 无新 observation 的隔离→取消→再次隔离不会发生 transition ID 冲突。
- 所有状态机测试不依赖真实 Jenkins、网络或 wall-clock sleep。

### 阶段 2：动态看板基础、Skip 快照与 Shadow

#### 开发内容

- 建立统一只读查询服务，供 CLI、FastAPI、看板和 Pipeline Summary 复用。
- 提供只读 FastAPI 接口和服务端渲染页面；本阶段尚不挂载一键验证写路由。
- 看板至少包含：总览、治理列表、用例详情/证据时间线、Probe attempt、OVERDUE 和本轮 Skip 决策。
- STABLE 必须同时展示 stable_outcome，并分别标为“稳定通过”或“稳定失败”，不得把 STABLE 一律渲染成健康。
- 支持状态、owner、是否超期、环境、执行画像、路径和关键字筛选；列表必须分页，禁止无界查询。
- 页面明确显示数据更新时间、数据库健康状态、当前规则/Schema 版本和 Shadow/Enforce 模式。
- 实现只读 Skip 候选查询和版本化快照生成器。
- 调整 Runner 生命周期为：创建 run context → 尝试生成一次快照 → 权威 collect → 生成决策计划 → 分池 → 执行 → 汇总。当前测试结束后才运行的 `quality_flaky_stage` 仍只负责导入/评估，不能承担收集前快照。
- 扩展权威 `CollectedTestCase`，使用 `quality.identifiers` 的同一算法在 collect 时生成 case_id 和 param_hash；不得只靠 nodeid 猜测参数身份。
- pytest 插件只消费 Runner 传入的不可变 `nodeid -> 决策` 计划，在 Shadow 模式不添加 mark，在 Enforce 模式添加 skip mark；collect-only、serial/parallel 和 xdist 都必须取得同一计划。
- `flaky-skip-decisions.json` 由 Runner 主进程按 `(run_id, flaky_key)` 统一写一次；pool/worker 不得竞争写同一文件。执行结束后核对计划决策与实际 pytest 结果，差异标记 DEGRADED。
- 明确决策 reason code：`MATCHED`、`NO_GOVERNANCE`、`OUT_OF_SCOPE`、`IDENTITY_MISMATCH`、`SNAPSHOT_INVALID` 等。
- 看板和 Pipeline Summary 展示 Shadow 候选数，但不更改构建结果。

#### 看板安全与可用性

- 查询路径使用 SQLite read-only URI 和短连接；后续一键验证只能通过受限应用服务进入单写者入口。
- 默认关闭跨域访问，校验 Host 和 Origin，使用 SameSite Cookie/CSRF token 防止第三方页面诱导触发，并对 case 名称、reason、owner 等非可信内容统一转义。
- 健康端点分为 `/health/live` 和 `/health/ready`；ready 检查数据库可读、Schema 兼容和 quick check 缓存结果。
- 页面查询失败时显示降级状态和错误编号，不回显数据库绝对路径、SQL、堆栈或敏感配置。
- FastAPI、Uvicorn 和模板依赖固定兼容版本并进入依赖锁定与漏洞检查。

#### 开关

```text
QUALITY_FLAKY_SKIP_MODE=off|shadow|enforce
QUALITY_FLAKY_AUTO_SKIP_ENABLE=0   # 最高优先级 kill switch
```

未知或冲突配置一律退化为 `off` 并告警。

#### 验收门槛

- `CONFIRMED` 但未人工隔离的用例始终为 RUN。
- 参数化用例只命中目标参数，不能扩大到同 case 的其他参数。
- 环境、画像、epoch、include/exclude 任一不匹配都不命中。
- serial/parallel 和 xdist worker 的 Shadow 决策一致。
- 快照确定发生在第一次权威 collect 之前，且 run_id 在 collect、各执行池和最终决策产物中一致。
- 看板、CLI、数据库查询和 Pipeline Summary 对同一快照的数量与状态完全一致。
- 本阶段所有 HTTP 路由均为只读；服务只监听明确配置的管理地址，公网入口检查必须失败。
- 启动参数为 wildcard address、代理来源不受信或防火墙 allowlist 未生效时，部署验收必须失败。
- 分页、恶意筛选参数和 HTML/脚本内容不会造成无界查询、SQL 注入或 XSS。
- 连续至少 10 个正式 Smoke Run 的候选可逐条人工核对，零身份扩大、零范围外候选后才进入下一阶段。

### 阶段 3：看板一键触发、Probe Job 与恢复证据

#### 调度方式

- 看板按钮是主入口，CLI 只作为调用同一 trigger application service 的故障兜底；禁止人工绕过 trigger ledger 直接执行 Jenkins `Build with Parameters`。
- 一次点击创建一个 attempt，并触发一个 Probe 编排构建；该构建负责完成最多 5 次计数 PASS，各轮使用独立 run_id，等待期间释放 Agent。
- 新增独立 `Jenkinsfile.probe`（或等价 Job DSL）：顶层 `agent none`、每轮单独申请受限 Agent、轮次间不占 executor，总 timeout 覆盖 72 小时策略上限。不得复用当前顶层 Windows Agent、60 分钟 timeout 的主 Jenkinsfile。
- 可信 FAIL 立即停止编排；不合格结果不计数。超过 attempt 有效期时结束为 EXPIRED；达到非计数上限或构建丢失时结束为 INCONCLUSIVE；两种情况都让 governance 回到 ACTIVE。
- Job 级 `disableConcurrentBuilds()` 防止 Probe 互相覆盖；数据库唯一约束和单写者入口保证重放幂等。
- Job 只接受 trigger_id、dispatch_token 和 plan_digest，不接受自由拼接的 target SHA、nodeid、Job URL 或 pytest 表达式。
- 每轮执行生成新的 run_id；相同产物重放只命中幂等，不重复计数。
- MVP 全局最多允许一个 QUEUED/RUNNING Probe，其他请求返回 429 而不是无限进入 Jenkins queue；同时限制请求体、字段长度、触发频率、最大轮次、最长运行时间和外部调用预算。

#### 一键触发协议

- 仅提供 `POST /api/v1/governances/{governance_id}/probe-attempts`，不提供通用 Jenkins 代理接口。
- 页面只提交 reason、governance row_version、CSRF token 和 UUIDv4 request_id；服务端从治理记录生成用例、环境和画像，并在新鲜 fetch 后把 target SHA 固定为当前受保护 `origin/dev3` HEAD。浏览器不能指定 SHA、nodeid、Job URL 或 pytest 参数。
- 请求只允许作用于 ACTIVE、范围内、没有活动 attempt 且容量门禁允许的治理记录。
- 同一事务把 governance 从 ACTIVE 改为 RECOVERING，并创建 attempt、不可变 Probe 计划、随机 128-bit dispatch_token 和 PENDING trigger；事务提交后 dispatcher 才调用固定的 Jenkins `buildWithParameters`。
- dispatcher 先 CAS 为 DISPATCHING，再发送 trigger_id、dispatch_token 和 plan_digest。Jenkins 构建在 checkout 前必须经单写者入口原子 claim；未取得 claim 的重复构建立即成功退出且不产生证据。
- Jenkins 响应明确成功时仅保存 queue/build 数字 ID，由固定 Jenkins origin 生成页面链接；HTTP 结果不确定时标记 DISPATCH_UNKNOWN，reconciler 先按 dispatch_token 查询 Jenkins，无法确认未接收时禁止盲重试。
- PENDING 或明确未接收的 FAILED 可以再次派发；已 QUEUED/RUNNING/COMPLETED 或 DISPATCH_UNKNOWN 的 trigger 不得直接重发。
- Jenkins 凭据只保存在服务端 secret store，使用独立 service account，仅授予指定 folder/job 的 Read/Build，不授予 Configure/Delete/Script；固定 HTTPS origin、校验证书、禁止跨主机重定向并设置连接/总超时。
- 不做用户身份或权限判断；审计 actor 固定为 `dashboard-anonymous`，不能把用户填写的名字当作可信身份。
- request body、reason、filter 和 header 均设长度上限并移除控制字符；使用结构化脱敏日志。request payload hash 由服务端对治理 ID、row_version、reason、Schema version 的规范化表示计算。

#### 触发开关

```text
QUALITY_FLAKY_TRIGGER_ENABLE=0|1
```

默认关闭。关闭时隐藏或禁用按钮、拒绝新 POST、冻结 PENDING/FAILED 派发；dispatcher 每次调用 Jenkins 前、Probe 每轮开始前都重新检查开关。提供运维命令取消 queue item/运行中 build，并把 attempt 原子结束为 CANCELLED、governance 恢复 ACTIVE。重复点击、页面刷新和网络重试由 request_id 幂等吸收。

#### 双工作区

- 控制工作区固定受信任 controller commit，负责读取计划、校验目标产物和写数据库。
- 目标工作区固定服务端解析的受保护分支 HEAD，只负责 collect、执行和生成 P0 事实，不持有数据库或 Jenkins 凭据；仅注入测试必需的最小 API 凭据并限制出网目标、CPU、内存和时长，结束后清理 workspace。
- target commit 只接受 40 位十六进制 SHA，必须在新鲜 fetch 后等于当前 `origin/dev3` HEAD；所有 Git/进程调用使用参数数组，禁止把输入拼接为 shell 命令。未合入分支或任意历史 SHA 的验证不属于无权限 MVP。
- collect 必须把 `case_id + param_hash` 唯一映射到 nodeid；零匹配或多匹配时本轮记为不合格，禁止扩大选择范围。
- Probe 计划固化原 execution_profile 对应的 numprocesses、dist 和 serial 约束；无法复现原画像时拒绝计数。
- Probe 显式绕过治理 Skip，但不能绕过业务自身 skip/xfail。
- 每轮结束后由控制工作区生成 evidence envelope，绑定 attempt、trigger、plan digest、round_no、target/controller SHA、Jenkins build number、控制端可信开始时间和 P0 文件 hash；目标工作区不能取得 envelope 签名密钥。

#### 关闭语义

- 达到 PASS 门槛只把 attempt 标为 READY_TO_CLOSE，不自动关闭。
- 人工关闭前必须确认 trigger/build 已到终态、计划内所有 round 均已登记、没有在途 evidence 导入，并在同一事务内复验证据、row_version 和治理关系。
- 关闭时 target SHA 必须仍等于当前受保护分支 HEAD/实际部署版本；分支已经推进时必须针对新 HEAD 创建新 attempt，不能仅凭 ancestor 关系关闭。
- 关闭事务同时把 attempt 标记 CLOSED，关闭 governance 并写审计事件。
- 关闭后从下一轮快照开始不再 Skip。
- 关闭同时设置该 flaky_key 的新检测锚点；旧证据保留可查，但不立即把旧波动窗口重新投影成新 CONFIRMED。Probe PASS 不伪装成 NORMAL 样本。
- epoch reset 仍只用于“用例语义/身份边界改变”，不能用作普通恢复计数清零工具。

#### 验收门槛

- 完成一次 5 次合格 PASS → READY_TO_CLOSE → 人工关闭的真实演练。
- 完成 PASS 中途出现可信 FAIL → attempt FAILED → governance ACTIVE 的真实演练。
- 一次有效点击只创建一个 attempt 和 trigger；即使网络不确定造成重复 queue item，也最多一个构建取得 claim 并有效执行，双击和请求重放不重复计数。
- Jenkins 不可用或响应不确定时，页面准确显示 PENDING、FAILED 或 DISPATCH_UNKNOWN，数据库保持一致；只有能够证明 Jenkins 未接收的请求才允许安全重试。
- 非法 SHA、过期 row_version、非 ACTIVE 记录、跨域请求和任意 Jenkins/pytest 参数均被拒绝。
- Jenkins 接收后连接中断、dispatcher 崩溃/双实例、queue item 取消、直接重放 Build 和 dispatch_token 重放均不会产生第二次有效执行。
- 间隔不足、基础设施失败、错误 Commit、错误环境、重复和乱序结果均不误推进。
- Probe 运行前后，同一批 NORMAL 历史重放的 detected_state 不变。

### 阶段 4：治理驱动 Skip 灰度

#### 开发内容

- 仅对 `branch=dev3 + path=module/smoke` 将 Skip 模式由 `shadow` 切到 `enforce`；`dev3` 是分支门禁，不是 china/overseas 环境维度。
- 命中时添加结构化原因，例如 `FLAKY_QUARANTINED:<governance_id>`。
- 自动 Skip 不生成 PASS/FAIL Flaky observation；它只生成独立 Skip 决策事实。
- OVERDUE 继续 Skip 并产生高可见告警，必须由 owner 显式处理。
- 快照/数据库异常时 fail-open：正常执行测试。测试自身若失败，仍按 pytest 原始结果处理。
- 治理告警和质量收尾异常不得覆盖 pytest 原始退出码。

#### 验收门槛

- ACTIVE、RECOVERING 精确命中；CLOSED、CANCELLED、无治理和范围外记录均不命中。
- 快照缺失、损坏、版本不兼容和数据库 busy 均会执行测试并产生结构化告警。
- kill switch 关闭后，下一轮 Smoke 不再产生治理 Skip。
- 人工关闭后，下一轮恢复执行；迟到 Probe 结果不能再次 Skip。
- 灰度期间逐条核对 Skip 决策与数据库治理记录，无幽灵 Skip、无扩大 Skip。

### 阶段 5：看板完善、报告、运维与完成验收

#### 看板与报告

看板和现有 Pipeline Summary 使用同一查询服务，并补充：

- 新增 CONFIRMED（仅检测，不代表已 Skip）。
- ACTIVE、RECOVERING、READY_TO_CLOSE 和 OVERDUE 数量。
- 本轮 WOULD_SKIP、实际治理 Skip、业务 Skip 和 fail-open 数量。
- Probe 的计数 PASS、可信 FAIL和各类不合格证据。
- 一键触发的 PENDING、DISPATCHING、QUEUED、RUNNING、COMPLETED、FAILED、DISPATCH_UNKNOWN、CANCELLED 状态，Jenkins queue/build 链接和安全错误码。
- 本轮状态变化及治理记录链接/标识。

看板详情页必须能够从 governance 追溯到 detected state、NORMAL evidence、trigger、Jenkins build、verification attempt、Probe evidence 和 Skip decision。除“一键验证”外，不提供其他状态修改按钮。

报告结论可以显示 WARN，但不得直接修改 Jenkins `currentBuild.result`。

#### 运维

- `flaky-db-check` 覆盖 schema version、quick_check、外键、孤儿记录和活动 attempt 一致性。
- 看板服务提供启动、停止、健康检查、日志轮转和故障降级操作手册。
- 监控长期 PENDING/DISPATCHING/DISPATCH_UNKNOWN trigger、Jenkins 调用失败、孤儿 queue/build、重复请求和触发频率；触发开关必须可独立熔断。
- 建立迁移前备份、定期备份、WAL 一致性、磁盘空间和恢复演练手册；在线备份使用 SQLite backup API，离线备份先停写/checkpoint，备份附 schema/checksum 清单。
- 恢复前停止全部访问者并隔离旧 WAL/SHM；恢复后通过 quick_check、foreign_key_check、迁移 checksum 和活动治理/attempt/trigger 一致性检查才重新开放。
- 监控数据库写失败/busy、快照 fail-open、过期治理、Probe 不合格率和长期 READY_TO_CLOSE。
- 数据库文件和备份不得进入 Jenkins artifact 或 Git。
- 日志不得记录凭据、完整请求密钥或未经转义的非可信内容。

#### 完成门槛

- 从“人工隔离”到“下一轮精确 Skip”再到“Probe 验证、人工关闭、下一轮恢复执行”至少完成一次端到端演练。
- 看板能够实时查看该演练的完整状态和证据链，且与 CLI、数据库及 Pipeline Summary 一致。
- 成功链路和可信 FAIL、基础设施失败、快照损坏、并发写、迁移回滚五类异常链路均有自动化测试。
- 数据库、JSON 产物、CLI 查询和 Pipeline Summary 对同一事实给出一致结论。
- kill switch、备份恢复和失败回退均有可执行且验证过的操作步骤。

## 8. 必测场景清单

### 单元与属性测试

- 纯检测状态机对同一有序事实输入保持确定性。
- Probe 计数在重复、乱序和时间边界下保持幂等。
- 任意状态转换都不能产生两个活动治理记录或两个活动 attempt。
- 身份序列化、快照校验和与配置 revision 稳定。

### 迁移测试

- 空库初始化、v2 ACTIVE/RECOVERING/CLOSED 迁移、重复迁移和损坏 checksum 拒绝。
- 迁移失败完整回滚；备份可恢复到迁移前状态。
- 旧 RECOVERING 进度不会被误当成新 Probe 证据。

### 集成测试

- CONFIRMED 无治理记录时正常执行。
- ACTIVE 只 Skip 精确参数，兄弟参数、其他环境和画像正常执行。
- RECOVERING 仍 Skip 普通 Smoke，但 Probe 自身绕过治理 Skip。
- 任意数量 NORMAL PASS/FAIL 都不能推进 Probe、结束 attempt 或关闭 RECOVERING governance。
- Probe run 不能写入 case_observation，history/rebuild/db-check 发现非 NORMAL 检测证据时必须报错。
- 同一 Probe run 不能跨 attempt 重放，旧 attempt 的 evidence 不能用于新 attempt。
- Probe PASS/FAIL/UNKNOWN 分别按矩阵处理，且不污染 NORMAL 检测。
- NORMAL/Probe 的 FAILED、ERROR、DEGRADED 和 UNKNOWN 按版本化 reason-code 决策表执行。
- READY_TO_CLOSE 与可信 FAIL 并发时，关闭请求因 row_version 变化而失败。
- trigger/build 未终结、有在途 round，或 target SHA 不再等于受保护分支 HEAD 时禁止关闭。
- 关闭、快照生成和迟到证据并发时，不产生幽灵 Skip。

### 故障测试

- SQLite busy、只读、磁盘满、文件损坏和 schema 不兼容。
- 快照截断、校验和错误、旧版本和过期。
- Jenkins 接收请求后连接中断、dispatcher 在提交/派发前后崩溃、dispatcher 双实例和 queue item 被取消。
- Jenkins Agent 中断、直接重放 Build、重复 queue item、错误 checkout、错误 round 和产物/envelope 篡改。
- 两个 Uvicorn worker、CLI、NORMAL importer 与 Probe importer 并发争抢写锁。
- kill switch 配置缺失、非法值，以及存在 PENDING/QUEUED/RUNNING 时切换。
- WAL 活跃时备份、恢复时遗留 WAL/SHM、匿名请求洪泛和输入/日志边界。

## 9. 灰度与回滚顺序

1. 在隔离数据库副本上完成 v2 历史审计与离线回放，生产 Skip 保持关闭。
2. 停止数据库访问者，备份并显式迁移 v3；部署兼容代码和单写者锁后恢复访问。
3. 启用 v3 history/state 准入，确认只有合格 NORMAL 进入检测。
4. 部署看板查询功能，保持一键验证关闭；开启 `QUALITY_FLAKY_SKIP_MODE=shadow`，通过看板完成至少 10 轮人工核对。
5. 部署独立 Probe Job 并开启 `QUALITY_FLAKY_TRIGGER_ENABLE=1`，通过看板完成一次成功恢复、一次可信 FAIL 回退和一次派发未知对账演练。
6. 仅对 `branch=dev3 + path=module/smoke` 开启 `enforce`。
7. 观察 Skip 决策、fail-open、数据库写等待、OVERDUE、匿名触发容量和报告一致性。
8. 演练 `QUALITY_FLAKY_AUTO_SKIP_ENABLE=0` 与 `QUALITY_FLAKY_TRIGGER_ENABLE=0`，确认下一轮全部恢复执行且待派发任务被冻结。
9. 满足验收门槛后再逐步扩大 include_paths；每次扩容都重新经过 Shadow。

回滚时优先关闭 Skip，不删除治理数据。若必须回滚到不支持 v3 的旧代码，停止所有访问者并恢复迁移前备份；禁止对数据库执行手工降级 SQL。

## 10. MVP 完成定义

满足以下全部条件才视为完成：

- 自动检测、人工治理、Probe 证据和执行决策四层边界在代码与测试中一致。
- 未经人工隔离的用例绝不会因检测状态被 Skip。
- 参数、环境、画像或 epoch 不同的用例绝不会被扩大 Skip。
- Probe 证据不进入 NORMAL 检测，恢复失败不解除隔离。
- NORMAL Run 永远不能推进恢复或关闭治理，旧恢复入口不能绕过 attempt。
- 达标不会自动关闭，关闭必须由可审计的人工操作完成。
- 动态看板可查询、筛选并追溯完整证据链；一键验证允许至少一次派发，但在重复投递下最多只有一个 Jenkins 构建有效执行和计数。
- 看板除一键验证外没有其他写能力；无权限校验的风险由网络隔离、独立触发开关和最小 Jenkins 凭据约束。
- Runner 决策异常 fail-open 且显式告警，治理写入异常 fail-closed 且事务回滚。
- SQLite 满足本地持久化、单写者、备份和恢复约束。
- 灰度、熔断、恢复和迁移回滚均完成演练。

## 11. MVP 后续演进

只有当 CLI/Jenkins 与看板闭环稳定后，再按真实痛点选择建设：

- 登录、SSO/RBAC、审批，以及隔离/取消/关闭等其他看板操作按钮。
- 多 Job 并行调度、通用消息基础设施和跨 Jenkins Controller 高可用；一键验证必需的专用 trigger ledger、dispatcher、reconciler、claim/fencing 属于本期。
- Jira/飞书通知、owner SLA 和升级策略。
- 中心数据库、多 Jenkins Controller 与高可用部署。
- 基于历史数据校准不同测试族的检测和恢复阈值。
- 自动建议隔离；即使引入，也应保留人工批准后才改变执行的安全边界。

## 12. 代码落点建议

- `quality/models.py`、`quality/flaky_models.py`、P0 producer/manifest：新增版本化 Run kind、attempt、Probe envelope/evidence 和 Skip artifact 契约，并提供 v1/v2 dual-reader。
- `quality/config.py` 与示例环境文件：新增 Skip/Trigger 开关、范围、容量、超时和安全默认值。
- `quality/flaky_store/migrations/0003_*.sql`：仅做增量 Schema 变更。
- `quality/flaky_store/`：新增跨进程 writer lock、verification/snapshot/trigger/reconcile 服务，保持 facade 为公共入口，并把自动迁移拆成显式命令。
- `quality/cli.py`：恢复 attempt、关闭和快照命令。
- `quality/identifiers.py`、`quality/pytest_plugin.py`：共享 collect 身份构造、精确匹配及 Shadow/Enforce 行为。
- `run_orchestration/runner.py`、`quality_lifecycle.py`、`pytest_execution.py` 和 `master_service.py`：把 run context/快照前移到权威 collect 之前，扩展 collected case 身份并传递决策计划。
- `run_orchestration/quality_flaky_stage.py`：测试后的 NORMAL 导入和检测；不得处理 Probe 恢复。
- `Jenkinsfile.probe`（或等价 Job DSL）：顶层 agent none、受限目标 Agent、分轮执行、trigger claim、熔断和超时。
- `flaky_dashboard/`：FastAPI 查询、一键验证应用服务、Jenkins dispatcher、模板、静态资源和健康检查。
- `pipeline_reporting/`：消费结构化 Skip/Probe 产物，不解析自由文本。
- `tests/quality/`：按状态机、迁移、事务边界、插件、看板、报告和端到端分层补测试。
- `requirements-dashboard.in` 与带 hash 的锁定文件：固定 FastAPI/Uvicorn/模板/HTTP 客户端及审计工具版本，CI 执行依赖漏洞检查。

实施时每个阶段单独提交，先交付契约与测试，再交付外部行为；不得在同一次上线中同时开启 Probe 和 Enforce。
