# Flaky 治理 MVP 阶段 2：动态看板、Skip 快照与 Shadow

## 1. 阶段结论

阶段 2 交付只读看板、统一查询服务、运行级不可变 Skip 快照和 Shadow 决策链。所有命中只产生 `WOULD_SKIP`，测试仍实际执行；任何配置、数据库、快照或身份异常都退化为 `RUN` 并留下结构化原因。

当前状态：`SHADOW_VALIDATED / ENFORCE_NOT_AUTHORIZED`。阶段 2 实现、自动化测试与独立智能体验收已通过；2026-09-01 已按 6 个并发 Smoke 用例完成 10 个连续真实运行的逐轮核对，零身份扩大、零范围外候选、零实际 Skip。观察期间治理库无 ACTIVE/RECOVERING 候选，因此本轮只覆盖 `governance_not_matched -> RUN` 的真实运行链路；`WOULD_SKIP` 命中行为继续由自动化测试覆盖，本状态不授权 Enforce。详见《flaky治理MVP阶段2Shadow观察记录.md》。

## 2. 目标、范围与非目标

### 2.1 目标

- 建立一个只读查询服务，统一支撑 CLI、FastAPI、服务端页面和 Pipeline Summary。
- 展示总览、治理记录、检测投影、证据时间线、verification attempt、OVERDUE 和运行决策。
- 在 Runner 的权威收集前生成绑定本轮 run_id 的不可变快照。
- 权威收集后使用完整身份生成 `RUN` 或 `WOULD_SKIP` 决策计划。
- serial、parallel 和所有 xdist worker 消费同一份决策计划。
- 生成可校验、可追溯的决策与执行核对产物。
- 连续观察正式 Smoke 的 Shadow 结果，为后续是否进入 Enforce 提供人工证据。

### 2.2 明确不做

- 不产生实际 pytest skip mark，不接受 `SKIP` 作为阶段 2 的有效运行结果。
- 不提供任何 HTTP 写路由，不创建 attempt，不调用 Jenkins。
- 不实现登录、RBAC、审批、多租户、WebSocket、消息推送或复杂前端框架。
- 不支持公网监听、反向代理信任链或跨域访问；MVP 仅监听 loopback。
- 不建设通用报表平台或实时分析系统。
- 不改变测试原始退出码，不把 Shadow 候选计入 pytest skipped。

## 3. 前置条件

- 阶段 1 的显式 migration、v3 identity/governance/query 表和只读连接已经可用。
- 运行时 Store 遇到 pending/too-new Schema 会拒绝访问，不会自动迁移。
- `quality.v2` Run 契约能够在权威 collect 前创建稳定 run_id。
- 当前在制证据发布链路已完成或被隔离，不影响 Quality 全量测试收集。
- 阶段 0 的 `flaky-skip-decision.v1` reason code 与优先级继续作为机器契约。

任一前置条件不满足时，本阶段保持 `BLOCKED_BY_STAGE1`，不得用兼容分支绕过。

## 4. 统一只读查询服务

### 4.1 服务边界

新增 `FlakyReadService`，它从治理数据库和显式指定的单个 Run 产物目录读取数据，只返回版本化 DTO，不返回 SQLite row、SQL 文本或本地路径。CLI、HTTP 路由、页面 view-model 和 Pipeline Summary 必须调用该服务或消费其同版序列化结果，禁止各自复制查询和统计口径；服务不得扫描任意历史目录。

每次数据库查询：

- 以 SQLite read-only URI 打开短连接。
- 在一个只读事务中取得列表、计数、Schema/策略版本和 `data_as_of`。
- 校验 Schema 版本；pending/too-new 返回稳定错误码。
- 对 owner、状态、环境、画像、路径和关键字使用固定字段映射及绑定参数。
- 默认页大小 50、最大 100；使用稳定排序键和不透明游标，不允许调用方传 SQL 排序字段。
- 关键字最大 128 个字符，LIKE 通配符按字面量转义。

### 4.2 统一 DTO

最小 DTO：

