# 测试用例编写指南

本文档说明如何基于当前 API 测试框架编写、收集、执行和排查测试用例。当前框架已经具备请求中间件、配置校验与安全脱敏、契约断言、重试策略、轮询状态机、测试上下文、轻量 Mock 与故障模拟能力；用例编写应直接复用这些能力，不在 `test_*.py` 中重复实现底层请求、轮询、日志和变量传递逻辑。

## 1. 编写原则

第一性原理：测试用例的本质是表达“输入、业务动作、可验证结果”。框架已经承担 HTTP 请求、Allure 日志、脱敏、重试、轮询、Schema 断言和上下文变量传递；用例文件只保留测试数据、业务调用顺序和断言。

TOC 约束：当前新增用例最容易失控的瓶颈不是语法，而是把框架能力绕开后造成重复封装、日志缺失、状态机失效或密钥泄漏。因此所有真实业务用例优先走模块 `Task`，所有通用能力优先下沉到 `common/` 或 `util/`。

约束：

- 不在测试类中定义 `__init__`，否则 pytest 不会收集该测试类。
- 不在用例中硬编码完整环境域名、全局 API Key 或控制台密钥。
- B 账号、zero 账号等特殊账号只允许在明确用例或局部 fixture 中使用，不进入全局统一配置。
- 不再使用旧版 `success_json_path` / `failure_json_path` 轮询参数，轮询统一使用 `PollingPolicy`。
- 不为了变量传递引入 YAML、Excel 或隐式 DSL；跨步骤变量使用 `test_context`。
- 业务用例不允许直接从 `common` 导入或调用函数式断言；断言必须通过当前模块的 `Assertions` 实例调用。

## 2. 目录与分层

真实业务用例统一放在 `module/<module_name>/`：

```text
module/<module_name>/
  request.py
  assertions.py
  decorators.py
  task.py
  test_*.py
  __init__.py
```

pytest 收集规则：

```text
文件名：test_*.py
测试类：Test*
测试方法：test_*
```

分层职责：

```text
request.py      当前模块私有接口路径或特殊请求方法
task.py         当前模块业务动作组合、payload builder、模块专属流程
assertions.py   当前模块专属断言；通用断言直接复用 BaseAssertions
decorators.py   当前模块装饰器扩展；通用 Allure step 优先复用 BaseDecorators
test_*.py       测试数据、业务调用顺序和断言
common/         所有模块可复用的基础能力
util/           无业务状态的工具能力，例如日志、脱敏、配置校验、cURL、媒体资源
tests/          框架基础能力单测、mock helpers 和故障模拟测试
```

新增模块时，每个模块类分别继承 `common` 中的公共基类：

```python
from common import BaseAssertions, BaseDecorators, BaseRequest, BaseTask


class XxxRequest(BaseRequest):
    pass


class XxxAssertions(BaseAssertions):
    pass


class XxxDecorators(BaseDecorators):
    pass


class XxxTask(BaseTask):
    pass
```

不同模块的 `task.py` 不互相引用。如果一个能力可跨模块复用，应下沉到 `BaseTask`、`BaseRequest`、`BaseAssertions` 或 `util/`。

## 3. 标准用例模板

推荐使用 `setup_method` 初始化 request、assertions、task 对象，使用 `teardown_method` 关闭 request session。

```python
from __future__ import annotations

from module.smoke import SmokeAssertions, SmokeRequest, SmokeTask


class TestChatCompletion:
    def setup_method(self):
        self.smoke_request = SmokeRequest()
        self.smoke_assertions = SmokeAssertions()
        self.smoke_task = SmokeTask()

    def teardown_method(self):
        self.smoke_request.close()

    def test_chat_completion_returns_success_schema(self):
        payload = self.smoke_task.build_chat_completions_payload()

        response = self.smoke_task.create_chat_completion(
            self.smoke_request,
            payload,
        )

        self.smoke_assertions.assert_status_code(response, 200)
```

真实业务用例优先通过 `Task` 表达业务动作。只有在测试 `BaseRequest` 自身行为、框架中间件、重试或轮询底层能力时，才直接调用 request 客户端。

## 4. Payload 编写规范

payload 直接使用 Python 字典，不使用 YAML 或 Excel。

JSON 值需要转换为 Python 写法：

```text
true  -> True
false -> False
null  -> None
```

示例：

```python
payload = {
    "model": "glm-5",
    "messages": [
        {"role": "user", "content": "用一句话介绍接口自动化测试。"},
    ],
}
```

