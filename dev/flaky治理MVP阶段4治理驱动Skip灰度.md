# Flaky 治理 MVP 阶段 4：治理驱动 Skip 灰度

## 1. 阶段结论

阶段 4 只把阶段 2 已验证的 `WOULD_SKIP` 决策链切换为受控 `SKIP`：在 controller 已验证的 `dev3` 分支上，仅对 `module/smoke/` 范围内、完整身份精确命中 ACTIVE/RECOVERING governance 的 pytest item 应用治理 Skip。决定仍来自运行开始时冻结的快照和决策计划，worker 不访问数据库；任何配置、快照、计划、身份或应用异常均 fail-open 为执行测试。

本阶段不改变自动检测、恢复证据或人工关闭语义，不扩展看板和运维平台。当前状态：`PLAN_REVIEWED / STAGE3_DEPENDENCY`。只有阶段 3 达到 `PROBE_VALIDATED / ENFORCE_NOT_AUTHORIZED` 后才可实施；方案评审完成不代表实际 Enforce 已开启或灰度验收已通过。

## 2. 目标、范围与非目标

### 2.1 目标

- 复用 `flaky-skip-snapshot.v1`、`flaky-skip-decisions.v1` 和公共 pytest item identity，不建立第二套匹配规则。
- 仅在 `auto_skip_enabled=true + mode=enforce + verified branch=dev3` 时允许产生 `SKIP`。
- 对命中的精确用例应用结构化原因 `FLAKY_QUARANTINED:<governance_id>`。
- 为计划、mark 应用和最终 pytest 结果建立独立、不可变的核对事实。
- 保证治理 Skip 不产生 NORMAL PASS/FAIL observation，也不改变 detected state。
- OVERDUE governance 继续 Skip，并产生明确告警，禁止自动解除隔离。
- 快照/计划不可用时执行测试；治理辅助异常不覆盖 pytest 原始退出码。
- 提供可在下一轮 Run 生效的 kill switch 回滚，并完成受控灰度核对。

### 2.2 明确不做

- 不改变阶段 0 冻结的 Skip 公式、identity、path 或 reason-code 规则。
- 不对 `dev3` 以外分支、`module/smoke/` 以外路径或模糊 nodeid 启用 Skip。
- 不按百分比、用户、租户或动态流量做生产级灰度平台；灰度单位只是固定 Jenkins Job 的逐轮开关。
- 不让 worker、pytest 插件或报告查询治理数据库。
- 不将 CONFIRMED/SUSPECTED、OVERDUE 告警或 owner 字段单独当作 Skip 授权。
- 不解析 pytest 文本 reason、控制台日志或 JUnit message 来判断 Skip 来源。
- 不把治理 Skip 写成 PASS、FAIL 或 Probe evidence，不推进恢复 attempt。
- 不自动关闭、自动取消或自动重开 governance。
- 不新增数据库 migration；本阶段事实继续使用运行目录中的不可变 JSON 产物。
- 不在本阶段完成阶段 5 的看板详情、备份恢复、服务手册或综合验收报告。

## 3. 前置门槛

开始实现前必须同时满足：

1. 阶段 2 已达到 `SHADOW_VALIDATED`，连续至少 10 个正式 Smoke Run 为零身份扩大、零范围外候选。
2. 阶段 3 已达到 `PROBE_VALIDATED`，成功、可信 FAIL、DISPATCH_UNKNOWN 和取消演练均已完成。
3. 当前受版本控制基线、阶段 0 契约、阶段 1 状态机、阶段 2 Shadow 和阶段 3 Probe 测试全部通过。
4. `QUALITY_FLAKY_AUTO_SKIP_ENABLE` 默认仍为 `0`，部署阶段 4 代码时不同时打开 enforce。
5. 灰度 Job 能提供 controller 验证的 branch、唯一 run_id、固定策略 revision 和原始 pytest exit code。
6. 已准备至少一条受控 ACTIVE governance、一个 RECOVERING governance、兄弟参数和路径外用例用于正反例核对；不得为验收篡改生产事实。

