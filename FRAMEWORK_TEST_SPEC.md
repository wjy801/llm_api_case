# 框架目录结构与测试用例编写规范

本文档描述当前已实现框架能力的测试规范、用例写法、本地验收口径和 CI 质量边界。当前已通过根目录 `Jenkinsfile` 接入 Jenkins 参数化流水线；本地命令仍作为提交代码和修改流水线前的基础验收手段，CI 构建结果作为持续集成阶段的质量判断依据。

## 目录结构

```text
D:\Code\Form\llm_api_case
├─ common/                    # 通用基础能力
│  ├─ base_request.py         # HTTP 请求、中间件、重试、轮询
│  ├─ base_assertions.py      # 状态码、JSONPath、JSON Schema 断言
│  ├─ base_decorators.py      # Allure step、模型结果附件
│  ├─ base_task.py            # 通用业务骨架：创建、轮询、账单/用量查询
│  ├─ request_context.py      # 单次请求上下文
│  ├─ request_middleware.py   # 请求中间件协议与默认中间件
│  ├─ retry.py                # RetryPolicy 与重试策略
│  ├─ polling.py              # PollingPolicy 与轮询状态机
│  ├─ test_context.py         # 用例级上下文与变量传递
│  └─ __init__.py
├─ util/                      # 工具能力：日志、脱敏、配置校验、cURL、媒体资源
├─ module/                    # 业务/模型用例目录
│  ├─ conftest.py             # pytest fixture 与 Allure 报告生命周期
│  ├─ image_model/
│  ├─ video_model/
│  ├─ smoke/
│  └─ protocol_testing/
│     ├─ request.py           # 当前模块请求类
│     ├─ assertions.py        # 当前模块断言类
│     ├─ decorators.py        # 当前模块装饰器拓展
│     ├─ task.py              # 当前模块业务封装，继承 BaseTask
│     ├─ test_*.py            # 测试用例
│     └─ __init__.py
├─ tests/                     # 框架基础能力单测与 mock helpers
├─ dev/                       # 阶段设计方案
├─ code_history/              # 阶段独立变更历史
├─ config.py                  # Settings 与环境配置校验
├─ master_service.py          # pytest nodeid 收集服务
├─ run_master.py              # 框架执行入口
├─ pytest.ini                 # pytest 收集与 Allure 配置
├─ requirements.txt           # Python 依赖
└─ package.json               # Allure CLI 依赖
```

以下目录为本地生成产物，不应提交到代码仓库：

```text
allure-results/
allure-report/
history_report/
data/
.pytest_cache/
__pycache__/
node_modules/
```

## 分层职责

`common/` 只放所有模块都可复用的公共基础能力。

`util/` 放无业务状态的工具能力，例如日志、脱敏、配置校验、cURL 生成、媒体资源下载。

`module/<模块名>/` 放当前模块自己的请求类、断言类、装饰器拓展、业务封装、payload builder 和真实用例。

`tests/` 放框架基础能力单测、离线 mock helper 和故障模拟测试。真实环境样例可以放在 `tests/` 中，但必须显式命名并避免被误认为纯离线单测。

## 模块继承规范

`module/` 下每个新测试模块建议固定包含：

```text
request.py
assertions.py
decorators.py
task.py
test_*.py
__init__.py
```

每个独立文件的类必须分别继承 `common` 中对应公共基类：

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

约束：

1. `request.py` 只封装当前模块私有路径或特殊请求方法。
2. `assertions.py` 只封装当前模块特有断言；通用断言优先使用 `BaseAssertions`。
3. `decorators.py` 只承接当前模块装饰器拓展。
4. `task.py` 负责当前模块业务动作组合，已沉淀到 `BaseTask` 的公共能力不要重复实现。
5. 不同模块的 `task.py` 不互相引用；如果能力可跨模块复用，应下沉到 `common/` 或 `util/`。

## 用例编写规范

1. 用例统一放在 `module/<模块名>/test_*.py`。
2. 文件名使用 `test_*.py`，测试类使用 `Test*`，测试方法使用 `test_*`。
3. 测试类不要定义 `__init__`，否则 pytest 不会收集该测试类。
4. 使用 `setup_method` 初始化 request、assertions、task 对象。
5. 使用 `teardown_method` 关闭 request session。
6. 用例中通过模块继承类调用公共能力，例如 `self.smoke_task.create_chat_completion(...)`。
7. `common/base_task.py` 只放公共业务骨架，不放真实业务 payload 数据。
8. 真实测试数据、模型 ID、payload builder 放在对应模块的 `task.py` 或用例中。
9. 当一次测试动作需要“发起请求并获取响应”作为一组业务操作时，应在 `task.py` 中封装该组动作，并使用 `allure_step` 写明业务步骤名。
10. payload 使用 Python 字典，不使用 YAML 或 Excel。
11. JSON 中的 `true/false/null` 应改为 Python 的 `True/False/None`。
12. 用例中不要硬编码完整环境域名和 API Key。
13. B 账号、zero 账号等特殊账号只允许在明确用例或局部 fixture 中使用，不进入全局统一配置。
14. 新增或修改用例后，先执行 `--collect-only -q` 确认可收集。