媒体输入资源放在服务端协议要求的位置。当前中间件会在 POST payload 中存在 `input.media.url` 时自动收集前置媒体资源，并写入 Allure 的 `前置资源` 步骤。

不要在 payload、headers、params 中写入真实密钥。需要特殊账号时，在明确用例或局部 fixture 内读取对应环境变量，并用 `pytest.skip()` 处理缺失配置。

## 5. Request、Task 与业务调用

`BaseTask` 已提供常用业务动作：

```python
# 同步图片生成
response = self.image_task.create_image_generation(self.image_request, payload)

# 文本对话补全
response = self.smoke_task.create_chat_completion(self.smoke_request, payload)

# 异步媒体任务创建
create_response = self.image_task.create_media_generation(self.image_request, payload)

# 异步媒体任务轮询
poll_response = self.image_task.poll_media_generation_result(
    self.image_request,
    task_id,
    poll_interval=2,
    poll_timeout=900,
)

# 异步媒体创建并轮询
poll_response = self.image_task.create_and_poll_media_generation(
    self.image_request,
    payload,
    poll_interval=2,
    poll_timeout=900,
)
```

`request.py` 只封装当前模块确实需要暴露的路径或特殊请求。通用 `/v1/images/generations`、`/v1/chat/completions`、`/v1/media/generations`、媒体任务轮询、账户余额和用量记录查询优先复用 `BaseTask`。

如果一个业务动作由多次请求组成，应在模块 `task.py` 中封装，并使用 `allure_step` 给出业务步骤名称；不要在测试方法中堆多段底层 HTTP 细节。

## 6. 请求中间件使用规范

`BaseRequest` 默认启用请求中间件管线：

```python
RequestMiddleware.before_request(context)
RequestMiddleware.after_response(context, response)
RequestMiddleware.on_exception(context, error)
```

默认中间件：

- `MediaResourceMiddleware`：POST 前收集 `input.media.url` 前置资源。
- `RedactionMiddleware`：为日志和 cURL 构造脱敏副本。
- `LoggingMiddleware`：写入请求、响应、异常、重试和轮询日志。

用例编写要求：

- 真实业务用例不要手工调用日志工具；通过 `BaseRequest` 和 `BaseTask` 自动写入 Allure。
- 不要让中间件修改调用方原始 payload；需要变更请求只修改 `RequestContext.kwargs`。
- `middlewares=[]` 是合法配置，框架底层测试可以用它验证无中间件场景。
- 日志、异常、cURL、Allure 附件中的敏感信息必须走统一脱敏规则。

## 7. 契约断言规范

通用断言来自 `BaseAssertions`：

```python
self.smoke_assertions.assert_status_code(response, 200)
self.smoke_assertions.assert_json_value(response, "$.model", "glm-5")
self.smoke_assertions.assert_json_path_exists(response, "$.choices")
self.smoke_assertions.assert_schema(response, CHAT_COMPLETION_SUCCESS_SCHEMA)
```

业务用例必须通过当前模块的 `Assertions` 实例使用断言能力。不要在 `module/<module_name>/test_*.py` 中直接 `from common import assert_schema`、`assert_status_code`、`assert_json_value` 或其它函数式断言；这样会绕过模块断言层，后续模块专属断言和规范收敛会失去统一入口。

Schema 编写建议：

- 成功响应和标准错误响应分别维护 Schema。
- Schema 放在对应业务模块中，例如 `module/smoke/response_schemas.py`。
- 对稳定字段做严格约束，对不稳定字段避免过度绑定。
- 断言失败信息会输出 JSONPath、Schema path、期望值、实际类型和实际值；敏感值会脱敏。

示例：

```python
from module.smoke.response_schemas import CHAT_COMPLETION_SUCCESS_SCHEMA


def test_chat_completion_schema(self):
    response = self.smoke_task.create_chat_completion(
        self.smoke_request,
        self.smoke_task.build_chat_completions_payload(),
    )

    self.smoke_assertions.assert_status_code(response, 200)
    self.smoke_assertions.assert_schema(response, CHAT_COMPLETION_SUCCESS_SCHEMA)
```

## 8. 重试策略使用规范

默认请求不自动重试。只有用例或业务封装显式传入 `RetryPolicy` 时才启用重试。

```python
from common import RetryPolicy

response = self.smoke_request.get(
    "/v1/models",
    retry_policy=RetryPolicy(max_attempts=3),
)
```

POST 重试必须满足幂等约束：

```python
from common import RetryPolicy

response = self.smoke_request.post(
    "/v1/safe-operation",
    json=payload,
    headers={"Idempotency-Key": "case-001"},
    retry_policy=RetryPolicy(max_attempts=3),
)
```