任一门槛缺失时最多开发和运行离线测试，不得开启真实治理 Skip。

## 4. 冻结的决策与应用边界

### 4.1 三层事实

| 层次 | 事实 | 唯一职责 |
| --- | --- | --- |
| 决策计划 | `RUN / WOULD_SKIP / SKIP` | 运行前依据快照确定该 item 应如何处理 |
| 应用记录 | `ARMED / NOT_APPLIED / FAIL_OPEN / NOT_EXECUTED` | pytest 进程是否通过校验并获准应用治理 mark |
| 执行事实 | P0 CaseResult 与完整性事实 | pytest 最终发生了什么，不反推治理决定 |

三层不可互相伪造：计划 `SKIP` 不等于 mark 已生效；CaseResult 为 SKIPPED 不证明它来自治理；应用记录也不能被转换成 NORMAL observation。只有 `SKIP + ARMED + P0 SKIPPED` 的完整核对才能称为实际治理 Skip。

### 4.2 唯一 Skip 公式

继续使用阶段 0/2 的完整公式：

```text
auto_skip_enabled == true
AND mode_effective == enforce
AND snapshot.run_id == current_run.run_id
AND snapshot.branch == controller_verified_branch == dev3
AND snapshot.generated_at <= collection_started_at <= snapshot.valid_until
AND snapshot.policy_revision == current_policy_revision
AND governance.status IN (ACTIVE, RECOVERING)
AND snapshot entry identity == collected item identity
AND normalized_case_path HAS_DIRECTORY_PREFIX module/smoke/
AND normalized_case_path NOT_HAS_DIRECTORY_PREFIX exclude_paths
AND snapshot/plan schema、checksum 和引用全部有效
```

identity 必须逐字段相等：`case_id + param_hash + environment + execution_profile + state_epoch`。`dev3` 只表示分支门禁，不是 china/overseas 环境值；环境仍是 identity 的独立维度。

### 4.3 优先级

1. kill switch 关闭或 `mode=off`：所有 item 为 RUN，不读取治理库。
2. 配置未知、分支不可验证、快照/计划缺失、过期、损坏或版本不兼容：全部或受影响 item fail-open 为 RUN。
3. identity/path 冲突、重复 flaky key、兄弟参数或范围外路径：受影响 item RUN，并写稳定诊断码。
4. 完整公式命中且 `mode=shadow`：WOULD_SKIP，测试仍执行。
5. 完整公式命中且 `mode=enforce`：计划为 SKIP，进入 pytest 应用门禁。
6. 其余情况：RUN。

不得用数据库当前状态覆盖已冻结的当前 Run。治理在快照后关闭，本轮仍按原计划执行；从下一轮新快照开始恢复 RUN。治理在快照后新建，同样从下一轮开始命中。

## 5. 配置与激活门禁

```text
QUALITY_FLAKY_AUTO_SKIP_ENABLE=0
QUALITY_FLAKY_SKIP_MODE=off|shadow|enforce
QUALITY_FLAKY_SNAPSHOT_MAX_AGE_MINUTES=15
```

- 默认值仍为 `0 + off`；未知布尔值、未知 mode、空值冲突一律 effective off 并告警。
- `mode_requested=enforce` 只有在开关开启、controller 验证 branch 为 `dev3` 且策略/Schema 兼容时才得到 `mode_effective=enforce`。
- 非 dev3 请求 enforce 时 effective off，不退化为 shadow，也不访问治理数据库生成可执行候选。
- 快照生成器只在 effective mode 为 shadow 或 enforce 时读取数据库；off/disabled 直接生成不含候选的 DISABLED envelope。
- path 范围来自冻结策略，调用者不能用环境变量扩大 include/exclude。
- 配置在 run context 创建时读取一次并写入产物；worker 只消费计划中的 effective mode，不重新读取环境形成分叉。
- kill switch 在当前 Run 计划冻结后变化，不改写已开始的 Run；关闭后的下一轮必须生成 DISABLED 快照和全 RUN 计划。

