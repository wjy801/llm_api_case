# 框架测试用例编写指南

## 1. 文档目标

本文档规定如何在当前 API 测试框架中新增、组织和验收真实业务用例。目标是保证新用例同时具备：

- pytest 可收集、可独立执行。
- 通过 Request/Task/Assertions 分层复用框架能力。
- 自动生成 Allure、JUnit、P0 和 P1 质量证据。
- 能参与并发池、串行池和 Flaky 历史比较。
- 不泄露 API Key、账号、请求和响应中的敏感信息。

框架坚持代码式用例，不引入 YAML、Excel 或隐式 DSL。测试数据使用 Python 类型表达。

## 2. 用例执行与质量数据链路

```text
test_*.py
-> 模块 Task 组织业务动作
-> 模块 Request 发起 HTTP/SSE/轮询请求
-> BaseRequest 中间件记录脱敏日志、cURL、重试和轮询
-> pytest 产生用例结果和 Allure/JUnit
-> run_master.py 归并 P0 Case/请求/失败事实
-> P1 聚合逻辑调用、耗时、usage 和 Flaky 状态
```

新用例不需要直接调用 P0/P1 聚合器。只要通过标准入口执行并遵循本文质量语义规范，框架会自动采集。

## 3. 分层职责

| 层级 | 目录/文件 | 职责 | 禁止事项 |
| --- | --- | --- | --- |
| 公共请求层 | `common/base_request.py` | HTTP、重试、轮询、中间件、质量请求语义 | 放业务路径、模型 ID、真实 payload |
| 公共业务层 | `common/base_task.py` | 跨模块通用的模型创建、轮询、账单/usage 骨架 | 放单一模块专用逻辑 |
| 公共断言层 | `common/base_assertions.py` | 状态码、JSONPath、JSON Schema | 放具体业务字段规则 |
| 工具层 | `util/` | 脱敏、日志、cURL、配置校验、媒体附件 | 持有业务状态 |
| 模块请求层 | `module/<模块>/request.py` | 模块路径、请求参数、质量角色 | 写业务流程和复杂断言 |
| 模块任务层 | `module/<模块>/task.py` | payload builder、业务动作组合 | 重复实现 BaseTask 已有能力 |
| 模块断言层 | `module/<模块>/assertions.py` | 模块专用断言和字段解析 | 发请求、修改共享状态 |
| 用例层 | `module/<模块>/test_*.py` | 场景编排和最终断言 | 硬编码域名、Key、复制底层请求代码 |
| 框架单测 | `tests/` | 离线验证框架、质量模块和 Jenkinsfile | 默认执行真实付费接口 |

不同业务模块的 `task.py` 不互相引用。真正跨模块的能力下沉到 `common/` 或 `util/`，并增加 `tests/` 离线回归。

## 4. 新模块标准目录

### 4.1 最小必需结构

创建一个规范化新模块时，至少创建以下 6 个文件：

```text
module/
└─ example_model/
   ├─ __init__.py
   ├─ request.py
   ├─ assertions.py
   ├─ decorators.py
   ├─ task.py
   └─ test_generation.py
```

### 4.2 按需增加的文件

```text
module/example_model/
├─ response_schemas.py    # 响应字段较多时维护 JSON Schema
├─ payloads.py            # payload 很多且 task.py 过大时拆分
├─ conftest.py            # 只服务该模块的 fixture
└─ test_data.py           # 复用的 Python 测试数据；不要放密钥
```

规则：

- 模块目录和 Python 文件使用 `snake_case`。
- 测试文件必须匹配 `test_*.py`。
- 测试类以 `Test` 开头，测试方法以 `test_` 开头。
- 不要为单个常量创建目录或文件；达到职责拆分需要时再增加可选文件。
- `data/`、JSON、YAML、Excel 不作为默认用例数据入口。

## 5. 新模块完整文件模板

以下模板使用 `example_model` 作为示例。创建真实模块时统一替换模块名、类名、路径和业务字段。

### 5.1 `request.py`

