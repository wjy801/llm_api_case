# Flaky 治理 MVP 阶段 5：看板、报告、运维与完成验收

## 1. 阶段结论

阶段 5 收拢前四个阶段的事实和操作边界，交付可查询的完整证据链、与 Pipeline Summary 一致的版本化视图、SQLite 检查/备份/非覆盖恢复工具、最小运行手册，以及成功与异常链路的端到端验收。它不再改变检测、Probe 或 Skip 的核心状态机。

当前状态：`PLAN_REVIEWED / STAGE4_DEPENDENCY`。只有阶段 4 达到 `ENFORCE_VALIDATED / MVP_COMPLETION_PENDING`，且阶段 0 的真实 v2 数据审计不再阻塞目标部署，才能执行最终完成验收。本文完成只表示开发方案已评审，不代表 MVP 代码或演练已经完成。

## 2. 目标、范围与非目标

### 2.1 目标

- 让看板、CLI、数据库查询和 Pipeline Summary 共享同一查询/序列化口径。
- 在不混合状态轴的前提下，追溯 detection、governance、attempt、trigger/build、Probe evidence 和 Skip decision。
- 缺数据、版本不兼容或来源不可信时显示 UNKNOWN/DEGRADED，不以 0 冒充健康。
- 补齐 `flaky-db-check` 的 v3/`0004`/`0005` 领域一致性检查和运行产物检查。
- 提供 Dashboard 启停、健康检查、日志轮转、降级、trigger/Skip 熔断操作手册。
- 提供带 manifest/checksum 的 SQLite 在线备份、离线备份和只恢复到新路径的恢复流程。
- 对成功闭环及关键异常链路执行可复核的最终演练，并生成验收清单。
- 证明 MVP 默认配置仍是安全关闭，且可通过两个独立 kill switch 回退。

### 2.2 明确不做

- 不新增检测、治理、attempt、trigger 或 Skip 状态，不调整阶段 0 的策略阈值。
- 不增加新的 Web 写入口；页面仍只有“一键验证”一个 POST。
- 不实现登录、SSO/RBAC、多租户、审批、Jira/飞书通知或 owner SLA 平台。
- 不建设 Prometheus/Grafana、集中日志、分布式追踪、数据仓库或通用告警平台。
- 不实现数据库高可用、主从、跨主机锁、跨地域容灾或自动故障切换。
- 不实现任意历史目录扫描、任意文件下载或任意 Jenkins URL 跳转。
- 不在线降级 SQLite Schema，不用手写逆向 SQL 回滚 migration。
- 不自动修复损坏记录、不自动关闭 OVERDUE/READY_TO_CLOSE，也不自动重试 DISPATCH_UNKNOWN。
- 不把一次 MVP 演练包装成长期稳定性、容量或安全合规证明。

## 3. 前置门槛与现状差距

### 3.1 前置门槛

1. 阶段 0～4 的契约、自动化测试和各阶段真实观察/演练门槛全部有可核对记录。
2. 阶段 4 已完成至少 10 个连续正式 dev3 Smoke Run，无幽灵 Skip、无扩大 Skip。
3. 数据库已通过显式 `0003`/`0004` migration；运行时不会自动迁移。
4. Dashboard、dispatcher、reconciler、NORMAL/Probe importer 和 CLI 全部使用统一单写者入口。
5. 非生产环境可安全执行数据库损坏、恢复、Jenkins 不可用和 kill-switch 演练。
6. 生产迁移若基于既有 v2 数据，阶段 0 的真实只读审计和迁移签字必须完成；合成 fixture 不能替代。

### 3.2 当前能力差距

- 现有 backup helper 只生成固定名称的 migration 前备份并执行 `quick_check`，没有独立备份命令、manifest、保留策略或非覆盖恢复流程。
- 现有 `flaky-db-check` 尚未覆盖阶段 1/3 规划的 projection、attempt、trigger、round、slot 和 evidence 跨表不变量。
- 现有 Pipeline Summary 能输出 Markdown/JSON 并保留 pytest 退出事实，但尚未消费完整 Skip/Probe 产物。
- 阶段 2 看板是基础视图；阶段 3/4 新增的 trigger、Jenkins、application 和 reconciliation 链仍需统一展示。

上述是阶段 5 的实现清单，不得被描述为当前已经具备。

## 4. 事实所有权与一致性口径

### 4.1 事实源矩阵

