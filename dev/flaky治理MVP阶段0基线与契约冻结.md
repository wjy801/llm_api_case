# Flaky 治理 MVP 阶段 0：基线验证与契约冻结

## 1. 阶段结论

阶段 0 只交付基线结论、冻结契约、可执行回放样本和运维草案，不启用新的生产执行行为。当前状态为：`CONTRACT_READY / PRODUCTION_AUDIT_COMPLETE`。

### 1.1 明确不做

- 不生成治理驱动的 pytest Skip。
- 不创建 v3 表，不迁移生产数据库。
- 不触发 Jenkins Probe。
- 不把无法证明类型的现有运行视为 NORMAL。

截至 2026-09-01，当前 `dev3` 基准提交 `efef315` 上受版本控制的 Quality 测试基线为 **356 passed**，其中原有 Flaky 测试为 **90 passed**；加入阶段 0 契约和只读审计测试后，受控 Quality 测试为 **379 passed**。当前 `.env` 配置的 v2 SQLite 已通过 backup API 生成临时一致性副本并完成只读脱敏审计，结果见 `dev/flaky治理MVP阶段0v2审计报告.md`。

当前受控 `tests/quality` 可完整收集并通过；先前记录的 `FlakyOutboxRecord` 在制链路阻塞在当前 `dev3` 工作区中不存在。仓库仍有与本阶段无关的未跟踪业务测试和文档，本次基线与提交均不纳入这些文件。

## 2. 基线盘点

### 2.1 已有能力

| 能力 | 当前落点 | 基线结论 |
| --- | --- | --- |
| P0 执行事实 | `quality/models.py`、聚合器与 manifest | 已有 `RunRecord`、`CaseResult`、`FailureRecord`、`IntegrityIssue`，并校验产物 hash |
| Flaky 身份 | `quality/flaky_identity.py` | 已按 `case_id + param_hash + environment + execution_profile + state_epoch` 生成稳定 key，并由 importer 复用 |
| NORMAL 历史雏形 | `flaky_import_run`、`case_observation` | 已支持幂等导入和完整事务回滚，但当前没有显式 `run_kind` |
| 自动检测 | `quality/flaky.py`、`flaky_state`、`flaky_transition` | 已支持 OBSERVING/STABLE/SUSPECTED/CONFIRMED 和确定性事件排序 |
| 人工治理 | `flaky_governance`、CLI | 已支持 quarantine、start recovery、cancel，包含 owner/reason/expires_at |
| 数据完整性 | migrations、quick check、foreign keys、WAL | 已有迁移 checksum、迁移前在线备份和 `BEGIN IMMEDIATE` |
| 可观测产物 | `flaky-import.json`、`flaky-evaluation.json`、Pipeline Summary | 已有结构化导入和检测摘要 |
| pytest 事实采集 | `quality/pytest_plugin_runtime.py` | 只采集身份和 phase 结果，没有治理 Skip 逻辑 |

### 2.2 与 MVP 契约的缺口

| 编号 | 缺口 | 风险 | 后续阶段 |
| --- | --- | --- | --- |
| G-01 | P0 `RunRecord`/manifest 只有来源信息，没有显式 `run_kind` | Probe、本地调试或旧数据可能被当作检测历史 | 阶段 1 |
| G-02 | 没有来源 Job allowlist 和统一 `flaky_input_status` | 非正式运行可能进入状态机 | 阶段 1 |
| G-03 | 没有 comparability fingerprint | 跨测试定义、SUT 或配置版本的 P/F 可能共同确认 Flaky | 阶段 1 |
| G-04 | 当前基础设施/环境类 Failure 在事实完整时仍可能折叠为 FAIL observation | 环境故障可能污染检测窗口 | 阶段 1 |
| G-05 | `FlakyState.current_state` 混合自动检测和人工治理语义 | 查询方容易把 CONFIRMED/QUARANTINED 混为执行决定 | 阶段 1 |
| G-06 | 当前 recovery 直接消费 NORMAL `case_observation`，达到阈值会自动关闭治理 | 违反 Probe 隔离和人工关闭边界 | 阶段 1、3 |
| G-07 | 没有 verification attempt、trigger ledger、Probe evidence | 无法形成固定 commit 的恢复证据链 | 阶段 1、3 |
| G-08 | 没有 Skip 快照、shadow/enforce、kill switch 和决策事实 | 尚不能安全接入 Runner | 阶段 2、4 |
| G-09 | 所有 Store 写操作仅靠 SQLite `BEGIN IMMEDIATE`，没有跨进程 OS 文件锁 | 多 CLI/服务进程并发时缺少单活动写者约束 | 阶段 1 |
| G-10 | 读路径也会自动执行待处理 migration | 生产访问者可能隐式改变 Schema | 阶段 1 |
| G-12 | 无用例级 rerun 事实模型 | 将来启用 rerun 后可能把“失败后通过”折叠成普通 PASS | 启用 rerun 前必须升级 Schema |

