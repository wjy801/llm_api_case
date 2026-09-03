# 阶段 3 Jenkins Probe Job 固定配置清单

本清单是阶段 3 非生产真实演练的前置门禁。任一项未满足时，`QUALITY_FLAKY_TRIGGER_ENABLE` 必须保持 `0`。

## Job 与 Pipeline

- 使用专用且固定全名的 Probe Job；名称必须与 `QUALITY_FLAKY_JENKINS_JOB` 完全一致。
- Pipeline 只能来自受保护的 controller 仓库与批准的固定 revision，不得从待验证 target 分支加载。
- 登记并复核 `QUALITY_FLAKY_CONTROLLER_COMMIT` 与 `QUALITY_FLAKY_CONTROLLER_JENKINSFILE_SHA256`。
- 仅声明 `TRIGGER_ID`、masked `DISPATCH_TOKEN`、`PLAN_DIGEST` 三个参数。
- 仅 dispatcher service account 拥有该 Job 的 Read、Build、Cancel 权限；普通用户不得直接 Build with Parameters。

## Controller 节点

- `probe-controller` label 只能分配到专用受信节点，且不得同时配置 `probe-target-restricted` label。
- controller checkout 固定为 `QUALITY_FLAKY_CONTROLLER_ROOT`，由专用 OS 身份拥有并限制写权限。
- 运行前必须通过 HEAD、干净工作树与 `Jenkinsfile.probe` digest 校验。
- `QUALITY_FLAKY_DB_PATH` 必须是 controller 可访问、仓库外的宿主机绝对路径，不得作为 Jenkins file credential 临时复制。
- Jenkins service account、数据库和 evidence HMAC key 只能绑定到 controller 节点/步骤。

## Target 节点

- `probe-target-restricted` 必须是与 controller 不同的 Jenkins node，并使用不同的受限 OS 身份；Pipeline 会在运行时再次拒绝同节点或同身份。
- target OS 身份必须被 ACL 拒绝读取 controller checkout、共享治理数据库、Jenkins credential 与 evidence HMAC key。
- `QUALITY_FLAKY_TARGET_PYTHON` 必须位于仓库外，并只提供运行目标测试所需依赖。
- 只向 target 注入非生产、最小权限且受资源/时长限制的 API 凭据。

## 演练前核对

- 在两个节点分别记录 `NODE_NAME` 与 `whoami`，确认节点和 OS 身份均不同。
- 用 target 身份验证 controller root、数据库和密钥文件均不可读。
- 验证重复 queue item 只有一个 build 能 claim，未 claim build 在 checkout 前退出。
- 验证构建日志、数据库、归档产物和 target workspace 均不包含 raw dispatch token、HMAC key 或 Jenkins 凭据。
- 完成 PASS、可信 FAIL、DISPATCH_UNKNOWN、运行中取消四类演练并保存脱敏证据后，才可将阶段状态提升为 `PROBE_VALIDATED`。