| 事实 | 唯一事实源 | 可派生视图 |
| --- | --- | --- |
| NORMAL observation 与 detection projection | SQLite v3 | CLI、Dashboard |
| Governance/event/attempt/Probe evidence | SQLite v3 | CLI、Dashboard |
| Trigger/round/Jenkins claim | SQLite `0004` | Dashboard、ops status |
| 某一 Run 的 snapshot/decision/application/reconciliation | 该 Run 的不可变 JSON bundle | Dashboard run view、Pipeline Summary |
| pytest 原始结果与退出码 | P0/runner execution artifact | Pipeline Summary、run view |
| 备份有效性 | backup file + `flaky-db-backup.v1` manifest | verify/restore 命令 |

不把同一 Run decision 再写入 SQLite。所谓“数据库、JSON、CLI 和 Summary 一致”是指各消费者对其所属事实源给出相同解释，并通过稳定 ID/hash 正确关联，而不是制造 SQLite/JSON 双写。

### 4.2 非原子跨源视图

SQLite 查询与文件读取无法形成一个原子快照。组合 DTO 必须同时暴露：

```text
db_data_as_of
run_artifact_generated_at
run_id
artifact_checksums
consistency_status = CONSISTENT | DEGRADED | UNKNOWN
```

- 数据库部分在一个只读事务内取得 `db_data_as_of`、计数和明细。
- Run 部分只读取调用方从服务端受限 catalog 中明确选择的 bundle，不扫描任意目录。
- 关联 ID/hash 不存在或时间点不同导致结论无法证明时显示 DEGRADED/UNKNOWN，不声称“实时一致”。
- Pipeline Summary 只描述该 Run 冻结的事实，不用构建结束后的 live database 状态改写历史结论。

## 5. 统一查询服务与 DTO

继续扩展阶段 2 的 `FlakyReadService`，CLI、FastAPI、Jinja view-model 和 `pipeline_reporting` 只能调用该服务或消费其同版本序列化 DTO。查询/DTO 核心不得依赖 FastAPI/Jinja，使未安装 Dashboard 可选依赖的 Jenkins 报告仍可运行。

### 5.1 最小 DTO

- `DashboardSummary`：数据库/Run 数据时间、Schema/策略版本、数据可用性和四条状态轴计数。
- `GovernanceListItem`：完整 identity、owner、expires_at、governance 状态、OVERDUE、当前 attempt/trigger 摘要。
- `FlakyCaseDetail`：各 comparability cohort 的 detection projection、NORMAL evidence、governance event、attempt、Probe evidence 和关闭锚点。
- `ProbeAttemptDetail`：不可变 plan、trigger 状态、queue/build 数字身份、round、计数/不计数证据和安全错误码。
- `RunDecisionSummary`：RUN/WOULD_SKIP/计划 SKIP、实际治理 Skip、business marker precedence、fail-open 和 UNKNOWN 数量。
- `OperationsSummary`：数据库检查摘要、长期状态、开关 effective value、最近错误码和备份新鲜度。

DTO 必须保留 detected state、governance state、attempt state、trigger state 和 execution decision 五个独立字段，禁止压成一个“当前状态”。`STABLE` 必须携带 stable outcome，不得一律展示为健康。

### 5.2 查询约束

- 数据库使用只读 URI、短连接、绑定参数、稳定排序和游标分页；默认 50、最大 100。
- 关键词最大 128 字符，通配符按字面量转义；所有时间使用 UTC 且显式时区。
- Run bundle 只能来自启动配置提供的有界只读 catalog；catalog 将 run id 显式映射到允许根目录内的 manifest，HTTP 参数不能成为文件系统路径，也不能触发目录遍历扫描。
- 读取文件前验证 resolve 后仍在允许根内，拒绝符号链接越界、过大文件、未知 Schema 和 checksum 不符。
- 不返回 SQL、堆栈、数据库绝对路径、secret 路径、raw token、HMAC 或 Jenkins credential。

## 6. 看板完成形态

### 6.1 页面与 API

保留阶段 2 的 GET 页面/API和阶段 3 唯一 POST，不新增其他写操作。页面补齐：

