# 轻量 Mock 与故障模拟开发方案

## 1. 需求理解

根据 `FRAMEWORK_CAPABILITY_ROADMAP.md`，P2「轻量 Mock 与故障模拟」的目标是让框架关键分支可以在离线环境中稳定回归。

结合当前代码，本阶段不是建设完整 Mock Server，也不是替换真实业务 smoke 用例，而是把已经散落在测试中的 `monkeypatch session.request`、fake response、fake logger 等模式沉淀为可复用的轻量测试工具：

- 模拟连接失败、超时、429、5xx、非法 JSON、字段类型错误。
- 模拟轮询状态迁移，例如 `queued -> running -> succeeded`、失败状态、未知状态和超时。
- 模拟 SSE 正常流、非法 chunk、中途断流和缺失 `[DONE]`。
- 能断言重试次数、退避等待、日志内容和最终异常类型。
- 在 CI 中不访问真实模型接口，也能覆盖请求、重试、轮询、日志、SSE 解析等框架核心分支。

第一版优先使用 pytest `monkeypatch` 和自定义 fake 对象。只有当重复匹配 URL、方法、请求体断言变得复杂时，再考虑引入 `responses` 或 `requests-mock`。

## 2. 第一性原理与 TOC 分析

Mock 的本质不是“伪造一个接口”，而是控制外部不确定性，使框架逻辑在固定输入下产生可验证输出。

接口测试框架的核心风险来自三类不确定性：

1. 网络层不确定：连接失败、超时、读超时、服务端 5xx。
2. 协议层不确定：响应不是合法 JSON、SSE chunk 非法、流中断。
3. 业务状态不确定：异步任务状态迁移、未知状态、失败状态、缺少请求 ID。

当前约束点不在“能不能 mock”，而在 mock 场景缺少统一表达：

1. `tests/test_base_request_retry_polling.py` 已经通过 `client.session.request = ...` 模拟重试和轮询，但每个测试都手写响应序列。
2. `tests/test_base_request_middleware.py` 已经模拟超时、中间件异常和轮询日志，但 fake response / fake logger 不是公共工具。
3. `module/smoke/task.py` 中 SSE 解析依赖真实 `requests.Response.iter_lines()`，缺少离线的流式响应模拟。
4. 重试等待依赖 `time.sleep`，当前用例各自 monkeypatch，缺少统一的 `SleepRecorder`。
5. 如果直接引入独立 Mock Server，会把第一版的瓶颈从“故障场景可复现”转移成“服务生命周期和端口管理”，投入不匹配。

TOC 决策：

- 第一版先建立 `tests/mock_helpers.py`，服务于框架单元测试，不进入生产运行时代码。
- 不修改 `BaseRequest` 主链路，不引入运行时 mock 模式。
- 先用 in-process fake 和 monkeypatch 覆盖稳定性分支。
- 只有真实 Socket、HTTP 协议细节或 SSE 断流无法用 fake 覆盖时，再引入本地 Mock Server。
- Mock 场景必须可读、可重复、可断言，不追求平台化 DSL。

## 3. 当前代码基础

### 3.1 请求与中间件

`common/base_request.py` 已具备：

- `BaseRequest.request()` 统一请求入口。
- 每次请求构造独立 `RequestContext`。
- 中间件生命周期：`before_request()`、`after_response()`、`on_exception()`。
- `LoggingMiddleware` 与 `RedactionMiddleware`。
- `_request_without_attach()` 支持轮询内部请求和最终日志挂载。

Mock 切入点：

- 替换 `client.session.request`。
- 替换 `common.base_request.time.sleep`。
- 替换 `common.request_middleware.ApiCallLogger`。

### 3.2 重试与轮询

当前已实现：

- `common/retry.py`
  - `RetryPolicy`
  - `RetryAttemptRecord`
  - `calculate_retry_delay()`
  - `should_retry_response()`
  - `should_retry_exception()`

- `common/polling.py`
  - `PollingPolicy`
  - `PollingState`
  - `PollingTransition`
  - `PollingFailedError`
  - `PollingUnknownStateError`
  - `PollingTimeoutError`

现有测试已经覆盖大量分支，但 mock 结构重复，适合在 P2 阶段沉淀。