```python
from __future__ import annotations

from typing import Any

import requests

from common import BaseRequest


class ExampleModelRequest(BaseRequest):
    generation_path = "/v1/example/generations"

    def create_generation(self, payload: dict[str, Any]) -> requests.Response:
        return self.post(
            self.generation_path,
            json=payload,
            _quality_operation_name="example_generation",
            _quality_traffic_role="workload",
        )
```

要求：

- 路径使用相对路径，环境域名由 `BaseRequest` 和 `config.py` 处理。
- `_quality_operation_name` 使用稳定的业务名称，不能包含 request ID、时间戳或随机数。
- 真实模型调用使用 `workload`；余额、usage、管理查询使用 `control`。
- payload 中的 `model` 会被框架自动提取为 P1 的 `model_id`。
- `_quality_*` 参数由框架消费，不会发送给 `requests`。
- 临时修改 Header 时必须在 `finally` 中恢复，优先复用 BaseTask 的控制接口方法。

### 5.2 `assertions.py`

```python
from __future__ import annotations

import requests

from common import BaseAssertions, allure_step


class ExampleModelAssertions(BaseAssertions):
    @allure_step("校验生成响应包含任务 ID")
    def assert_generation_id(self, response: requests.Response) -> requests.Response:
        self.assert_status_code(response, 200)
        self.assert_json_path_exists(response, "$.id")
        generation_id = response.json()["id"]
        assert isinstance(generation_id, str) and generation_id.strip(), (
            f"response.id should be a non-empty string, actual: {generation_id!r}"
        )
        return response
```

要求：

- 通用断言直接调用 `BaseAssertions`，不要重复实现。
- 模块断言应返回响应对象，便于链式使用。
- 错误信息说明字段路径、期望和实际值。
- 敏感字段不得原样拼接到错误信息；需要输出响应时确认已有脱敏边界。
- 金额使用 `Decimal(str(value))`，禁止用二进制 float 做精确账单比较。

### 5.3 `decorators.py`

```python
from __future__ import annotations

from common import BaseDecorators


class ExampleModelDecorators(BaseDecorators):
    pass
```

没有模块专用装饰器时保留空继承，维持模块导出结构一致。公共 Allure 能力优先使用 `common.allure_step`。

### 5.4 `task.py`

```python
from __future__ import annotations

from typing import Any

import requests

from common import BaseTask, allure_step
from module.example_model.request import ExampleModelRequest


DEFAULT_EXAMPLE_MODEL_ID = "example-model"


class ExampleModelTask(BaseTask):
    @allure_step("创建 Example 模型任务")
    def create_example_generation(
        self,
        request_client: ExampleModelRequest,
        payload: dict[str, Any],
    ) -> requests.Response:
        return request_client.create_generation(payload)

    @staticmethod
    def build_generation_payload(
        model_id: str = DEFAULT_EXAMPLE_MODEL_ID,
    ) -> dict[str, Any]:
        return {
            "model": model_id,
            "prompt": "用于接口自动化验证的最小提示词",
        }
```

要求：

- Task 表达业务动作，测试方法只负责场景编排和断言。
- payload builder 返回新字典，不能复用并修改模块级可变对象。
- 能使用 `BaseTask.create_chat_completion()`、`create_image_generation()`、`create_and_poll_media_generation()` 时不要重复封装底层请求。
- 一个 Task 方法内组合多次请求时，必须考虑逻辑调用语义，见“P1 质量语义规范”。

### 5.5 `response_schemas.py`（推荐）

```python
from __future__ import annotations


EXAMPLE_GENERATION_SUCCESS_SCHEMA = {
    "type": "object",
    "required": ["id", "status"],
    "properties": {
        "id": {"type": "string", "minLength": 1},
        "status": {"type": "string"},
    },
    "additionalProperties": True,
}
```

只约束稳定契约。业务仍在快速变化的字段不要过度限制；成功响应和标准错误响应分开维护。

### 5.6 `__init__.py`

```python
from __future__ import annotations

from module.example_model.assertions import ExampleModelAssertions
from module.example_model.decorators import ExampleModelDecorators
from module.example_model.request import ExampleModelRequest
from module.example_model.task import ExampleModelTask


__all__ = [
    "ExampleModelAssertions",
    "ExampleModelDecorators",
    "ExampleModelRequest",
    "ExampleModelTask",
]
```