- 总览：CONFIRMED（明确标注“仅检测”）、ACTIVE、RECOVERING、READY_TO_CLOSE、OVERDUE 和 UNKNOWN。
- 治理列表：状态、owner、是否超期、环境、画像、路径、关键字和稳定分页。
- Case 详情：按时间展示 detection transition、NORMAL evidence、governance event、attempt、Probe evidence 和 detection generation。
- Probe attempt：PENDING、DISPATCHING、QUEUED、RUNNING、COMPLETED、FAILED、DISPATCH_UNKNOWN、CANCEL_REQUESTED、CANCELLED，及 queue/build 链接和安全错误码。
- Run 决策：WOULD_SKIP、计划 SKIP、实际治理 Skip、业务 marker 旁路、fail-open、缺失/冲突及 reason 分布。
- 页面页眉持续显示匿名触发限制、数据库/Run 数据时间和当前两个 kill switch 的 effective value。

时间线排序固定为 `occurred_at, event_kind, event_id`；同一时间戳不能依赖数据库默认顺序。Jenkins 链接只由固定 HTTPS origin、固定 Job full name 和已验证的数字 ID 构造，使用安全链接属性，不显示或接受外部 URL。

### 6.2 不可用语义

- 任一来源不可用时，其卡片显示 UNKNOWN/DEGRADED、稳定错误码和数据时间，不能显示 0。
- 一个来源失败不抹掉其他已验证来源；例如 Jenkins 不可达不影响数据库历史只读展示。
- 列表/详情 ID 不存在返回 404，输入非法返回 400，数据库/Schema 不可用返回 503。
- HTML 继续自动转义；JSON 使用固定 DTO；错误页不回显非可信输入。
- “实时”仅指页面可轮询刷新最近持久事实，不引入 WebSocket，也不承诺跨源原子更新。

## 7. Pipeline Summary 与报告契约

### 7.1 单一机器模型

`pipeline_reporting` 从当前 Run 的已验证 artifact bundle 构造一个版本化机器模型，再由同一对象渲染：

```text
reports/pipeline-summary.json
reports/pipeline-summary.md
reports/pipeline-email-subject.txt
reports/pipeline-email.html
```

不得让 Markdown、HTML 和 JSON 分别计算数量。Flaky 部分至少包含：

- detected transition 与 NORMAL admission 摘要。
- WOULD_SKIP、计划 SKIP、实际治理 Skip、业务 marker precedence、fail-open 和 UNKNOWN。
- Probe COUNT_PASS、TRUSTED_FAIL、各 NON_COUNTING reason（仅当当前 bundle 明确引用）。
- governance/attempt/trigger 状态变化和安全 ID/链接。
- 数据可用性、Schema/策略版本、run id 和各源 checksum。

### 7.2 结论边界

- 缺文件、旧版/未来版、hash 错误或 run id 不匹配只使对应小节 DEGRADED/UNKNOWN，不以空数组显示“0 问题”。
- 旧 Shadow reconciliation reader 继续受支持；没有 application/worker 来源的 v1 不能推导实际治理 Skip。
- 报告可以显示 WARN 和建议动作，但不得直接写 Jenkins `currentBuild.result/currentBuild.currentResult`。
- Jenkins 构建结果继续来自 pytest/Stage 原始执行；报告生成失败使用现有 fallback，不覆盖先前失败。
- 邮件只包含安全摘要和固定 Jenkins 构建链接，不包含 owner reason 全文、数据库路径或 token。

## 8. 完整证据链与可追溯规则

### 8.1 关联链

```text
Flaky identity
  -> NORMAL evidence -> detection projection/transition
  -> governance -> governance events
  -> verification attempt -> Probe plan
  -> trigger -> Jenkins queue/build claim
  -> round -> evidence envelope -> Probe evidence
  -> manual close -> detection generation anchor
  -> next Run snapshot -> decision -> worker application -> P0 -> reconciliation
```

每条边必须由外键、稳定 ID 或内容 hash 证明。页面只提供已验证方向的链接；关联缺失显示 broken-link 错误码，不猜测最近记录或相似 nodeid。

### 8.2 时间与状态展示

- detected state 按 `(flaky_key, generation, comparability fingerprint)` 展示。
- governance/attempt/trigger 各自展示状态和最后事件，不合成为单一 badge。
- READY_TO_CLOSE 明确写“等待人工关闭”，不能显示“已恢复”。
- COMPLETED trigger 只表示编排收敛，不能显示“Probe 通过”。
- OVERDUE 只表示治理期限已到，ACTIVE/RECOVERING 仍可能继续 Skip。
- CLOSED 后的迟到 Probe evidence 标为 AUDIT_ONLY，不改变关闭或后续快照。

## 9. 数据库与运行产物检查