## 3. 冻结的领域边界

后续实现必须保持五类事实分离：

| 领域 | 唯一事实源 | 能否授权 Skip |
| --- | --- | --- |
| 执行事实 | P0 CaseResult / Failure / IntegrityIssue | 否 |
| 自动检测 | detected state + NORMAL evidence | 否 |
| 人工治理 | governance record | 是，但仍需满足完整 Skip 公式 |
| 恢复验证 | verification attempt + Probe evidence | 否，只产生人工关闭资格 |
| 执行决策 | 本轮不可变 Skip snapshot + decision fact | 是 |

冻结约束：

1. `CONFIRMED` 不是 quarantine，quarantine 不是 Skip，Probe 达标也不是自动关闭。
2. NORMAL 只推进自动检测；FLAKY_PROBE 只推进对应 verification attempt。
3. 报告文本、pytest skip reason 和 Jenkins Console 不是事实源。
4. 数据不可用时使用 `UNKNOWN`/`DEGRADED`，不得用 0 表示“没有问题”。
5. `state_epoch` 表示测试语义边界变化；detection generation 表示治理关闭后的重新观察，两者不得互相替代。

## 4. 冻结的数据契约

### 4.1 Run 类型

契约版本：`flaky-run-kind.v1`。

| `run_kind` | 含义 | 可进入 NORMAL 检测 | 可进入 Probe 计数 |
| --- | --- | --- | --- |
| `NORMAL` | 经批准的常规测试运行 | 满足准入条件时可以 | 否 |
| `FLAKY_PROBE` | 固定计划与 commit 的专用恢复运行 | 绝不可以 | 满足 Probe 准入条件时可以 |
| `LEGACY_UNKNOWN` | 无法证明来源语义的旧运行 | 绝不可以 | 绝不可以 |

禁止从 `source_kind=jenkins/local`、Job 名、分支名或产物目录推断 `run_kind`。旧 `quality.v1` reader 必须显式输出 `LEGACY_UNKNOWN`。

### 4.2 Flaky 身份

契约版本沿用 `flaky-identity.v1`，字段顺序和含义冻结为：

```text
case_id
+ param_hash
+ environment
+ execution_profile
+ state_epoch
= flaky_key
```

- `case_id` 是去除参数实例后的稳定 pytest nodeid。
- `param_hash` 来自规范化参数值；禁止从展示文本或模糊 nodeid 重建。
- `environment` 只接受规范值 `china`、`overseas`。
- `execution_profile` 至少区分 `serial`、`parallel`、`manual-serial`、`manual-parallel` 和受约束的 `custom:*`。
- `state_epoch >= 1`，仅在测试语义/身份边界改变时人工递增。
- epoch scope 固定为 `case_id + environment + execution_profile`，因此同一 scope 内所有 `param_hash` 共享 epoch reset；若只需处理单一参数实例，应使用治理动作而不是 reset epoch。
- 唯一实现入口为 `quality/flaky_identity.py`；Importer、Runner、快照和 Probe 只能调用该入口。
- 现有 key 算法和前缀 `flaky-v1-<sha256>` 保持不变，重构不得改写历史 key。

### 4.3 Comparability fingerprint

