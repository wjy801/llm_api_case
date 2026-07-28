# 重试执行器抽离变更历史

## 变更时间

2026-07-26

## 变更范围

- `common/retry_executor.py`
- `common/base_request.py`
- `tests/test_retry_executor.py`

## 变更背景

当前重试策略已经由 `RetryPolicy` 显式控制，但重试执行循环仍位于 `BaseRequest._send_with_retry()` 内部，导致 `BaseRequest` 同时承担请求上下文构造、单次请求发送、中间件生命周期、重试编排、退避等待、耗时预算和重试记录协调。

本次改造按照 `dev/retry_executor_extraction_design.md` 执行，将重试执行循环抽离为独立执行器，降低 `BaseRequest` 职责厚度，并为后续 CI 重试指标采集、质量门禁和失败分类留下更清晰的扩展点。

## 核心变更

1. 新增 `common/retry_executor.py`。
   - 新增 `RetryExecutor`。
   - 负责执行 `RetryPolicy` 下的重试循环。
   - 支持注入 `sleeper` 和 `monotonic`，便于单元测试避免真实等待。
   - 保持 GET/HEAD 默认可重试、POST 幂等约束、状态码/异常判断、退避等待和 `max_elapsed` 语义。
   - 不直接依赖 `BaseRequest`、`ApiCallLogger` 或 Allure。

2. 改造 `BaseRequest`。
   - `BaseRequest.__init__()` 增加可选 `retry_executor` 注入参数。
   - 默认创建 `RetryExecutor(sleeper=time.sleep, monotonic=time.monotonic)`。
   - `_send_with_retry()` 从重试主循环改为薄适配层。
   - `BaseRequest` 继续负责构造 `RequestContext`、执行 `_send()`、提供 `_attach_retry_records()` 回调。
   - 外部 `retry_policy` 调用方式保持不变。
   - 轮询内 `retry_policy` 语义保持不变，仍表示单次 poll GET 内部重试。

3. 新增 `tests/test_retry_executor.py`。
   - 覆盖 GET 503 后成功。
   - 覆盖 Timeout 后成功。
   - 覆盖最终 Timeout 抛出原始异常。
   - 覆盖 POST 无幂等键不重试。
   - 覆盖 POST 带 `Idempotency-Key` 可重试。
   - 覆盖 `allow_post=True` 可重试。
   - 覆盖 `max_attempts` 返回最后 retryable 响应。
   - 覆盖响应路径和异常路径的 `max_elapsed` 行为。
   - 覆盖 `context_recorder` 指向最新 context。
   - 覆盖每次 attempt 使用独立 `RequestContext`。

## 保持不变

- 默认请求不自动重试。
- `RetryPolicy` 字段、默认值和校验语义不变。
- POST 默认不重试，除非带幂等键或 `allow_post=True`。
- 最终网络异常继续抛出原始异常，不包装成新的 executor 异常。
- 重试记录仍通过 `BaseRequest._attach_retry_records()` 写入现有 logger。
- 轮询状态机和 `PollingPolicy` 行为不变。

## 验证结果

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_retry_executor.py tests\test_retry_policy.py tests\test_base_request_retry_polling.py -q
```

结果：`41 passed`

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_base_request_middleware.py tests\test_base_task.py tests\test_polling_state_machine.py -q
```

结果：`42 passed`

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
```

结果：`192 passed, 1 skipped`

```powershell
.\.venv\Scripts\python.exe run_master.py module\smoke --collect-only -q
```

结果：`42 tests collected`