### 9.1 `flaky-db-check`

保持只读、无迁移、无修复，覆盖：

- 文件存在且是预期 SQLite、Schema version、migration checksum、`quick_check`、`foreign_key_check`。
- identity 唯一、generation 非负、projection/observation/cohort 引用一致。
- 只有 NORMAL observation 参与 detection，Probe run 不出现在 NORMAL 表。
- 同一 flaky key 最多一个未关闭 governance，同一 governance 最多一个活动/READY attempt。
- CLOSED/非活动记录的结束字段齐全，治理事件 causal ID 无孤儿。
- plan digest、trigger/attempt 一对一、dispatch token 仅 hash、build claim 唯一。
- capacity slot 与活动 transport 状态双向一致，DISPATCH_UNKNOWN/CANCEL_REQUESTED 不被提前释放。
- round/evidence/run id、APPLIED round 唯一、计数重算和 attempt 状态一致。
- READY_TO_CLOSE、CLOSED 与 trigger/round/evidence 门禁一致。

命令输出版本化 JSON，健康返回 0；结构/一致性失败返回非 0 和稳定错误码。默认只执行有界索引查询；完整 evidence 重算通过显式 `--deep` 在维护窗口运行，不把慢检查放进 HTTP 请求。

### 9.2 `flaky-artifact-check`

新增只读命令：

```text
flaky-artifact-check --run-dir <explicit-run-directory>
```

它验证 P0、snapshot、decision、application、reconciliation、Probe 引用和 Pipeline Summary machine model 的 Schema、run id、相对路径、大小、hash 及计数关系。命令不扫描父目录、不访问网络、不写数据库；未来版本返回 unsupported，而不是尝试兼容解析。

### 9.3 `0005` Restore fence

阶段 5 增加一个只用于恢复安全的不可变 migration `0005_restore_fence`：单行 `flaky_store_control` 保存 `restore_fence`、backup id、恢复时间和解除 fence 的审计字段，不新增业务状态机。

- 正常迁移后 fence 默认为关闭；`flaky-db-restore` 创建的新库必须在开放前设置为开启。
- fence 开启时允许只读查询和完整性检查，但所有 NORMAL/Probe import、治理命令和 dispatcher 均 fail-closed；snapshot 只能返回 FENCED/UNAVAILABLE，不能生成可执行治理候选。
- runtime 遇到 pending `0005` 仍只返回 `schema_migration_required`，不能自动迁移。
- fence 只能由专用 CLI 在完成外部 Jenkins 核对后显式清除，并记录 actor、reason、backup id 和 reconciliation evidence hash。

## 10. 健康、日志与最小运维观察

### 10.1 健康检查

- `/health/live` 只证明进程事件循环可响应，不访问数据库/Jenkins。
- `/health/ready` 返回 read、trigger 和 artifact 三个 component；read 不可用时 HTTP 503，Jenkins 不可达只使 trigger component DEGRADED，不关闭历史只读页面。
- readiness 使用短时缓存的 Schema、只读连接和 quick-check 结果，不在每个请求执行全库检查。
- health 响应只含布尔/枚举、版本和安全错误码，不含路径、凭据、异常堆栈或数据库内容。

### 10.2 结构化日志

日志只记录：UTC 时间、event code、run/governance/attempt/trigger/build 安全 ID、旧/新状态、结果、耗时和 correlation id。

- 不记录 raw dispatch/CSRF/HMAC/Jenkins/API token、Authorization/Cookie header、完整用户 reason 或任意响应正文。
- 非可信字段使用 allowlist、长度上限和转义；异常对外只返回稳定错误码。
- 单机 MVP 使用本地滚动日志，默认每文件 10 MiB、保留 5 个；部署可交由现有 OS 服务管理，但不建设集中日志平台。
- 日志写失败不得改变 pytest 结果；关键治理事实仍以数据库事件和不可变 Run 产物为准。

### 10.3 `flaky-ops-status`

新增只读 JSON 命令，供运维手工/系统计划任务调用，至少报告：

- 数据库可用性、最近 writer busy/失败摘要和磁盘剩余空间。
- OVERDUE、READY_TO_CLOSE 及其 age。
- PENDING/DISPATCHING/DISPATCH_UNKNOWN/QUEUED/RUNNING/CANCEL_REQUESTED trigger 数量和 age。
- Jenkins 最近调用失败、孤儿 queue/build、重复 request 和触发频率。
- Probe COUNT_PASS/TRUSTED_FAIL/NON_COUNTING 数量及 reason 分布。
- 当前所选 Run 的 snapshot/application fail-open 和 artifact check 状态。
- 最近成功备份时间、manifest 校验结果和备份目录可用性。