约束：

- GET/HEAD 默认允许重试。
- POST 只有带幂等键，或显式设置 `allow_post=True`，才允许重试。
- 可重试状态码默认包含 429、500、502、503、504。
- 连接异常、连接超时、读取超时可按策略重试。
- 每次重试原因、等待时间、响应状态或异常类型会写入 Allure。

## 9. 轮询状态机使用规范

异步任务轮询统一使用 `PollingPolicy`。`BaseRequest.poll_get()` 必须显式传入 `polling_policy`，不再支持旧版 `success_json_path` / `failure_json_path`。

媒体生成类任务优先使用 `BaseTask` 默认策略：

```python
poll_response = self.image_task.create_and_poll_media_generation(
    self.image_request,
    payload,
    poll_interval=2,
    poll_timeout=900,
)
```

默认媒体策略为 `DEFAULT_MEDIA_POLLING_POLICY`，适用于当前媒体任务响应：

```python
from common import DEFAULT_MEDIA_POLLING_POLICY
```

直接调用底层轮询时必须传入策略：

```python
from common import PollingPolicy

policy = PollingPolicy(
    status_json_path="$.status",
    pending={"queued", "running", "processing"},
    success={"succeeded", "success", "completed"},
    failure={"failed", "cancelled", "canceled"},
    result_json_path="$.result.urls",
    error_json_path="$.error",
)

response = self.smoke_request.poll_get(
    "/v1/media/tasks/task_id",
    poll_interval=2,
    poll_timeout=900,
    polling_policy=policy,
)
```

状态语义：

- pending：继续等待。
- success：返回最终响应。
- failure：抛出 `PollingFailedError`。
- unknown：默认抛出 `PollingUnknownStateError`，不要当成功处理。
- timeout：抛出 `PollingTimeoutError`，异常中保留最后状态、最后响应和迁移序列。

如果只是媒体生成，不要在用例中自定义策略；直接使用 `create_and_poll_media_generation()` 或 `poll_media_generation_result()`，让 `BaseTask` 自动传入默认状态机。

## 10. 测试上下文与变量传递

跨步骤变量使用 `test_context` fixture。该上下文是用例级隔离，不做跨用例共享。

```python
def test_query_usage_by_request_id(self, test_context):
    response = self.smoke_task.create_chat_completion(
        self.smoke_request,
        self.smoke_task.build_chat_completions_payload(),
    )

    test_context.extract(
        "request_id",
        response,
        header="x-oneapi-request-id",
        expected_type=str,
    )

    request_id = test_context.require("request_id", expected_type=str)
    usage_response = self.smoke_task.query_usage_records_for_billing(
        self.smoke_request,
        request_id=request_id,
    )

    self.smoke_assertions.assert_status_code(usage_response, 200)
```

支持的提取来源：

- `json_path`
- `header`
- `cookie`
- `regex`

常用方法：

```python
test_context.set("task_id", task_id)
test_context.get("task_id", default=None)
test_context.require("task_id", expected_type=str)
test_context.delete("task_id")
test_context.snapshot()
test_context.add_cleanup(cleanup_callback, arg1, name="value")
```

需要从多个候选来源提取时使用 `extract_first()`：

```python
test_context.extract_first(
    "request_id",
    response,
    sources=[
        {"header": "x-oneapi-request-id"},
        {"json_path": "$.request_id"},
    ],
    expected_type=str,
)
```

## 11. 配置与安全规范

全局环境配置由 `.env` 和 `config.py` 承接：

```text
USE_CHINA_ENVIRONMENT=TRUE

CHINA_TEST_ENVIRONMENT_BASE_URL=https://pre.juhemoxing.com
CHINA_API_KEY=your-china-api-key

OVERSEAS_TEST_BASE_URL=https://pre.tokensave.pro
OVERSEAS_API_KEY=your-overseas-api-key

API_TIMEOUT=600
GENERATE_ALLURE_REPORT=TRUE
GENERATE_HISTORY_REPORT=FALSE
HISTORY_REPORT_KEEP_LIMIT=30
```

环境开关：

```text
USE_CHINA_ENVIRONMENT=TRUE   # 国内环境
USE_CHINA_ENVIRONMENT=FALSE  # 海外环境
```

注意：

