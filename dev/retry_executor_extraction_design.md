# 重试执行器抽离开发方案

## 1. 需求理解

当前框架已经实现显式 `RetryPolicy`、请求级重试、轮询内单次 GET 重试和 Allure 重试记录。但重试执行循环仍内嵌在 `BaseRequest._send_with_retry()` 中，导致 `BaseRequest` 同时承担请求上下文构造、单次请求发送、中间件生命周期、重试编排、退避等待、耗时预算和重试记录协调。

本方案目标是：在保持外部调用方式不变的前提下，将重试执行循环从 `BaseRequest` 抽离为独立执行器，让 `BaseRequest` 回到“构造上下文 + 执行单次请求 + 协调中间件”的职责。

外部调用保持不变：

```python
client.get("/v1/models", retry_policy=RetryPolicy(max_attempts=3))
client.post(
    "/v1/safe-operation",
    json=payload,
    headers={"Idempotency-Key": "case-001"},
    retry_policy=RetryPolicy(max_attempts=3),
)
client.poll_get(
    "/v1/media/tasks/task-id",
    polling_policy=policy,
    retry_policy=RetryPolicy(max_attempts=2),
)
```

本阶段是框架内部治理，不改变 `RetryPolicy` 的公开语义，不改变 `polling_policy` 轮询状态机入口。

## 2. 第一性原理与 TOC 分析

重试机制的本质是包装一个“单次请求动作”：

```text
send_once(context) -> response 或 exception
```

重试执行器只需要负责：

1. 判断当前 HTTP 方法是否允许重试。
2. 执行第 N 次尝试。
3. 判断响应或异常是否可重试。
4. 计算等待时间。
5. 判断是否超过最大尝试次数或最大总耗时。
6. 记录每次重试原因。
7. 返回最终响应或继续抛出原始异常。

它不应该负责：

- 构造 URL。
- 合并请求头。
- 执行中间件。
- 直接访问 `requests.Session`。
- 直接写 Allure。
- 解析轮询业务状态。

TOC 约束：

- 当前约束不在 `RetryPolicy`，策略对象已经清晰。
- 当前约束在 `BaseRequest._send_with_retry()` 继续膨胀。
- 如果后续加入熔断、限流、请求预算、指标统计，内嵌式实现会进一步拉高 `BaseRequest` 复杂度。

决策：

- 不使用简单 Python `@retry` 装饰器，因为当前策略是每次请求动态传入，且需要 request context、日志、轮询和幂等判断协作。
- 不把重试做成当前 `RequestMiddleware`，因为现有中间件协议没有 around/next 语义，无法自然控制“重新发送请求”。
- 采用 `RetryExecutor` 包装器模式，抽离执行循环，`BaseRequest` 只提供 context factory、send_once 和日志记录回调。

## 3. 当前代码基础

### 3.1 已有能力

`common/retry.py` 已提供：

- `RetryPolicy`
- `RetryAttemptRecord`
- `is_method_retry_allowed()`
- `should_retry_exception()`
- `should_retry_response()`
- `retry_reason_for_exception()`
- `retry_reason_for_response()`
- `parse_retry_after()`
- `calculate_retry_delay()`

`common/base_request.py` 已提供：

- `request()` 中读取 `retry_policy`。
- `_send()` 执行单次请求和中间件生命周期。
- `_send_with_retry()` 执行重试循环。
- `_request_without_attach()` 支持轮询中的单次 GET 重试。
- `_attach_retry_records()` 将重试记录写入 logger。
- `_kwargs_with_session_headers()` 为 POST 幂等判断提供合并后的请求头。

### 3.2 当前耦合点

`BaseRequest._send_with_retry()` 当前同时处理：

- 首次 context 构造。
- POST 幂等判断。
- 重试循环。
- 异常重试判断。
- 响应状态码重试判断。
- `RetryAttemptRecord` 构造。
- `time.sleep()`。
- `time.monotonic()`。
- `max_elapsed` 判断。
- `context_recorder` 维护。
- 日志附件回调。

其中重试循环、等待、预算和记录构造可以抽离；context 构造和单次发送应留在 `BaseRequest`。

## 4. 目标与非目标

### 4.1 目标

1. 新增 `common/retry_executor.py`。
2. 将重试执行循环从 `BaseRequest._send_with_retry()` 移入 `RetryExecutor`。
3. 保持外部调用 API 不变。
4. 保持 `RetryPolicy` 字段、默认值和校验语义不变。
5. 保持轮询内 `retry_policy` 语义不变：每一次 poll GET 内部可重试，不重启整个轮询状态机。
6. 保持最终异常为原始网络异常，不包装成新的 executor 异常。
7. 每次尝试仍重新构造独立 `RequestContext`。
8. 重试记录仍可写入 Allure，并在单测中可验证。
9. 新增 `RetryExecutor` 独立单测，降低 `BaseRequest` 单测承载压力。

