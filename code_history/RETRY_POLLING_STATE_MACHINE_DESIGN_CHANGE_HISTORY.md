# 重试策略与轮询状态机开发方案变更历史

## 2026-07-26

### 重试策略与轮询状态机开发方案

根据 `FRAMEWORK_CAPABILITY_ROADMAP.md` 中 P1「重试策略与轮询状态机」，在当前代码基础上输出开发方案。

变更内容：

- 新增 `dev/retry_polling_state_machine_design.md`
  - 梳理当前 `BaseRequest.request()`、`_send()`、`_request_without_attach()` 和 `poll_get()` 的现状。
  - 明确第一版目标：显式 `RetryPolicy`、默认不重试、显式 `PollingPolicy`、保留旧 `poll_get()` 参数兼容。
  - 设计 `RetryPolicy` 字段、可重试异常、可重试状态码、幂等方法、POST 幂等键判断、指数退避、jitter、`Retry-After` 和 `max_elapsed`。
  - 明确 POST 无幂等保证时不自动重试，避免重复创建任务或重复扣费。
  - 设计 `PollingPolicy`、`PollingState`、`PollingTransition` 和轮询异常。
  - 明确 pending/success/failure/unknown 状态处理，未知状态默认失败。
  - 规划 `BaseRequest` 接入方式：`retry_policy`、`polling_policy`、`_send_with_retry()` 和每次尝试独立 `RequestContext`。
  - 规划日志策略：普通请求保持现状，重试保留摘要记录，轮询仍只挂载最终响应并补状态迁移摘要。
  - 明确第一版不把重试逻辑做成当前协议下的普通 middleware，避免绕开发送主链路。
  - 补充单元测试设计、实施顺序、验证命令、风险处理和完成标准。

验证记录：

- 本次为开发方案输出，只新增设计文档和变更历史，未改动运行时代码，未执行测试。

### 重试策略与轮询状态机第一版实现

根据 `dev/retry_polling_state_machine_design.md` 落地 P1「重试策略与轮询状态机」第一版。

变更内容：

- 新增 `common/retry.py`
  - 定义 `RetryPolicy`。
  - 定义 `RetryAttemptRecord`。
  - 支持可重试状态码：429、500、502、503、504。
  - 支持可重试异常：`requests.ConnectionError`、`requests.Timeout`。
  - 排除 `requests.exceptions.SSLError` 和 `requests.exceptions.TooManyRedirects`。
  - 支持 GET/HEAD 幂等方法重试。
  - POST 默认不重试；只有提供 `Idempotency-Key` 或 `allow_post=True` 时允许重试。
  - 支持 fixed/exponential backoff、jitter、数字和 HTTP 日期格式 `Retry-After`。

- 新增 `common/polling.py`
  - 定义 `PollingPolicy`。
  - 定义 `PollingState`、`PollingEvaluation`、`PollingTransition`。
  - 定义 `PollingFailedError`、`PollingUnknownStateError`、`PollingTimeoutError`。
  - 支持 pending/success/failure/unknown 状态分类。
  - 支持 `result_json_path` 和 `error_json_path`。
  - 未知状态默认失败。
  - 失败和超时异常携带最后状态、最后响应和 transitions。

- 更新 `common/base_request.py`
  - `request()` 支持显式 `retry_policy` 参数。
  - 未传 `retry_policy` 时保持原单次请求行为。
  - 新增 `_send_with_retry()`。
  - 每次重试重新构建独立 `RequestContext`，避免中间件修改污染下一次尝试。
  - 重试受 `max_attempts` 和 `max_elapsed` 限制。
  - `poll_get()` 支持 `polling_policy` 和 `retry_policy`。
  - 未传 `polling_policy` 时保持旧 `success_json_path` / `failure_json_path` 行为。
  - 显式 `PollingPolicy` 下记录状态迁移，并在成功、失败、未知或超时时挂载最终响应。
  - 轮询请求内部启用重试时，最终响应日志和状态迁移绑定到最后一次真实请求 logger。

- 更新 `util/api_call_logger.py`
  - 新增 `attach_retry_records()`，挂载重试摘要。
  - 新增 `attach_polling_transitions()`，挂载轮询状态迁移摘要。
  - 新增文本脱敏辅助复用逻辑，避免重试和轮询附件泄露敏感信息。

- 更新 `common/__init__.py`
  - 导出 `RetryPolicy`。
  - 导出 `PollingPolicy`、`PollingState`、`PollingTimeoutError`。

- 更新 `common/base_task.py`
  - `poll_media_generation_result()` 增加可选 `polling_policy` / `retry_policy` 并透传。
  - `create_and_poll_media_generation()` 增加可选 `polling_policy` / `retry_policy` 并透传。
  - 默认参数和旧调用方式保持不变。

- 新增 `tests/test_retry_policy.py`
  - 覆盖 `RetryPolicy` 参数校验、Retry-After 数字和 HTTP 日期解析、退避、jitter、幂等方法、POST 幂等键、可重试状态码和异常。

- 新增 `tests/test_polling_state_machine.py`
  - 覆盖 pending/success/failure/unknown、result/error JSONPath、非法 JSON、异常上下文和状态迁移格式化。

- 新增 `tests/test_base_request_retry_polling.py`
  - 覆盖默认不重试。
  - 覆盖 GET 503、GET 429 + Retry-After、Timeout 后重试成功。
  - 覆盖 `max_attempts`、`max_elapsed`。
  - 覆盖 POST 无幂等键不重试、POST 有幂等键重试。
  - 覆盖每次重试独立 `RequestContext`。
  - 覆盖重试记录和轮询状态迁移可观测。
  - 覆盖轮询成功、失败、未知状态、超时和轮询请求内重试。
  - 覆盖轮询请求内重试后，最终响应日志挂载到最后一次真实请求 logger。

- 更新 `tests/test_base_task.py`
  - fake request client 支持 `polling_policy` / `retry_policy`。
  - 补充 `BaseTask` 透传策略参数测试。

验证记录：

目标测试首次运行时有 1 个失败：

```text
test_max_elapsed_stops_retry_without_sleep
```

原因是实现将退避等待时间压缩到剩余 `max_elapsed` 后仍继续重试。修正为：如果原始退避等待无法放入剩余总耗时预算，则停止重试，不做压缩等待。

修正后执行目标测试：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_retry_policy.py tests/test_polling_state_machine.py tests/test_base_request_retry_polling.py -q
```

结果：

```text
42 passed
```

执行相关回归：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_base_request_middleware.py tests/test_base_task.py tests/test_api_call_logger.py -q
```

结果：

```text
32 passed
```

执行全量单测：

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
```

结果：

```text
128 passed, 1 skipped
```

执行 smoke 收集：

```powershell
.\.venv\Scripts\python.exe run_master.py module/smoke --collect-only -q
```

结果：

```text
42 tests collected
```

补充轮询内重试最终 logger 归属修正后，重新执行目标测试、相关回归、全量测试和 smoke 收集，结果保持一致：

```text
42 passed
32 passed
128 passed, 1 skipped
42 tests collected
```