- 旧变量 `BASE_URL`、`API_KEY` 当前不会被 `config.py` 读取。
- 配置缺失、类型错误、非法 URL 会在执行前通过 `ConfigValidationError` 暴露。
- `Settings`、`RetryPolicy`、`PollingPolicy` 等结构化校验模型基于 Pydantic。
- 日志、异常、cURL 和 Allure 附件必须统一脱敏。
- B 账号、zero 账号、控制台账号等特殊账号不进入全局统一配置；只在明确用例、局部 fixture 或业务 helper 中按需读取。

## 12. 轻量 Mock 与故障模拟

框架基础能力单测优先使用 `tests/mock_helpers.py`，不要依赖真实接口。

常用工具：

```python
from tests.mock_helpers import (
    SequenceTransport,
    SleepRecorder,
    connection_error,
    make_response,
    polling_responses,
)
```

示例：模拟轮询状态迁移：

```python
responses = polling_responses(
    "https://example.test/v1/media/tasks/task-1",
    ["queued", "running", "succeeded"],
    result={"urls": ["https://example.test/result.png"]},
)
transport = SequenceTransport(responses)
```

适用场景：

- 连接失败、连接超时、读取超时。
- 429、500、502、503、504 等重试状态。
- 非法 JSON、字段类型错误、Schema 失败。
- SSE 中途断流、非法 chunk、缺少 `[DONE]`。
- 轮询 `queued -> running -> succeeded`、failed、cancelled、unknown、timeout。

Mock helper 用于框架核心分支单测，不替代真实环境 smoke 用例。

## 13. Allure 报告规范

pytest 默认输出 Allure 原始结果到：

```text
allure-results/
```

测试结束后，`module/conftest.py` 会按配置生成 HTML 报告：

```text
allure-report/
```

打开报告：

```powershell
node_modules\.bin\allure.cmd open allure-report
```

常见结构：

```text
同步图片任务调用：/v1/images/generations
  接口请求
  接口响应

文本模型对话调用：/v1/chat/completions
  接口请求
  接口响应

异步媒体任务创建：/v1/media/generations
  接口请求
  接口响应
轮询媒体生成结果: task_id
  轮询结果请求
  轮询结果响应
模型响应结果
```

说明：

- 业务层步骤由 `allure_step` 生成。
- 非轮询请求会记录 `接口请求`、`接口响应`。
- 轮询只保留最后一次请求和响应，并附加状态迁移记录。
- POST payload 存在 `input.media.url` 时，会出现 `前置资源`。
- 轮询成功后如有结果 URL，会下载并挂载到 `模型响应结果`。

如果报告没有生成，优先检查：

```powershell
npm install
java -version
```

以及 pytest 输出中是否出现：

```text
Allure HTML report generation failed
```

## 14. 执行与验收命令

当前仓库执行入口是 `run_master.py`。新增或修改用例后，优先只验证收集：

```powershell
.\.venv\Scripts\python.exe run_master.py module/smoke --collect-only -q
```

执行指定模块：

```powershell
.\.venv\Scripts\python.exe run_master.py module/smoke
```

执行指定文件：

```powershell
.\.venv\Scripts\python.exe run_master.py module/smoke/test_response_body_validation.py
```

并发执行：

```powershell
.\.venv\Scripts\python.exe run_master.py module/smoke -n 2
```

直接执行框架单测：

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
```

文档或用例结构变更的最低验收建议：

```powershell
.\.venv\Scripts\python.exe run_master.py module/smoke --collect-only -q
.\.venv\Scripts\python.exe -m pytest tests -q
```

当前 CI 尚未接入，本地命令结果是主要验收依据。

## 15. 新增用例检查清单

```text
文件名是否为 test_*.py
测试类是否以 Test 开头
测试方法是否以 test_ 开头
测试类是否没有 __init__
是否使用 setup_method 初始化 request/assertions/task
是否使用 teardown_method 关闭 request session
是否通过模块 Task 调用业务动作
是否没有在 test_*.py 中重复拼接通用接口路径
payload 是否为 Python 字典
true/false/null 是否改为 True/False/None
是否没有硬编码完整环境域名
是否没有硬编码 API Key 或控制台密钥
特殊账号是否限制在明确用例或局部 fixture 中
需要 Schema 时是否通过模块 Assertions 实例调用 assert_schema
是否没有直接从 common 导入或调用函数式断言
需要 JSONPath 时是否以 $ 开头
需要重试时是否显式传入 RetryPolicy
POST 重试是否满足幂等约束
需要轮询时是否使用 PollingPolicy 或 BaseTask 默认媒体策略
是否没有继续使用 success_json_path / failure_json_path
跨步骤变量是否使用 test_context
长耗时异步任务是否设置 poll_timeout
新增后是否执行 --collect-only -q
```
