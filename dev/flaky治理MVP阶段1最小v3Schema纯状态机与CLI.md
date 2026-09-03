# Flaky 治理 MVP 阶段 1：最小 v3 Schema、纯状态机与 CLI

## 1. 阶段结论

阶段 1 只建立本地可验证的数据层、纯状态机与 CLI 控制面：显式迁移、NORMAL 准入、按可比性群组隔离的检测投影、独立 Probe 证据、人工恢复门禁和单写者约束。本阶段不接入 Web、Jenkins、pytest Skip 或生产数据库，因此不宣称具备真实恢复闭环。

当前状态：`LOCAL_V3_READY / PRODUCTION_MIGRATION_BLOCKED`。

进入条件：

- 阶段 0 状态保持 `CONTRACT_READY / PRODUCTION_AUDIT_PENDING`。
- 生产 v2 数据库副本未提供，因此只允许使用空库和合成 v2 fixture 验证迁移。
- 工作区中的在制证据发布链路必须由其 owner 完成或隔离，不能把缺失的 `FlakyOutboxRecord` 顺手并入本阶段。

## 2. 目标、范围与非目标

### 2.1 目标

- 发布显式的 `quality.v2` Run/manifest 契约和 v1 只读兼容 reader。
- 新增不可变 `0003` 迁移，保留 v1/v2 数据作为 legacy 审计事实。
- NORMAL 样本只有通过版本化准入后，才能进入 `(flaky_key, detection_generation, comparability_fingerprint)` 投影。
- FLAKY_PROBE 证据只推进 verification attempt，绝不写入 NORMAL observation。
- NORMAL 运行不再推进恢复、关闭治理或解除隔离。
- 提供开始、查询、关闭和取消恢复验证的 CLI。
- 所有写入口共用一把数据库级跨进程锁，并以单事务完成状态变化。
- 运行期发现待迁移或过新 Schema 时明确拒绝，不再隐式迁移。

### 2.2 明确不做

- 不实现 Dashboard、FastAPI 路由或用户权限。
- 不调用 Jenkins，不创建真实 queue/build，不实现 dispatcher/reconciler。
- 不提供绕过 trigger/evidence 门禁的调试命令；CLI close 只能消费通过正常导入路径形成的 READY_TO_CLOSE attempt。
- 不生成 Skip 快照，不接入 pytest collection，不开启 shadow/enforce。
- 不迁移生产数据库，不承诺生产高可用、跨主机协调或在线无停机迁移。
- 不建设通用工作流、消息队列、插件系统或多租户策略中心。

## 3. 现状与改造边界

| 当前实现 | 阶段 1 处理 |
| --- | --- |
| `quality.v1` 的 `RunRecord` 没有 `run_kind` | 发布 `quality.v2`，v1 reader 固定映射为 `LEGACY_UNKNOWN` |
| `case_observation` 没有准入结果、generation 和 fingerprint | v2 表只读保留；合格 v3 NORMAL 写入新表 |
| `flaky_state` 以 `flaky_key` 为唯一行且混合治理状态 | v2 表只读保留；新检测投影按三元组独立存储 |
| `flaky_transition` 同时记录自动与人工转换 | v2 表只读保留；v3 检测转换与治理事件分表 |
| NORMAL evaluation 会执行 recovery 并自动关闭 governance | 删除该路径，恢复只由 Probe attempt 推进 |
| `initialize_store()` 在读写路径自动迁移 | 拆成只校验 Schema 与显式迁移两个入口 |
| SQLite 仅有 `BEGIN IMMEDIATE` | 在所有 public write 外层增加同库路径的 OS 文件锁 |
| `flaky-start-recovery` 只改变状态 | 替换为创建 attempt 的 `flaky-recovery-start` |

## 4. 冻结的领域不变量

### 4.1 四条独立状态轴

| 状态轴 | 状态 | 唯一写入来源 |
| --- | --- | --- |
| 检测投影 | UNOBSERVED、OBSERVING、STABLE、SUSPECTED、CONFIRMED | 合格 NORMAL observation 的确定性重放 |
| 治理记录 | ACTIVE、RECOVERING、CLOSED | 人工治理命令和 Probe attempt 终态事务 |
| 验证尝试 | ACTIVE、READY_TO_CLOSE、FAILED、INCONCLUSIVE、EXPIRED、CANCELLED、CLOSED | Probe evidence 与人工命令 |
| 执行决策 | RUN、WOULD_SKIP、SKIP | 阶段 2/4；阶段 1 不产生 |