契约版本：`flaky-comparability.v1`。它不替代 Flaky 身份，而是在同一 Flaky 身份内划分可比较样本群组。

规范输入为：

```json
{
  "configuration_revision": "<non-empty immutable revision>",
  "environment": "china|overseas",
  "execution_profile": "<normalized profile>",
  "sut_revision": "<immutable service/model revision>",
  "test_definition_digest": "sha256:<64 lowercase hex>"
}
```

字段来源同时冻结：

- `test_definition_digest`：控制端对规范 manifest 求 SHA-256；manifest 包含稳定 case_id、测试模块、加载到该 Case 的 conftest/插件以及声明式测试数据文件，各路径为仓库相对 POSIX 路径并记录文件字节 SHA-256。任一已加载输入无法定位或越出仓库即不合格。
- `sut_revision`：由 Jenkins Controller/部署系统提供并校验的不可变部署 revision；不得信任目标工作区自由填写。
- `configuration_revision`：对版本化 allowlist 中所有“会影响测试结果”的有效配置做脱敏规范化后求 SHA-256；未知配置键导致不合格。
- `environment`、`execution_profile`：使用 `quality/flaky_identity.py` 的规范值。

计算规则：字段名排序、UTF-8、无多余空白、禁止 NaN，结果为 `flaky-comparability-v1-<sha256>`。任一字段缺失、空白、格式非法或无法证明，都使样本不合格。

检测投影的唯一作用域冻结为 `(flaky_key, detection_generation, comparability_fingerprint)`。每个 cohort 独立排序、计数和转换，不存在可变的“当前 cohort”，也不跨 cohort 合并 P/F。查询当前 Run 时只展示与该 Run fingerprint 匹配的投影；看板可并列展示历史 cohort，但不得合成 detected state。首次见到新 fingerprint 时创建该 cohort 的 OBSERVING 投影，并记录 `comparability_cohort_started`。

### 4.4 版本化策略

契约版本：`flaky-governance-policy.v1`。

```yaml
schema_version: flaky-governance-policy.v1
normal_admission_rule_version: flaky-normal-admission.v1
probe_evidence_rule_version: flaky-probe-evidence.v1
skip_decision_rule_version: flaky-skip-decision.v1
required_consecutive_passes: 5
min_interval_minutes: 30
max_attempt_age_hours: 72
max_non_counting_runs: 3
snapshot_max_age_minutes: 15
allowed_branches: [dev3]
include_path_prefixes: [module/smoke/]
exclude_path_prefixes: []
```

策略必须以规范 JSON 计算 revision，并把 revision 随 Run、attempt、快照、decision 和 evidence 持久化。任何默认值变化都必须产生新 revision，不能静默改变进行中的 attempt。

## 5. NORMAL 准入与 reason-code 矩阵

输出枚举冻结为 `ELIGIBLE`、`INELIGIBLE`。一次 Run 先做 Run 级准入，再对每个 invocation 做 Case 级准入；任一层不合格都不写 `case_observation`，但保留审计结果。

准入结果必须同时记录 `reason_codes` 和 `primary_reason_code`。`reason_codes` 是按下表优先级、再按 ASCII 排序的去重数组；`primary_reason_code` 是数组首项。这样同一事实即使同时违反多个条件，也得到相同审计结果。

### 5.1 Run 级

| 优先级 | 条件 | 不满足时 reason code | 结果 |
| --- | --- | --- | --- |
| 10 | `run_kind == NORMAL` | `normal_run_kind_mismatch` | INELIGIBLE |
| 20 | 来源 Job 在 allowlist | `normal_source_job_not_allowed` | INELIGIBLE |
| 30 | 分支符合策略 | `normal_branch_not_allowed` | INELIGIBLE |
| 40 | 环境符合策略 | `normal_environment_not_allowed` | INELIGIBLE |
| 50 | 执行画像符合策略 | `normal_execution_profile_not_allowed` | INELIGIBLE |
| 60 | Run 为 FINISHED | `normal_run_not_finished` | INELIGIBLE |
| 70 | P0 Schema/插件/规则版本兼容 | `normal_version_incompatible` | INELIGIBLE |
| 80 | 必要产物存在且 hash 复验通过 | `normal_artifact_untrusted` | INELIGIBLE |
| 90 | COMPLETE，或所有 DEGRADED issue 均在安全 allowlist | `normal_integrity_ineligible` | INELIGIBLE |
| 100 | comparability fingerprint 完整有效 | `normal_comparability_missing` | INELIGIBLE |
| 110 | 未使用无法展开的用例级 rerun | `normal_rerun_unsupported` | INELIGIBLE |
| 120 | 出现未知 Schema/integrity/枚举/规则值 | `normal_unknown_contract_value` | INELIGIBLE |
| 1000 | 全部满足 | `normal_eligible` | ELIGIBLE |

