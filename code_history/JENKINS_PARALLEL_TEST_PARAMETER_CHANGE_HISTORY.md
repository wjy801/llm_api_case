# Jenkins Parallel Test Parameter Change History

## 变更目标

- 在 Jenkins 流水线中加入用例并发执行控制参数。
- 允许每次构建按需选择串行执行或 pytest-xdist 并发执行。

## 代码变更

- 修改 `Jenkinsfile`
  - 新增参数 `TEST_PARALLEL_WORKERS`：
    - `off`：串行执行，默认值。
    - `auto`：由 pytest-xdist 自动决定 worker 数。
    - `2`、`4`、`8`：指定固定 worker 数。
  - `Framework Unit Tests` 阶段根据参数决定是否追加 `-n`。
  - `Real Smoke` 阶段根据参数决定是否追加 `-n`。
  - `Collect Smoke Cases` 阶段保持串行收集，不启用并发。
  - `ciPowerShell` 统一注入 `TEST_PARALLEL_WORKERS` 环境变量。

## 行为说明

- 默认行为保持不变：`TEST_PARALLEL_WORKERS=off` 时不启用并发。
- 启用并发时会在 Jenkins 控制台输出当前 worker 配置。
- 真实 Smoke 使用并发可能放大账号额度、环境状态、接口限流等外部因素影响，应按需开启。