### 4.2 非目标

1. 不改变 `RetryPolicy` 为其它配置格式。
2. 不默认启用全局重试。
3. 不改变 POST 幂等约束。
4. 不引入熔断器。
5. 不引入全局指标聚合。
6. 不重构请求中间件协议。
7. 不改变轮询状态机的 `PollingPolicy` 行为。
8. 不删除 `BaseRequest._send_with_retry()` 这个内部适配方法，第一阶段只让它变薄。

## 5. 目标结构

```text
common/
  retry.py              # 策略、判断函数、delay 计算、RetryAttemptRecord
  retry_executor.py     # 重试执行循环
  base_request.py       # 请求上下文、中间件、单次发送、executor 适配
tests/
  test_retry_executor.py
  test_retry_policy.py
  test_base_request_retry_polling.py
```

职责边界：

- `RetryPolicy`：描述策略，不执行请求。
- `RetryExecutor`：执行重试循环，不知道 URL 构造、中间件、Allure。
- `BaseRequest`：提供 context factory、send_once、attach_records。
- `ApiCallLogger`：保留记录职责。

## 6. RetryExecutor 接口设计

新增文件：`common/retry_executor.py`

建议接口：

```python
from __future__ import annotations

from collections.abc import Callable, Mapping
import time
from typing import Any

import requests

from common.request_context import RequestContext
from common.retry import RetryAttemptRecord, RetryPolicy


class RetryExecutor:
    def __init__(
        self,
        *,
        sleeper: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        self.sleeper = sleeper
        self.monotonic = monotonic

    def execute(
        self,
        *,
        method: str,
        request_kwargs: Mapping[str, Any],
        policy: RetryPolicy,
        context_factory: Callable[[int], RequestContext],
        send_once: Callable[[RequestContext], requests.Response],
        attach_records: Callable[[RequestContext, list[RetryAttemptRecord]], None],
        context_recorder: list[RequestContext] | None = None,
    ) -> requests.Response:
        ...
```

参数说明：

- `method`：已标准化的 HTTP 方法，用于幂等判断。
- `request_kwargs`：合并 session headers 后的请求参数，用于 `is_method_retry_allowed()`。
- `policy`：重试策略。
- `context_factory(attempt_index)`：每次尝试创建新的 `RequestContext`。
- `send_once(context)`：执行单次请求，通常是 `BaseRequest._send()`。
- `attach_records(context, records)`：附加重试记录，通常是 `BaseRequest._attach_retry_records()`。
- `context_recorder`：用于轮询场景获得最后一次 context。

设计理由：

- Executor 不直接依赖 `BaseRequest`。
- Executor 不直接依赖 `LoggingMiddleware` 或 `ApiCallLogger`。
- Executor 不复用同一个 context，避免中间件污染跨 attempt。
- 测试可注入 `sleeper` 和 `monotonic`，避免真实等待。

## 7. RetryExecutor 执行语义

### 7.1 方法是否允许重试

执行开始时先判断：

```python
if not is_method_retry_allowed(method, request_kwargs, policy):
    context = context_factory(1)
    if context_recorder is not None:
        context_recorder[:] = [context]
    return send_once(context)
```

语义保持：

- GET/HEAD 默认可重试。
- POST 默认不可重试。
- POST 带 `Idempotency-Key` 或 `allow_post=True` 才允许重试。

### 7.2 每次 attempt

每次循环：

```python
for attempt_index in range(1, policy.max_attempts + 1):
    context = context_factory(attempt_index)
    context.attributes["attempt_index"] = attempt_index
    context.attributes["max_attempts"] = policy.max_attempts
    context.attributes["retry_records"] = retry_records
```

注意：即使 `BaseRequest.context_factory()` 已设置 attributes，executor 也可以再次设置，保证 executor 单测中语义完整。

### 7.3 异常路径

保持当前语义：

```text
send_once 抛异常
  -> 如果达到 max_attempts 或异常不可重试：attach_records，抛出原异常
  -> 否则记录 RetryAttemptRecord
  -> attach_records
  -> 如果超过 max_elapsed：抛出原异常
  -> sleep
  -> 下一轮
```

要求：

- 不能包装异常。
- `SSLError`、`TooManyRedirects` 仍不可重试。
- `exception_message` 仍记录原异常文本，后续由日志脱敏出口处理。

### 7.4 响应路径

保持当前语义：

```text
send_once 返回 response
  -> 如果达到 max_attempts 或 response 不可重试：attach_records，返回 response
  -> 否则记录 RetryAttemptRecord
  -> attach_records
  -> 如果超过 max_elapsed：返回当前 response
  -> sleep
  -> 下一轮
```