### 3.3 SSE 解析

`module/smoke/task.py` 中：

- `collect_stream_chat_completion_chunks()` 解析 `data:` 行。
- 要求最后一行是 `data: [DONE]`。
- 非 JSON chunk 抛 `AssertionError`。
- finally 中关闭响应。
- `interrupt_stream_chat_completion()` 可中途停止读取并返回 request id。

当前 SSE 验证依赖真实接口。第一版应新增 fake stream response，用离线测试覆盖正常和异常流。

## 4. 第一版目标

第一版交付以下能力：

- 新增测试专用 mock helper，不进入业务运行时代码。
- 统一构造 `requests.Response`。
- 支持按顺序返回响应或抛出异常。
- 支持记录请求方法、URL、headers、params、json、data、timeout。
- 支持模拟连接失败、连接超时、读取超时。
- 支持模拟 429、500、502、503、504 响应。
- 支持模拟非法 JSON、字段类型错误、缺少请求 ID。
- 支持轮询状态迁移响应序列。
- 支持 fake SSE response 的 `iter_lines()`、`close()`。
- 支持模拟 SSE 非法 chunk、中途断流、缺少 `[DONE]`。
- 支持记录 `time.sleep()` 调用，断言退避策略。
- 支持 fake logger 收集成功响应、失败异常、重试记录和轮询迁移。
- 用第一版 helper 重构或补充部分框架测试，验证 helper 本身有价值。

## 5. 不做范围

第一版明确不做：

- 不自研独立 Mock Server。
- 不在 `BaseRequest` 增加 mock 模式。
- 不给业务 smoke 用例默认启用 mock。
- 不做 YAML / JSON 场景 DSL。
- 不做录制回放。
- 不做复杂 URL 路由器。
- 不模拟真实 TLS、DNS、Socket 半开连接。
- 不模拟所有第三方模型协议，只覆盖框架已出现的公共分支。
- 不引入数据库、Redis 或进程外依赖。

## 6. 文件结构

建议新增：

```text
tests/
  mock_helpers.py
  test_mock_helpers.py
  test_stream_fault_simulation.py
```

可选调整：

```text
tests/
  test_base_request_retry_polling.py
  test_base_request_middleware.py
```

职责划分：

- `tests/mock_helpers.py`
  - 测试专用工具，不被生产代码导入。
  - 提供 fake response、fake session transport、sleep recorder、fake logger、fake stream response。

- `tests/test_mock_helpers.py`
  - 验证 helper 自身行为稳定。

- `tests/test_stream_fault_simulation.py`
  - 离线覆盖 SSE 正常流和故障流。

- 现有 `test_base_request_retry_polling.py`
  - 可逐步用 helper 替代重复的 response list / lambda。

第一版不建议放到 `common` 或 `util`，因为它只服务测试。

## 7. Mock Helper API 设计

### 7.1 响应构造

```python
from tests.mock_helpers import make_response

response = make_response(
    url="https://example.com/v1/models",
    method="GET",
    status_code=503,
    json_body={"error": {"message": "temporary unavailable"}},
    headers={"Retry-After": "2"},
)
```

能力要求：

- 自动设置 `response.status_code`、`reason`、`headers`、`_content`。
- 自动构造 `response.request = requests.Request(...).prepare()`。
- 支持 `json_body`、`text_body`、`content_type`。
- 默认 `Content-Type: application/json`。
- 保留 headers 覆盖能力。

### 7.2 顺序 Transport

```python
from tests.mock_helpers import SequenceTransport

transport = SequenceTransport(
    [
        make_response(..., status_code=503),
        requests.Timeout("temporary timeout"),
        make_response(..., status_code=200),
    ]
)
client.session.request = transport
```

行为：

- 每次调用消耗一个结果。
- 结果是 `requests.Response` 时返回。
- 结果是异常时抛出。
- 记录每次请求为 `RequestCall`。
- 结果耗尽时抛明确错误，避免测试误通过。

建议数据模型：

```python
@dataclass(frozen=True)
class RequestCall:
    method: str
    url: str
    kwargs: dict[str, Any]
```

