# 重试策略与轮询状态机开发方案

## 1. 需求理解

根据 `FRAMEWORK_CAPABILITY_ROADMAP.md`，P1「重试策略与轮询状态机」的目标是提升异常场景下的执行稳定性和语义准确性。

结合当前代码，本阶段不是简单地在请求外面包一层循环，而是要把两类不同问题分开治理：

- 请求重试：只处理网络瞬态错误、429 和明确可重试的 5xx，且默认关闭，由用例或接口显式启用。
- 轮询状态机：把异步任务轮询从“某个 JSONPath 有值即成功”升级为明确的等待、成功、失败、未知状态判断。

当前已有请求中间件、脱敏日志、配置校验和基础契约断言。第一版应利用这些基础，建立可测试、可观测、不会掩盖真实缺陷的最小闭环。

## 2. 第一性原理与 TOC 分析

请求重试的本质不是“失败后再试一次”，而是在不改变业务语义的前提下，对明确的瞬态故障做有限恢复。

轮询状态机的本质不是“循环请求直到不为空”，而是把远端任务状态映射为有限状态迁移，并对成功、失败、超时和未知状态给出确定结论。

当前约束点不在请求能不能发出去，而在失败语义不够清晰：

1. `BaseRequest.request()` 当前每次只发送一次请求，缺少可显式启用的重试入口。
2. `poll_get()` 当前通过 `success_json_path` / `failure_json_path` 判断结果，没有状态模型。
3. 轮询超时异常虽然包含最后响应，但没有状态迁移序列和每个状态停留时间。
4. 未知状态没有独立语义，容易被“未取到成功字段”吞掉，最后只表现为超时。
5. 如果直接在默认请求链路中启用重试，会掩盖服务端真实稳定性问题，尤其是非幂等 POST。
6. 如果把重试、轮询、日志和状态统计全部塞进 `BaseRequest.poll_get()`，会重新造成 `BaseRequest` 膨胀，抵消请求中间件机制的价值。

TOC 决策：

- 第一版先把 `RetryPolicy` 和 `PollingPolicy` 建成显式策略对象。
- 重试默认关闭，只在调用方传入策略时启用。
- 轮询保持现有 `poll_get()` 参数兼容，同时新增 `polling_policy` 入口。
- 网络重试和业务状态机分层：每一次轮询请求可以使用重试策略，但状态判断只处理响应内容。
- 日志先保证每次重试和最终轮询结论可见，指标聚合留给后续 P2/P3。

## 3. 当前代码基础

### 3.1 请求主链路

`common/base_request.py` 当前结构：

- `BaseRequest.request()` 构造 `RequestContext` 后调用 `_send()`。
- `_send()` 负责执行中间件和 `requests.Session.request()`。
- `get/post/put/patch/delete` 都走统一 `request()`。
- `_request_without_attach()` 供 `poll_get()` 使用，保持轮询只挂载最终响应日志。
- 中间件已支持 `before_request()`、`after_response()`、`on_exception()`。

### 3.2 日志与脱敏

`ApiCallLogger` 已能输出：

- 请求 cURL。
- 请求行、请求头、请求体。
- 响应行、响应头、响应体。
- 异常类型和异常内容。

`util.redaction` 已能对请求参数、响应文本和异常文本脱敏。重试和轮询新增日志必须复用这套脱敏能力。

### 3.3 轮询现状

当前 `poll_get()` 行为：

- 校验 `poll_interval` 和 `poll_timeout`。
- 循环调用 `_request_without_attach("GET", ...)`。
- 如果 `failure_json_path` 有值并匹配到内容，抛 `AssertionError`。
- 如果 `success_json_path` 匹配到内容，返回最终响应。
- 超时后抛 `TimeoutError`，带最后响应文本。
- 只在成功、失败、异常或超时时挂载最后一次轮询日志。

现有兼容性必须保留，尤其是：

- `middlewares=[]` 时 `poll_get()` 仍可用。
- 轮询不自动挂载每一次中间响应日志。
- 现有 `BaseTask.poll_media_generation_result()` 不需要立即重写。

## 4. 第一版目标

第一版交付以下能力：