标准模板：

```python
from __future__ import annotations

from module.smoke import SmokeAssertions, SmokeRequest, SmokeTask


class TestDemo:
    def setup_method(self):
        self.smoke_request = SmokeRequest()
        self.smoke_assertions = SmokeAssertions()
        self.smoke_task = SmokeTask()

    def teardown_method(self):
        self.smoke_request.close()

    def test_chat_completion(self):
        payload = self.smoke_task.build_chat_completions_payload()

        response = self.smoke_task.create_chat_completion(
            self.smoke_request,
            payload,
        )

        self.smoke_assertions.assert_status_code(response, 200)
```

## Task 调用规范

同步图片生成：

```python
response = self.image_task.create_image_generation(
    self.image_request,
    payload,
)
```

文本对话补全：

```python
response = self.smoke_task.create_chat_completion(
    self.smoke_request,
    payload,
)
```

异步媒体任务创建：

```python
create_response = self.image_task.create_media_generation(
    self.image_request,
    payload,
)
```

异步媒体任务轮询：

```python
poll_response = self.image_task.poll_media_generation_result(
    self.image_request,
    task_id,
)
```

异步媒体完整流程统一使用：

```python
poll_response = self.image_task.create_and_poll_media_generation(
    self.image_request,
    payload,
)
```

账单用量查询应先产生一次模型调用响应，再通过继承自 `BaseTask` 的公共方法查询：

```python
chat_response = self.smoke_task.create_chat_completion(
    self.smoke_request,
    self.smoke_task.build_chat_completions_payload(),
)

usage_response = self.smoke_task.query_usage_records_for_billing(
    self.smoke_request,
    model_response=chat_response,
)
```

## 请求中间件规范

`BaseRequest` 已使用请求中间件管线承接请求生命周期：

```python
RequestMiddleware.before_request(context)
RequestMiddleware.after_response(context, response)
RequestMiddleware.on_exception(context, error)
```

测试与扩展要求：

1. 新增请求生命周期能力时优先新增中间件，不直接堆入 `BaseRequest.request()` 主流程。
2. 中间件不得修改调用方原始 payload；如需变更请求上下文，只修改 `RequestContext.kwargs`。
3. `before_request()` 和 `after_response()` 失败可以暴露中间件异常来源。
4. `on_exception()` 失败不能覆盖原始网络异常，只能作为附加诊断信息保留。
5. 请求日志、异常日志和 cURL 输出必须经过脱敏。
6. `middlewares=[]` 是合法配置，自定义管线不能破坏基本请求和轮询返回。

相关测试：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_request_middleware.py tests/test_base_request_middleware.py -q
```

## 配置校验与安全保护规范

`config.py` 只负责全局环境配置：

- `USE_CHINA_ENVIRONMENT`
- `CHINA_TEST_ENVIRONMENT_BASE_URL`
- `CHINA_API_KEY`
- `OVERSEAS_TEST_BASE_URL`
- `OVERSEAS_API_KEY`
- `API_TIMEOUT`
- `GENERATE_ALLURE_REPORT`
- `GENERATE_HISTORY_REPORT`
- `HISTORY_REPORT_KEEP_LIMIT`

约束：

1. 配置缺失或类型错误必须在执行前暴露为明确变量名错误。
2. 错误信息可以指出变量名，但不能输出 API Key 明文。
3. `BASE_URL`、`API_KEY` 等旧变量不作为当前配置来源。
4. 高风险账号或特殊账号不进入全局配置。
5. 日志、异常、cURL、Allure 附件共用 `util/redaction.py` 脱敏规则。
6. 配置、重试策略、轮询策略等校验边界优先使用 Pydantic 进行结构化校验，减少新增属性类和手写字段解析。

相关测试：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_config_validation.py tests/test_api_call_logger.py tests/test_curl_builder.py -q
```

## Pydantic 校验模型规范

当前以下校验边界已使用 Pydantic frozen model：