```python
class SequenceTransport:
    calls: list[RequestCall]
    remaining: int
```

### 7.3 路由 Transport

第一版可以先不实现复杂路由。如果已经需要按 method + path 返回不同响应，可提供最小 `RouteTransport`：

```python
transport = RouteTransport()
transport.add("GET", "/v1/models", [make_response(...), make_response(...)])
transport.add("POST", "/v1/chat/completions", make_response(...))
client.session.request = transport
```

第一版建议只在真实重复出现两个以上 URL 场景时再加，避免过早复杂化。

### 7.4 故障构造

```python
from tests.mock_helpers import connection_error, read_timeout, connect_timeout

transport = SequenceTransport(
    [
        connect_timeout("connect timeout"),
        read_timeout("read timeout"),
        connection_error("connection reset"),
    ]
)
```

返回标准 `requests` 异常：

- `requests.ConnectionError`
- `requests.ConnectTimeout`
- `requests.ReadTimeout`
- `requests.Timeout`

### 7.5 轮询响应序列

```python
from tests.mock_helpers import polling_responses

transport = SequenceTransport(
    polling_responses(
        "https://example.com/v1/media/tasks/task-001",
        ["queued", "running", "succeeded"],
    )
)
```

支持：

- 状态字段默认 `status`。
- 可传 `result`、`error`。
- 可指定状态码。
- 可生成 unknown、failed、cancelled。

### 7.6 SleepRecorder

```python
sleep = SleepRecorder()
monkeypatch.setattr("common.base_request.time.sleep", sleep)

assert sleep.calls == [0.1, 0.2]
```

能力：

- 记录 sleep 秒数。
- 默认不实际等待。
- 可选 `advance_clock`，与 fake monotonic 配合模拟超时。

第一版如果只需要记录退避时间，先不做 fake clock。

### 7.7 FakeLogger

```python
created_loggers: list[FakeApiCallLogger] = []
monkeypatch.setattr(
    "common.request_middleware.ApiCallLogger",
    lambda *args, **kwargs: create_fake_logger(created_loggers, *args, **kwargs),
)
```

记录：

- `success_responses`
- `failure_errors`
- `retry_records`
- `polling_transitions`
- 初始化参数

用途：

- 验证日志挂载次数。
- 验证异常日志不会丢。
- 验证重试记录和轮询迁移摘要写入。

## 8. SSE 故障模拟设计

### 8.1 FakeStreamResponse

```python
response = FakeStreamResponse(
    lines=[
        b"data: {\"id\":\"chatcmpl-001\",\"choices\":[]}",
        b"data: [DONE]",
    ],
    headers={"x-oneapi-request-id": "request-001"},
)
```

必须提供：

- `headers`
- `status_code`
- `text`
- `iter_lines(decode_unicode=False)`
- `close()`
- `closed` 状态

### 8.2 正常 SSE 场景

覆盖：

- 至少一个 JSON chunk。
- 最后一行 `data: [DONE]`。
- `collect_stream_chat_completion_chunks()` 返回 raw lines 和 parsed chunks。
- 调用结束后 response 被关闭。

### 8.3 非法 chunk

场景：

```text
data: not-json
data: [DONE]
```

预期：

- 抛 `AssertionError("Stream data chunk is not valid JSON")`。
- response.close() 被调用。

### 8.4 缺失 data 前缀

场景：

```text
event: message
data: [DONE]
```

预期：

- `collect_stream_chat_completion_chunks()` 抛断言错误。
- 错误中包含实际行。

### 8.5 缺失 [DONE]

场景：

```text
data: {"id":"chunk-1"}
```

预期：

- 抛 `AssertionError("Stream response should end with 'data: [DONE]'")`。
- response.close() 被调用。

### 8.6 中途断流

`iter_lines()` 可在指定行抛出：

```python
FakeStreamResponse(
    lines=[b'data: {"id":"chunk-1"}'],
    error_after=1,
    error=requests.ChunkedEncodingError("stream interrupted"),
)
```

预期：

- 原始异常继续抛出。
- response.close() 被调用。

## 9. 故障场景矩阵

第一版建议覆盖以下场景：