1. 新增 `RetryPolicy`。
2. 请求调用支持显式传入 `retry_policy`。
3. 默认不重试。
4. 只对连接失败、连接超时、读取超时、429 和指定 5xx 自动重试。
5. GET、HEAD 默认可在启用策略后重试。
6. POST 必须显式声明 `allow_post=True` 或提供幂等键，才允许重试。
7. 支持固定退避、指数退避、jitter 和 `Retry-After`。
8. 同时限制最大尝试次数和最大重试总耗时。
9. 每次重试原因、等待时间、尝试次数写入 Allure 或可测试的记录对象。
10. 新增 `PollingPolicy`。
11. `poll_get()` 支持 `polling_policy`，同时兼容现有 `success_json_path` / `failure_json_path`。
12. 轮询明确区分 pending、success、failure、unknown。
13. 轮询失败和超时异常携带最后状态、最后响应和状态迁移序列。
14. 单元测试覆盖 429、5xx、超时、Retry-After、非幂等 POST、轮询成功迁移、失败状态、未知状态和超时。

第一版不做：

- 不默认开启全局重试。
- 不把所有 POST 自动重试。
- 不建设复杂熔断器。
- 不建设全局指标平台。
- 不引入异步请求客户端。
- 不做 SSE 流式响应重试。
- 不做跨进程持久化重试。
- 不做复杂 mock server，优先使用 monkeypatch 或轻量 mock。

## 5. 建议文件结构

```text
common/
  base_request.py
  retry.py
  polling.py
  request_context.py
  request_middleware.py
tests/
  test_retry_policy.py
  test_polling_state_machine.py
  test_base_request_retry_polling.py
```

职责边界：

- `common/retry.py`
  - 定义 `RetryPolicy`。
  - 定义 `RetryDecision`、`RetryAttemptRecord`。
  - 处理可重试异常、可重试状态码、退避时间、jitter 和 `Retry-After`。
  - 不直接依赖 `BaseRequest`。

- `common/polling.py`
  - 定义 `PollingPolicy`。
  - 定义 `PollingState`、`PollingTransition`。
  - 定义 `PollingResult` 或异常对象。
  - 只负责响应内容到状态机的解析和迁移记录。

- `common/base_request.py`
  - 在 `request()` 中接收 `retry_policy`。
  - 将单次发送拆成 `_send_once()`。
  - 新增 `_send_with_retry()`。
  - 在 `poll_get()` 中接收 `polling_policy` 和可选 `retry_policy`。
  - 保持原有参数兼容。

- `common/request_context.py`
  - 增加可选字段或 attributes：
    - `attempt_index`
    - `max_attempts`
    - `retry_policy`
    - `retry_records`

- `util/api_call_logger.py`
  - 增加重试记录附件函数，或复用现有附件能力添加“重试记录”文本。

## 6. RetryPolicy 设计

### 6.1 数据结构

建议使用 dataclass：

```python
@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    retry_statuses: frozenset[int] = frozenset({429, 500, 502, 503, 504})
    retry_exceptions: tuple[type[BaseException], ...] = (
        requests.ConnectionError,
        requests.Timeout,
    )
    backoff: str = "exponential"
    base_delay: float = 0.5
    max_delay: float = 10.0
    jitter: bool = True
    respect_retry_after: bool = True
    max_elapsed: float | None = 30.0
    allowed_methods: frozenset[str] = frozenset({"GET", "HEAD"})
    allow_post: bool = False
    idempotency_header: str = "Idempotency-Key"
```

约束：

- `max_attempts >= 1`。
- `base_delay >= 0`。
- `max_delay >= base_delay`。
- `max_elapsed is None or max_elapsed > 0`。
- `retry_statuses` 只应包含明确可重试状态。

### 6.2 调用方式

普通 GET：

```python
client.get("/v1/models", retry_policy=RetryPolicy(max_attempts=3))
```

POST 有幂等键：

```python
client.post(
    "/v1/media/generations",
    json=payload,
    headers={"Idempotency-Key": request_id},
    retry_policy=RetryPolicy(max_attempts=2),
)
```

POST 显式业务允许：

```python
client.post(
    "/v1/safe-operation",
    json=payload,
    retry_policy=RetryPolicy(max_attempts=2, allow_post=True),
)
```