reason code 集合属于版本化契约。增加新代码必须升级 admission rule version；禁止运行时生成通配代码或使用“其他”。

### 5.2 Case 级

| 优先级 | 事实 | reason code | 是否形成 observation |
| --- | --- | --- | --- |
| 10 | phase 缺失/重复/身份冲突 | `case_lifecycle_invalid` | 否 |
| 20 | skip/xfail/xpass | `case_expected_outcome_excluded` | 否 |
| 30 | collection failure | `case_collection_failure` | 否 |
| 40 | Failure 缺失、重复或无法关联 | `case_failure_evidence_invalid` | 否 |
| 50 | FRAMEWORK_DEFECT、ENVIRONMENT、CONFIGURATION、TRANSIENT，或 owner 为 FRAMEWORK/ENVIRONMENT/CONFIGURATION | `case_infrastructure_failure` | 否 |
| 60 | UNKNOWN category/owner/confidence，或未知 status 组合 | `case_classification_unknown` | 否 |
| 1000 | 唯一、完整的 PASSED call 生命周期 | `case_pass_eligible` | PASS |
| 1000 | 唯一、完整、可验证的 PRODUCT_DEFECT/TEST_DEFECT FAILED/ERROR | `case_fail_eligible` | FAIL |

基础设施排除必须依据结构化 phase、status、category、confidence 和 integrity code，不解析错误消息，不调用 AI 决策。

## 6. Probe 准入、计数与失败语义

分类枚举冻结为 `COUNT_PASS`、`TRUSTED_FAIL`、`NON_COUNTING`。

Probe 只产生一个分类和一个 primary reason，按下表从上到下首次命中；额外问题写入排序后的 `diagnostic_codes`，不改变分类。

| 优先级 | 输入事实 | 分类 | reason code | 消耗非计数配额 | 对 attempt 的影响 |
| --- | --- | --- | --- | --- | --- |
| 10 | run_id 已存在 | NON_COUNTING | `probe_duplicate_run` | 否 | 幂等命中，不重复写 evidence |
| 20 | attempt 已结束或不是当前活动 attempt | NON_COUNTING | `probe_attempt_inactive` | 否 | 作为迟到证据审计 |
| 30 | run kind、commit、身份、环境、画像、计划或版本不匹配 | NON_COUNTING | `probe_plan_mismatch` | 否 | 作为越界证据审计 |
| 40 | P0 不完整、基础设施失败或未知分类 | NON_COUNTING | `probe_evidence_untrusted` | 是 | 写入当前 attempt 审计 |
| 50 | 使用无法展开的用例级 rerun | NON_COUNTING | `probe_rerun_unsupported` | 是 | 写入当前 attempt 审计 |
| 60 | skip/xfail/xpass/NO_DATA | NON_COUNTING | `probe_outcome_not_countable` | 是 | 写入当前 attempt 审计 |
| 70 | P0 可信且目标 Case 有唯一决定性 PRODUCT_DEFECT/TEST_DEFECT FAILED/ERROR | TRUSTED_FAIL | `probe_trusted_fail` | 否 | attempt -> FAILED；governance -> ACTIVE |
| 80 | PASS 但距上次计数不足 30 分钟 | NON_COUNTING | `probe_interval_too_short` | 否 | 只审计，允许按计划补跑 |
| 1000 | 当前活动 attempt、计划完全匹配、P0 可信、明确 PASS、间隔满足 | COUNT_PASS | `probe_count_pass` | 否 | 确定性重算连续 PASS |