| 场景 | 模拟方式 | 目标断言 |
| --- | --- | --- |
| GET 503 后成功 | `SequenceTransport([503, 200])` | 重试 1 次，最终 200，sleep 记录正确 |
| GET 429 + Retry-After | `Retry-After: 2` | sleep 为 2，不使用 jitter |
| Timeout 后成功 | `SequenceTransport([Timeout, 200])` | 原异常可重试，最终成功 |
| POST 无幂等键 503 | 单个 503 | 不重试 |
| POST 有幂等键 503 后成功 | headers 含 `Idempotency-Key` | 允许重试 |
| 轮询成功 | `queued -> running -> succeeded` | transitions 正确 |
| 轮询失败 | `queued -> failed` | 抛 `PollingFailedError` |
| 轮询未知 | `paused` | 抛 `PollingUnknownStateError` |
| 轮询超时 | 固定 pending + fake time | 抛 `PollingTimeoutError` |
| 非法 JSON | text body 非 JSON | 抛明确断言或提取错误 |
| 缺少 request id | header 不含 `x-oneapi-request-id` | 业务 helper 抛明确错误 |
| SSE 正常结束 | fake stream lines | chunks 和 `[DONE]` 正确 |
| SSE 非法 JSON chunk | `data: not-json` | 抛解析错误 |
| SSE 缺失 `[DONE]` | 无 done 行 | 抛结束断言 |
| SSE 中途断流 | `ChunkedEncodingError` | 原异常传播且关闭响应 |

## 10. 与现有测试的关系

第一版不要求一次性重写所有已存在测试。

建议迁移顺序：

1. 新增 `tests/mock_helpers.py` 和 `tests/test_mock_helpers.py`。
2. 新增 `tests/test_stream_fault_simulation.py`，补上当前缺失的 SSE 离线故障覆盖。
3. 在 `tests/test_base_request_retry_polling.py` 中选择 2 到 3 个重复最明显的场景改用 helper。
4. 在 `tests/test_base_request_middleware.py` 中选择 fake logger 相关场景改用 helper。
5. 如果迁移后可读性下降，不强制替换所有测试，保留局部显式 fake。

核心原则：

- helper 是为了减少重复，不是为了隐藏测试意图。
- 关键断言仍留在具体测试中。
- 不为了“统一”牺牲失败可读性。

## 11. 依赖选择

第一版建议不新增第三方依赖。

原因：

- 当前 `session.request` 替换已经能覆盖多数框架分支。
- `responses` / `requests-mock` 更适合 URL 匹配和真实 `requests` 调用拦截，但会增加学习和维护成本。
- 当前最需要的是统一 fake response、请求记录、故障序列和 SSE fake。

后续引入条件：

- 出现大量“按 URL + method + body 匹配响应”的测试。
- 需要验证 `requests.Session` 级别行为，而手写 fake 已经不足。
- 需要更接近真实 HTTP 的请求匹配错误提示。

如果引入，优先选择 `requests-mock`：

- pytest fixture 体验好。
- 与 `requests` 生态直接匹配。
- 不需要启动端口。

`responses` 也可选，但第一版不必二选一。

## 12. 本地 Mock Server 启动条件

只有出现以下需求时再建设本地 Mock Server：

- 需要验证真实 socket 连接超时或读超时。
- 需要验证分块传输、SSE 半包、连接提前关闭等协议行为。
- 需要验证代理、DNS、TLS、重定向等 requests fake 无法表达的行为。
- 需要多个进程或非 Python 客户端访问同一个 mock 服务。

本地 Mock Server 如果后续实现，建议独立为 fixture：

```python
@pytest.fixture
def mock_server():
    server = LocalMockServer()
    server.start()
    try:
        yield server
    finally:
        server.stop()
```

第一版不进入。

## 13. 单元测试设计

### 13.1 `tests/test_mock_helpers.py`

覆盖：

- `make_response()` 生成 JSON 响应。
- `make_response()` 生成 text 响应。
- `SequenceTransport` 按顺序返回响应。
- `SequenceTransport` 按顺序抛异常。
- `SequenceTransport` 记录请求调用。
- `SequenceTransport` 结果耗尽时抛明确错误。
- `polling_responses()` 生成状态序列。
- `SleepRecorder` 记录 sleep 调用。
- `FakeApiCallLogger` 记录 success/failure/retry/polling。