禁止重新引入可混合上述语义的 `current_state`。查询层可以组合展示，但必须保留各状态轴的字段名。

### 4.2 检测周期与空投影

- `flaky_identity.current_detection_generation` 表示当前检测周期。
- 新身份和成功关闭恢复后的新 generation 初始为 UNOBSERVED。
- UNOBSERVED 是“当前 generation 尚无合格 NORMAL 样本”的派生展示，不创建伪 observation 或伪 fingerprint。
- 首条合格 NORMAL 根据自己的 fingerprint 创建 OBSERVING 投影。
- 同一 identity/generation 可以同时存在多个 fingerprint 投影，彼此不合并计数。
- 关闭只递增当前 `flaky_key` 的 detection generation，不改变同 epoch 下其他参数实例。

### 4.3 治理与验证

- 同一 `flaky_key` 最多一条未关闭 governance。
- 同一 governance 最多一个 ACTIVE 或 READY_TO_CLOSE attempt。
- attempt 达标仅产生关闭资格；只有人工 close 才关闭 governance。
- 可信 Probe FAIL、超时或证据不足结束当前 attempt，并把 governance 恢复为 ACTIVE。
- 迟到证据只审计，不重新打开、关闭或推进任何记录。
- 所有治理变化追加 governance event；自动检测变化只写 detection transition。

## 5. P0 `quality.v2` 契约

### 5.1 Run 字段

`RunRecord` 与 manifest 共同增加：

```text
run_kind
policy_revision
controller_commit_sha
attempt_id?
trigger_id?
plan_digest?
round_no?
target_commit_sha?
jenkins_job_name?
jenkins_build_number?
fact_schema_version
plugin_version
```

条件约束：

- NORMAL：`run_kind=NORMAL`；Probe 专属字段必须为空；来源 Job、branch、commit 和版本字段必须可验证。
- FLAKY_PROBE：attempt、trigger、plan、round、target/controller SHA 和 Jenkins 身份必须全部存在。
- LEGACY_UNKNOWN：仅由 v1 reader 或迁移产生，新 v2 producer 禁止主动写入。
- 未知 `run_kind`、缺失条件字段、SHA 非 40 位小写十六进制或 round 小于 1，均拒绝准入。
- v1 reader 不猜测类型，不补造 Probe 字段，只输出 `LEGACY_UNKNOWN`。

### 5.2 NORMAL 准入输出

Run 和 Case 两级准入复用阶段 0 的优先级表，输出：

```text
status: ELIGIBLE | INELIGIBLE
reason_codes: 按优先级及 ASCII 稳定排序的去重数组
primary_reason_code: reason_codes[0]
policy_revision
rule_version
```

只有 Run 与 Case 都为 ELIGIBLE 时才写 v3 NORMAL observation。所有拒绝结果仍写准入审计记录。

## 6. `0003` 最小 Schema

### 6.1 Legacy 保留策略

- 保留现有 `case_observation`、`flaky_state`、`flaky_transition` 和 `flaky_override`，迁移后仅供 legacy 审计查询。
- `flaky_import_run` 增加 v3 Run 元数据；所有既有行回填 `run_kind=LEGACY_UNKNOWN` 和 `legacy_record=1`。
- 新服务不得继续向 legacy 检测表写入。
- 不把 v2 detected state 复制为 v3 活动投影；它只写入 `flaky_identity.legacy_detected_state`。

选择并行 v3 表而不是原地扩展 `flaky_state`，原因是原表以 `flaky_key` 为唯一行，无法无歧义地表达多个 generation/fingerprint 投影；同时它的 `current_state` 已混入治理语义。

### 6.2 新增或重建的数据结构

