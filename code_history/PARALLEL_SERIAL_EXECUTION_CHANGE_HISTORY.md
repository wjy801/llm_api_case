# Parallel Serial Execution Change History

## 变更目标

- 在 master 用例收集阶段识别 `serial` 标记。
- 启用并发时优先执行未标记 `serial` 的并发池。
- 并发池执行完毕后，如果串行池数量大于 0，再同步执行串行池。

## 代码变更

- 修改 `master_service.py`
  - 新增 `CollectedTestCase` 结构化用例对象。
  - 新增 `collect_test_case_items()`，通过 pytest collection hook 获取 nodeid 与 marker。
  - 新增 `split_test_cases()`，按 `serial` marker 拆分并发池与串行池。
  - 保留 `collect_test_cases()` 兼容旧调用。
  - 收集阶段隔离外部 pytest 插件自动加载，并清空执行期 `addopts`，避免全局插件或 Allure 参数干扰纯收集。

- 修改 `run_master.py`
  - 支持 `-n/--numprocesses` 触发 parallel-first 调度。
  - 未传 `-n` 时保持全量串行执行。
  - 传入 `-n` 时先执行并发池，再执行串行池。
  - 串行池为空或并发池为空时跳过对应阶段，不误判失败。
  - 并发池失败后仍继续执行串行池，最终合并返回失败状态。
  - 双阶段执行时自动拆分 JUnit 文件名，例如 `smoke-tests-parallel.xml` 与 `smoke-tests-serial.xml`。
  - 串行阶段执行后恢复并发阶段 Allure 原始结果，避免第二阶段清理覆盖第一阶段结果。

- 修改 `pytest.ini`
  - 注册 `serial` marker。

- 修改 `Jenkinsfile`
  - Real Smoke 阶段继续将 `-n` 传入 `run_master.py`。
  - 控制台文案调整为 parallel-first smoke execution。

- 标记真实共享状态 smoke 用例
  - `test_call_billing_correctness.py` 文件级 `serial`。
  - `test_key_api.py` 文件级 `serial`。
  - `test_图片生成同步调用.py` 文件级 `serial`。
  - `test_图片生成异步调用.py` 文件级 `serial`。
  - `test_response_body_validation.py` 中 zero 账号用例方法级 `serial`。

- 新增 `tests/test_master_service_parallel_serial.py`
  - 覆盖 marker 收集、执行池拆分、并发池优先、串行池后置、空池跳过、失败合并、JUnit 文件拆分。

## 行为说明

- `TEST_PARALLEL_WORKERS=off` 时行为保持串行。
- `TEST_PARALLEL_WORKERS=2/4/8/auto` 时：
  - 未标记 `serial` 的用例进入并发池。
  - 标记 `serial` 的用例不会进入 xdist 并发执行。
  - 串行池在并发池之后执行。

## 验证重点

- `run_master.py module/smoke --collect-only -q` 应输出总用例数、并发池数量、串行池数量。
- Jenkins Real Smoke 启用并发时由 master 执行器负责拆池调度。