### 13.2 `tests/test_stream_fault_simulation.py`

覆盖：

- 正常 SSE chunks。
- 非法 JSON chunk。
- 非 `data:` 行。
- 缺失 `[DONE]`。
- 中途断流。
- `interrupt_stream_chat_completion()` 可读取 request id 并关闭响应。

### 13.3 回归测试迁移

选择性更新：

- `tests/test_base_request_retry_polling.py`
  - 503 后成功。
  - Timeout 后成功。
  - 轮询状态迁移。

- `tests/test_base_request_middleware.py`
  - 日志 fake logger。
  - poll_get 最终响应日志。

迁移后要保持原测试断言强度不下降。

## 14. 实施顺序

建议按以下顺序实施：

1. 新增 `tests/mock_helpers.py`。
2. 实现 `make_response()` 和 `SequenceTransport`。
3. 实现故障异常工厂和 `SleepRecorder`。
4. 实现 `FakeApiCallLogger`。
5. 实现 `polling_responses()`。
6. 实现 `FakeStreamResponse`。
7. 新增 `tests/test_mock_helpers.py`。
8. 新增 `tests/test_stream_fault_simulation.py`。
9. 选择性迁移重试/轮询/中间件测试中的重复 fake。
10. 执行目标测试、相关回归、全量单测和 smoke collect-only。

## 15. 验证命令

目标测试：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_mock_helpers.py tests/test_stream_fault_simulation.py -q
```

相关回归：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_base_request_retry_polling.py tests/test_base_request_middleware.py tests/test_request_middleware.py tests/test_api_call_logger.py -q
```

全量单测：

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
```

smoke 收集：

```powershell
.\.venv\Scripts\python.exe run_master.py module/smoke --collect-only -q
```

## 16. 风险与处理

### 16.1 Mock 过度抽象导致测试不可读

风险：helper 封装过多后，测试看不出真实故障场景。

处理：helper 只负责构造和记录；业务断言留在具体测试中。

### 16.2 fake 与 requests 真实行为偏离

风险：fake response 缺少真实 `requests.Response` 的行为，导致测试误判。

处理：优先使用真实 `requests.Response` 对象构造；fake stream 只模拟当前代码实际使用的属性和方法。

### 16.3 忽略日志与脱敏验证

风险：只验证最终响应，不验证日志附件、重试记录和异常脱敏。

处理：`FakeApiCallLogger` 必须记录 retry、polling、failure；关键测试断言日志行为。

### 16.4 时间相关测试不稳定

风险：真实 `sleep` 或真实 `monotonic` 导致慢或不稳定。

处理：使用 `SleepRecorder`，必要时增加 fake clock；第一版禁止真实等待。

### 16.5 Mock 掩盖真实环境问题

风险：离线测试通过但真实接口失败。

处理：Mock 只验证框架分支；真实 smoke 用例仍保留，并通过显式开关控制成本和风险。

## 17. 第一版完成标准

第一版验收标准：

- 不访问真实接口即可覆盖重试、轮询、日志和 SSE 关键故障分支。
- Mock 场景可以稳定重复执行。
- 能断言请求调用次数、请求参数、sleep 时间、最终响应或异常。
- 能断言重试记录、轮询迁移和失败日志。
- SSE 正常流、非法 chunk、缺失 `[DONE]`、中途断流均有离线测试。
- 不新增运行时代码依赖。
- 不引入全局 mock 模式。
- 不启动独立 Mock Server。
- 全量 `tests` 通过，smoke collect-only 仍为 42 项。

## 18. 后续扩展

后续可按真实需求扩展：

- 引入 `requests-mock` 处理复杂 URL / method / body 匹配。
- 增加 fake clock，统一控制 `time.monotonic()` 和 `time.sleep()`。
- 增加本地 Mock Server 覆盖真实 SSE 断流和 socket 层行为。
- 将故障场景与 Flaky 指纹分类联动。
- 生成故障场景覆盖矩阵报告。

这些扩展应在第一版 helper 稳定、重复场景明确后再做。