诊断阈值只产生 banner/日志，不自动变更状态：DISPATCH_UNKNOWN 立即高亮；PENDING/DISPATCHING、QUEUED、CANCEL_REQUESTED 和 READY_TO_CLOSE 的具体 age 阈值放在版本化运维配置并展示 effective value。MVP 不发送通知、不自动取消或修复。

## 11. SQLite 备份

### 11.1 在线备份

新增：

```text
flaky-db-backup --db <absolute-local-path> --output-dir <dedicated-directory>
flaky-db-verify-backup --backup <file> --manifest <file>
```

- backup 命令先取得同库 OS writer lock，阻止新写事务，再使用 SQLite backup API 生成一致副本；不对活动数据库文件做普通文件复制。
- 输出使用唯一 `backup_id` 和临时文件，副本及 manifest 验证成功后分别原子 rename；不覆盖既有备份。
- 对副本执行 `quick_check`、`foreign_key_check`、Schema 和 migration checksum 检查。
- `flaky-db-backup.v1` manifest 至少包含 backup id、UTC 时间、数据库 id/generation、Schema、migration checksums、SQLite 版本、文件大小和 SHA-256。
- 备份失败删除未完成临时文件但保留已有成功备份，不修改源库。
- 通过 OS 计划任务至少每日执行；保留策略由本地部署容量明确配置，只有新备份验证成功后才可删除过期备份。

### 11.2 离线备份

维护或迁移前：

1. 关闭 Skip/Trigger 开关，停止 Dashboard、dispatcher/reconciler、importer 和治理 CLI 写入。
2. 确认没有活动数据库访问者并取得 writer lock。
3. 执行 WAL checkpoint，记录结果；不得忽略 busy。
4. 使用 SQLite backup API 生成备份和 manifest；不只复制主 `.db` 文件。
5. 对备份执行 verify，失败则保持服务关闭并调查。

数据库、WAL/SHM、备份、manifest 中可能包含治理信息，均不得进入 Git、Jenkins artifact 或公开共享目录。应用层不另建加密系统，依赖受限 OS ACL 和现有磁盘加密能力。

## 12. 非覆盖恢复与迁移回滚

### 12.1 恢复命令

```text
flaky-db-restore --backup <file> --manifest <file> --target-new <nonexistent-path>
```

- 命令只允许恢复到不存在的新绝对本地路径，拒绝覆盖原库、目录、符号链接和仓库/Artifact 目录。
- 恢复前验证 manifest、backup hash、Schema、migration checksums、`quick_check` 和 `foreign_key_check`。
- 通过 SQLite backup API 写入临时目标，执行完整 `flaky-db-check --deep` 后原子 rename 到新路径。
- 恢复副本在原子 rename 前设置 `restore_fence=1`；check 输出必须明确区分“数据完整但尚未允许运行写侧”的状态。
- restore report 记录源 backup/manifest hash、目标库设置 fence 后的新 hash 和所有检查结果，避免把恢复副本误认为与 backup 文件逐字节相同。
- 命令不自动切换服务配置；operator 在全部检查通过后显式修改数据库路径。

### 12.2 恢复步骤

1. 同时关闭 `QUALITY_FLAKY_AUTO_SKIP_ENABLE` 和 `QUALITY_FLAKY_TRIGGER_ENABLE`。
2. 停止 Dashboard、dispatcher/reconciler、NORMAL/Probe importer、Jenkins controller DB step 和全部 CLI。
3. 保存故障数据库及其 WAL/SHM 到隔离目录，不删除、不与备份混用。
4. 恢复到新路径并运行 quick/foreign-key/migration/domain deep checks。
5. 先以只读 Dashboard 核对 identity、governance、attempt、trigger、slot 和关键计数；查询固定 Jenkins Job 中 `backup.created_at` 之后的 queue/build，列出恢复库缺失或状态落后的外部执行。
6. 恢复库中的 PENDING/DISPATCHING/DISPATCH_UNKNOWN/QUEUED/RUNNING/CANCEL_REQUESTED 一律按“可能已产生外部副作用”处理；取消或对账收敛前禁止派发，不能因备份中显示 PENDING 就安全重试。
7. 运行 `flaky-restore-unfence --db ... --backup-id ... --actor ... --reason ... --reconciliation-evidence ...`；命令复验 fixed Job 已无未处置执行、数据库 deep check 通过且两个开关关闭，才清除 fence。
8. 再开放单写者；先恢复 NORMAL import，Probe trigger 和 Enforce 继续关闭。
9. 完成一次 Shadow Run 后再分别恢复 Probe、最后恢复 Enforce。