| 表 | 最小职责与关键约束 |
| --- | --- |
| `flaky_identity` | `flaky_key` 主键；保存完整身份、`current_detection_generation >= 1`、legacy detected state；身份五元组唯一 |
| `flaky_normal_observation` | 只保存合格 NORMAL；包含 generation、fingerprint、规则/策略版本；`(run_id, flaky_key)` 唯一 |
| `flaky_evidence_admission` | 保存 Run/Case 准入状态、primary reason 和完整 reason 数组；拒绝项也必须存在 |
| `flaky_detection_projection` | 主键为 `(flaky_key, detection_generation, comparability_fingerprint)`；状态只能是自动检测状态 |
| `flaky_detection_transition` | 只追加检测投影转换；自动重放使用 `transition-v1`，人工纠正使用包含 override ID 的 `transition-v2` |
| `flaky_detection_override` | 保存人工 confirm/mark-not-flaky；必须指定完整 projection 三元组、actor、reason 和幂等键 |
| `flaky_governance` | 从 v2 数据重建；增加 `row_version`、`closed_by`、`close_reason`、`close_attempt_id`，外键改指向 identity |
| `flaky_verification_attempt` | 保存 attempt_no、状态、目标 SHA、策略版本、计数进度、时间边界和结束原因 |
| `flaky_probe_evidence` | 保存所有 Probe 结果和准入原因；全局 `run_id` 唯一 |
| `flaky_probe_trigger` | 保存 request/plan/dispatch 身份和本地状态；阶段 1 不执行外部投递 |
| `flaky_governance_event` | 只追加治理生命周期事件，事件 ID 包含稳定 causal ID |

必须落实的数据库约束：

- `flaky_governance(flaky_key)` 对 ACTIVE/RECOVERING 建部分唯一索引。
- `flaky_verification_attempt(governance_id)` 对 ACTIVE/READY_TO_CLOSE 建部分唯一索引。
- `(governance_id, attempt_no)`、`request_id` 和 Probe `run_id` 唯一；阶段 1 不生成或保存 dispatch token，阶段 3 的 `0004` 只增加可空且非空时唯一的 token hash。
- `(attempt_id, round_no)` 只对 `effect_status=APPLIED` 的 evidence 建部分唯一索引；其他 run 的重复 round、迟到或越界 evidence 标为 AUDIT_ONLY 后仍可保留。重复 `run_id` 只返回已有结果，不重复写 evidence。
- CLOSED governance 必须同时具有 `closed_at`、`closed_by`、`close_reason` 和 resolution；未关闭记录这些字段必须为空。
- 非活动 attempt 必须具有 `ended_at` 和 `end_reason`；活动状态二者必须为空。
- 所有状态、枚举、非负计数和时间顺序使用 CHECK；跨表不变量由同一事务及 `flaky-db-check` 双重验证。

### 6.3 v2 数据处置

- ACTIVE governance 原样进入 v3，标记 `legacy_governance=1`。
- RECOVERING governance 回退为 ACTIVE，并追加 `legacy_recovery_requires_new_attempt` 事件。
- CLOSED(CANCELLED/RECOVERED/REGRESSED) 保留原始处置和 legacy 标记，不创建 attempt、Probe evidence 或 v3 projection。
- identity 回填取 `flaky_state`、`case_observation` 和 `flaky_governance` 可关联身份的并集；同一 `flaky_key` 出现身份冲突或治理记录无法解析身份时迁移失败。
- v2 `flaky_state` 仅提供 legacy detected state；当前 generation 为 1，且没有 projection，因此展示 UNOBSERVED。只有 observation、没有 state 的合法身份也保留，legacy detected state 为空。
- 孤儿或互相矛盾的数据写入迁移审计结果，并使迁移失败；不猜测补全。
- 迁移脚本不访问生产库；真实 v2 数据仍需阶段 0 的只读审计和签字。

## 7. 显式迁移与单写者

### 7.1 入口拆分

- `flaky-db-migrate --db <absolute-local-path>` 是唯一可应用 migration 的入口。
- 空库也必须先运行显式迁移，再允许 import 或治理写入。
- Store 的普通读写入口只校验 migration checksum 和当前版本。
- 存在 pending migration 时返回 `schema_migration_required`；Schema 过新返回 `schema_too_new`。
- `flaky-db-check` 以只读方式运行，不创建库、不备份、不迁移。

### 7.2 写锁

- 每个数据库使用同目录固定锁文件，锁的身份由规范化绝对数据库路径确定。
- public write 在打开 SQLite 写连接前获取 OS 锁，并持有到事务提交或回滚完成。
- 获锁后执行 `BEGIN IMMEDIATE`；超时返回 `db_writer_lock_timeout`，不留下部分数据。
- 同一进程内不得通过不同 Store 实例绕过锁；测试使用两个进程验证互斥。
- 本阶段只支持单宿主机本地文件，不引入网络锁或高可用方案。