默认调用：

```python
client.get("/v1/models")
```

不重试，行为与当前一致。

### 6.3 可重试规则

允许重试：

- `requests.ConnectionError`
- `requests.Timeout`
- HTTP 429
- HTTP 500
- HTTP 502
- HTTP 503
- HTTP 504

不允许重试：

- HTTP 400、401、403、404、409、422。
- `requests.SSLError`，除非后续明确纳入策略。
- `requests.TooManyRedirects`。
- schema 断言失败、业务断言失败。
- POST 且无幂等键、无 `allow_post=True`。

### 6.4 POST 幂等判断

函数建议：

```python
def is_method_retry_allowed(method: str, kwargs: Mapping[str, Any], policy: RetryPolicy) -> bool:
    ...
```

规则：

1. `method.upper()` 在 `policy.allowed_methods` 中，允许。
2. `POST` 且 `policy.allow_post=True`，允许。
3. `POST` 且合并后的请求头里存在 `policy.idempotency_header`，允许。
4. 其他情况不允许。

注意：这里必须使用合并后的 headers，因为 `BaseRequest._build_request_context()` 会合并 session 默认 headers 和调用方 headers。

### 6.5 Retry-After 解析

支持两种格式：

- 数字秒数：

  ```text
  Retry-After: 3
  ```

- HTTP 日期：

  ```text
  Retry-After: Wed, 21 Oct 2015 07:28:00 GMT
  ```

建议使用：

```python
from email.utils import parsedate_to_datetime
```

解析规则：

- 数字小于 0 时忽略。
- 日期早于当前时间时等待 0 秒。
- 解析失败时回退到普通 backoff。
- 最终等待时间必须受 `max_delay` 和剩余 `max_elapsed` 约束。

### 6.6 退避计算

指数退避：

```python
delay = min(max_delay, base_delay * (2 ** (attempt_index - 1)))
```

固定退避：

```python
delay = min(max_delay, base_delay)
```

jitter：

```python
delay = random.uniform(0, delay)
```

测试中应允许注入随机函数和 sleep 函数，避免真实等待和随机失败。

## 7. BaseRequest 接入设计

### 7.1 参数入口

`request()` 增加内部弹出参数：

```python
retry_policy = kwargs.pop("retry_policy", None)
```

保持对 `requests.Session.request()` 透明，不把 `retry_policy` 传给 requests。

### 7.2 发送流程

建议拆分：

```python
def request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
    retry_policy = kwargs.pop("retry_policy", None)
    context = self._build_request_context(method, path, attach_log=attach_log, **kwargs)
    if retry_policy is None:
        return self._send_once(context)
    return self._send_with_retry(context, retry_policy)
```

但要注意当前 `_send()` 已是单次发送语义。建议：

- 将当前 `_send()` 保留为单次发送，或重命名为 `_send_once()`。
- 新增 `_send_with_retry()` 包装单次发送。
- 为了降低第一版风险，可以保留 `_send()` 名称代表单次发送，新增 `_send_with_retry()`。

### 7.3 每次尝试的上下文

每次尝试必须使用新的 `RequestContext` 或深拷贝 kwargs，避免中间件修改污染下一次尝试。

推荐做法：

- `request()` 先保留原始 method、path、kwargs。
- `_send_with_retry()` 每次 attempt 调用 `_build_request_context()`。
- 每次尝试写入：
  - `context.attributes["attempt_index"]`
  - `context.attributes["max_attempts"]`
  - `context.attributes["retry_records"]`

原因：当前中间件可修改 `context.kwargs`，复用同一个 context 会让重试之间互相污染。

### 7.4 日志策略

第一版日志原则：

- 普通未启用重试请求保持现有日志。
- 启用重试后，每次失败尝试都必须留下证据，不能只显示最终成功。
- 最终成功仍挂载最终响应。
- 中间失败尝试可以挂载为“接口重试记录”附件，避免 Allure 步骤过多。

建议新增记录文本：

```text
Attempt 1/3
Reason: HTTP 503
Wait seconds: 0.5
Response status: 503
Response body: ...
```

日志实现可以分两步：

1. 第一版先在 `ApiCallLogger` 增加 `attach_retry_records(records)`。
2. 后续再拆成 `RetryLoggingMiddleware` 或 `RetryMiddleware`。

