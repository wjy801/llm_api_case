# 请求中间件机制开发方案

## 1. 背景

当前请求主流程集中在 `common/base_request.py`：

- `BaseRequest.request()` 负责 URL 拼接、timeout 默认值、Header 合并、前置媒体下载、请求发送和 Allure 请求日志。
- `BaseRequest._request_without_attach()` 为 `poll_get()` 复制了一套请求发送和日志创建逻辑。
- `ApiCallLogger` 负责生成 Allure 中的 `接口请求`、`接口响应`、`轮询结果请求` 和 `轮询结果响应`。
- `SmokeRequest.create_stream_chat_completion()` 通过 `_attach_log=False` 跳过流式响应日志，避免提前消费 SSE 响应体。

随着脱敏、重试、trace、metrics 等能力增加，如果继续写入 `BaseRequest.request()`，会导致主流程膨胀。第一版中间件机制的目标是建立稳定扩展点，同时保持现有 `get/post/put/patch/delete/poll_get` 调用方式兼容。

## 2. 第一版目标

第一版只解决以下问题：

1. 建立每次请求独立的 `RequestContext`。
2. 建立 `RequestMiddleware` 生命周期。
3. 将日志能力迁移为 `LoggingMiddleware`。
4. 将脱敏能力沉淀为 `RedactionMiddleware` 和统一脱敏工具。
5. 保留 `_attach_log=False`、`poll_get()`、Allure 步骤结构和现有模块调用方式。

第一版暂不实现：

- `RetryMiddleware`
- `TraceMiddleware`
- `MetricsMiddleware`
- 动态发现、热插拔、复杂依赖注入
- `core/plugins/adapters` 式目录重构

## 3. 建议文件结构

不做整体目录重构，新能力优先放入 `common`：

```text
common/
  request_context.py
  request_middleware.py
```

`request_context.py` 负责定义请求上下文。

`request_middleware.py` 负责定义中间件接口、内置中间件和默认中间件列表。

## 4. RequestContext 设计

建议结构：

```python
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from util import API_REQUEST_STEP_NAME, API_RESPONSE_STEP_NAME


@dataclass
class RequestContext:
    method: str
    path: str
    url: str
    kwargs: dict[str, Any]
    attach_log: bool = True
    request_step_name: str = API_REQUEST_STEP_NAME
    response_step_name: str = API_RESPONSE_STEP_NAME
    attributes: dict[str, Any] = field(default_factory=dict)
```

设计要求：

- `kwargs` 是本次请求独立副本，不与调用方或其他请求共享。
- `attributes` 用于中间件之间传递数据，例如 logger、脱敏后的请求信息、trace id。
- `attach_log` 只控制日志挂载，不影响其他中间件执行。
- `request_step_name` 和 `response_step_name` 用于兼容普通请求与 `poll_get()` 的不同 Allure 步骤名。

## 5. RequestMiddleware 生命周期

建议接口：

```python
from __future__ import annotations

from typing import Protocol

import requests

from common.request_context import RequestContext


class RequestMiddleware(Protocol):
    def before_request(self, context: RequestContext) -> None:
        ...

    def after_response(self, context: RequestContext, response: requests.Response) -> None:
        ...

    def on_exception(self, context: RequestContext, error: BaseException) -> None:
        ...
```

生命周期：

```text
BaseRequest.request()
  1. 解析框架内部参数，例如 _attach_log
  2. 构造 URL、timeout、headers
  3. 创建 RequestContext
  4. 依次执行 middleware.before_request(context)
  5. 执行 self.session.request(method=context.method, url=context.url, **context.kwargs)
  6. 请求成功后依次执行 middleware.after_response(context, response)
  7. 请求异常时依次执行 middleware.on_exception(context, error)，然后继续抛出原异常
```

第一版 `before_request`、`after_response`、`on_exception` 都按注册顺序执行，便于理解和测试。

## 6. 内置中间件

### 6.1 LoggingMiddleware

职责：

- 复用现有 `ApiCallLogger`。
- 在 `before_request` 创建 logger，并放入 `context.attributes`。
- 在 `after_response` 根据 `context.attach_log` 决定是否挂载成功日志。
- 在 `on_exception` 根据 `context.attach_log` 决定是否挂载失败日志。

伪代码：