- `config.Settings`
- `common.retry.RetryPolicy`
- `common.retry.RetryAttemptRecord`
- `common.polling.PollingPolicy`
- `common.polling.PollingEvaluation`
- `common.polling.PollingTransition`

实现约束：

1. 新增面向配置、策略、状态机的结构化校验类时，优先使用 Pydantic `BaseModel`。
2. 运行期不应被修改的策略模型必须配置为 frozen。
3. 对外错误类型要保持业务语义，例如配置错误继续通过 `ConfigValidationError` 暴露。
4. 对外构造方式应保持兼容，不能因为迁移 Pydantic 破坏现有用例调用。
5. 纯内部记录结构、测试 helper、无校验价值的轻量对象不强制迁移。
6. Pydantic 的校验错误可以用于内部实现，但最终用户可见文案应保留明确变量名和字段名。

相关测试：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_config_validation.py tests/test_retry_policy.py tests/test_polling_state_machine.py -q
```

## 契约断言规范

通用断言优先使用 `BaseAssertions`：

```python
self.smoke_assertions.assert_status_code(response, 200)
self.smoke_assertions.assert_json_value(response, "$.id", expected_id)
self.smoke_assertions.assert_json_path_exists(response, "$.choices")
self.smoke_assertions.assert_schema(response, CHAT_COMPLETION_SUCCESS_SCHEMA)
```

JSON Schema 断言要求：

1. Schema 优先放在对应业务模块中，例如 `module/smoke/response_schemas.py`。
2. 成功响应和标准错误响应应分别维护 Schema。
3. 断言失败信息必须能定位到 JSONPath 和 Schema path。
4. 失败信息中的实际值必须脱敏。
5. 不稳定或业务尚未定型的字段不要过度约束。

相关测试：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_base_assertions_schema.py -q
```

## 重试策略规范

默认请求不自动重试。只有用例或业务封装显式传入 `RetryPolicy` 时才启用重试。

```python
from common import RetryPolicy

response = self.smoke_request.get(
    "/v1/models",
    retry_policy=RetryPolicy(max_attempts=3),
)
```

约束：

1. GET/HEAD 可默认允许重试。
2. POST 必须带幂等键或显式 `allow_post=True` 才允许重试。
3. 可重试状态码限制在 429 和明确配置的 5xx。
4. 连接异常、连接超时、读取超时可按策略重试。
5. `Retry-After`、退避、jitter 和最大总耗时必须可测试。
6. 每次重试记录应进入 Allure 附件。

相关测试：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_retry_policy.py tests/test_base_request_retry_polling.py -q
```

## 轮询状态机规范

轮询统一使用 `PollingPolicy`。`BaseRequest.poll_get()` 不再接收 `success_json_path` / `failure_json_path`：

```python
from common import PollingPolicy

policy = PollingPolicy(
    status_json_path="$.status",
    pending={"queued", "running"},
    success={"succeeded"},
    failure={"failed", "cancelled"},
)

response = client.poll_get(
    "/v1/media/tasks/task_id",
    poll_interval=2,
    poll_timeout=900,
    polling_policy=policy,
)
```

约束：

1. 必须区分 pending、success、failure、unknown、timeout。
2. 失败状态抛 `PollingFailedError`。
3. 未知状态抛 `PollingUnknownStateError`，不能当成功处理。
4. 超时抛 `PollingTimeoutError`，异常中保留最后状态、最后响应和迁移序列。
5. 成功和失败都要保留最后一次轮询日志。
6. 直接调用 `BaseRequest.poll_get()` 必须显式传入 `polling_policy`。
7. 媒体生成类任务默认使用 `DEFAULT_MEDIA_POLLING_POLICY`，业务用例优先通过 `BaseTask` 调用。

相关测试：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_polling_state_machine.py tests/test_base_request_retry_polling.py -q
```

## 测试上下文规范

用例需要跨步骤传递变量时使用 `test_context` fixture：

```python
def test_request_chain(self, test_context):
    response = self.smoke_task.create_chat_completion(self.smoke_request, payload)
    test_context.extract("request_id", response, header="x-oneapi-request-id")
    request_id = test_context.require("request_id", expected_type=str)
```

支持来源：

- JSONPath
- Header
- Cookie
- Regex

约束：

1. 第一版只使用用例级上下文，不做跨用例共享。
2. 不为了变量传递引入 YAML、Excel 或隐式 DSL。
3. 变量不存在、类型不匹配、提取失败必须给出明确错误。
4. 错误摘要必须脱敏。
5. 需要清理资源时使用 `add_cleanup()`，用例结束后由 fixture 调用 `cleanup()`。