测试优先从模块包导入，避免依赖内部文件路径。

### 5.7 `test_generation.py`

下例采用推荐的 `response_schemas.py`；如果接口暂时没有稳定契约，可以先移除 Schema 导入和 `assert_schema()`，但仍需保留状态码与关键业务字段断言。

```python
from __future__ import annotations

from module.example_model import (
    ExampleModelAssertions,
    ExampleModelRequest,
    ExampleModelTask,
)
from module.example_model.response_schemas import EXAMPLE_GENERATION_SUCCESS_SCHEMA


class TestExampleGeneration:
    def setup_method(self):
        self.example_request = ExampleModelRequest()
        self.example_assertions = ExampleModelAssertions()
        self.example_task = ExampleModelTask()

    def teardown_method(self):
        self.example_request.close()

    def test_create_generation_returns_valid_contract(self):
        payload = self.example_task.build_generation_payload()

        response = self.example_task.create_example_generation(
            self.example_request,
            payload,
        )

        self.example_assertions.assert_status_code(response, 200)
        self.example_assertions.assert_schema(
            response,
            EXAMPLE_GENERATION_SUCCESS_SCHEMA,
        )
        self.example_assertions.assert_generation_id(response)
```

测试类禁止定义 `__init__`，否则 pytest 不收集。`teardown_method` 必须关闭请求 Session；需要释放业务资源时使用 `test_context.add_cleanup()`。

## 6. 优先使用 BaseTask 已有能力

### 6.1 文本、同步图片和异步媒体

```python
chat_response = self.task.create_chat_completion(self.request, chat_payload)

image_response = self.task.create_image_generation(self.request, image_payload)

final_response = self.task.create_and_poll_media_generation(
    self.request,
    media_payload,
    poll_interval=2,
    poll_timeout=900,
)
```

这些公共方法已经提供：

- Allure 业务步骤。
- P1 逻辑调用类型、名称、角色和 model ID。
- 异步任务创建、task ID 提取和轮询组合。
- 轮询成功、失败、未知和超时状态记录。

不要在模块 Task 中复制这些流程。

### 6.2 账单和 usage

```python
before_balance = self.task.query_account_balance_for_billing(self.request)

model_response = self.task.create_chat_completion(self.request, payload)
usage_response = self.task.query_usage_records_by_model_response_for_billing(
    self.request,
    model_response,
)

after_balance = self.task.query_account_balance_after_settlement_for_billing(
    self.request,
)
```

规范：

- 模型调用前的余额立即查询。
- 模型调用后的余额使用 `query_account_balance_after_settlement_for_billing()`，默认等待 5 秒处理预扣款刷新。
- usage 必须通过模型响应的 request ID 查询，禁止按时间范围猜测归属。
- 并发调用时分别保存每个 request ID，分别查询 usage 后求和。
- 余额扣减与 usage 金额使用 `Decimal` 区间断言，当前容差为 `±0.01` 元。
- P1 当前不计算估算成本，也不做账本价格对账。
- 账单、余额和共享账号用例必须标记 `serial`。

失败调用也应等待结算后再验证余额未变化，避免延迟扣费造成假通过。

## 7. Request 与中间件使用规范

所有真实请求都应通过 `BaseRequest` 或其子类发起：

```python
response = self.request.get("/v1/models")
response = self.request.post("/v1/items", json=payload)
```

框架默认中间件负责：

- 请求/响应/异常日志。
- cURL 生成。
- Authorization、Key、Token 等敏感信息脱敏。
- POST 媒体 URL 前置资源附件。
- 重试和轮询诊断附件。

禁止：

- 在真实用例中直接使用全新 `requests.Session()` 绕过框架。
- 在用例中硬编码完整环境域名。
- 将 API Key 写入 payload、测试参数、文件名或 Allure 标题。
- 中间件修改调用方原始 payload。
- 自定义 `on_exception()` 覆盖原始网络异常。

## 8. 契约断言规范

通用断言：