```python
class LoggingMiddleware:
    LOGGER_ATTR = "api_call_logger"

    def before_request(self, context: RequestContext) -> None:
        context.attributes[self.LOGGER_ATTR] = ApiCallLogger(
            context.method,
            context.url,
            context.kwargs,
            step_name=context.request_step_name,
            response_step_name=context.response_step_name,
        )

    def after_response(self, context: RequestContext, response: requests.Response) -> None:
        if not context.attach_log:
            return
        self.get_logger(context).attach_success(response)

    def on_exception(self, context: RequestContext, error: BaseException) -> None:
        if not context.attach_log:
            return
        self.get_logger(context).attach_failure(error)
```

`poll_get()` 需要延迟挂载最后一次轮询日志，因此需要支持不自动 attach：

- `_request_without_attach()` 创建上下文时设置 `attach_log=False`。
- 请求返回后从 `context.attributes["api_call_logger"]` 取出 logger。
- `poll_get()` 保持现有逻辑：成功、失败状态、JSONPath 异常或超时时再调用 `logger.attach_success(last_response)`。

### 6.2 RedactionMiddleware

职责：

- 提供统一脱敏规则。
- 不修改真实请求，只生成脱敏副本。
- 后续 `ApiCallLogger`、配置校验、异常输出和 cURL 生成共用同一套规则。

建议脱敏字段：

```text
Header:
  authorization
  cookie
  proxy-authorization
  set-cookie
  x-api-key

Body / params:
  api_key
  key
  token
  access_token
  refresh_token
  secret
  password
  authorization
```

建议行为：

- 递归处理 dict/list/tuple。
- Header key 大小写不敏感。
- query params 和 json/data 中的敏感字段统一替换为 `<redacted>`。
- 原始 `context.kwargs` 不被修改。

第一版可以先将脱敏副本放入：

```python
context.attributes["redacted_kwargs"] = redact_request_kwargs(context.kwargs)
```

随后改造 `ApiCallLogger` 支持接收脱敏 kwargs 或脱敏函数。

### 6.3 MediaResourceMiddleware

当前 `start_media_downloads()` 在 `BaseRequest.request()` 中直接调用。它属于请求前生命周期，可以迁移到中间件。

职责：

```python
class MediaResourceMiddleware:
    def before_request(self, context: RequestContext) -> None:
        if context.method == "POST":
            start_media_downloads(context.kwargs.get("json"))
```

注意：

- 附件仍然由 `module/conftest.py` 在 teardown 阶段统一挂载。
- 不改变当前 `前置资源` Allure 步骤出现时机。
- 如果希望进一步降低第一版风险，也可以暂时保留在 `BaseRequest.request()` 中，后续再迁移。

推荐第一版一起迁移，原因是实现简单，且能让 `BaseRequest` 主流程更干净。

## 7. BaseRequest 改造方案

### 7.1 初始化

```python
class BaseRequest:
    def __init__(self, config: Settings = settings, middlewares: list[RequestMiddleware] | None = None):
        self.config = config
        self.session = requests.Session()
        self.default_headers = self._build_default_headers()
        self.session.headers.update(self.default_headers)
        self.middlewares = list(middlewares or self._default_middlewares())

    def _default_middlewares(self) -> list[RequestMiddleware]:
        return [
            MediaResourceMiddleware(),
            RedactionMiddleware(),
            LoggingMiddleware(),
        ]
```

### 7.2 统一上下文构建

新增 `_build_request_context()`：

```python
def _build_request_context(
    self,
    method: str,
    path: str,
    *,
    attach_log: bool = True,
    request_step_name: str = API_REQUEST_STEP_NAME,
    response_step_name: str = API_RESPONSE_STEP_NAME,
    **kwargs: Any,
) -> RequestContext:
    url = self._build_url(path)
    request_kwargs = dict(kwargs)
    request_kwargs.setdefault("timeout", self.config.timeout)

    headers = request_kwargs.pop("headers", None)
    if headers:
        request_kwargs["headers"] = self._merge_headers(headers)

    return RequestContext(
        method=method.upper(),
        path=path,
        url=url,
        kwargs=request_kwargs,
        attach_log=attach_log,
        request_step_name=request_step_name,
        response_step_name=response_step_name,
    )
```

### 7.3 统一发送入口

新增 `_send()`：

```python
def _send(self, context: RequestContext) -> requests.Response:
    for middleware in self.middlewares:
        middleware.before_request(context)

    try:
        response = self.session.request(
            method=context.method,
            url=context.url,
            **context.kwargs,
        )
    except Exception as error:
        for middleware in self.middlewares:
            middleware.on_exception(context, error)
        raise

    for middleware in self.middlewares:
        middleware.after_response(context, response)

    return response
```