相关测试：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_test_context.py -q
```

## 轻量 Mock 与故障模拟规范

框架核心能力单测优先使用 `tests/mock_helpers.py` 的离线工具，不依赖真实接口：

- `make_response()`
- `SequenceTransport`
- `SleepRecorder`
- `FakeApiCallLogger`
- `FakeStreamResponse`
- `polling_responses()`
- timeout/connection exception factories

覆盖场景：

1. 连接失败、连接超时、读取超时。
2. 429、500、502、503、504。
3. 非法 JSON 和字段类型错误。
4. SSE 中途断流、非法 chunk、缺少 `[DONE]`。
5. 轮询 `queued -> running -> succeeded`。
6. 轮询进入 failed、cancelled 或 unknown。
7. 重试等待时间、重试次数和最终异常类型。

相关测试：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_mock_helpers.py tests/test_stream_fault_simulation.py -q
```

## Allure 步骤规范

业务层步骤由 `common/base_decorators.py` 中的 `allure_step` 装饰器生成，步骤标题直接写入装饰器。

推荐结构：

```text
POST /v1/images/generations
  接口请求
  接口响应

POST /v1/media/generations
  接口请求
  接口响应
轮询媒体生成结果: task_id
  轮询结果请求
  轮询结果响应
模型响应结果
```

约束：

1. 真实业务用例中，非轮询 HTTP 日志应被明确业务步骤包裹。
2. 直接测试 `BaseRequest` 行为的框架单测可以直接调用 request 方法。
3. 当 POST payload 中存在 `input.media.url` 时，框架会在 Allure 中额外挂载 `前置资源` 步骤。
4. 请求头、请求体、响应体、异常、cURL、重试记录和轮询迁移记录都必须脱敏。

## 执行命令

只收集用例，不执行接口：

```powershell
.\.venv\Scripts\python.exe run_master.py module/smoke --collect-only -q
```

执行指定模块：

```powershell
.\.venv\Scripts\python.exe run_master.py module/smoke
```

执行全部框架单测：

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
```

执行指定框架能力单测：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_base_task.py -q
```

打开 Allure 报告：

```powershell
node_modules\.bin\allure.cmd open allure-report
```

## CI 边界

当前已通过根目录 `Jenkinsfile` 接入 Jenkins 参数化流水线，支持：

- 执行 `tests/` 下的框架测试。
- 只收集 `module/smoke` 用例，不调用真实接口。
- 按参数选择是否执行真实 Smoke 用例。
- 使用 pytest-xdist 执行普通并发用例，并在并发池结束后串行执行 `serial` 用例。
- 发布 JUnit 和 Allure 报告，归档测试产物，并按构建状态发送邮件通知。

当前已实现的基础质量阻断：

- 框架测试失败会使构建失败。
- Smoke 收集失败会使构建失败。
- 真实 Smoke 返回非零退出码会使构建失败。
- Jenkins 超时和节点执行异常会反映到构建状态。

当前尚未实现，不能写成已具备能力：

- 基于通过率阈值的质量门禁。
- 状态码分布、接口耗时 P95/P99 和长期趋势门禁。
- 重试率、轮询超时率等稳定性指标聚合与门禁。
- SSE 首 token 延迟、chunk 间隔和总耗时指标门禁。
- 异步任务排队、执行和完成耗时指标门禁。
- Flaky 用例自动识别、隔离和历史治理。
- 可被 CI 读取的统一 JSON 汇总与跨构建可视化看板。

提交代码或修改 CI 配置前，仍建议先执行本地验收：

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
.\.venv\Scripts\python.exe run_master.py module/smoke --collect-only -q
```

## 新增用例检查清单

```text
文件名是否为 test_*.py
测试类是否以 Test 开头
测试方法是否以 test_ 开头
测试类是否没有 __init__
是否使用 setup_method 初始化对象
是否使用 teardown_method 关闭 request session
新测试模块的 request/assertions/decorators/task 文件是否分别继承 common 公共基类
payload 是否为 Python 字典
true/false/null 是否改为 True/False/None
是否没有硬编码 API Key
是否没有硬编码完整环境域名
特殊账号是否限制在明确用例或局部 fixture 中
公共能力是否通过模块 Task 继承调用
成组请求是否已在 task.py 中封装并写明 Allure 业务步骤名
是否没有在模块 task.py 重复实现 BaseTask 已具备的方法
长耗时任务是否设置 poll_timeout
需要状态语义时是否使用 PollingPolicy
需要重试时是否显式传入 RetryPolicy
跨步骤变量是否使用 test_context
新增后是否执行 --collect-only -q
```