### 12.3 代码/迁移回滚

- 行为回滚优先使用两个 kill switch，不删除数据、不降 Schema。
- 旧代码若不能识别当前 Schema 必须拒绝启动，不能以兼容模式写入。
- 必须回到 migration 前版本时，停止全部访问者并恢复对应 pre-migration backup；新数据库原样保留取证。
- 禁止手工执行逆向 SQL、把旧 WAL/SHM 放到恢复库旁或仅回滚部分表。
- 恢复会丢失 backup 创建后的本地事务，这是明确的 RPO 边界；必须把 Jenkins 后续 build 和旧库差异列入恢复报告，不能声称零数据丢失。

## 13. 运行手册

交付 `docs/flaky-governance-operations.md`，只覆盖 MVP 所需操作：

- 单进程 Dashboard 的启动、停止、配置校验和 loopback/管理网检查。
- liveness/readiness、日志轮转、磁盘空间和依赖不可用时的降级判断。
- `flaky-db-check`、artifact check、ops status、在线/离线备份和非覆盖恢复。
- PENDING/DISPATCHING/DISPATCH_UNKNOWN/QUEUED/RUNNING/CANCEL_REQUESTED 的诊断顺序。
- 安全重试、取消和“不得盲重试/不得强制释放 slot”的边界。
- Skip 与 Trigger 两个开关的独立关闭、恢复顺序和预期下一轮行为。
- 迁移失败、快照损坏、Jenkins 不可用、磁盘不足和数据库损坏的最小处理步骤。

手册命令必须可复制执行，但使用占位路径且要求 operator 先解析并核对绝对目标；不得提供会覆盖数据库或删除宽泛目录的示例。

## 14. 最终端到端验收

### 14.1 成功闭环

在受控环境使用一条精确参数化 Case 完成并保存证据：

1. 合格 NORMAL 历史形成 detection projection；CONFIRMED 本身仍执行。
2. 人工 CLI 创建 governance，记录 owner/reason/expires_at。
3. 下一轮 dev3 Smoke 生成有效 snapshot/decision，只 Skip 目标参数，兄弟参数执行。
4. Dashboard POST 创建 attempt/plan/trigger；固定 Probe Job 取得唯一 claim。
5. 五次满足间隔的 COUNT_PASS 形成 READY_TO_CLOSE；NORMAL detection 不变。
6. 人工 CLI close，trigger/round/evidence 和 target HEAD 门禁全部复验。
7. 下一轮 fresh snapshot 不含已关闭 governance，目标 Case 恢复执行并开启新 detection generation。

### 14.2 必须演练的异常链

| 异常链 | 必须证明的结论 |
| --- | --- |
| 可信 Probe FAIL | attempt FAILED、governance ACTIVE，下一轮普通 Smoke 继续精确 Skip |
| 基础设施/不合格 Probe | 不误计 PASS/FAIL；达到配额后 INCONCLUSIVE 并回 ACTIVE |
| 快照/计划损坏或数据库 busy | fail-open 执行测试，有稳定错误码，pytest 原始退出码保留 |
| 并发写/重复派发 | 单 writer、一个有效 claim、无重复 evidence 或部分事务 |
| 迁移/恢复失败 | 服务保持关闭，原库/备份保留；只有新路径通过全检查才可启用 |
| DISPATCH_UNKNOWN/取消 | 不盲重试；未确认终态前不提前释放治理和容量 |
| kill switch | Skip 关闭后下一轮全 RUN；Trigger 关闭后新请求拒绝、在途任务按取消协议收敛 |

### 14.3 验收证据包

生成本地受限的 `flaky-mvp-acceptance.v1` manifest，引用而不复制：

- 各阶段版本、Git commit、策略 revision、Schema/migration checksum。
- 成功闭环与异常链的 run/governance/attempt/trigger/build 安全 ID。
- 数据库检查、artifact check、备份 verify/restore check 的输出 hash。
- Dashboard/API/CLI/Pipeline Summary 一致性断言结果。
- kill-switch 前后 Run 的 decision/reconciliation hash。
- 每个验收项的 `PASS | FAIL | NOT_RUN` 和证据引用。

