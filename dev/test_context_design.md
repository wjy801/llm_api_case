# 测试上下文与变量传递开发方案

## 1. 需求理解

根据 `FRAMEWORK_CAPABILITY_ROADMAP.md`，P1「测试上下文与变量传递」的目标是支持复杂接口链路中的变量提取、传递、隔离和清理。

结合当前代码，本阶段不是增加一个全局变量字典，也不是引入 YAML / Excel 驱动用例，而是在代码式 pytest 用例中提供一个用例级 `TestContext`：

- 从响应 JSON、Header、Cookie、文本正则中提取变量。
- 在同一个测试用例内安全读取变量，支撑创建任务、轮询、计费查询等链路。
- 变量缺失或类型错误时给出明确异常。
- 用例结束时执行资源清理回调。
- 在 xdist、线程并发和异步任务下不发生跨用例污染。

第一版只解决用例级变量传递。类级、会话级、跨进程持久化变量共享都不进入本阶段。

## 2. 第一性原理与 TOC 分析

测试上下文的本质不是“保存几个值”，而是把接口链路执行过程中产生的临时事实建立生命周期边界。

一个变量要能被可靠使用，至少需要回答五个问题：

1. 变量从哪个响应位置产生。
2. 提取失败时应该立即失败还是使用默认值。
3. 下游读取时是否能确认类型。
4. 变量在什么时候失效。
5. 并发执行时变量是否只属于当前用例。

当前瓶颈不在 JSONPath 能不能取值，而在变量生命周期没有统一建模：

1. 异步任务用例中 `task_id`、`request_id`、`status`、`image_url` 的提取逻辑分散在私有方法里。
2. `BaseTask.extract_task_id()` 和 `BaseTask.get_request_id_from_response()` 只覆盖少量固定字段，无法表达 Cookie、正则或多路径兜底。
3. 并发用例手动维护 `dict[int, str]`，变量隔离依赖调用方自律。
4. 提取失败的错误信息不统一，有些包含原始响应，有些只体现断言失败。
5. 用例产生的临时资源没有统一清理栈，后续如果创建文件、远端任务或临时账户状态，清理逻辑会继续散落。

TOC 决策：

- 第一版瓶颈是用例内链路变量的边界和诊断，不是跨用例共享。
- 先实现 `TestContext` 独立对象，不放入全局单例。
- pytest fixture 只负责创建和清理，不负责隐式注入业务配置。
- 提取能力覆盖 JSONPath、Header、Cookie、Regex，但 API 保持代码式、显式调用。
- 与请求中间件、重试、轮询保持松耦合：上下文使用响应对象，不介入请求发送主链路。
- B 账号、zero 账号继续限定在具体用例或业务模块中，不纳入全局测试上下文配置。

## 3. 当前代码基础

### 3.1 已有框架能力

当前代码已经具备以下基础：

- `common/base_request.py` 已统一请求入口，并支持请求中间件、重试策略和轮询状态机。
- `common/request_context.py` 已解决单次请求生命周期上下文，但它服务于请求中间件，不等同于用例级上下文。
- `common/base_assertions.py` 已具备 JSONPath 和 JSON Schema 相关断言能力。
- `common/base_task.py` 已封装异步任务创建、轮询和计费查询流程。
- `module/conftest.py` 已有 function 级 fixture 和 teardown hook，用于收集媒体下载、模型结果等资源。

### 3.2 重复变量提取现状

当前已有重复点：

- `BaseTask.extract_task_id(create_response)` 从 JSON 响应中读取 `task_id`。
- `BaseTask.get_request_id_from_response(response)` 从 `x-oneapi-request-id` 响应头读取 request id。
- `module/smoke/test_图片生成异步调用.py` 中存在 `_extract_task_id()`、`_extract_request_id()`、`_extract_status()`、`_extract_image_url()` 和 `_extract_first_json_path_value()`。
- `module/smoke/test_call_billing_correctness.py` 中并发请求手动维护 `request_ids_by_index`。

这些代码说明需求已经出现，但还没有形成可复用的框架能力。

## 4. 第一版目标

第一版交付以下能力：