## 8. 纯状态机与服务边界

### 8.1 纯函数

新增无数据库、无环境变量、无 wall-clock 读取的函数：

- NORMAL Run/Case 准入与 reason-code 排序。
- comparability fingerprint 规范化和计算。
- 单 cohort 检测重放。
- Probe evidence 分类及 non-counting 配额判断。
- attempt 全量证据重算。
- governance/attempt 命令的状态转换判定。

时间、策略、当前分支和当前 SHA 均由调用方显式传入。

### 8.2 事务服务

- NORMAL import：校验 P0 → 写准入审计 → 仅为合格 Case 写 observation → 重算对应 projection。
- Probe import：校验计划和 P0 → 写 evidence → 重算 attempt；禁止调用 NORMAL observation/reprojection。
- start：条件更新 governance row_version → 创建 attempt 与本地 PENDING trigger → 追加事件。
- cancel：结束 attempt → governance 回到 ACTIVE → 追加事件。
- close：在同一事务中重新读取 attempt、trigger/evidence、row_version 和目标 SHA 校验结果 → 关闭 attempt/governance → generation 加一 → 追加事件。
- 任何冲突返回稳定错误码，不做部分提交。

NORMAL evaluator 中的 `evaluate_recovery` 和自动 `close_governance` 调用必须移除。旧纯函数可暂时保留用于 legacy 回放测试，但不得被 v3 NORMAL 路径调用。

## 9. CLI 契约

### 9.1 数据库命令

```text
flaky-db-migrate --db PATH
flaky-db-check --db PATH
```

迁移命令输出迁移前后版本、备份路径、checksum 和检查结果；不打印数据库内的敏感正文。

### 9.2 恢复命令

```text
flaky-recovery-start
  --db PATH --flaky-key KEY --target-commit-sha SHA
  --actor ACTOR --reason TEXT --request-id UUID
  --expected-row-version N

flaky-recovery-status --db PATH --flaky-key KEY

flaky-recovery-close
  --db PATH --attempt-id ID --actor ACTOR --reason TEXT
  --expected-row-version N --verified-branch-head SHA

flaky-recovery-cancel
  --db PATH --attempt-id ID --actor ACTOR --reason TEXT
  --expected-row-version N
```

- 输出统一为版本化 JSON，成功为 0，业务冲突为非 0，并包含稳定错误码。
- `flaky-start-recovery` 删除；帮助文本明确使用新命令，避免保留“不创建 attempt”的旧入口。
- `flaky-confirm` 和 `flaky-mark-not-flaky` 增加必填的 `--detection-generation` 与 `--comparability-fingerprint`；旧式仅传 `flaky_key` 的调用返回 `projection_identity_required`。
- 阶段 1 的 start 只创建本地 trigger 记录，不调用 Jenkins。
- close 必须校验 READY_TO_CLOSE、无活动/未知 trigger、证据无缺口、row_version 和已验证分支 HEAD。
- 阶段 1 中 READY_TO_CLOSE 只通过合成 P0 fixture 和正常 Probe import 服务验证；真实触发、构建认领和外部证据闭环属于阶段 3。

## 10. 实施工作包

| 顺序 | 工作包 | 交付物 | 完成条件 |
| --- | --- | --- | --- |
| S1-01 | 契约与纯函数 | v2 P0 模型、策略模型、准入/指纹/Probe 分类函数 | 阶段 0 fixture 全部成为可执行测试 |
| S1-02 | 显式迁移 | `0003`、迁移命令、只校验 Schema 的运行时入口 | 空库、合成 v2、重复迁移、过新/待迁移测试通过 |
| S1-03 | 单写者 | OS 锁和统一 write coordinator | 双进程争锁与超时回滚测试通过 |
| S1-04 | NORMAL 检测 | 准入审计、v3 observation、cohort 投影 | 不合格样本不写 observation，不跨 cohort 合并 |
| S1-05 | Probe/attempt | evidence 分类、attempt 重算和治理事件 | PASS/FAIL/NON_COUNTING、乱序、重复、迟到均确定性 |
| S1-06 | CLI | migrate、start/status/close/cancel | CLI 集成测试覆盖成功、冲突与错误码 |
| S1-07 | 边界清理 | 移除 NORMAL recovery，更新 db-check 与查询 | NORMAL 无法关闭治理，legacy 只读 |