要求：

- 最后一次仍是可重试状态码时，返回最后响应，不抛异常。
- `max_elapsed` 不足时，响应路径返回当前响应；异常路径抛原异常。

### 7.5 等待时间

Executor 内部调用 `calculate_retry_delay(policy, attempt_index, response=response)`。

第一阶段可以沿用当前 `calculate_retry_delay()` 的默认随机函数；如果需要完全可控 jitter，后续再扩展 `RetryExecutor` 注入 `random_uniform`，本阶段不强制。

## 8. BaseRequest 改造设计

### 8.1 初始化 executor

`BaseRequest.__init__()` 增加可选参数：

```python
def __init__(
    self,
    config: Settings = settings,
    middlewares: list[RequestMiddleware] | None = None,
    retry_executor: RetryExecutor | None = None,
):
    ...
    self.retry_executor = retry_executor or RetryExecutor()
```

兼容性：

- 现有调用 `BaseRequest()` 不变。
- 测试可以注入 fake executor。

### 8.2 精简 `_send_with_retry()`

当前 `_send_with_retry()` 保留签名，改为适配 executor：

```python
def _send_with_retry(
    self,
    method: str,
    path: str,
    retry_policy: RetryPolicy,
    *,
    attach_log: bool = True,
    request_step_name: str = API_REQUEST_STEP_NAME,
    response_step_name: str = API_RESPONSE_STEP_NAME,
    context_recorder: list[RequestContext] | None = None,
    **kwargs: Any,
) -> requests.Response:
    first_context = self._build_request_context(
        method,
        path,
        attach_log=attach_log,
        request_step_name=request_step_name,
        response_step_name=response_step_name,
        **kwargs,
    )
    request_kwargs = self._kwargs_with_session_headers(first_context.kwargs)

    def context_factory(attempt_index: int) -> RequestContext:
        context = self._build_request_context(
            method,
            path,
            attach_log=attach_log,
            request_step_name=request_step_name,
            response_step_name=response_step_name,
            **kwargs,
        )
        context.attributes["attempt_index"] = attempt_index
        context.attributes["max_attempts"] = retry_policy.max_attempts
        return context

    return self.retry_executor.execute(
        method=first_context.method,
        request_kwargs=request_kwargs,
        policy=retry_policy,
        context_factory=context_factory,
        send_once=self._send,
        attach_records=self._attach_retry_records,
        context_recorder=context_recorder,
    )
```

说明：

- `first_context` 只用于标准化 method 和获得合并 headers 后的 `request_kwargs`。
- 真正发送由 executor 创建 attempt context。
- 如果 method 不允许重试，executor 会只执行一次。

### 8.3 import 清理

`BaseRequest` 抽离后不再直接需要：

- `RetryAttemptRecord`
- `calculate_retry_delay`
- `is_method_retry_allowed`
- `retry_reason_for_exception`
- `retry_reason_for_response`
- `should_retry_exception`
- `should_retry_response`

但 `NoopApiCallLogger.attach_retry_records()` 类型注解仍需要 `RetryAttemptRecord`。可以保留该 import，或使用 `TYPE_CHECKING` 降低运行期依赖。

### 8.4 保持轮询链路

当前链路保持：

```text
poll_get
  -> _poll_get_with_policy
    -> _request_without_attach
      -> _send_with_retry
        -> RetryExecutor.execute
```

`_request_without_attach()` 中的 `context_recorder` 仍用于获得最终 logger context。

## 9. 测试设计

### 9.1 新增 `tests/test_retry_executor.py`

核心测试应直接覆盖 executor，不依赖真实 `BaseRequest`。

建议测试 helper：

```python
class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds
```

覆盖用例：

1. GET 503 后重试并最终返回 200。
2. GET 429 后重试并记录 `HTTP 429`。
3. GET Timeout 后重试并最终返回 200。
4. GET 最终 Timeout 时抛原始 Timeout。
5. POST 无幂等键时只发送一次。
6. POST 有 `Idempotency-Key` 时允许重试。
7. POST `allow_post=True` 时允许重试。
8. `max_attempts` 到达后返回最后一个 retryable response。
9. 异常路径超过 `max_elapsed` 时抛原始异常。
10. 响应路径超过 `max_elapsed` 时返回当前响应。
11. `attach_records()` 在重试记录变化后被调用。
12. `context_recorder` 记录最后一次 context。
13. 每次 attempt 使用新的 `RequestContext` 对象。

### 9.2 回归 `tests/test_base_request_retry_polling.py`

现有测试应继续通过，必要时仅调整断言以适配 executor 注入。

重点关注：