- 新增用例级 `TestContext`。
- 支持变量 `set()`、`get()`、`require()`、`has()`、`delete()`、`clear()`。
- 支持 `extract()` 从 `requests.Response` 中提取变量。
- 支持 JSONPath、Header、Cookie、Regex 四类提取来源。
- 支持一个变量多个提取候选来源，按顺序取第一个非空值。
- 支持默认值、必填控制、类型校验和类型转换。
- 支持用例结束清理回调，按后进先出顺序执行。
- 支持错误信息携带变量名、来源、表达式、期望类型和响应摘要。
- 保证上下文对象无全局共享状态，适配 xdist、线程和异步执行。
- 提供 pytest fixture 接入方案。
- 补充离线单元测试，不依赖真实接口。

## 5. 不做范围

第一版明确不做：

- 不做 session / class / module 级共享上下文。
- 不做跨进程、跨用例变量持久化。
- 不做数据库、Redis、文件型上下文存储。
- 不引入 YAML / Excel / DSL 用例表达。
- 不自动把请求响应写入上下文。
- 不把 B 账号、zero 账号、控制台密钥等账号配置纳入全局上下文。
- 不在 `BaseRequest` 中隐式读取或写入 `TestContext`。
- 不批量重写现有业务用例，先以新增能力和少量示例验证为主。

## 6. 文件结构

建议新增或调整：

```text
common/
  test_context.py
tests/
  test_test_context.py
module/
  conftest.py
```

职责划分：

- `common/test_context.py`
  - 定义 `TestContext`。
  - 定义提取规则、异常类型和清理回调模型。
  - 不依赖 pytest。

- `tests/test_test_context.py`
  - 覆盖上下文本身的离线单元测试。
  - 使用 fake response 或 `requests.Response` 构造对象，不访问真实接口。

- `module/conftest.py`
  - 可选新增 `test_context` fixture。
  - 在 fixture teardown 中执行 `context.cleanup()`。
  - 保持现有资源收集 fixture 不变。

如果后续希望 `tests/` 和 `module/` 都直接使用 fixture，可在根目录新增 `conftest.py`。第一版建议先放在 `module/conftest.py` 或显式从测试模块中创建 `TestContext()`，减少全局影响面。

## 7. API 设计

### 7.1 基础读写

```python
from common.test_context import TestContext

context = TestContext()
context.set("task_id", "task-001")

task_id = context.get("task_id")
request_id = context.get("request_id", default=None)
context.require("task_id", expected_type=str)
```

建议行为：

- `set(name, value)` 写入变量并返回 value。
- `get(name, default=UNSET, expected_type=None)` 读取变量。
- `require(name, expected_type=None)` 等价于无默认值读取，缺失时失败。
- `has(name)` 判断是否存在。
- `delete(name)` 删除变量。
- `clear()` 清空变量，不执行清理回调。
- `snapshot()` 返回变量浅拷贝，便于调试。

### 7.2 从响应提取

路线图中的目标 API：

```python
context.extract("task_id", response, json_path="$.task_id")
context.extract("request_id", response, header="x-oneapi-request-id")

task_id = context.get("task_id")
```

建议扩展：

```python
task_id = context.extract(
    "task_id",
    response,
    json_path="$.task_id",
    required=True,
    expected_type=str,
)

request_id = context.extract(
    "request_id",
    response,
    header="x-oneapi-request-id",
    required=True,
)

image_url = context.extract(
    "image_url",
    result_response,
    json_path="$.result.urls[0]",
    required=True,
    expected_type=str,
)
```

`extract()` 应返回提取出的值，方便用例内直接使用，同时写入上下文。

### 7.3 多候选来源

异步图片用例当前会按 `task_id -> id -> request_id` 兜底。第一版可以提供 `extract_first()`：

```python
context.extract_first(
    "task_id",
    response,
    sources=[
        {"json_path": "$.task_id"},
        {"json_path": "$.id"},
        {"json_path": "$.request_id"},
    ],
    required=True,
    expected_type=str,
)
```

如果暂时不想引入 `sources` 结构，也可以第一版只提供 `extract()`，在业务用例中显式多次尝试。考虑当前重复场景已经存在，建议第一版实现 `extract_first()`，但保持结构简单。

## 8. 数据模型

### 8.1 TestContext

建议结构：

```python
class TestContext:
    def __init__(self, *, name: str | None = None):
        self.name = name
        self._variables: dict[str, Any] = {}
        self._cleanup_callbacks: list[CleanupCallback] = []
```

要求：