manifest 不包含数据库文件、secret、完整人工 reason 或测试 API 响应。任何 `NOT_RUN`、缺失 hash、检查失败或无法关联的证据都阻止 MVP 标记完成。

## 15. 测试矩阵

### 15.1 查询、页面与报告

- 同 fixture 下 DB DTO、CLI JSON、API JSON 和页面 view-model 字段/计数一致。
- 同 Run bundle 下 artifact check、Run DTO、Pipeline Summary JSON/Markdown/HTML 数量一致。
- DB 与文件时间点不同、关联缺失、未来 Schema、hash 错误均显示 DEGRADED/UNKNOWN。
- 五条状态轴不混合；STABLE(PASS)/STABLE(FAIL)、READY_TO_CLOSE/RECOVERED、COMPLETED/PASS 明确区分。
- 分页、恶意筛选、HTML/URL 注入、越界 run path 和超大文件被拒绝。
- Jenkins 链接只能落在固定 origin/Job；DISPATCH_UNKNOWN 无重试按钮。
- 报告失败不覆盖 pytest 失败，不直接修改 Jenkins build result。

### 15.2 数据库、备份与恢复

- quick check、foreign key、migration checksum、孤儿、重复活动记录和状态组合逐项故障注入。
- NORMAL/Probe 污染、重复 run/round、非法 plan digest、slot 泄漏和关闭门禁不一致可被发现。
- WAL 活跃时在线 backup 一致，源库仍可用且 writer 被有界串行化。
- backup 临时写失败、磁盘不足、hash/manifest 篡改和目标路径已存在均安全失败。
- 恢复不会覆盖源库，不混用旧 WAL/SHM；恢复库 deep check 与原备份计数一致。
- restore fence 开启时全部写侧拒绝；备份后 Jenkins 状态未对账时不能清除 fence 或重新派发。
- v2->v3/`0003`->`0004`->`0005` 迁移失败完整回滚，pre-migration backup 可恢复。

### 15.3 运维与端到端

- live/readiness component 在 DB、Jenkins、artifact 分别失败时给出正确独立状态。
- 日志脱敏覆盖 token、Cookie、Authorization、reason、路径和异常正文；轮转不丢关键 ID。
- 长期 trigger、OVERDUE、READY、磁盘不足和备份过期只告警，不自动改变业务状态。
- 两个 kill switch 分别关闭、同时关闭和非法配置的行为符合手册。
- 第 14 节成功闭环和全部异常链在隔离环境可重复，证据包检查可确定失败。

## 16. 实施工作包

| 顺序 | 工作包 | 交付物 | 完成条件 |
| --- | --- | --- | --- |
| S5-01 | 查询模型收口 | 扩展 DTO、跨源一致性状态、固定 run bundle resolver | CLI/API/页面/报告契约测试一致 |
| S5-02 | 看板与报告 | 完整页面、machine model、Markdown/HTML 渲染 | 状态轴、UNKNOWN 和链接安全测试通过 |
| S5-03 | 完整性工具 | `0005` restore fence、扩展 db-check、artifact-check、ops-status | 故障注入和恢复后误派发均被阻止 |
| S5-04 | 备份恢复 | backup/verify/restore-new 命令和 manifest | 在线/离线备份、非覆盖恢复演练通过 |
| S5-05 | 运行手册 | 启停、健康、日志、熔断、故障和恢复步骤 | 新环境按手册可重复演练 |
| S5-06 | 最终验收 | 成功闭环、异常链、acceptance manifest | 无 NOT_RUN/FAIL，所有引用 hash 可验证 |

每个工作包独立测试和评审。不得以“最终阶段”为理由并入权限系统、HA、集中监控或通用备份服务。

## 17. 完成门槛

只有以下全部成立，实施状态才可标记 `MVP_ACCEPTED`：