阶段 4 不增加“强制跳过某 nodeid”或“忽略快照校验”的调试开关。

## 6. Enforce 决策计划

`flaky-skip-decisions.v1` 已冻结 SKIP 枚举和必要字段，本阶段不升级其 Schema，只解除阶段 2 对 enforce 的强制降级。生成器必须：

- 在第一次权威 collect 后、分池完成且执行前，对公共 item identity 做一次决策。
- 保持 run_id、snapshot id/checksum、collection time、policy revision、mode requested/effective 和完整 identity。
- 对每个 item 写 decision id、decision、primary reason、diagnostic codes 和 governance id。
- 对 SKIP 条目验证 governance status 只能为 ACTIVE/RECOVERING；损坏产物中出现 CLOSED/CANCELLED + SKIP 时整条 fail-open。
- 只在规范路径 `module/smoke/` 下生成 SKIP；`module/smoke_extra/`、符号链接越界和非仓库文件全部 RUN。
- 继续由 Runner 主进程原子写一次，写完后以 checksum 只读传给 serial/parallel/xdist 进程。

collect-only 可以生成 SKIP 计划用于检查，但没有执行和 mark 应用，实际治理 Skip 计数必须为 0，应用状态为 `NOT_EXECUTED`。

## 7. pytest 应用协议

### 7.1 应用前校验

每个 pytest 进程在 `pytest_collection_modifyitems` 的 `trylast=True` hook 中：

1. 读取同一只读决策计划并复验 Schema、文件 checksum、run id、mode effective 和 snapshot 引用。
2. 使用公共 item identity 重新计算 item 映射；禁止仅以 nodeid 文本查找。
3. 验证本进程收到的 item 集合无缺失映射、重复 decision id 或一对多 identity。
4. 计算该进程的完整应用记录，并在添加任何 mark 前原子写入 shard；写入失败时该进程不添加任何治理 mark。
5. 仅对已持久化为 ARMED 的 decision=SKIP item 应用治理 mark。
6. 任一计划级错误使该进程全部 RUN；单 item identity 错误只使该 item RUN。

worker 不访问数据库、不重算策略、不自行查询 governance，也不修改共享决策文件。

### 7.2 治理 mark

应用形式固定为：

```text
pytest.mark.skip(reason="FLAKY_QUARANTINED:<governance_id>")
```

- governance id 必须来自已校验计划并符合内部 ID 格式；reason 不包含 owner、人工原因或其他非可信文本。
- reason 仅供人阅读，不是事实源；应用事实通过 decision id、governance id 和 item identity 结构化记录。
- item 已存在 `skip`、`skipif` 或 `xfail` marker 时，为避免把业务语义误报为治理 Skip，本阶段保守地不追加治理 mark，记录 `NOT_APPLIED/business_marker_present` 并执行 pytest 原有语义。
- 不解析或提前执行任意 skipif 表达式。带业务 marker 的治理候选属于明确 fail-open 限制，灰度清单必须逐条显示。
- Probe Job 继续通过阶段 3 的受信计划显式绕过治理 mark；不能靠删除普通业务 marker 绕过 skip/xfail。

### 7.3 应用事实

每个进程写独立 `flaky-skip-application.v1` shard，至少包含：

```text
run_id / worker_id / decisions_checksum
decision_id / nodeid / full identity / governance_id
planned_decision
application_status = ARMED | NOT_APPLIED | FAIL_OPEN | NOT_EXECUTED
reason_code / diagnostic_codes
```