- 不使用模块级变量保存上下文。
- 不使用可变默认参数。
- 不复用其他用例传入的内部字典。
- `snapshot()` 返回拷贝，防止调用方直接修改内部状态。

### 8.2 变量名规则

建议变量名只允许：

```text
[A-Za-z_][A-Za-z0-9_.-]*
```

原因：

- 允许 `task_id`、`request.id`、`billing.request_id` 等常见命名。
- 避免空字符串、不可见字符和过度自由的 key 影响错误定位。

非法变量名抛出 `ContextVariableError`。

### 8.3 异常类型

建议异常：

```python
class TestContextError(AssertionError):
    pass

class ContextVariableNotFound(TestContextError):
    pass

class ContextVariableTypeError(TestContextError):
    pass

class ContextExtractionError(TestContextError):
    pass

class ContextCleanupError(TestContextError):
    pass
```

继承 `AssertionError` 的原因：

- 与测试失败语义一致。
- pytest 输出更贴近用例断言失败。
- 不会被误判为框架内部崩溃。

## 9. 提取规则

### 9.1 JSONPath

```python
context.extract("task_id", response, json_path="$.task_id")
```

实现要求：

- 使用 `jsonpath_ng.ext.parse`，保持现有依赖一致。
- `response.json()` 失败时抛 `ContextExtractionError`。
- JSONPath 无匹配且 `required=True` 时失败。
- 多个匹配默认取第一个值。
- 可选支持 `multiple=True` 返回全部匹配列表。

错误信息应包含：

- 变量名。
- JSONPath 表达式。
- 响应状态码。
- 脱敏后的响应摘要。

### 9.2 Header

```python
context.extract("request_id", response, header="x-oneapi-request-id")
```

实现要求：

- Header 名大小写不敏感，依赖 `requests.Response.headers` 的行为即可。
- 默认对字符串执行 `strip()`。
- 空字符串按未提取到处理。
- Header 缺失时输出当前响应头名称列表，避免泄露值。

### 9.3 Cookie

```python
context.extract("session_id", response, cookie="session_id")
```

实现要求：

- 使用 `response.cookies.get(cookie_name)`。
- 空值按未提取到处理。
- 错误信息只输出 cookie 名称，不输出全部 cookie 值。

### 9.4 Regex

```python
context.extract("image_url", response, regex=r"https://[^\"\\s]+")
```

实现要求：

- 默认匹配 `response.text`。
- 支持 `group=1` 或命名分组。
- 支持传入 `source_text`，便于对自定义文本提取。
- 正则无匹配且 `required=True` 时失败。
- 错误信息输出正则表达式和脱敏文本摘要。

### 9.5 来源互斥

`extract()` 中 `json_path`、`header`、`cookie`、`regex` 第一版应要求四选一。

原因：

- API 语义清晰。
- 错误定位简单。
- 多来源兜底由 `extract_first()` 表达，避免一个方法承担两种语义。

## 10. 类型校验、默认值与转换

建议参数：

```python
context.extract(
    "task_id",
    response,
    json_path="$.task_id",
    required=True,
    expected_type=str,
    transform=str,
)
```

规则：

- `required=True` 且未提取到值：抛 `ContextExtractionError`。
- `required=False` 且未提取到值：不写入变量，返回默认值。
- `default` 提供时，未提取到值则写入或返回默认值需要明确：
  - 建议默认写入 `default`，使后续 `get()` 行为一致。
- `expected_type` 校验最终值。
- `transform` 在类型校验前执行，用于 `str`、`int` 等简单转换。
- `None` 是否允许由 `allow_none` 控制，默认不允许必填变量为 `None`。

`get()` 也支持类型校验：

```python
request_id = context.get("request_id", expected_type=str)
```

类型错误信息应包含变量名、期望类型、实际类型和值摘要。

## 11. 清理回调

### 11.1 API

```python
context.add_cleanup(callback, *args, **kwargs)
context.cleanup()
```

用例示例：

```python
context.add_cleanup(temp_file.unlink, missing_ok=True)
context.add_cleanup(smoke_request.close)
```

### 11.2 执行规则

- 回调按 LIFO 顺序执行。
- `cleanup()` 可以重复调用，已执行过的回调不重复执行。
- 一个回调失败后继续执行剩余回调。
- 所有失败聚合为 `ContextCleanupError`。
- pytest fixture teardown 中如果清理失败，应让测试失败并保留原异常链。