计数顺序固定为 `round_no ASC, trusted_started_at ASC, run_id ASC`。每次导入后从全部 evidence 重算，禁止按到达顺序执行 `pass_count += 1`。

状态语义冻结如下：

- 达到 5 次计数 PASS：attempt -> READY_TO_CLOSE；governance 保持 RECOVERING。
- READY_TO_CLOSE 后到达 TRUSTED_FAIL：attempt -> FAILED；governance -> ACTIVE。
- ACTIVE attempt 超过 72 小时：attempt -> EXPIRED；governance -> ACTIVE。
- NON_COUNTING 达到 3 次且仍无法完成：attempt -> INCONCLUSIVE；governance -> ACTIVE。
- 人工关闭前必须再次校验 attempt、全部 trigger/build 终态、无在途 evidence、row_version 和目标分支 HEAD。
- 只有人工 close 才能令 attempt -> CLOSED、governance -> CLOSED，并从下一轮停止 Skip。
- Probe 证据永远不写 `case_observation`，不改变 detected state。

## 7. Skip 决策契约

决策枚举冻结为 `RUN`、`WOULD_SKIP`、`SKIP`。MVP 路径范围不是 glob，而是规范化仓库相对目录前缀；路径统一为 Unicode NFC 和 `/` 分隔符，保持 Git 大小写，拒绝绝对路径、空段、`.`、`..` 和越出仓库的符号链接。exclude 前缀优先于 include 前缀。

唯一允许产生 `SKIP` 的公式为：

```text
skip_mode == enforce
AND auto_skip_enabled == true
AND snapshot.run_id == current_run.run_id
AND snapshot.branch == controller_verified_branch == "dev3"
AND snapshot.generated_at <= collection_started_at <= snapshot.valid_until
AND snapshot.policy_revision == current_policy_revision
AND governance.status IN (ACTIVE, RECOVERING)
AND snapshot.entries[collected_case.flaky_key].identity == collected_case.identity
AND normalized_case_path HAS_PREFIX include_path_prefixes
AND normalized_case_path NOT_HAS_PREFIX exclude_path_prefixes
AND snapshot schema/database schema/content checksum 全部有效
```

规则优先级：

1. kill switch 关闭、`skip_mode=off`：`RUN`。
2. 快照 run/branch/policy 不匹配，或快照缺失、过期、损坏、版本不兼容、身份字段不全：fail-open 为 `RUN`，并写结构化告警。
3. 精确公式命中且 `skip_mode=shadow`：`WOULD_SKIP`，pytest 仍执行。
4. 精确公式命中且 `skip_mode=enforce`：`SKIP`。
5. 其余情况：`RUN`。

`CONFIRMED`、`SUSPECTED`、`OVERDUE`、只有 case_id 相同或模糊 nodeid 匹配都不是 Skip 条件。Runner 主进程只能在收集前读库一次并生成不可变快照；pytest worker 禁止访问数据库。

`snapshot.valid_until = generated_at + snapshot_max_age_minutes`。快照只能用于绑定的单一 run；超时只影响收集时判定，已开始执行的测试不会因快照随后到期而改变。

### 7.1 转换事件身份

- 自动 observation/reprojection 转换继续使用确定性的 `transition-v1`，同一规范事实重放必须得到同一 ID。
- 人工转换使用 `transition-v2`，哈希输入额外包含稳定 `causal_id`：隔离使用 governance_id，恢复使用 attempt_id，确认/纠正/取消使用命令幂等键或 override_id。
- 同一 `causal_id` 重放必须幂等；不同治理 occurrence 即使复用同一最新 observation，也必须得到不同 ID。

## 8. 代表性历史回放样本

时间按表格顺序严格递增，`fail:A` 表示同一失败指纹；除特别说明外，身份、generation 和 comparability fingerprint 相同。