- `DashboardSummary`：数据库健康、data_as_of、Schema/规则/策略版本、各 detected/governance/attempt 状态计数、OVERDUE 数量。
- `GovernanceListItem`：完整 identity、各状态轴、owner、expires_at、最新 evidence 时间。
- `FlakyCaseDetail`：identity、各 fingerprint projection、governance、attempt、NORMAL/Probe evidence 和治理事件时间线。
- `RunDecisionSummary`：run_id、snapshot_id、请求/生效模式、RUN/WOULD_SKIP/SKIP/fail-open 数量及 reason 分布。
- `Page[T]`：items、next_cursor、page_size、data_as_of；不得返回无界 total 明细。

`STABLE` 必须带 `stable_outcome`，页面分别显示“稳定通过”和“稳定失败”，不得统一显示为健康。
时间线固定按 `occurred_at、event_kind、event_id` 排序，不能依赖数据库默认顺序。

## 5. 只读 FastAPI 与页面

### 5.1 路由

只提供 GET/HEAD：

```text
GET /health/live
GET /health/ready
GET /api/v1/summary
GET /api/v1/governance
GET /api/v1/cases/{flaky_key}
GET /api/v1/runs/{run_id}/decisions
GET /
GET /governance
GET /cases/{flaky_key}
GET /runs/{run_id}/decisions
```

- API 使用相同版本化 DTO；HTML 仅做服务端模板渲染。
- 页面可用少量脚本定时刷新摘要，但不引入 SPA、Node 构建或前端状态库。
- 未找到资源返回 404；参数非法返回 400；数据库不可用返回 503 和稳定错误码。
- 错误响应不包含 SQL、堆栈、数据库绝对路径或配置值。

### 5.2 MVP 安全边界

- 默认且唯一允许绑定 `127.0.0.1` 或 `::1`；wildcard、域名和非 loopback 地址启动失败。
- 不启用 CORS，不设置会话 Cookie；阶段 2 无写路由，因此不引入无效的 CSRF 流程。
- Jinja 自动转义保持开启；case、owner、reason 和查询条件均作为文本输出。
- 限制筛选参数长度、页大小和时间范围，避免无界查询。
- `/health/live` 只表示进程存活；`/health/ready` 使用短时缓存的只读 Schema、连接和 quick-check 结果。
- FastAPI、Uvicorn、Jinja2 置于独立的 dashboard requirements 文件并锁定兼容版本，不影响不启用看板的测试运行。

阶段 3 若增加 POST，再单独引入 Origin、SameSite Cookie 和 CSRF；不得为未来写路由在本阶段建立权限平台。

## 6. Skip 快照契约

契约版本：`flaky-skip-snapshot.v1`。

```json
{
  "schema_version": "flaky-skip-snapshot.v1",
  "status": "READY",
  "snapshot_id": "snapshot-v1-<sha256>",
  "run_id": "<current-run-id>",
  "branch": "dev3",
  "generated_at": "<UTC>",
  "valid_until": "<UTC + 15 minutes>",
  "policy_revision": "<revision>",
  "database_schema_version": 3,
  "mode_requested": "shadow",
  "mode_effective": "shadow",
  "entries": [],
  "content_checksum": "sha256:<hex>"
}
```

每个 entry 至少包含：

```text
flaky_key
case_id
param_hash
environment
execution_profile
state_epoch
governance_id
governance_status
normalized_case_path
```

生成规则：

- 仅当 kill switch 开启且 effective mode 为 shadow 时访问数据库生成快照；off/disabled 直接生成 `status=DISABLED` 的审计 envelope。
- 可用快照在第一次权威 collect 之前生成，且只绑定一个 run_id。
- 只读取 ACTIVE/RECOVERING governance；OVERDUE 仍是候选，但同时产生告警。
- 快照按 `flaky_key` 排序；发现重复键、身份不完整或非法路径时整份快照无效。
- `snapshot_id` 和 `content_checksum` 使用规范 JSON；checksum 计算时排除自身字段。
- `valid_until = generated_at + 15 minutes`，只在 collection_started_at 判定有效性。
- `status` 只允许 READY、DISABLED、UNAVAILABLE；后两者不得携带治理候选。
- 数据库不可读、Schema/策略不兼容或生成失败时，不伪造空的“健康快照”，而是返回带稳定错误码的 UNAVAILABLE envelope。