### 11.3 为什么清理栈要放在上下文中

接口链路变量和临时资源经常同时出现。例如：

- 创建异步任务后记录 `task_id`，用例结束时取消任务。
- 下载临时图片后记录 `image_url`，用例结束时删除本地文件。
- 切换请求头后注册恢复动作。

把清理回调绑定到用例级上下文，可以减少业务用例手写 `try/finally` 的重复。

## 12. 并发隔离设计

### 12.1 xdist

pytest-xdist 本身是多进程执行。只要 `TestContext` 不使用文件、环境变量或模块级共享状态，进程间天然隔离。

第一版不做跨 worker 共享变量。

### 12.2 线程并发

线程并发下隔离原则：

- 每个并发任务创建自己的 `TestContext`，或由主用例显式传入不同上下文。
- 不提供线程全局默认上下文。
- 不提供 `get_current_context()` 这类隐式 API。

并发测试建议：

```python
def worker(index: int) -> str:
    context = TestContext()
    context.set("request_id", f"request-{index}")
    return context.require("request_id")
```

验证多个线程的变量互不串扰。

### 12.3 async 任务

第一版不引入 `contextvars.ContextVar` 作为默认机制。

原因：

- 当前项目主体是 `pytest + requests` 同步模型。
- 隐式上下文会增加调试成本。
- 明确传参更符合当前代码式用例风格。

如果后续真实出现 asyncio 测试用例，再单独评估 `contextvars`。

## 13. pytest fixture 接入

建议 fixture：

```python
@pytest.fixture
def test_context():
    context = TestContext()
    try:
        yield context
    finally:
        context.cleanup()
```

使用方式：

```python
def test_async_image_generation(test_context, smoke_request, smoke_task):
    create_response = smoke_task.create_async_image_generation(smoke_request)
    task_id = test_context.extract("task_id", create_response, json_path="$.task_id")

    result_response = smoke_task.poll_media_generation_result(smoke_request, task_id)
    image_url = test_context.extract("image_url", result_response, json_path="$.result.urls[0]")

    assert image_url
```

fixture 设计原则：

- function scope。
- 不 autouse。
- 不绑定业务账号。
- 不读取全局配置。
- 不隐式修改 `BaseRequest`。

## 14. 与 BaseTask 和业务用例的关系

第一版不要求立即删除 `BaseTask.extract_task_id()` 和 `get_request_id_from_response()`。

建议迁移路径：

1. 先实现 `TestContext` 和单元测试。
2. 在新用例中直接使用 `test_context`。
3. 选择一个重复最明显的异步图片用例做小范围示例迁移。
4. 如果示例稳定，再评估是否让 `BaseTask` 提供基于 `TestContext` 的辅助方法。

可选辅助方法：

```python
def extract_media_task_id(self, context: TestContext, response: requests.Response) -> str:
    return context.extract_first(
        "task_id",
        response,
        sources=[
            {"json_path": "$.task_id"},
            {"json_path": "$.id"},
            {"json_path": "$.request_id"},
        ],
        required=True,
        expected_type=str,
        transform=str,
    )
```

不建议把 `TestContext` 注入 `BaseRequest` 的原因：

- `BaseRequest` 负责 HTTP 生命周期。
- `TestContext` 负责测试用例链路生命周期。
- 两者边界清晰，后续排障更直接。

## 15. 日志与脱敏

上下文错误信息可能包含响应文本，因此必须复用已有脱敏能力。

要求：

- 错误中的响应摘要使用 `util.redaction` 处理。
- Header 提取失败时不输出完整 header 值。
- Cookie 提取失败时不输出完整 cookie 值。
- `snapshot()` 默认返回真实值，只用于代码内调试；如果后续需要输出到 Allure，应提供 `redacted_snapshot()`。
- 不在上下文中保存 API Key、Authorization、控制台密钥等敏感值。

如果用例确实需要临时保存敏感值，应由调用方负责，不作为第一版推荐路径。

## 16. 单元测试设计

新增 `tests/test_test_context.py`，覆盖：