| 样本 ID | 输入签名 | 预期最终 detected state | 预期关键转换 |
| --- | --- | --- | --- |
| R-01 | `fail:A` | OBSERVING | `first_observation` |
| R-02 | `fail:A, fail:A, fail:A` | STABLE(FAIL:A) | 第 3 条 `consistent_signature_threshold_met` |
| R-03 | `pass, fail:A, fail:A, pass` | CONFIRMED | 第 2 条 `outcome_changed`，第 4 条 `confirmation_threshold_met` |
| R-04 | `fail:A, fail:B, fail:A, fail:B` | SUSPECTED | 只有失败指纹变化，没有 PASS/FAIL 波动，不确认 Flaky |
| R-05 | `pass, fail:A, pass, pass, pass, pass, pass` | STABLE(PASS) | SUSPECTED 后连续 5 个同签名，`suspected_cleared_by_streak` |
| R-06 | `pass, fail:A, fail:A, pass, pass...` | CONFIRMED | 同一 generation 内 CONFIRMED 保持粘性 |
| R-07 | 可比组 A=`pass,pass`；可比组 B=`fail:A,fail:A` | 两组均 OBSERVING | 不得跨 fingerprint 合并为波动 |
| R-08 | 环境/Agent/凭据/收集故障 | 原状态不变 | 不形成 observation，reason=`case_infrastructure_failure` |
| R-09 | 与 R-03 相同事实、导入顺序打乱 | 与 R-03 完全一致 | 排序后 state、evidence refs、transition ID 相同 |
| R-10 | 重复导入同一 run_id/source digest | 原状态不变 | 幂等 NOOP，不新增 observation/transition |

上述样本已固化到 `tests/quality/fixtures/flaky_stage0_contract/replay_cases.json`，并由 `tests/quality/test_flaky_stage0_contract.py` 校验当前可执行的重放行为、转换 ID 和冻结枚举。阶段 1 继续复用同一 fixture 实现 comparability、Probe 与 Skip 行为，不得复制另一套期望值。

## 9. v2 历史审计与处置规则

### 9.1 审计清单

对数据库只读副本输出以下计数和明细，不直接修改原库：

- schema migration 版本、checksum、`quick_check`、`foreign_key_check`。
- `flaky_import_run` 总数以及 source_kind/job/branch/environment/profile 分布。
- 无法证明为 NORMAL 的所有 Run；v2 默认全部标为 `LEGACY_UNKNOWN`。
- OBSERVING、STABLE、SUSPECTED、CONFIRMED 状态及最近证据。
- ACTIVE、RECOVERING、CLOSED governance，包含 owner、expires_at、resolution 和锚点。
- 缺 projection、stale projection、孤儿 transition/governance、重复活动治理记录。
- 当前 RECOVERING 进度是否由 NORMAL observation 推动。
- CLOSED(RECOVERED/REGRESSED/CANCELLED) 对应证据链是否完整。

输出必须去除请求/响应正文、凭据和本地绝对路径，仅保留稳定 ID、状态、时间、版本和 reason code。

### 9.2 迁移处置

| v2 记录 | v3 处置 |
| --- | --- |
| 所有 v2 run/observation | 写入只读 legacy 分区，run_kind=`LEGACY_UNKNOWN`；不参与任何 v3 detection projection |
| OBSERVING/STABLE/SUSPECTED/CONFIRMED | 仅保留为 `legacy_detected_state` 审计字段；v3 活动投影为 UNOBSERVED，首条合格 NORMAL 按其 fingerprint 创建 cohort |
| governance ACTIVE / state QUARANTINED | 作为 `legacy_governance=true` 的活动治理保留，不自动解除隔离；它可继续参与精确 Skip，但不能证明 v3 detected state |
| governance RECOVERING | 安全回退为 ACTIVE，并记录 `legacy_recovery_requires_new_attempt`；不继承 v2 NORMAL 恢复计数 |
| CLOSED(RECOVERED) | 保留关闭事实，不伪造 Probe evidence；新 generation 从首条合格 NORMAL 开始 |
| CLOSED(REGRESSED) | 保留历史关闭事实，不自动新建治理；仍需隔离时由人工新建治理记录 |
| CLOSED(CANCELLED) | 保留历史，永不产生 Skip |
| 不一致或孤儿记录 | 隔离到审计清单，迁移 fail-closed，不猜测修复 |