```python
self.assertions.assert_status_code(response, 200)
self.assertions.assert_json_value(response, "$.status", "succeeded")
self.assertions.assert_json_path_exists(response, "$.data")
self.assertions.assert_schema(response, schema)
```

异步测试函数可使用 `async_assert_status_code()`、`async_assert_json_value()`、`async_assert_json_path_exists()` 和 `async_assert_schema()`；不要为了使用异步断言而把同步 `requests` 调用强行包装成 asyncio。

规则：

- 状态码、关键业务值和响应结构分层断言。
- JSON Schema 失败能定位 JSONPath 和 Schema path。
- 标准错误响应必须验证 `error.code`、`error.type` 等稳定契约。
- 负向用例返回预期 4xx/业务失败时，用例可以通过；P1 中该逻辑调用仍会如实记为失败。
- 不要用宽泛 `assert response.json()` 代替明确断言。

## 9. 重试规范

请求默认不重试。只有明确允许掩盖瞬时网络问题时才显式传入 `RetryPolicy`：

```python
from common import RetryPolicy

response = self.request.get(
    "/v1/models",
    retry_policy=RetryPolicy(max_attempts=3),
)
```

约束：

- GET/HEAD 默认允许按策略重试。
- POST 只有带幂等键或显式 `allow_post=True` 才可重试。
- 429、明确配置的 5xx、连接异常和超时可重试。
- SSL 错误、非法请求、断言失败不能用重试掩盖。
- 重试次数、等待、`Retry-After` 和最终结果会进入质量/Allure 证据。

POST 示例：

```python
policy = RetryPolicy(max_attempts=3)
response = self.request.post(
    "/v1/idempotent-operation",
    json=payload,
    headers={"Idempotency-Key": idempotency_key},
    retry_policy=policy,
)
```

## 10. 轮询状态机规范

直接调用 `poll_get()` 必须显式提供 `PollingPolicy`：

```python
from common import PollingPolicy

policy = PollingPolicy(
    status_json_path="$.status",
    pending={"queued", "running"},
    success={"succeeded"},
    failure={"failed", "cancelled"},
)

response = self.request.poll_get(
    f"/v1/tasks/{task_id}",
    poll_interval=2,
    poll_timeout=900,
    polling_policy=policy,
)
```

必须区分：

- pending：继续等待。
- success：返回最终响应。
- failure：抛 `PollingFailedError`。
- unknown：默认抛 `PollingUnknownStateError`。
- timeout：抛 `PollingTimeoutError`，保留最后状态和迁移序列。

媒体任务优先使用 `BaseTask.poll_media_generation_result()` 或 `create_and_poll_media_generation()`。

## 11. SSE/流式响应规范

使用框架的 `iter_sse_lines()`，并确保响应最终关闭：

```python
from common import iter_sse_lines

try:
    for line in iter_sse_lines(response):
        if not line:
            continue
        assert line.startswith("data:")
        if line == "data: [DONE]":
            break
finally:
    response.close()
```

流式 Request 应设置：

```python
def create_stream_chat_completion(self, payload):
    return self.post(
        "/v1/chat/completions",
        json=payload,
        stream=True,
        headers={"Accept": "text/event-stream"},
        _attach_log=False,
        _quality_operation_name="chat_completion_stream",
        _quality_traffic_role="workload",
    )
```

禁止把未关闭的流式响应留到 teardown 之后。非法 chunk、提前断流和缺少 `[DONE]` 应有明确断言。

## 12. 测试上下文与资源清理

同一个用例跨步骤传值时使用 `test_context`：

```python
def test_request_chain(self, test_context):
    response = self.task.create_example_generation(self.request, payload)
    test_context.extract("request_id", response, header="x-oneapi-request-id")
    request_id = test_context.require("request_id", expected_type=str)
```

支持 JSONPath、Header、Cookie 和 Regex。不要通过模块全局变量或用例执行顺序跨用例传值。

需要清理业务资源时：

```python
test_context.add_cleanup(self.task.delete_resource, self.request, resource_id)
```

清理失败会以明确的上下文异常暴露，敏感信息仍需脱敏。

## 13. 并发、串行与 ContextVar