- 阶段 0 的真实 v2 数据审计、阶段 1 migration/状态机、阶段 2 Shadow、阶段 3 Probe 和阶段 4 Enforce 各自门槛均已完成。
- 自动检测、人工治理、Probe 证据、触发调度和执行决策边界在代码、Schema、产物及页面中一致。
- 未经人工治理的 Case 不会被 Skip；完整 identity 任一维度不同不会扩大 Skip。
- NORMAL 永不推进恢复，Probe 永不进入 NORMAL 检测，治理 Skip 永不成为 PASS/FAIL observation。
- 达标只到 READY_TO_CLOSE，人工 close 后下一轮恢复执行，迟到结果不重开。
- Dashboard 除一键验证外无写入口，匿名限制和数据时间明确可见。
- SQLite 单写者、显式 migration、检查、备份和非覆盖恢复均有真实演练证据。
- 成功闭环和第 14.2 节全部异常链通过，acceptance manifest 无 FAIL/NOT_RUN。
- 两个 kill switch、失败回退和 migration restore 步骤均按手册演练成功。
- 默认配置安全关闭；数据库/secret/备份未进入 Git 或 Jenkins artifact。

若自动化测试完成但任一真实演练、生产 v2 审计或备份恢复缺失，状态只能是 `MVP_READY / ACCEPTANCE_PENDING`，不得宣称完成。

## 18. 对抗式审计记录

| 发现 | 风险 | 修正或处置 |
| --- | --- | --- |
| 把数据库和 Run 文件描述成原子统一事实源 | 两者无法原子读取，会显示时间穿越的一致性 | 明确事实所有权，组合 DTO 暴露两类时间和 consistency status |
| Pipeline Summary 查询 live DB | 构建后治理变化会改写该 Run 的历史结论 | Summary 只消费当前 Run 冻结 artifact bundle |
| 为“统一”把 decision 双写 SQLite | 数据库与 JSON 无法原子提交，反而产生两个真相 | decision/application/reconciliation 继续只存不可变 Run 产物 |
| 缺失数据按空数组渲染 | 故障会伪装成 0 风险、0 Skip | DTO 和渲染强制 UNKNOWN/DEGRADED 与稳定错误码 |
| 把 COMPLETED trigger 显示为恢复成功 | 调度终态与 evidence 结果不是同一状态轴 | 页面分别展示 trigger 与 attempt，READY 也标注等待人工关闭 |
| Dashboard 按 run id 扫描磁盘 | 可造成路径穿越、无界 I/O 和敏感文件暴露 | 只允许受限 catalog 中显式登记的 bundle，manifest 相对路径逐项验证 |
| readiness 每次运行 deep check | 大库上阻塞请求并争抢 SQLite | readiness 使用短时缓存轻检查，deep check 仅维护窗口 CLI |
| 运维告警自动取消/修复状态 | 误报会改变治理事实，形成新控制面 | ops status 只读和告警，所有状态变化沿既有事务命令 |
| 活跃 WAL 时直接复制 `.db` | 备份可能缺事务或不可恢复 | 在线/离线均使用 SQLite backup API；离线另做 checkpoint |
| backup 只做 quick_check | hash、migration 或领域关系损坏仍可能漏过 | manifest + hash + foreign key + migration + deep domain check |
| restore 直接覆盖现用数据库 | 错误目标或旧 WAL 可造成不可逆破坏 | 只恢复到不存在新路径，隔离旧 WAL/SHM，人工切换配置 |
| 恢复旧备份后直接启动 dispatcher | 备份中的 PENDING 可能已在 Jenkins 投递，造成重复外部执行 | `0005` restore fence 阻断全部写侧；核对 backup 时间后的固定 Job 后显式解除 |
| 把备份视为零数据丢失 | backup 之后的治理事件/build 不在恢复库 | 明确 RPO 边界，保留旧库并生成外部执行/数据库差异报告 |
| 代码回滚同时手工降 Schema | 部分回滚会让旧代码误写新结构 | 优先 kill switch；不兼容时停服并恢复完整 pre-migration backup |
| 只演练 happy path | 无法证明 fail-open、幂等、取消和恢复边界 | 成功闭环外强制七类异常链，NOT_RUN 阻止验收 |
| 把一次演练称为生产稳定性证明 | 超出 MVP 证据能力 | 完成状态只声明最小闭环通过，不声明 SLA/容量/HA |
| 为监控引入完整平台 | 扩大依赖和运维面 | 仅提供结构化滚动日志、health 和只读 ops-status |
| 最终阶段新增更多 Web 操作 | 绕过既有 CLI 审计和匿名风险边界 | 保持唯一 POST，其余治理动作仍由 CLI 完成 |

审计后未发现需要在 MVP 内引入用户权限、高可用数据库、分布式队列、集中监控、自动通知或在线 Schema 降级的理由。