快照、决策计划和执行后核对结果均写入当前 Run 的质量产物目录，作为执行决策事实源。阶段 2 不新增数据库迁移，也不把同一决策双写到 SQLite；看板查看某一 Run 时必须由调用方显式提供该 Run 的产物目录。

## 7. 收集身份与路径规范化

### 7.1 单一身份算法

把当前 pytest 插件私有的 item 身份构造提取为公共函数，由权威 collector 和执行插件共同调用：

```text
pytest.Item
  -> normalize_nodeid
  -> case_id
  -> canonicalized callspec.params + parameter_id
  -> param_hash
```

`CollectedTestCase` 扩展为：

```text
nodeid
markers
case_id
param_hash
normalized_case_path
```

execution_profile 在分池后确定；最终再与 environment、state_epoch 组合为 flaky_key。禁止从 nodeid 展示文本重建 param_hash。

### 7.2 路径范围

- 从 pytest item 的真实文件路径生成仓库相对 POSIX 路径并做 Unicode NFC。
- 快照候选的路径由持久化 `case_id` 的首个 `::` 段通过同一严格解析器取得，不使用模糊 nodeid 匹配；文件不存在或解析失败时该候选无效。
- resolve 后必须位于仓库根目录内；绝对输入、空段、`.`、`..` 和越界符号链接均拒绝。
- prefix 必须规范化为目录边界；`module/smoke/` 不匹配 `module/smoke_extra/`。
- exclude 先于 include；默认 include 仅为 `module/smoke/`。
- 同一 run 中若两个 nodeid 计算出相同完整 flaky_key，两个用例均 fail-open 为 RUN，primary reason 使用 `snapshot_invalid`，diagnostic code 为 `collection_identity_conflict`，并把运行标为 DEGRADED。

## 8. Runner 生命周期

阶段 2 的顺序固定为：

```text
解析参数
  -> 创建 run context 与初始 RunRecord
  -> 尝试读取治理库并生成一次快照
  -> 记录 collection_started_at
  -> 权威 collect
  -> 分配 execution_profile
  -> 生成并原子写入决策计划
  -> 分 parallel/serial pool
  -> 各 pytest 进程只读消费同一计划
  -> 汇总 P0
  -> 写独立核对产物
  -> 运行现有 NORMAL import/evaluation
```

关键约束：

- 当前 `QualityRunLifecycle` 创建晚于 collect，必须前移；同一个 run_id 贯穿初始 RunRecord、快照、collect、pool、P0 和报告。
- 快照只生成一次；collect、pool 和 worker 禁止访问治理数据库。
- 用户显式 `--collect-only` 时仍可生成快照和 Shadow 决策供审计，但不执行 pool、不生成实际结果。
- collection 失败时不生成伪决策；写 unavailable/DEGRADED 结果并保留 pytest 原退出码。
- 当前测试结束后的 `quality_flaky_stage` 继续只负责 NORMAL 导入与检测，不得承担收集前快照。

## 9. Shadow 决策计划

阶段 2 只允许：

```text
auto_skip_enabled=false                 -> RUN
auto_skip_enabled=true + mode=off       -> RUN
auto_skip_enabled=true + mode=shadow    -> RUN 或 WOULD_SKIP
mode=enforce                            -> 降级为 off，并告警 skip_enforce_not_available
```

决策严格复用阶段 0 `flaky-skip-decision.v1` 的 lower_snake_case reason code 和优先级，不新增一套大写别名。细节错误进入排序后的 `diagnostic_codes`。

每条决策必须同时验证：run_id、controller 验证的 dev3 分支、15 分钟时间窗、策略 revision、快照/数据库 Schema、content checksum、完整 identity、治理状态和路径范围。

决策计划契约 `flaky-skip-decisions.v1` 至少包含：