## 8. RetryMiddleware 是否第一版实现

路线图提到后续接入 `RetryMiddleware`。结合当前代码，第一版建议不把真正重试逻辑写成普通中间件，原因：

- 当前中间件协议没有“重新发送请求”的控制权。
- 如果让 middleware 调用 `session.request()`，会绕开 `BaseRequest` 的发送流程和异常语义。
- 重试需要控制 sleep、attempt context、最终异常和最终响应，放在 `BaseRequest` 更清晰。

第一版可以做：

- `RetryPolicy` 独立建模。
- `_send_with_retry()` 在 `BaseRequest` 中实现。
- 重试日志复用 `ApiCallLogger` 或 request context attributes。

后续如果要落 `RetryMiddleware`，应先升级中间件协议，让 middleware 能返回 decision，而不是直接发送请求。

## 9. PollingPolicy 设计

### 9.1 数据结构

```python
@dataclass(frozen=True)
class PollingPolicy:
    status_json_path: str = "$.status"
    pending: frozenset[Any] = frozenset({"queued", "running"})
    success: frozenset[Any] = frozenset({"succeeded"})
    failure: frozenset[Any] = frozenset({"failed", "cancelled"})
    result_json_path: str | None = None
    error_json_path: str | None = "$.error"
    unknown: str = "fail"
```

`unknown` 支持：

- `"fail"`：遇到未知状态立即失败，推荐默认值。
- `"pending"`：把未知状态当作等待，保留兼容逃生口。
- `"ignore"`：忽略未知状态，只看 result/error JSONPath，不推荐业务默认使用。

### 9.2 状态枚举

```python
class PollingState(Enum):
    PENDING = "pending"
    SUCCESS = "success"
    FAILURE = "failure"
    UNKNOWN = "unknown"
```

### 9.3 迁移记录

```python
@dataclass(frozen=True)
class PollingTransition:
    attempt_index: int
    elapsed_seconds: float
    state: PollingState
    raw_status: Any
    response_status_code: int
```

如果要记录每个状态持续时间，可以在状态变化时计算：

```python
@dataclass(frozen=True)
class PollingStateDuration:
    raw_status: Any
    state: PollingState
    duration_seconds: float
```

第一版建议先记录每次 attempt 的状态和 elapsed，持续时间可由相邻 transition 推导；测试只需验证迁移序列。

### 9.4 状态解析规则

优先级：

1. 如果 `error_json_path` 匹配到值，判定 `FAILURE`。
2. 如果 `result_json_path` 匹配到值，判定 `SUCCESS`。
3. 如果 `status_json_path` 匹配到状态：
   - 在 `pending` 中，判定 `PENDING`。
   - 在 `success` 中，判定 `SUCCESS`。
   - 在 `failure` 中，判定 `FAILURE`。
   - 否则判定 `UNKNOWN`。
4. 如果没有状态字段：
   - 当保持旧参数兼容时，可继续用 `success_json_path` / `failure_json_path`。
   - 当显式传入 `PollingPolicy` 时，默认判定 `UNKNOWN`，不能直接成功。

## 10. poll_get 接入设计

### 10.1 兼容签名

建议签名：

```python
def poll_get(
    self,
    path: str,
    *,
    poll_interval: float = 2,
    poll_timeout: float | None = None,
    success_json_path: str | None = None,
    failure_json_path: str | None = None,
    polling_policy: PollingPolicy | None = None,
    retry_policy: RetryPolicy | None = None,
    **kwargs: Any,
) -> requests.Response:
    ...
```

兼容策略：

- 未传 `polling_policy` 时，保持现有 `success_json_path` / `failure_json_path` 行为。
- 传入 `polling_policy` 时，走新状态机。
- `success_json_path` / `failure_json_path` 可自动转换成兼容型 `PollingPolicy`，但第一版不强制。

### 10.2 异常设计

新增异常：

```python
class PollingError(AssertionError):
    ...

class PollingFailedError(PollingError):
    ...

class PollingUnknownStateError(PollingError):
    ...

class PollingTimeoutError(TimeoutError):
    ...
```

异常属性：