`request()` 简化为：

```python
def request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
    attach_log = kwargs.pop("_attach_log", True)
    context = self._build_request_context(method, path, attach_log=attach_log, **kwargs)
    return self._send(context)
```

### 7.4 poll_get 兼容

`_request_without_attach()` 改为：

```python
def _request_without_attach(...) -> tuple[requests.Response, ApiCallLogger]:
    context = self._build_request_context(
        method,
        path,
        attach_log=False,
        request_step_name=step_name,
        response_step_name=response_step_name or API_RESPONSE_STEP_NAME,
        **kwargs,
    )
    response = self._send(context)
    logger = context.attributes[LoggingMiddleware.LOGGER_ATTR]
    return response, logger
```

这样 `poll_get()` 的行为保持不变。

## 8. Allure 兼容性

普通请求仍保持：

```text
POST /v1/chat/completions
  接口请求
  接口响应
```

轮询请求仍保持：

```text
轮询媒体生成结果: task_id
  轮询结果请求
  轮询结果响应
```

SSE 请求继续通过 `_attach_log=False` 跳过日志附件，避免 `ApiCallLogger` 读取 `response.text` 导致流被提前消费。

## 9. 单元测试设计

新增：

```text
tests/test_request_middleware.py
tests/test_base_request_middleware.py
```

建议覆盖：

1. 中间件 `before_request`、`after_response`、`on_exception` 顺序正确。
2. 每次请求创建独立 `RequestContext`，`attributes` 不串。
3. `LoggingMiddleware.after_response()` 成功时调用 `attach_success`。
4. `LoggingMiddleware.on_exception()` 异常时调用 `attach_failure`。
5. `_attach_log=False` 不挂载日志，但请求仍正常发送。
6. `poll_get()` 仍只在成功、失败状态或超时时挂载最后一次轮询日志。
7. `RedactionMiddleware` 不修改真实请求 kwargs。
8. Authorization、Cookie、API Key 不出现在脱敏后的日志数据中。
9. POST 请求会触发 `MediaResourceMiddleware`，GET 不触发。

## 10. 验证命令

单测：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_api_call_logger.py tests/test_curl_builder.py tests/test_request_middleware.py tests/test_base_request_middleware.py -q
```

框架已有基础单测：

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
```

用例收集：

```powershell
.\.venv\Scripts\python.exe run_master.py module/smoke --collect-only -q
```

## 11. 实施顺序

1. 新增 `RequestContext`。
2. 新增 `RequestMiddleware`、`LoggingMiddleware`、`RedactionMiddleware`。
3. 新增脱敏工具函数，先保证不修改原始请求。
4. 新增 `MediaResourceMiddleware`。
5. 改造 `BaseRequest.request()` 走统一 `_send()`。
6. 改造 `_request_without_attach()`，保持 `poll_get()` 行为兼容。
7. 补充单元测试。
8. 跑单测和 `--collect-only -q`。

## 12. 风险与处理

### 12.1 SSE 响应被提前消费

风险：`ApiCallLogger.attach_success()` 会读取 `response.text`。

处理：保留 `_attach_log=False`，并确保该参数只关闭日志 attach，不关闭其他中间件。

### 12.2 poll_get 日志挂载时机变化

风险：如果中间件自动挂载每次轮询日志，会破坏现有报告结构。

处理：`_request_without_attach()` 使用 `attach_log=False`，继续由 `poll_get()` 手动挂载最后一次轮询日志。

### 12.3 脱敏误改真实请求

风险：如果直接修改 `context.kwargs`，可能把 `<redacted>` 发到服务端。

处理：脱敏只生成副本，不修改真实请求数据。

### 12.4 中间件异常定位不清

风险：中间件内部异常抛出后，不知道来源。

处理：第一版可以在 `_run_before_middlewares/_run_after_middlewares/_run_exception_middlewares` 中包装异常消息，包含中间件类名，例如 `Request middleware LoggingMiddleware failed in after_response`。

## 13. 第一版完成标准

- `BaseRequest.request()` 主流程不再直接创建 `ApiCallLogger`。
- 普通请求、轮询请求、SSE 请求调用方式保持兼容。
- Authorization、Cookie、API Key 等敏感字段具备统一脱敏能力。
- 中间件顺序、异常传播、并发隔离和日志兼容均有单元测试。
- `tests` 单测通过。
- `run_master.py module/smoke --collect-only -q` 可正常收集。