- ARMED 表示 mark 获得应用授权，最终是否生效由 P0 核对；添加 mark 的过程中若出现异常，必须移除本 hook 已添加的 mark 并让测试执行，核对结果记为 mismatch。
- shard 路径和 worker id 使用既有 P0 分片规则，不写共享文件；预期 shard 落盘失败时不得先行 Skip。
- parent 只合并同 run id、同决策 checksum 的 shard。同一 worker id 的重复 shard、预期 shard 缺失或外来 shard 标记 DEGRADED。
- xdist 各 worker 会重复收集同一 decision；跨 worker 的重复 decision 属于预期，按 `(worker_id, decision_id)` 保留。最终 CaseResult 只与实际执行它的 worker 应用记录关联，不跨 worker 随意取一条。
- P0 merge 必须保留每条 CaseResult 的来源 shard/worker 引用；若来源不可证明，该结果不能被归类为实际治理 Skip，只能标记 `execution_evidence_ambiguous`。
- 应用事实不写 SQLite，不参与 detection 或 attempt 状态机。

## 8. 执行后核对与事实隔离

新增 `flaky-skip-reconciliation.v2`，连接计划、应用 shard 和 P0 CaseResult：

| 计划/应用/结果 | 核对结论 |
| --- | --- |
| SKIP / ARMED / SKIPPED | `governance_skip_observed` |
| SKIP / NOT_APPLIED(business marker) / pytest 原结果 | `business_marker_precedence` |
| SKIP / FAIL_OPEN / PASSED或FAILED | `governance_skip_failed_open` |
| RUN或WOULD_SKIP / 未应用 / 正常结果 | `execution_as_planned` |
| RUN或WOULD_SKIP / 任意治理 mark | `unexpected_governance_skip`，DEGRADED |
| SKIP / ARMED / PASSED或FAILED/缺失 | `skip_application_mismatch`，DEGRADED |
| 任意 / 未知或重复 CaseResult | `execution_evidence_ambiguous`，DEGRADED |

核对不得从 JUnit skip message 解析 governance id。治理来源由应用 shard 证明，P0 只证明最终结果。

- v2 writer 只生成新版本；reader 必须继续读取阶段 2 的 v1 Shadow 核对产物，但不得为缺少 application/worker 来源的历史文件补造“实际治理 Skip”。
- 不支持的未来版本显示 UNKNOWN/DEGRADED，不回退解析自由文本，也不把缺失字段当作 0。

### 8.1 NORMAL 与 Probe 隔离

- NORMAL importer 继续把 SKIPPED/XFAILED/XPASSED 分类为 `case_expected_outcome_excluded`，不写 `flaky_normal_observation`。
- `governance_skip_observed` 只存在于决策/应用/核对产物，不伪装为 PASS、FAIL 或 NO_DATA observation。
- Probe importer 不读取普通 Smoke 的 Skip application；Probe 的治理绕过由阶段 3 计划验证。
- 执行治理 Skip 前后，对相同合格 NORMAL 历史重放得到的 detected state 和 transition id 必须一致。
- CLOSED 后的迟到 Probe evidence 只审计，不能重开 governance，也不能进入后续 Run 的快照。

## 9. OVERDUE、关闭与运行时竞态

### 9.1 OVERDUE

- OVERDUE 是 `expires_at <= snapshot.generated_at` 的派生告警，不是第四种治理状态。
- ACTIVE/RECOVERING 即使 OVERDUE 仍按完整公式 SKIP，并在快照、计划、核对和最小 Pipeline Summary 中标记 `governance_overdue`。
- 到期不自动关闭、不自动回 ACTIVE、不自动从快照移除；owner 必须通过既有人工流程处置。

### 9.2 关闭与迟到结果

- 人工 close 在当前 Run 快照之后发生时，不回写当前快照或决策计划。
- close 成功后的下一轮 fresh snapshot 不再包含该 governance，因此目标 item 恢复 RUN。
- attempt FAILED/CANCELLED/EXPIRED/INCONCLUSIVE 使 governance 回到 ACTIVE；下一轮仍应 Skip。
- 迟到 Probe evidence 对 CLOSED governance 只形成审计记录，不能使下一轮重新 Skip。
- 当前 Run 的治理 mark 一旦按有效计划应用，不因数据库随后 busy、治理变更或快照到期而撤销。

## 10. Fail-open 与退出码

### 10.1 Fail-open 矩阵