- run_id、snapshot_id、snapshot checksum、collection_started_at。
- mode_requested、mode_effective、policy_revision。
- 每个 nodeid 的完整 identity、decision、primary reason、diagnostic codes 和 governance_id。
- RUN、WOULD_SKIP、SKIP、fail-open 计数。

阶段 2 中 `SKIP` 计数必须恒为 0。`flaky-skip-decisions.json` 由 Runner 主进程在执行前原子写一次；执行后核对另写 `flaky-skip-reconciliation.json`，不得回写或覆盖原决策计划。

## 10. pytest 插件消费

- Runner 通过只读文件路径和 checksum 把决策计划传给每个 pool。
- pytest 插件在 collection modify 阶段校验 schema、run_id、checksum 和 nodeid/identity 对应关系。
- 阶段 2 即使命中 WOULD_SKIP 也不添加 skip mark，只记录匹配结果。
- worker 不写共享决策文件；P0 shard 仍按现有 worker 规则独立生成。
- worker 看到缺失、损坏或身份不符的计划时执行测试，并写结构化 integrity issue。
- 父进程在结束后对比计划 nodeid、实际 CaseResult 和 pool 结果；缺失、重复或意外 skipped 标记 DEGRADED，但不覆盖原始 pytest 失败码。

## 11. 看板与 Pipeline Summary

- 看板、CLI 和 Pipeline Summary 对状态及数量均使用 `FlakyReadService` 的 DTO 或同一序列化产物。
- 总览显示数据时间、数据库健康、Schema/规则/策略版本和 mode_requested/mode_effective。
- 治理列表支持状态、owner、超期、环境、画像、路径和关键字筛选。
- Case 详情按时间展示 governance event、attempt、Probe evidence、各 fingerprint detection transition；不得把不同状态轴合成单一状态。
- Pipeline Summary 新增本轮 WOULD_SKIP、RUN、fail-open 及 reason 分布；实际治理 Skip 固定显示 0。
- 数据不可用显示 UNKNOWN/DEGRADED 和错误编号，不显示 0。

## 12. 配置与默认值

```text
QUALITY_FLAKY_AUTO_SKIP_ENABLE=0
QUALITY_FLAKY_SKIP_MODE=off
QUALITY_FLAKY_SNAPSHOT_MAX_AGE_MINUTES=15
QUALITY_FLAKY_DASHBOARD_HOST=127.0.0.1
QUALITY_FLAKY_DASHBOARD_PORT=<fixed-local-port>
```

- 未知布尔值或模式统一降级为关闭并告警。
- 阶段 2 仅 `off` 和 `shadow` 可成为 effective mode。
- 环境变量只选择版本化策略，不允许逐项覆盖 identity、路径和 reason-code 规则。
- 配置解析失败不阻止 pytest 执行；治理匹配全部 fail-open。

## 13. 实施工作包

| 顺序 | 工作包 | 交付物 | 完成条件 |
| --- | --- | --- | --- |
| S2-01 | 查询契约 | DTO、read-only repository、`FlakyReadService` | CLI/API/reporting 对同一 fixture 输出一致 |
| S2-02 | 只读看板 | FastAPI GET 路由、Jinja 页面、health | loopback、安全转义、分页和错误降级测试通过 |
| S2-03 | 运行产物契约 | snapshot、decision plan、reconciliation | 规范化序列化、原子写入和 checksum 测试通过 |
| S2-04 | 快照生成 | 规范化 snapshot 与 checksum | run/branch/policy/时间/身份损坏均 fail-open |
| S2-05 | 收集身份 | 公共 item identity、扩展 CollectedTestCase | 与 P0 插件对参数化用例逐项一致 |
| S2-06 | Runner 接线 | 生命周期前移、单计划共享、原子产物 | serial/parallel/xdist 使用同一 run 与 checksum |
| S2-07 | Shadow 核对 | reconciliation 与 Pipeline Summary | WOULD_SKIP 不改变 pytest 结果，差异标 DEGRADED |
| S2-08 | 观察门禁 | 10 个正式 Smoke Run 的人工核对记录 | 零身份扩大、零范围外候选 |

## 14. 测试与验收

### 14.1 自动化测试