- `path`
- `last_status`
- `last_response`
- `transitions`
- `timeout`

错误信息示例：

```text
poll_get failed: path='/v1/media/tasks/task-1', status='failed', error={'message': 'render failed'}, transitions=queued -> running -> failed
```

超时示例：

```text
poll_get timed out after 120 seconds: path='/v1/media/tasks/task-1', last_status='running', transitions=queued -> running, last response=...
```

### 10.3 日志策略

保持现有原则：

- 不挂载每一次轮询响应正文，避免 Allure 报告爆炸。
- 最终成功、失败、未知状态或超时时，挂载最后一次轮询响应。
- 额外挂载“轮询状态迁移”附件：

```text
1. 0.000s queued -> pending HTTP 200
2. 2.008s running -> pending HTTP 200
3. 4.015s succeeded -> success HTTP 200
```

如果 `middlewares=[]` 没有 logger，则不挂载附件，但异常对象仍携带 transitions。

## 11. 与现有业务用例的关系

### 11.1 BaseTask

`BaseTask.poll_media_generation_result()` 当前默认：

```python
success_json_path="$.result.urls"
failure_json_path="$.error"
```

第一版不强制改默认业务行为。建议新增可选参数：

```python
polling_policy: PollingPolicy | None = None
retry_policy: RetryPolicy | None = None
```

然后透传给 `request_client.poll_get()`。

### 11.2 异步媒体任务状态

如果接口返回标准状态字段，建议后续迁移为：

```python
MEDIA_GENERATION_POLLING_POLICY = PollingPolicy(
    status_json_path="$.status",
    pending=frozenset({"queued", "running", "processing"}),
    success=frozenset({"succeeded"}),
    failure=frozenset({"failed", "cancelled"}),
    result_json_path="$.result.urls",
    error_json_path="$.error",
)
```

如果当前接口并没有稳定 `status` 字段，第一版仍保留旧 JSONPath 兼容模式，避免过早绑定不稳定协议。

## 12. 单元测试设计

### 12.1 `tests/test_retry_policy.py`

覆盖：

1. `max_attempts < 1` 拒绝。
2. `Retry-After: 3` 解析为 3 秒。
3. HTTP 日期格式 `Retry-After` 正确解析。
4. 过期 HTTP 日期等待 0 秒。
5. 解析失败回退到指数退避。
6. 指数退避受 `max_delay` 限制。
7. jitter 可通过注入随机函数稳定测试。
8. GET 在启用策略后允许重试。
9. POST 无幂等键且 `allow_post=False` 不允许重试。
10. POST 有 `Idempotency-Key` 允许重试。
11. 400/404 不重试。
12. 429/503 可重试。
13. `requests.Timeout` 可重试。

### 12.2 `tests/test_base_request_retry_polling.py`

覆盖：

1. 默认请求不重试。
2. GET 遇到 503 后重试并最终成功。
3. GET 遇到 429 且 `Retry-After` 时按头部等待。
4. `requests.Timeout` 后重试并最终成功。
5. 超过 `max_attempts` 后抛出最后一次异常或返回最后响应错误。
6. 超过 `max_elapsed` 后停止重试。
7. POST 无幂等键时，即使传入 `RetryPolicy` 也不重试。
8. POST 有幂等键时允许重试。
9. 每次尝试使用独立 request context。
10. 重试记录可被日志或 context 获取。

测试中必须 monkeypatch sleep，避免真实等待。

### 12.3 `tests/test_polling_state_machine.py`

覆盖：

1. `queued -> running -> succeeded` 返回最终响应。
2. `queued -> failed` 抛 `PollingFailedError`。
3. `queued -> cancelled` 抛 `PollingFailedError`。
4. 未知状态默认抛 `PollingUnknownStateError`。
5. `unknown="pending"` 时未知状态继续等待。
6. `result_json_path` 命中时成功。
7. `error_json_path` 命中时失败。
8. 非法 JSON 抛可定位异常。
9. 超时抛 `PollingTimeoutError`，携带最后状态和最后响应。
10. transitions 记录每次状态。

## 13. 实施顺序

1. 新增 `common/retry.py`
   - 实现 `RetryPolicy`。
   - 实现 Retry-After 解析。
   - 实现可重试方法、状态码和异常判断。
   - 实现退避计算。