| 故障 | 当前 Run 行为 | 结构化事实 |
| --- | --- | --- |
| 数据库 busy/不可读 | 全部 RUN | snapshot UNAVAILABLE + `snapshot_database_unavailable` |
| Schema/策略不兼容 | 全部 RUN | `snapshot_version_incompatible` |
| 快照缺失/过期/checksum 错误 | 全部 RUN | `snapshot_invalid` |
| 决策计划缺失/损坏/run id 不符 | 对应进程全部 RUN | application FAIL_OPEN |
| 单 item identity/path 冲突 | 该 item RUN | item-level FAIL_OPEN |
| shard 缺失或核对不一致 | 不重跑、不改结果 | reconciliation DEGRADED |
| kill switch 关闭 | 下一轮全部 RUN | DISABLED envelope |

UNAVAILABLE 不能伪装成空 READY 快照；“未 Skip”与“没有治理候选”必须可区分。

### 10.2 pytest/Jenkins 结果

- 治理逻辑的预期校验异常在应用边界内转换为 fail-open，不应中断 pytest collection。
- 非治理用例失败、治理候选因 fail-open 后失败，均保留 pytest 原始失败与退出码。
- 决策/应用/核对异常可以令质量摘要显示 WARN/DEGRADED，但不得修改 `currentBuild.result` 或最终 pytest exit code。
- pytest 本身的 collection/internal error 仍按 pytest 原始语义失败，不能被治理 fail-open 吞掉。
- 收尾写产物失败不得把原始测试失败改成成功，也不得把成功构建伪造成已验证的治理 Skip。

## 11. 最小展示要求

阶段 4 只修正开启 enforce 后必需的信息准确性：

- Pipeline Summary 从核对产物显示计划 SKIP、实际 `governance_skip_observed`、business marker precedence、fail-open 和 OVERDUE 数量。
- 数据缺失显示 UNKNOWN/DEGRADED，不显示 0。
- pytest/Jenkins 控制台可显示固定治理 reason，但统计不得解析该文本。
- 看板沿用阶段 2 查询页；阶段 5 再补完整 evidence 链、筛选和运维视图。

## 12. 灰度与回滚

### 12.1 开启顺序

1. 以 `AUTO_SKIP_ENABLE=0 + mode=off` 部署代码，证明所有 item RUN，原始退出码不变。
2. 切到 shadow，复跑阶段 2 的身份、路径、serial/parallel/xdist 核对。
3. 只在一个固定 dev3 Smoke Job 的单次受控构建设置 `AUTO_SKIP_ENABLE=1 + mode=enforce`。
4. 核对 ACTIVE、RECOVERING、OVERDUE、兄弟参数、路径外用例和业务 marker 正反例。
5. 完成至少 10 个连续正式 dev3 Smoke Run，且每个活动治理候选至少被 collect 一次；出现新候选时重新逐条核对。

不开启按比例流量或多 Job 扩散。若候选集合为空，只能证明“enforce 无候选时不误 Skip”；还需使用非生产受控 governance 完成至少一次真实 mark 应用，不能用零计数冒充成功灰度。

### 12.2 立即回滚条件

出现任一情况即关闭 kill switch，并从下一轮恢复全 RUN：

- 兄弟参数、错误环境/画像/epoch 或 `module/smoke/` 范围外 item 被治理 Skip。
- RUN/WOULD_SKIP item 出现治理 mark，或治理 Skip 缺少可追溯 application fact。
- snapshot/plan 损坏时仍发生 Skip。
- governance 已在本轮快照生成前 CLOSED，却仍进入计划。
- 任何治理异常改变 pytest 原始退出码或覆盖真实测试失败。
- 实际 Skip 与核对产物、CLI/数据库事实不一致。

回滚只修改 Job 配置中的开关/mode，不删除 governance、快照、decision 或 application 产物，不回滚数据库 Schema。已冻结的在途 Run 仍按原计划完成并保留审计；验收以关闭后的下一轮为准。

## 13. 实施工作包