- Read service 的筛选、稳定游标、页大小上限和同事务 data_as_of。
- HTML 转义、恶意筛选、未知字段、超长参数、非 loopback 绑定拒绝。
- STABLE(PASS) 与 STABLE(FAIL) 的 API 和页面呈现不同。
- 快照内容排序、checksum、15 分钟边界、run/branch/policy/Schema 不匹配。
- 精确参数、兄弟参数、环境、execution profile、epoch 和路径边界匹配。
- 重复 flaky_key、符号链接越界、`module/smoke_extra` 前缀欺骗均 fail-open。
- collect-only、serial、parallel、xdist 的计划 checksum 和 run_id 一致。
- worker 无数据库访问；损坏决策计划时测试仍执行。
- 决策文件只写一次，核对结果写独立文件。
- HTTP 路由列表中不存在 POST/PUT/PATCH/DELETE。
- 配置要求 enforce 时 effective mode 为 off，且 SKIP 恒为 0。

### 14.2 退出门槛

- 阶段 1 全部门槛继续通过。
- 新增自动化测试通过，且原有 pytest 原始退出码语义不变。
- 看板、CLI、数据库和 Pipeline Summary 对固定 fixture 的计数一致。
- 10 个正式 Smoke Run 完成人工逐项核对，零身份扩大、零范围外候选。
- 未实现或未启用任何实际 Skip 和 HTTP 写入。

自动化完成但观察窗口不足时，状态为 `SHADOW_READY / OBSERVATION_PENDING`；全部门槛完成后为 `SHADOW_VALIDATED / ENFORCE_NOT_AUTHORIZED`。

## 15. 对抗式审计记录

| 发现 | 风险 | 处置 |
| --- | --- | --- |
| 当前 run context 在权威 collect 之后创建 | 快照、collect 和执行可能使用不同 run_id | 生命周期前移，run_id 在任何 collect 前冻结 |
| collector 只有 nodeid/marker | 参数身份只能靠展示文本猜测，可能扩大匹配 | 提取公共 pytest item identity，collector 与 P0 插件共用 |
| 快照先于 collect，但 execution profile 后于分池确定 | 提前计算 flaky_key 会使用错误画像 | 快照保存完整候选，分池后再组合最终 identity |
| `module/smoke/` 用普通 startswith | `module/smoke_extra/` 可能越界命中 | 规范化为目录边界并先应用 exclude |
| 同一参数值可能对应多个 nodeid | `(run_id, flaky_key)` 不能唯一代表两个执行项 | 收集时判为 identity conflict，相关项全部 RUN 并标 DEGRADED |
| 决策文件既要求只写一次又要求执行后核对 | 回写会破坏执行时不可变证据 | 决策计划与核对结果拆成两个产物 |
| 同一决策同时写 SQLite 和 JSON | 两次写入无法原子提交，查询与执行事实可能分叉 | 阶段 2 只把不可变运行产物作为决策事实，不新增 `0004` |
| 阶段 2 接受 enforce 配置 | 实现缺陷可能提前产生真实 Skip | enforce 强制降级为 off，插件不包含添加 skip mark 的分支 |
| 只读页面仍设计 Cookie/CSRF/RBAC | 增加无效复杂度并偏离 MVP | 阶段 2 无会话、无写路由，仅 loopback、转义和输入限制 |
| Dashboard 每次请求执行完整 quick_check | 阻塞查询并放大数据库压力 | ready 使用短时缓存，业务查询只做版本与读事务检查 |
| off/disabled 仍访问治理数据库 | 已关闭功能仍可能引入延迟和错误 | 直接生成 DISABLED envelope，跳过数据库读取 |
| Shadow 代码异常影响 pytest 退出码 | 治理辅助功能会破坏原测试结论 | 所有快照/决策失败均 RUN，错误进入结构化告警和 DEGRADED |
| 自动化测试替代正式 Smoke 观察 | 无法证明真实收集身份和路径没有扩大 | 10 轮人工核对保留为独立门槛，不用单测冒充完成 |

审计后未发现需要在阶段 2 引入公网部署、权限系统、实时推送、分布式缓存或高可用数据库的理由。