真实数据库副本到位后，owner 必须逐条确认 RECOVERING 和不一致记录。审计报告经签字前，不得对生产库执行 v3 migration。

审计程序不得调用当前 `FlakyStore` 读方法，因为这些方法会执行 `initialize_store` 并可能自动迁移。审计只能打开复制文件的 SQLite read-only URI（`mode=ro`），在独立输出目录生成报告。

## 10. SQLite 单写者、迁移与恢复草案

### 10.1 单写者

- 数据库使用 workspace 外、宿主机本地绝对路径；拒绝 UNC/SMB/NFS。
- 所有 public write 通过同一 Store 协调入口；在打开写连接前获取数据库同目录 OS 文件锁。
- 获锁后开启连接并执行 `BEGIN IMMEDIATE`；锁超时使整项操作失败，不在锁外重试部分步骤。
- Jenkins HTTP 请求只能在数据库事务提交后发生。
- 读操作使用短事务；Runner 读取一次后生成不可变快照，worker 不直连数据库。

### 10.2 显式迁移

1. 关闭 Dashboard、dispatcher、reconciler、Importer 和治理 CLI 写入。
2. 关闭治理 Skip 与 Probe trigger 开关。
3. 执行 WAL checkpoint，确认没有活动访问者。
4. 使用 SQLite backup API 创建带时间戳备份，并记录 schema、migration checksum、文件 checksum。
5. 在同一写锁内运行 `quick_check`、`foreign_key_check` 和 v2 审计。
6. 仅通过显式 `flaky-db-migrate` 应用不可变 `0003`。
7. 再次执行数据库及领域一致性检查；失败则保持服务关闭并恢复备份。
8. 先恢复只读查询，再恢复写入；Skip 继续保持 off/shadow。

生产读写路径发现 pending migration 时必须返回 `schema_migration_required`，不得自动应用。

### 10.3 恢复

1. 停止全部访问者并验证目标数据库绝对路径。
2. 保存故障库、WAL 和 SHM 作为取证材料，不覆盖备份。
3. 把已校验备份恢复为新的数据库文件；不得把旧 WAL/SHM 与备份混用。
4. 运行 `quick_check`、`foreign_key_check`、migration checksum 和领域一致性检查。
5. 先以只读模式开放，核对审计计数后再开放单写者。

## 11. 阶段 0 工作包与退出门槛

| 工作包 | 产出 | 完成条件 |
| --- | --- | --- |
| S0-01 基线测试 | 受控测试记录 | Flaky 核心和受版本控制 Quality 测试通过 |
| S0-02 现状盘点 | 本文第 2 节 | 已有/缺失逐项映射到代码和后续阶段 |
| S0-03 领域与契约冻结 | `CONTEXT.md`、本文第 3～7 节 | 术语、枚举、reason code、公式和默认策略无歧义 |
| S0-04 回放样本 | JSON fixture + 契约测试 | 已固化当前回放、未来准入、Probe 和 Skip 期望；阶段 1 直接复用 |
| S0-05 v2 审计 | 脱敏审计报告 | 已基于当前配置数据库的一致性临时副本完成，只读审计器和报告均已交付 |
| S0-06 运维草案 | 本文第 10 节 | 迁移、失败回滚和恢复顺序经评审 |

阶段 0 的最终 Go/No-Go：

- **Go（本地契约开发）**：可开始阶段 1 的 Schema、纯状态机和 CLI 开发。
- **No-Go（生产迁移）**：真实 v2 数据库审计已完成，但跨进程锁、显式迁移、run_kind 和 comparability fingerprint 尚未实现。
- **No-Go（Skip/Probe）**：阶段 0 不授权任何 shadow/enforce Skip 或 Jenkins Probe。

S0-05 真实数据审计与 S0-06 运维草案评审均已完成，阶段 0 状态为 `CONTRACT_READY / PRODUCTION_AUDIT_COMPLETE`。该状态只授权阶段 1 本地开发，不授权生产迁移、Skip 或 Probe。

## 12. 验证记录