| 顺序 | 工作包 | 交付物 | 完成条件 |
| --- | --- | --- | --- |
| S4-01 | Enforce 模式门禁 | 配置矩阵、branch/path 验证、决策生成 | 非 dev3/范围外/未知配置恒为 RUN |
| S4-02 | pytest 应用 | mark 应用、业务 marker 保守旁路、application shard | serial/parallel/xdist 精确且 worker 无 DB |
| S4-03 | 核对与隔离 | reconciliation v2、NORMAL importer 排除测试 | Skip 不形成 observation，异常可追溯 |
| S4-04 | 最小报告 | Summary 的实际 Skip/fail-open/OVERDUE 计数 | 不解析文本、不覆盖 pytest 结果 |
| S4-05 | 故障与回滚 | kill switch、损坏产物、退出码测试和回滚清单 | 下一轮全 RUN，可保留故障证据 |
| S4-06 | 受控灰度 | 单 Job、正反例和 10 轮逐项核对记录 | 无幽灵 Skip、无扩大 Skip |

不得在 S4 工作包中加入阶段 5 的备份服务、长期监控、权限平台或综合看板重构。

## 14. 测试与验收

### 14.1 自动化测试

- `auto_skip_enabled × requested mode × verified branch` 全矩阵及未知配置 fail-open。
- ACTIVE、RECOVERING 精确命中；CLOSED、CANCELLED resolution、无 governance 恒不命中。
- case id 相同但 param、environment、execution profile、epoch 任一不同均 RUN。
- `module/smoke/`、`module/smoke_extra/`、exclude、绝对路径、`..`、大小写和符号链接边界。
- 快照时间边界、run id/policy/schema/checksum 不匹配和重复 flaky key。
- 同一决策计划在 serial、parallel、xdist worker 得到完全相同的应用集合。
- 计划 SKIP 的普通 item得到 ARMED + SKIPPED；预有 skip/skipif/xfail marker 得到 NOT_APPLIED，且不解析表达式。
- collect-only 产生 NOT_EXECUTED，不计为实际治理 Skip。
- application shard 重复、缺失、异 run、checksum 不同和未知状态的核对。
- xdist 同 decision 的跨 worker 重复不误报；P0 worker 来源缺失时不猜测关联。
- 治理 Skip CaseResult 被准入审计排除，NORMAL observation、detected state 和 attempt 均不变化。
- governance 在快照前关闭不命中；快照后关闭只在下一轮恢复执行。
- OVERDUE 继续 Skip并告警；attempt 失败/取消回 ACTIVE 后下一轮继续 Skip。
- CLOSED 后迟到 Probe evidence 不进入下一轮快照。
- 数据库 busy、快照/计划损坏、worker 读取失败和 identity conflict 均实际执行测试。
- fail-open 后测试失败保留原始失败；治理收尾异常不覆盖 pytest/Jenkins 结果。
- kill switch 关闭后的下一轮 SKIP=0；当前已冻结 Run 不发生 worker 间模式分叉。

### 14.2 灰度验收

- 至少一次 ACTIVE、一次 RECOVERING 和一次 OVERDUE 精确治理 Skip 可从 snapshot -> decision -> application -> P0 -> reconciliation 全链追溯。
- 兄弟参数、错误环境/画像/epoch、范围外路径及 CLOSED governance 全部执行。
- 至少 10 个连续正式 dev3 Smoke Run 逐条核对，无幽灵 Skip、无扩大 Skip、无来源不明的 SKIPPED。
- 快照缺失、损坏、版本不兼容和数据库 busy 的受控演练均执行测试并留下 fail-open 事实。
- 人工 close 后下一轮恢复执行；迟到 Probe 结果不会重新产生 Skip。
- kill switch 关闭后的下一轮不再产生治理 mark，原始测试行为恢复。
- 数据库、CLI、快照、决策、应用和核对产物对每条实际治理 Skip 的 governance/identity 结论一致。

### 14.3 退出状态