2. 新增 `tests/test_retry_policy.py`
   - 先用纯函数测试策略逻辑。

3. 改造 `BaseRequest.request()`
   - 支持 `retry_policy`。
   - 保持默认不重试。
   - 新增 `_send_with_retry()`。
   - 每次重试重新构建 request context。

4. 新增请求重试集成测试
   - 使用 monkeypatch 模拟 `session.request` 返回序列和异常序列。
   - monkeypatch sleep。

5. 新增 `common/polling.py`
   - 实现 `PollingPolicy`。
   - 实现状态解析和 transitions。
   - 实现轮询异常类。

6. 改造 `BaseRequest.poll_get()`
   - 支持 `polling_policy` 和 `retry_policy`。
   - 未传 `polling_policy` 时保持旧行为。
   - 显式状态机路径使用新异常和 transitions。

7. 更新 `BaseTask.poll_media_generation_result()`
   - 增加可选 `polling_policy` / `retry_policy` 参数并透传。
   - 不改变默认参数。

8. 补充轮询状态机测试
   - 覆盖成功、失败、未知、超时和兼容旧 JSONPath。

9. 更新 code_history。

## 14. 验证命令

目标单测：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_retry_policy.py tests/test_polling_state_machine.py tests/test_base_request_retry_polling.py -q
```

相关回归：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_base_request_middleware.py tests/test_base_task.py tests/test_api_call_logger.py -q
```

全量单测：

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
```

业务用例收集：

```powershell
.\.venv\Scripts\python.exe run_master.py module/smoke --collect-only -q
```

## 15. 风险与处理

### 15.1 自动重试掩盖服务端真实问题

风险：如果默认启用重试，服务端偶发 5xx 会被最终成功掩盖。

处理：默认关闭，只有显式传入 `RetryPolicy` 才启用；重试记录保留首次失败和每次尝试。

### 15.2 非幂等 POST 重试导致重复创建任务或扣费

风险：媒体生成、模型调用等 POST 可能创建任务或产生费用，自动重试会造成重复副作用。

处理：POST 默认不重试；必须有幂等键或 `allow_post=True` 才允许。

### 15.3 Retry-After 造成测试变慢

风险：真实 sleep 会让单测和 CI 变慢。

处理：重试逻辑支持注入 sleep 函数；测试 monkeypatch sleep，只验证等待值。

### 15.4 轮询未知状态被误判成功

风险：服务端新增状态或返回异常结构时，如果只看 success JSONPath，可能语义不清。

处理：显式 `PollingPolicy` 下未知状态默认失败；兼容旧模式只在未传 policy 时保留。

### 15.5 Allure 报告过大

风险：每次轮询和每次重试都挂载完整响应会导致报告膨胀。

处理：轮询仍只挂载最终响应；重试中间过程挂载摘要记录，正文截断并脱敏。

### 15.6 与请求中间件职责冲突

风险：把重试做成当前协议下的普通 middleware 会绕开发送主链路。

处理：第一版重试由 `BaseRequest._send_with_retry()` 控制；后续如需 `RetryMiddleware`，先升级 middleware 协议为 decision 模式。

## 16. 第一版完成标准

- `RetryPolicy` 可显式传入请求调用。
- 未传 `RetryPolicy` 时请求行为与当前一致。
- GET/HEAD 在启用策略后可重试瞬态错误。
- POST 无幂等键且未显式允许时不会重试。
- POST 有幂等键或 `allow_post=True` 时可重试。
- `Retry-After` 支持数字和 HTTP 日期格式。
- 重试受 `max_attempts` 和 `max_elapsed` 限制。
- 重试记录能在测试中验证，并能输出到 Allure 摘要。
- `PollingPolicy` 支持 pending/success/failure/unknown。
- `poll_get()` 在显式 policy 下能记录状态迁移。
- 失败、未知状态和超时异常携带最后状态、最后响应和 transitions。
- 现有 `poll_get()` 旧参数行为兼容。
- 429、5xx、连接超时、失败状态、未知状态和超时均有单元测试覆盖。
- 全量 `tests` 通过。
- `module/smoke` 可正常 collect。