- `set()` / `get()` / `require()` / `has()` / `delete()` / `clear()`。
- 缺失变量抛 `ContextVariableNotFound`。
- 类型不匹配抛 `ContextVariableTypeError`。
- JSONPath 提取成功。
- JSONPath 无匹配且 required 失败。
- JSON 非法时提取失败。
- Header 提取大小写兼容。
- Header 缺失错误不泄露 header 值。
- Cookie 提取成功。
- Regex 默认从 `response.text` 提取。
- Regex group 提取。
- `extract_first()` 按顺序返回第一个非空值。
- `default`、`required=False`、`transform` 行为。
- 清理回调 LIFO 顺序。
- 清理回调失败后仍执行剩余回调，并聚合错误。
- 多个 `TestContext` 实例变量隔离。
- 多线程各自上下文变量隔离。
- 错误文本中的敏感信息被脱敏。
- pytest fixture teardown 执行 cleanup。

不需要真实环境测试。

## 17. 实施顺序

建议按以下顺序实施：

1. 新增 `common/test_context.py`，先实现变量读写和异常类型。
2. 实现 JSONPath、Header、Cookie、Regex 单来源提取。
3. 实现 `extract_first()` 和类型校验。
4. 实现清理回调栈和聚合清理异常。
5. 接入脱敏摘要。
6. 补齐 `tests/test_test_context.py`。
7. 可选在 `module/conftest.py` 增加非 autouse 的 `test_context` fixture。
8. 可选挑选一个异步图片用例做示例迁移。
9. 运行目标测试、全量单测和 smoke collect-only。

## 18. 验证命令

目标测试：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_test_context.py -q
```

相关回归：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_base_task.py tests/test_base_request_retry_polling.py tests/test_config_validation.py -q
```

全量单测：

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
```

smoke 收集：

```powershell
.\.venv\Scripts\python.exe run_master.py module/smoke --collect-only -q
```

如果实施了异步图片用例示例迁移，可单独执行 collect-only，避免误触真实接口：

```powershell
.\.venv\Scripts\python.exe run_master.py module/smoke/test_图片生成异步调用.py --collect-only -q
```

## 19. 风险与处理

### 19.1 上下文变成隐式全局状态

风险：如果提供全局 `context` 或自动绑定到 `BaseRequest`，变量来源会变得不透明。

处理：第一版只允许显式创建或 fixture 注入，禁止全局单例。

### 19.2 错误信息泄露敏感数据

风险：提取失败时输出响应体、header、cookie，可能泄露密钥。

处理：复用脱敏规则；header/cookie 错误只输出名称，不输出值。

### 19.3 清理失败掩盖原始用例失败

风险：teardown 中清理失败可能覆盖用例本身失败原因。

处理：清理错误聚合输出；pytest fixture 中保留异常链。第一版可先让清理失败独立暴露，后续根据 pytest hook 行为再优化多异常展示。

### 19.4 过早迁移业务用例导致改动面过大

风险：批量替换私有提取函数会引入不必要回归。

处理：先完成框架能力和单元测试，只选一个重复明显的用例做示例。

### 19.5 变量类型转换过度智能

风险：自动转换过多会隐藏接口真实类型错误。

处理：默认只校验不转换，必须显式传 `transform` 才转换。

## 20. 第一版完成标准

第一版验收标准：

- `TestContext` 不依赖真实接口即可完整单测。
- JSONPath、Header、Cookie、Regex 提取均有覆盖。
- 变量缺失、类型不匹配、JSON 非法、正则无匹配均有清晰错误。
- 错误信息不泄露 API Key、Authorization、Cookie 等敏感值。
- 清理回调按 LIFO 执行，失败后仍继续清理。
- 多实例和多线程场景变量隔离。
- pytest fixture 为 function scope，且非 autouse。
- 不引入跨用例共享、不引入持久化、不引入 YAML / Excel DSL。
- 现有 `BaseTask`、`BaseRequest` 调用方式保持兼容。

## 21. 后续扩展

只有出现明确业务需求后，再考虑：

- `contextvars` 支持 asyncio 隐式上下文。
- class/session 级共享上下文。
- 跨进程变量持久化。
- Allure 中输出脱敏变量快照。
- 与轮询状态机联动，自动记录最后状态、结果 URL 等变量。
- 与轻量 Mock 能力结合，生成链路变量测试样本。

这些能力不应进入第一版，否则会把当前瓶颈从“变量边界不清”扩大成“状态平台设计过重”。