- 代码和自动化测试完成、尚未跑足灰度窗口：`ENFORCE_READY / GREY_VALIDATION_PENDING`。
- 全部灰度验收完成：`ENFORCE_VALIDATED / MVP_COMPLETION_PENDING`。

阶段 4 完成不等于整个 MVP 完成；阶段 5 仍需完成看板/报告收尾、操作手册、备份恢复演练和端到端完成审计。

## 15. 对抗式审计记录

| 发现 | 风险 | 修正或处置 |
| --- | --- | --- |
| 只把配置 parser 改为接受 enforce | worker 可能在未验证快照或错误分支直接加 mark | 保留完整公式和应用前二次校验，任一失败均 RUN |
| 信任 `BRANCH_NAME` 字符串 | 调用者可伪装 dev3，在其他分支启用 Skip | 只接受 controller 验证的 branch 事实并固化进 run/plan |
| 普通字符串前缀判断路径 | `module/smoke_extra/` 或越界符号链接被扩大 Skip | 复用阶段 2 的规范路径和目录边界算法 |
| worker 实时查询 governance | 并发 worker 可看到不同时点，close 时产生混合结果 | worker 只读同一不可变决策计划，当前 Run 不被后续数据库变化改写 |
| kill switch 动态影响当前 Run | 一部分 worker Skip、另一部分执行，无法重放 | 配置在 run context 冻结；关闭保证下一轮全 RUN |
| 仅凭 CaseResult=SKIPPED 统计治理 Skip | 业务 skip/xfail 会被误计，文本 reason 也可伪造 | 新增结构化 application shard，并与计划/P0 核对，不解析文本 |
| item 已有业务 marker 时继续追加治理 mark | 最终 Skip 来源和业务语义不可判定 | 保守不追加治理 mark，记录 NOT_APPLIED 并保留 pytest 原语义 |
| 先添加 mark、后写 application shard | 文件写失败会留下不可追溯的实际 Skip | 先原子写 ARMED shard，成功后才添加 mark；最终由 P0 证明是否生效 |
| 把 xdist 跨 worker 的同 decision 当作重复 | 每个 worker 都收集全量 item，会产生误报警或错误去重 | shard 以 worker id 隔离，跨 worker 重复是预期，按实际执行 worker 与 P0 关联 |
| 治理 SKIPPED 被折叠成稳定 PASS | 自动检测会因隔离行为虚假变稳 | NORMAL 准入继续排除所有 skip/xfail；决策事实单独存储 |
| OVERDUE 自动解除 Skip | 到期可能让已知波动用例突然回归 Smoke | OVERDUE 只告警，ACTIVE/RECOVERING 继续 Skip，必须人工处置 |
| close 后回写当前计划 | 不可变证据被篡改，worker 行为无法复现 | close 只影响下一轮 snapshot；当前 plan 保持不变 |
| fail-open 被实现为“空 READY 快照” | 报告无法区分无候选与治理数据故障 | 使用 UNAVAILABLE/FAIL_OPEN 和稳定错误码，缺数据不显示 0 |
| 质量告警直接设置 Jenkins UNSTABLE/FAILURE | 治理辅助异常覆盖真实 pytest 结论 | 只在摘要显示 WARN/DEGRADED，禁止修改原始 exit code/currentBuild.result |
| collect-only 的 SKIP 计划被计为实际 Skip | 未执行场景会虚增治理效果 | application 使用 NOT_EXECUTED，实际 Skip 固定为 0 |
| 灰度按用户/比例建设平台 | 超出单 Job、单机 SQLite MVP | 灰度只用固定 Job 配置逐轮开启，不增加流量平台或权限系统 |
| 阶段 4 顺手完善看板和备份 | 混入阶段 5，扩大当前交付面 | 本阶段只修正必要 Summary；完整展示和运维仍留给阶段 5 |

审计后的已接受限制是：带既有 skip/skipif/xfail marker 的治理候选在 MVP 中保守 fail-open，不尝试求值任意条件；它必须作为明确核对项展示，后续如需精确合并业务 marker，再单独升级 pytest 应用契约。