每个工作包单独完成代码、测试和自审后再进入下一个；不得同时接入阶段 2/3/4 的外部行为。

## 11. 测试与验收

### 11.1 必测场景

- 空库显式初始化、合成 v2→v3、重复迁移、checksum 篡改、too-new 和备份恢复。
- v1 Run 读取为 LEGACY_UNKNOWN；v2 NORMAL/PROBE 条件字段校验。
- 同一 identity/generation 的两个 fingerprint 独立投影；乱序重放结果和 transition ID 一致。
- Run/Case 同时触发多个拒绝原因时，primary reason 稳定。
- NORMAL 任意 PASS/FAIL 都不改变 attempt 或 governance。
- Probe 重复 run、重复 round、乱序、迟到、间隔边界、可信 FAIL 和三种消耗配额的 NON_COUNTING。
- 同时 start、cancel、close 时只有符合 row_version 的一个事务成功。
- 无新 observation 的隔离→取消→再次隔离保持不同 transition/event ID。
- 两个进程同时写入时只有一个持锁；超时方没有数据库副作用。
- 所有状态机测试使用注入时间，不连接网络、不 sleep。

### 11.2 退出门槛

- 现有受版本控制 Quality 基线与阶段 0 契约测试全部通过。
- 阶段 1 新增测试全部通过，且没有依赖真实 Jenkins、网络或生产数据库。
- 运行期任何入口都不能自动应用 migration。
- v3 查询不把 legacy detected state 当作当前投影。
- NORMAL 和 Probe 的存储及状态推进路径完全隔离。
- 数据库检查能识别跨表状态不一致、重复活动记录和非法 evidence 资格。
- Skip、Dashboard 和外部 Probe 触发仍不存在或保持关闭。

阶段 1 完成后的状态应为 `LOCAL_V3_READY / PRODUCTION_MIGRATION_BLOCKED`；在真实 v2 审计完成前不得标记为可生产迁移。

## 12. 对抗式审计记录

本方案已按“假设错误、边界冲突、遗漏场景、不可验证条件、数据一致性、范围膨胀”六个方向审计，并完成以下修正：

| 发现 | 风险 | 处置 |
| --- | --- | --- |
| 原计划只给 `flaky_state` 增加字段 | 单行主键无法表达多个 fingerprint，且继续混合治理语义 | 保留 v2 表只读，新建三元组键的 v3 detection projection |
| UNOBSERVED 若落成伪 projection | 没有 fingerprint 和 observation 时会产生虚假事实 | 由 identity 当前 generation 无 projection 派生展示 |
| `(attempt_id, round_no)` 全表唯一 | 重复/迟到 evidence 无法留审计 | 唯一约束仅作用于取得计数资格的 evidence |
| runtime 与空库初始化共用自动迁移 | 普通读取也可能修改 Schema | 空库也要求先执行 `flaky-db-migrate`，运行时仅校验 |
| close 先查后写 | 迟到 FAIL、在途 trigger 或并发命令可能穿透门禁 | 同一写锁和事务内重读全部条件并执行 CAS |
| v2 detected state 直接回填 v3 | 未知 run_kind 和 fingerprint 会污染新投影 | 仅保存 legacy 审计值，v3 从 UNOBSERVED 开始 |
| identity 只从 v2 state 回填 | 缺 projection 的 observation 会丢失身份，孤儿治理也可能被掩盖 | 从三类历史事实求并集，冲突和不可解析记录使迁移失败 |
| 人工检测纠正只指定 `flaky_key` | 多 fingerprint 时无法判断要修改哪个投影 | 命令和 override 强制携带完整 projection 三元组 |
| 阶段 1 start 创建 PENDING trigger 却没有 Jenkins | 方案可能误称真实恢复闭环，或引入绕过门禁的调试接口 | 仅验证控制面与导入服务；真实 trigger 流转延后到阶段 3 |
| 阶段 1 同时实现 Web/Jenkins/Skip | 扩大 MVP 范围且无法隔离验证 | 明确延后至阶段 2～4，本阶段 trigger 仅落本地账本 |
| 缺少真实 v2 数据副本 | 合成迁移测试无法证明真实数据可迁移 | 保持生产迁移 No-Go，不伪造审计完成状态 |

审计后未发现必须在阶段 1 引入高可用、分布式锁、多租户、权限系统或通用消息队列的理由。