- `test_default_request_does_not_retry`
- `test_get_retries_retryable_status_and_returns_success`
- `test_post_without_idempotency_key_does_not_retry`
- `test_post_with_idempotency_key_retries`
- `test_polling_request_uses_retry_policy`

### 9.3 回归 `tests/test_retry_policy.py`

`RetryPolicy` 纯策略测试不应因本次抽离变化。

### 9.4 回归中间件与任务测试

需要覆盖：

- `tests/test_base_request_middleware.py`
- `tests/test_base_task.py`
- `tests/test_polling_state_machine.py`

原因：

- 重试仍需要完整进入中间件生命周期。
- `BaseTask.poll_media_generation_result()` 仍要透传 `retry_policy`。
- 轮询状态机不应受 executor 抽离影响。

## 10. 实施步骤

### 步骤 1：新增 RetryExecutor

新增 `common/retry_executor.py`，迁移 `_send_with_retry()` 中的循环逻辑。

验收点：

- 文件不依赖 `BaseRequest`。
- 文件不依赖 `ApiCallLogger`。
- 使用 `RetryPolicy` 和 `RetryAttemptRecord`。

### 步骤 2：接入 BaseRequest

修改 `BaseRequest.__init__()`，增加 `retry_executor` 可选参数。

修改 `_send_with_retry()`，改为构造 `context_factory` 后调用 executor。

验收点：

- 外部 request API 不变。
- `_request_without_attach()` 不需要改变调用方式。
- `poll_get()` 不需要改变调用方式。

### 步骤 3：迁移测试

新增 `tests/test_retry_executor.py`。

保留现有 `tests/test_base_request_retry_polling.py`，作为集成回归。

验收点：

- executor 单测覆盖核心分支。
- BaseRequest 集成测试继续证明中间件、日志和轮询链路不破。

### 步骤 4：清理 import 与文档注释

清理 `base_request.py` 中不再使用的 retry helper import。

必要时更新函数 docstring，说明 `_send_with_retry()` 是 executor 适配层。

### 步骤 5：写入框架代码变更历史

本方案执行涉及框架代码变更，完成开发后需要按当前规则单独写入 `code_history`，例如：

```text
code_history/RETRY_EXECUTOR_EXTRACTION_CHANGE_HISTORY.md
```

## 11. 验收命令

目标测试：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_retry_executor.py tests/test_retry_policy.py tests/test_base_request_retry_polling.py -q
```

相关回归：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_base_request_middleware.py tests/test_base_task.py tests/test_polling_state_machine.py -q
```

全量框架单测：

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
```

业务用例收集：

```powershell
.\.venv\Scripts\python.exe run_master.py module/smoke --collect-only -q
```

## 12. 风险与处理

### 12.1 context 复用导致中间件污染

风险：如果 executor 复用同一个 `RequestContext`，中间件写入的 attributes 或 kwargs 会污染后续 attempt。

处理：executor 每次通过 `context_factory(attempt_index)` 获取新 context。

### 12.2 POST 幂等判断丢失 session headers

风险：只看调用方 headers 会漏掉 session 默认 header，或无法准确判断幂等键。

处理：`BaseRequest` 继续通过 `_kwargs_with_session_headers(first_context.kwargs)` 传入合并后的 `request_kwargs`。

### 12.3 最终异常被包装

风险：抽离 executor 后为了表达失败创建新异常，破坏当前“暴露原始网络异常”的语义。

处理：executor 不定义新的重试异常；最终异常路径直接 `raise` 原异常。

### 12.4 Allure 日志断链

风险：executor 不知道 logger，如果 attach_records 回调时机不一致，Allure 中缺少重试记录。

处理：继续由 `BaseRequest._attach_retry_records()` 作为回调注入；executor 只负责调用回调。

### 12.5 轮询内重试语义变化

风险：错误地把整个轮询状态机包进 retry，导致状态迁移记录混乱。

处理：保持现有链路，只对 `_request_without_attach()` 中的单次 GET 使用 `_send_with_retry()`。

## 13. 完成标准

1. `common/retry_executor.py` 存在，且承载重试主循环。
2. `BaseRequest._send_with_retry()` 不再包含大段 retry loop，只作为适配层。
3. 外部 `retry_policy` 调用方式不变。
4. 默认请求不重试。
5. GET/HEAD 重试、POST 幂等约束、Retry-After、backoff、max_elapsed 行为不变。
6. 轮询中的 `retry_policy` 行为不变。
7. 每次 attempt 仍使用独立 `RequestContext`。
8. 重试记录仍能通过现有 logger 写入。
9. 新增 `tests/test_retry_executor.py` 覆盖 executor 分支。
10. 目标测试、相关回归、全量 `tests` 和 smoke collect-only 通过。