验证基准：Git `efef315`（分支 `dev3`）叠加本次阶段 0 改动；Python 3.14.6、pytest 9.1.1、SQLite 3.49.1。测试明确枚举受版本控制的 `tests/quality/*.py` 并追加阶段 0 契约与审计测试，避免无关未跟踪文件混入基线。

| 检查 | 结果 |
| --- | --- |
| 原有 Flaky 测试 | 90 passed |
| 受版本控制的 `tests/quality` | 356 passed |
| 阶段 0 新增契约/审计测试 | 23 passed |
| 受版本控制测试 + 阶段 0 新增测试 | 379 passed |
| R-03 正序/逆序重复回放 | 均为 CONFIRMED，转换 reason 序列及 3 个 transition ID 完全一致 |
| 阶段 0 机器契约 fixture | 已建立；当前可执行回放、固定 transition ID 与枚举校验均通过 |
| pytest 用例级 rerun 配置/依赖/标记静态检索 | 未发现 |
| Quality pytest 插件治理 Skip 逻辑 | 未发现 |
| `.env` 配置的 Flaky SQLite | 已通过临时一致性副本完成只读脱敏审计；44 Run、1717 observation、132 state、281 transition、0 governance |
| 受控 `tests/quality` | 完整收集并通过；未复现旧记录中的 `FlakyOutboxRecord` 缺失 |

测试使用仓库外临时目录，避免系统临时目录权限问题；测试生成的临时数据库和产物已清理。

## 13. 对抗式审计记录

| 发现 | 风险 | 修正或处置 |
| --- | --- | --- |
| 把无数据库路径理解为“生产数据健康” | 未检查的数据会被错误当作迁移依据 | 已对当前配置数据库的一致性副本完成只读审计；生产 migration 仍由阶段 1 门禁控制 |
| 从 `source_kind=jenkins/local` 推断 run kind | Probe、本地或旧 Run 可能污染 NORMAL 检测 | 冻结显式 NORMAL/FLAKY_PROBE/LEGACY_UNKNOWN，旧 v1 只能映射为 LEGACY_UNKNOWN |
| 将 CONFIRMED 直接等同 quarantine/Skip | 自动规则可隐藏真实失败 | 拆分执行事实、检测、人工治理、恢复验证和执行决策五层语义 |
| NORMAL PASS 推进恢复并自动关闭 | 常规样本可绕过固定 Commit 的 Probe 与人工门禁 | 冻结 NORMAL/Probe 隔离，READY_TO_CLOSE 后仍必须人工 close |
| 仅用 nodeid 或 case 名匹配 | 参数、环境、画像和 epoch 可能被扩大治理 | 冻结完整 Flaky identity，并指定唯一公共构造入口 |
| 不划分 comparability cohort | 跨版本、配置或测试定义样本会共同确认波动 | 新增不可变 comparability fingerprint，投影按 generation/fingerprint 隔离 |
| 用例级 rerun 被折叠成最终 PASS | 首次失败会被隐藏，检测和恢复计数失真 | 当前未发现 rerun；未来启用前必须升级事实 Schema，否则不准入 |
| 直接调用现有 Store 审计 v2 | 读路径可能隐式初始化或迁移源库 | 真实审计只对副本使用 SQLite read-only URI，不调用自动初始化入口 |
| 把工作区无关未跟踪文件纳入阶段基线 | 无关业务文件会污染可重复结论 | 基线明确枚举受版本控制测试，并显式追加本阶段测试文件 |
| 迁移或恢复时复制活动 `.db` | WAL 中事务可能丢失或与主库错配 | 使用 SQLite backup API；离线恢复隔离旧 WAL/SHM 并执行完整检查 |
| 阶段 0 提前实现 Web、Jenkins 或 Enforce | 无法先验证核心契约且扩大 MVP | 本阶段只交付契约、fixture、审计和运维草案，外部行为留给后续阶段 |

阶段 0 的真实 v2 数据审计和本地契约验证均已完成。生产迁移、v3 准入、单写者、Probe 和 Skip 仍由后续阶段门禁控制，不因阶段 0 完成而自动授权。