默认未标记用例可进入并发池。以下场景必须使用 `serial`：

```python
import pytest

pytestmark = pytest.mark.serial
```

- 余额、账单、usage 对账。
- 共享账号、共享 Header 或共享全局资源。
- 依赖服务端延迟结算。
- 并发执行会互相影响断言结果。

用例内部使用 `ThreadPoolExecutor` 时，必须通过 `submit_with_context()` 提交任务：

```python
from concurrent.futures import ThreadPoolExecutor

from common import submit_with_context
from module.example_model import ExampleModelRequest, ExampleModelTask


def call_one(payload):
    request_client = ExampleModelRequest()
    try:
        return ExampleModelTask().create_example_generation(request_client, payload)
    finally:
        request_client.close()


with ThreadPoolExecutor(max_workers=3) as executor:
    futures = [
        submit_with_context(executor, call_one, payload)
        for payload in payloads
    ]
```

直接 `executor.submit()` 不会自动复制质量 ContextVar，可能造成 P0/P1 请求无法归属到当前用例。每个线程应创建并关闭自己的 Request Client，不要并发共享同一个 `requests.Session`。

## 14. P1 质量语义规范

### 14.1 单请求业务动作

模块 Request 使用：

```python
_quality_operation_name="stable_business_name"
_quality_traffic_role="workload"  # 或 control
```

### 14.2 多请求复合业务动作

当一个 Task 方法代表一个创建+轮询或多请求业务动作时，使用逻辑调用作用域：

```python
from quality.semantic_context import model_id_from_kwargs, operation_scope
from quality.semantic_models import OperationKind, TrafficRole


def create_and_poll_example(self, request_client, payload):
    with operation_scope(
        OperationKind.ASYNC_TASK,
        name="example_generation",
        role=TrafficRole.WORKLOAD,
        model_id=model_id_from_kwargs({"json": payload}),
    ):
        create_response = self.create_media_generation(request_client, payload)
        task_id = self.extract_task_id(create_response)
        return self.poll_media_generation_result(request_client, task_id)
```

优先复用已有 BaseTask 作用域，只有公共能力无法表达时才手动创建。

### 14.3 稳定标识要求

- operation name 使用稳定蛇形名称。
- workload 表示被测业务调用，control 表示余额、usage、管理查询。
- 不把 request ID、task ID、时间戳拼入 operation name。
- 负向业务调用保留真实失败语义，不为了提高成功率改写成 success。
- usage 缺失保持 missing/no_data，不能写成 0。

## 15. Flaky 可比较性规范

Flaky 身份依赖 case ID、参数、环境、执行画像和 epoch。为了维持历史可比较性：

- 不随意重命名测试文件、测试类和测试方法。
- 参数化 ID 使用稳定、可读且不含密钥的值。
- 同一 nodeid 不应在不同提交中代表完全不同业务语义。
- 用例语义或实现边界明确变化时，使用 `flaky-reset-epoch` 开启新周期。
- 单次失败或一次“失败变通过”只会进入观察/疑似状态，不直接确认 Flaky。
- `QUARANTINED` 不会自动跳过用例；隔离必须指定 owner、原因和到期时间。

## 16. 配置与安全规范

全局环境配置由 `.env` 和 `config.py` 管理：

```text
USE_CHINA_ENVIRONMENT
CHINA_TEST_ENVIRONMENT_BASE_URL
CHINA_API_KEY
CHINA_CONTROL_API_KEY
OVERSEAS_TEST_BASE_URL
OVERSEAS_API_KEY
OVERSEAS_CONTROL_API_KEY
API_TIMEOUT
```

要求：

- 不使用旧 `BASE_URL`、`API_KEY`。
- 特殊账号只在明确用例/局部配置中使用。
- `.env`、数据库、真实响应和密钥不提交仓库。
- payload 使用 `True/False/None`，不是 JSON 的 `true/false/null`。
- 新的配置/策略/状态机模型优先使用 Pydantic frozen model。
- 日志、异常、cURL、Allure 附件都经过统一脱敏。

## 17. Allure 步骤规范

Task 的业务方法使用 `allure_step`：

