# Flaky 治理 MVP 上下文

## 当前阶段

- 当前执行阶段：阶段 0（基线验证与契约冻结）。
- 当前阶段状态：`CONTRACT_READY / PRODUCTION_AUDIT_COMPLETE`。
- 阶段 0 不启用治理 Skip、不触发 Jenkins Probe、不创建 v3 表，也不迁移生产数据库。
- 阶段 0 验收完成后，只授权开始阶段 1 的本地实现；生产迁移仍需通过阶段 1 的 Schema、单写者和迁移验收。

## 冻结边界

以下五类事实必须保持分离：

1. P0 CaseResult、Failure 和 IntegrityIssue 是执行事实。
2. detected state 只表示 NORMAL 历史的自动检测结论，不能授权 Skip。
3. governance record 表示人工隔离决定，只有 ACTIVE/RECOVERING 记录可能授权 Skip。
4. verification attempt 和 Probe evidence 只提供人工关闭资格，不进入 NORMAL 历史。
5. 每轮不可变 snapshot 和 decision fact 决定最终 RUN/WOULD_SKIP/SKIP。

NORMAL 运行不能推进 recovery、关闭 governance 或解除隔离。Probe 达到 5 次合格 PASS 后只进入 `READY_TO_CLOSE`，最终关闭仍需人工操作。

## 冻结契约

- Run kind：`NORMAL`、`FLAKY_PROBE`、`LEGACY_UNKNOWN`。
- Flaky identity：`case_id + param_hash + environment + execution_profile + state_epoch`。
- 检测投影作用域：`flaky_key + detection_generation + comparability_fingerprint`。
- Probe 分类：`COUNT_PASS`、`TRUSTED_FAIL`、`NON_COUNTING`。
- Skip 决策：`RUN`、`WOULD_SKIP`、`SKIP`。
- 默认恢复门槛：5 次合格 PASS、最小间隔 30 分钟、attempt 最长 72 小时、最多 3 次消耗配额的 NON_COUNTING。
- 首批范围：受验证的 `dev3` 分支和 `module/smoke/` 路径。

机器可读契约位于 `tests/quality/fixtures/flaky_stage0_contract/replay_cases.json`。所有 Flaky 身份构造必须复用 `quality/flaky_identity.py`。

## 阶段 0 交付物

- 开发计划：`dev/flaky治理MVP阶段0基线与契约冻结.md`。
- 机器契约与回放 fixture：`tests/quality/fixtures/flaky_stage0_contract/replay_cases.json`。
- 契约测试：`tests/quality/test_flaky_stage0_contract.py`。
- 公共身份入口：`quality/flaky_identity.py`。
- 只读 v2 审计器：`quality/flaky_v2_audit.py`。
- 脱敏审计报告：`dev/flaky治理MVP阶段0v2审计报告.md`。

## 后续阶段门禁

- 阶段 1 可以实现本地 v3 Schema、纯状态机和 CLI，但不得启用 Web、Jenkins Probe 或 pytest Skip。
- 生产读写路径不得继续隐式迁移；阶段 1 必须提供显式迁移命令和跨进程单写者锁。
- 阶段 2 必须先 Shadow 并完成至少 10 个正式 Smoke Run 的人工核对。
- 阶段 3 的 Probe 只能使用独立固定 Jenkins Job；看板和 CLI 复用同一 application service。
- 阶段 4 只有在 Probe 非生产演练通过后才能对限定范围启用 Enforce。

## 安全约束

- 数据不可用时显示 `UNKNOWN/DEGRADED`，不得用 0 冒充无问题。
- v2 审计只允许针对 SQLite 一致性副本使用 `mode=ro`，不得调用会初始化或迁移数据库的 `FlakyStore`。
- 数据库文件、备份、凭据、原始请求/响应正文和绝对数据库路径不得进入 Git、日志或 Jenkins artifact。
- Runner 决策异常必须 fail-open；治理写入异常必须 fail-closed 并完整回滚。