```python
from common import allure_step


@allure_step("创建 Example 模型任务")
def create_example_generation(self, request_client, payload):
    return request_client.create_generation(payload)
```

步骤标题：

- 表达业务动作，不只写 `request` 或 `step1`。
- 不包含 API Key、完整 prompt、账号余额和动态敏感值。
- 轮询步骤可以包含脱敏后的 task ID，但不要作为指标名称。

框架自动挂载请求、响应、异常、cURL、重试、轮询迁移和媒体结果附件，业务用例不要重复打印完整敏感响应。

## 18. 框架能力的离线测试

修改 `common/`、`quality/` 或 `util/` 时，必须在 `tests/` 增加离线回归，优先使用：

```text
tests/mock_helpers.py
make_response
SequenceTransport
SleepRecorder
FakeApiCallLogger
FakeStreamResponse
polling_responses
连接/超时异常工厂
```

框架单测不调用真实付费接口。需要测试文件内容、Jenkinsfile 或报告结构时使用结构测试和临时目录。

## 19. 本地执行与验收

### 19.1 收集检查

```powershell
.\.venv\Scripts\python.exe run_master.py `
  module/example_model `
  --collect-only -q
```

必须确认：

```text
用例总数正确
并发池/串行池划分正确
没有 PytestCollectionWarning
没有导入错误
```

### 19.2 执行指定模块

```powershell
.\.venv\Scripts\python.exe run_master.py module/example_model
```

### 19.3 并发优先、串行收尾

```powershell
.\.venv\Scripts\python.exe run_master.py module/example_model -n 2
```

### 19.4 框架回归

```powershell
.\.venv\Scripts\python.exe -m pytest tests/quality -q
.\.venv\Scripts\python.exe -m pytest tests -q
```

真实接口会产生调用费用；在未授权时只执行 collect-only 和离线框架测试。

## 20. 构建后报告使用

真实 Smoke 完成后按顺序查看：

1. `reports/quality/gate-report.md`：P0 数据完整性、用例结果、失败分类、5xx 和超时。
2. `reports/quality/p1-observation.md`：逻辑调用耗时、usage 覆盖和 Flaky 状态。
3. Allure：具体用例步骤、请求、响应和附件。
4. `flaky-evaluation.json`/CLI：需要人工治理时再查看完整状态。

注意：

- P0 是影子门禁，不覆盖 pytest/Jenkins 结果。
- 请求成功率不等于用例通过率；负向用例和轮询中间状态可能被记为失败请求。
- 逻辑调用失败数不等于测试失败数。
- P1 不估算成本，不建立性能基线，缺失值不按零计算。

## 21. 新增用例提交检查清单

```text
[ ] 模块包含 __init__.py/request.py/assertions.py/decorators.py/task.py/test_*.py
[ ] 模块类分别继承 BaseRequest/BaseAssertions/BaseDecorators/BaseTask
[ ] __init__.py 正确导出四个模块类
[ ] 测试类没有 __init__
[ ] setup_method 创建 Request/Assertions/Task
[ ] teardown_method 关闭 Request Session
[ ] 路径为相对路径，没有硬编码域名
[ ] 没有硬编码 API Key、账号和敏感数据
[ ] payload 使用 Python 类型并由 Task/payloads.py 构建
[ ] 优先复用 BaseTask，没有复制公共创建/轮询/账单逻辑
[ ] 模块 Request 设置稳定 operation name 和 workload/control 角色
[ ] 复合业务动作具有正确逻辑调用作用域
[ ] 线程池使用 submit_with_context
[ ] 共享状态、账单和延迟结算用例标记 serial
[ ] POST 重试具备幂等键或明确 allow_post
[ ] 轮询使用 PollingPolicy 并设置合理 poll_timeout
[ ] 流式响应在 finally 中关闭
[ ] 通用断言和 JSON Schema 已复用
[ ] 金额使用 Decimal，账单调用后等待结算查询
[ ] 用例 nodeid 和参数 ID 稳定，适合 Flaky 历史比较
[ ] collect-only 通过
[ ] 相关离线框架测试通过
[ ] P0/P1 报告没有完整性错误
```
