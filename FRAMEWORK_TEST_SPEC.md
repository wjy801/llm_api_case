# 框架测试用例编写指南

## 1. 文档目标

本文档规定如何在当前 API 测试框架中新增、组织和验收真实业务用例。目标是保证新用例同时具备：

- pytest 可收集、可独立执行。
- 通过 Request/Task/Assertions 分层复用框架能力。
- 按执行配置生成 Allure、JUnit、Pipeline Summary 和质量机器证据。
- 在执行画像一致且 Flaky 能力启用时参与历史比较。
- 不泄露 API Key、账号、请求和响应中的敏感信息。

框架坚持代码式用例，不引入 YAML、Excel 或隐式 DSL。测试数据使用 Python 类型表达。

静态测试输入统一在对应 `test_*.py` 文件开头、导入语句之后声明，例如模型 ID、提示词、媒体 URL、时长、分辨率、比例和功能开关。模块 `Task` 只保留抽象出的公共业务模板、字段映射和流程组合，并用简短注释说明模板目的及调用方责任，不保存某条用例专用的变量值。

该规则只适用于执行前已经确定的静态输入。request ID、task ID、asset ID 等运行时产生的数据仍通过局部变量、fixture 或 `TestContext` 传递，禁止改成模块全局共享变量。

## 2. 用例执行与质量数据链路

```text
test_*.py
-> 模块 Task 组织业务动作
-> 模块 Request 发起 HTTP/SSE/轮询请求
-> BaseRequest 中间件记录脱敏日志，并通过 Runtime Hooks 发出中性观察事件
-> Quality 开启时由 quality.runtime_adapter 映射为质量事实；关闭时使用 Noop
-> pytest 产生用例结果和 Allure/JUnit
-> run_master.py 入口委托 run_orchestration 归并 Case/请求/失败事实
-> Semantic、Metrics 和 Flaky 聚合逻辑调用、耗时、usage 与状态变化
-> pipeline-summary.md 提供唯一人工质量入口
```

新用例不需要直接调用质量聚合器。只要通过标准入口执行并遵循本文质量语义规范，框架会自动采集。

`run_master.py` 是用户和 Jenkins 共用的稳定入口；内部调度、环境恢复与各质量阶段分别位于 `run_orchestration/`。

## 3. 分层职责

| 层级 | 目录/文件 | 职责 | 禁止事项 |
| --- | --- | --- | --- |
| 公共请求层 | `common/base_request.py` | HTTP、重试、总 deadline、请求级 Header 和中间件 | 导入质量模型，放业务路径、模型 ID、真实 payload |
| 运行时观察层 | `common/runtime_hooks/` | 中性 metadata、RuntimeObserver、Noop、ContextVar 和生命周期 | 导入 `quality`，实现报告或聚合算法 |
| 兼容 Task 门面 | `common/base_task.py` | 保留现有方法、步骤和行为并委托领域能力 | 继续新增领域实现 |
| 领域能力层 | `common/task_capabilities/` | 可组合的媒体生成与账单能力 | 替代模块四件套、依赖具体业务模块 |
| 公共断言层 | `common/base_assertions.py` | 状态码、JSONPath、JSON Schema | 放具体业务字段规则 |
| 工具层 | `util/` | 脱敏、日志、cURL、配置校验、媒体附件 | 持有业务状态 |
| 质量适配层 | `quality/runtime_adapter.py` | 将 Runtime Hooks 映射到质量采集器 | 被业务用例直接调用 |
| 质量聚合层 | `quality/metrics/`、`quality/flaky_store/` | Metrics 聚合和 Flaky 状态存储 | 被业务用例直接调用或复制算法 |
| 可选扩展边界 | `quality/pytest_plugin.py`、`run_orchestration/quality_lifecycle.py` | 轻量插件入口、Noop/Enabled 生命周期和按开关加载 | 在关闭路径导入 Collector、Semantic、Metrics、Flaky |
| 执行编排层 | `run_master.py`、`run_orchestration/` | 稳定入口、pytest 调度、产物和质量阶段顺序 | 在业务用例中导入内部 stage |
| 报告数据源层 | `pipeline_reporting/sources.py`、`quality_sources.py` | 核心事实常驻、质量事实按需加载 | 用陈旧质量产物推断本轮已启用 |
| 模块请求层 | `module/<模块>/request.py` | 模块路径、请求参数、中性 runtime metadata | 写业务流程和复杂断言 |
| 模块任务层 | `module/<模块>/task.py` | 参数化公共 payload 模板、字段映射和业务动作组合；注释说明模板语义与调用方责任 | 保存模型 ID、提示词等用例专用变量；重复实现 BaseTask 已有能力 |
| 模块断言层 | `module/<模块>/assertions.py` | 模块专用断言和字段解析 | 发请求、修改共享状态 |
| 用例层 | `module/<模块>/test_*.py` | 文件开头声明静态测试变量，场景中显式传给 Task，并完成编排和最终断言 | 硬编码域名、Key、复制底层请求代码；用全局变量保存运行时 ID |
| 框架单测 | `tests/` | 离线验证框架、质量模块和 Jenkinsfile | 默认执行真实付费接口 |

不同业务模块的 `task.py` 不互相引用。真正跨模块的能力下沉到 `common/` 或 `util/`，并增加 `tests/` 离线回归。

依赖方向必须保持：

```text
module -> common
quality -> common.runtime_hooks
run_master -> run_orchestration
common -X-> quality
```

业务用例只能使用稳定公共入口。不得从 `quality.metrics.*`、`quality.flaky_store.*` 或 `run_orchestration.*` 导入内部 builder、repository、stage 和 writer；这些模块只服务框架实现与离线测试。

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

`request.py`、`assertions.py`、`decorators.py`、`task.py` 是强制四件套。四个文件必须分别定义继承 `BaseRequest`、`BaseAssertions`、`BaseDecorators`、`BaseTask` 的真实类；即使暂时只有空继承，也必须保留类身份、MRO、`__name__` 和稳定导入路径，不能用简单别名替代。

### 4.2 按需增加的文件

```text
module/example_model/
├─ response_schemas.py    # 响应字段较多时维护 JSON Schema
├─ payloads.py            # 公共参数化模板很多且 task.py 过大时拆分
└─ conftest.py            # 只服务该模块的 fixture
```

规则：

- 模块目录和 Python 文件使用 `snake_case`。
- 测试文件必须匹配 `test_*.py`。
- 测试类以 `Test` 开头，测试方法以 `test_` 开头。
- 不要为单个常量创建目录或文件；达到职责拆分需要时再增加可选文件。
- `data/`、JSON、YAML、Excel 不作为默认用例数据入口。
- 模型 ID、提示词、媒体 URL 和生成参数等静态变量统一声明在使用它们的 `test_*.py` 文件开头，不放入 `task.py` 或单独的 `test_data.py`。
- 多个测试文件确需共享同一稳定枚举时，先判断它是否属于公共领域合同；只有属于公共合同的符号才允许进入领域模块，单纯为复用测试数据不得下沉。

## 5. 新模块完整文件模板

以下模板使用 `example_model` 作为示例。创建真实模块时统一替换模块名、类名、路径和业务字段。

### 5.1 `request.py`

```python
from __future__ import annotations

from typing import Any

import requests

from common import BaseRequest
from common.runtime_hooks import (
    RuntimeOperationKind,
    RuntimeTrafficRole,
    runtime_metadata,
)


class ExampleModelRequest(BaseRequest):
    generation_path = "/v1/example/generations"

    def create_generation(self, payload: dict[str, Any]) -> requests.Response:
        return self.post(
            self.generation_path,
            json=payload,
            runtime_metadata=runtime_metadata(
                RuntimeOperationKind.HTTP,
                name="example_generation",
                role=RuntimeTrafficRole.WORKLOAD,
            ),
        )
```

要求：

- 路径使用相对路径，环境域名由 `BaseRequest` 和 `config.py` 处理。
- `runtime_metadata` 使用稳定的业务名称，不能包含 request ID、时间戳或随机数。
- 真实模型调用使用 `RuntimeTrafficRole.WORKLOAD`；余额、usage、管理查询使用 `RuntimeTrafficRole.CONTROL`。
- payload 中的 `model` 会被框架自动提取为 Metrics 的 `model_id`。
- `runtime_metadata` 由框架消费，不会发送给 `requests`；`_quality_*` 参数仅保留兼容解析，新代码不得使用。
- 单次 Header 通过请求参数传入，禁止为了协议或控制请求临时修改共享 `Session.headers`。

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
- `BaseAssertions` 同步方法是通用断言唯一实现源；异步方法、模块级函数和领域子类通过委托或继承复用，不复制算法。
- 模块 Assertions 必须保留真实类身份、MRO、`__name__` 和导入路径，不得改成简单别名。
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
        *,
        model_id: str,
        prompt: str,
    ) -> dict[str, Any]:
        # 公共模板只负责接口字段映射；具体测试值由 test_*.py 显式传入。
        return {
            "model": model_id,
            "prompt": prompt,
        }
```

要求：

- Task 表达抽象出的公共业务动作和参数化模板，测试方法负责提供静态变量、场景编排和断言。
- payload builder 返回新字典，不能复用并修改模块级可变对象；builder 参数应显式接收测试值，不在 Task 中定义某条用例的模型 ID、提示词、URL、时长或开关。
- 公共模板必须添加简短注释，说明模板负责的字段映射、稳定合同或调用方必须提供的值；注释解释设计意图，不逐行翻译代码。
- 现有媒体/账单流程继续复用 `BaseTask` 兼容入口或对应 task capability；新领域逻辑进入模块 Task，不再向 `BaseTask` 增加方法。
- 一个 Task 方法内组合多次请求时，必须考虑逻辑调用语义，见“质量语义规范”。
- 独立 CLI 调用本模块业务时，端点必须来自 Request，payload/流程/轮询必须来自 Task，响应规则必须来自 Assertions；CLI 只负责参数、展示和退出码。
- `--insecure` 等传输选项只能调整当前 Request Session，`--quiet` 等展示选项只能影响当前 CLI，不能修改框架全局状态。

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


EXAMPLE_MODEL_ID = "example-model"
EXAMPLE_PROMPT = "用于接口自动化验证的最小提示词"


class TestExampleGeneration:
    def setup_method(self):
        self.example_request = ExampleModelRequest()
        self.example_assertions = ExampleModelAssertions()
        self.example_task = ExampleModelTask()

    def teardown_method(self):
        self.example_request.close()

    def test_create_generation_returns_valid_contract(self):
        payload = self.example_task.build_generation_payload(
            model_id=EXAMPLE_MODEL_ID,
            prompt=EXAMPLE_PROMPT,
        )

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

测试类禁止定义 `__init__`，否则 pytest 不收集。Request Session 必须有唯一且可验证的所有者：使用 `setup_method` 创建时由 `teardown_method` 关闭；使用 fixture 创建时由 fixture 的 `yield` 收尾关闭。需要释放业务资源时使用 `test_context.add_cleanup()`。

示例中的 `EXAMPLE_MODEL_ID` 和 `EXAMPLE_PROMPT` 是静态测试输入，所以位于测试文件开头。响应中提取出的 request ID、task ID 等动态数据不按此方式声明，应继续使用局部变量或 `TestContext`。

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
- Metrics 逻辑调用类型、名称、角色和 model ID。
- 异步任务创建、task ID 提取和轮询组合。
- 轮询成功、失败、未知和超时状态记录。

不要在模块 Task 中复制这些流程。

### 6.2 账单和 usage

```python
model_response = self.task.create_chat_completion(self.request, payload)
request_id = self.task.get_request_id_from_response(model_response)
usage_response = self.task.query_usage_records_by_request_id_for_billing(
    self.request,
    request_id,
    retry_policy=SMOKE_GET_RETRY_POLICY,
)
self.assertions.assert_successful_usage_record(
    usage_response,
    expected_request_id=request_id,
)
```

规范：

- usage 必须通过模型响应的 request ID 查询，禁止按时间范围或共享钱包余额猜测归属。
- 成功调用校验 request ID、终态和正数 `quota_yuan`；失败调用校验自身 `quota_yuan == 0`。
- 并发调用时分别保存每个 request ID，并逐条验证对应 usage，不使用账户余额差汇总。
- usage GET 使用统一的有限重试和结算轮询；模型 POST 默认不重试，避免重复扣费。
- Metrics 当前不计算估算成本，也不做账本价格对账。
- 账户余额和其他共享状态用例必须标记 `serial`。

共享钱包只能用于余额可用性检查，不能作为单个请求是否扣费的归因证据。

## 7. Request 与中间件使用规范

所有真实请求都应通过 `BaseRequest` 或其子类发起：

```python
response = self.request.get("/v1/models")
response = self.request.post("/v1/items", json=payload)
```

框架默认中间件负责：

- 通过 `RuntimeObservationMiddleware` 观察请求开始、成功和异常；Quality 关闭时自动为空操作。
- POST 媒体 URL 前置资源附件。
- Authorization、Key、Token 等敏感信息脱敏。
- 请求/响应/异常日志和 cURL 生成。

默认注册与执行顺序固定为：

```text
RuntimeObservationMiddleware
-> MediaResourceMiddleware
-> RedactionMiddleware
-> LoggingMiddleware
```

重试和轮询诊断附件由请求执行链统一挂载。显式传入自定义中间件时按传入顺序执行；传入空列表表示禁用默认中间件。

输入媒体和输出结果捕获由 `CapturePolicy` 分别控制。关闭策略后不得访问外部媒体 URL；捕获、附件或观察失败均不得覆盖业务响应和原始异常。

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
- 负向用例返回预期 4xx/业务失败时，用例可以通过；Metrics 中该逻辑调用仍会如实记为失败。
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

`poll_timeout` 是 HTTP attempt、Retry backoff/`Retry-After` 和 poll sleep 共用的总 deadline。每次 transport timeout 必须截断到剩余预算，预算耗尽后不得再发下一次请求。

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
from common.runtime_hooks import (
    RuntimeOperationKind,
    RuntimeTrafficRole,
    runtime_metadata,
)


def create_stream_chat_completion(self, payload):
    return self.post(
        "/v1/chat/completions",
        json=payload,
        stream=True,
        headers={"Accept": "text/event-stream"},
        _attach_log=False,
        runtime_metadata=runtime_metadata(
            RuntimeOperationKind.SSE,
            name="chat_completion_stream",
            role=RuntimeTrafficRole.WORKLOAD,
        ),
    )
```

`_attach_log=False` 用于避免响应日志提前消费流；流式用例改为记录状态码、Header、终态和必要的脱敏片段。禁止把未关闭的流式响应留到 teardown 之后。非法 chunk、提前断流和缺少 `[DONE]` 应有明确断言。

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

- 共享余额及其他账户级状态校验。
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

直接 `executor.submit()` 不会自动复制 pytest run/case、operation 和 Runtime Hooks ContextVar，可能造成质量请求无法归属到当前用例。每个线程应创建并关闭自己的 Request Client，不要并发共享同一个 `requests.Session`。

## 14. 质量语义规范

### 14.1 单请求业务动作

模块 Request 使用中性 metadata：

```python
from common.runtime_hooks import (
    RuntimeOperationKind,
    RuntimeTrafficRole,
    runtime_metadata,
)


runtime_metadata=runtime_metadata(
    RuntimeOperationKind.HTTP,
    name="stable_business_name",
    role=RuntimeTrafficRole.WORKLOAD,  # 控制流量使用 CONTROL
)
```

`_quality_operation_name`、`_quality_traffic_role` 仅作为兼容输入被映射；新用例统一使用中性 `runtime_metadata`。

### 14.2 多请求复合业务动作

当一个 Task 方法代表一个创建+轮询或多请求业务动作时，使用逻辑调用作用域：

```python
from common.runtime_hooks import (
    RuntimeOperationKind,
    RuntimeTrafficRole,
    model_id_from_kwargs,
    operation_scope,
)


def create_and_poll_example(self, request_client, payload):
    with operation_scope(
        RuntimeOperationKind.ASYNC_TASK,
        name="example_generation",
        role=RuntimeTrafficRole.WORKLOAD,
        model_id=model_id_from_kwargs({"json": payload}),
    ):
        create_response = self.create_media_generation(request_client, payload)
        task_id = self.extract_task_id(create_response)
        return self.poll_media_generation_result(request_client, task_id)
```

优先复用已有 BaseTask 作用域，只有公共能力无法表达时才手动创建。业务模块不得重新导入 `quality.semantic_context` 或 `quality.semantic_models`；逻辑调用生命周期必须停留在中性的 `common.runtime_hooks` 边界。

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

- `BASE_URL`、`API_KEY` 不是有效配置变量。
- 特殊账号只在明确用例/局部配置中使用。
- `.env`、数据库、真实响应和密钥不提交仓库。
- payload 使用 `True/False/None`，不是 JSON 的 `true/false/null`。
- 新的配置/策略/状态机模型优先使用 Pydantic frozen model。
- 日志、异常、cURL、Allure 附件都经过统一脱敏。
- 环境选择、布尔/正数解析、当前环境 URL/Key 校验和错误聚合只在 `util.config_validation.validate_settings_values()` 编排；`config.py` 仅构造 frozen `Settings`。
- 新增配置规则时修改规范实现源，并为默认值、错误文本、错误顺序和导入时机增加离线测试，不在调用方重复校验。

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

离线验证分为三层，选择能够暴露目标故障的最低成本层级。三层不得互相替代，也不得为了离线验证复制公共实现。

### 18.1 离线测试三层结构

| 层级 | 位置 | 证明内容 |
| --- | --- | --- |
| 单元级故障模拟 | `tests/` 中的 FakeResponse、SequenceTransport 等 | 单一算法分支、异常和日志语义 |
| loopback 基础设施门禁 | `tests/test_offline_service.py` | 协议、实例隔离、并发计数、线程回收和网络边界 |
| 业务分类与黄金路径 | `module/offline_framework_example/test_*.py` | 四件套、真实请求生命周期、能力分类和稳定成功组合 |

第一层不启动网络服务，修改纯算法、异常分支、日志或观察适配时优先使用：

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

第二层和第三层共享真实 HTTP 生命周期的 loopback 服务。需要验证 `requests.Session`、Request、Middleware、Retry、Polling、Capture 或 Runtime Hooks 的组合行为时，复用：

```text
module/offline_framework_example/offline_service.py  # 127.0.0.1 随机端口服务
module/offline_framework_example/conftest.py         # 服务、Request 与观察 fixture
tests/test_offline_service.py                        # 协议、隔离、并发和线程回收门禁
module/offline_framework_example/test_*.py           # 四件套业务分类用例
```

截至 2026-08-06，离线服务冻结 9 个确定性场景，`tests/test_offline_service.py` 包含 18 项基础设施门禁；业务模块包含 23 条用例，分布为 `3/4/4/4/4/3/1`，Runner 计划为 23 项并发、0 项串行。该数量是当前快照，长期合同是最终计划等于并发池与串行池的互斥并集。

### 18.2 分类与黄金路径职责

| 文件 | 允许职责 | 不应继续加入 |
| --- | --- | --- |
| `test_request_pipeline.py` | Request、Middleware、payload 和 Header 不变性 | Retry、Polling 终态 |
| `test_retry.py` | 重试资格、等待和挽救 | Polling 业务状态 |
| `test_polling.py` | 状态转换、失败、未知和总 deadline | Context、Capture |
| `test_context_cleanup.py` | 提取、转换、LIFO 清理和错误归并 | 网络重试 |
| `test_capture_assertions.py` | Artifact 边界、Schema 路径和脱敏诊断 | 并发所有权 |
| `test_concurrency_context.py` | ContextVar、Session 和请求级 Header 隔离 | 黄金业务全链 |
| `test_full_framework_flow.py` | 稳定成功主链 | 互斥异常和全部边界排列 |

判断顺序：

```text
某一能力失效时能否精确定位 -> 分类用例
多个已验证能力在稳定成功主链中能否组合 -> 黄金路径
场景与黄金成功主链互斥 -> 保留在分类用例
```

黄金路径不得吸收 Polling failure、unknown、timeout、非幂等 POST 禁止重试、Capture 超限、cleanup 多错误和 Header 泄漏反例。当前稳定 nodeid 为：

```text
module/offline_framework_example/test_full_framework_flow.py::TestFullFrameworkFlow::test_offline_async_media_flow
```

### 18.3 确定性本地服务要求

- 服务只绑定 `127.0.0.1` 随机端口，客户端禁止读取系统代理；
- 请求守卫拒绝合同外目标，状态按服务实例隔离；
- 使用锁、计数器和 Event 协调时序，不使用随机 sleep 制造先后关系；
- fixture 负责服务启动与关闭，Request、Session、线程和临时文件在 `finally` 或 teardown 回收；
- Retry 和 Polling 使用公共实现，资源 URL 由本轮服务生成并校验为 loopback；
- 不读取真实 `.env` 服务 URL 或密钥完成业务请求；
- 不复制 BaseRequest、Retry、Polling、Capture、Runner 或 Quality 实现。

框架单测和离线分类用例都不得调用真实付费接口。需要测试文件内容、Jenkinsfile 或报告结构时使用结构测试和临时目录。

### 18.4 Quality边界

业务分类只能通过 `common.runtime_hooks`、Request `runtime_metadata`、逻辑调用作用域和 pytest/Runner 公开入口产生中性事实。禁止业务示例：

- 导入 `quality.metrics.*` 内部 builder；
- 导入 `quality.flaky_store.*` 内部 repository 或 projection；
- 读取 Quality 内部数据库表断言业务行为；
- 为 Metrics 修改业务断言；
- 把 Quality fail-open 解释为 Quality 成功。

Quality、Metrics 和 Flaky 只在运行级验收中通过机器产物和公开 CLI 验证，不能替代分类用例的业务断言。

### 18.5 新增能力归类算法

1. 优先归入现有单一职责分类；
2. 无法归入时判断是否存在新的独立框架职责；
3. 只有职责独立且具有稳定合同才新增分类文件；
4. 先写分类用例，再判断黄金路径是否需要增加稳定成功步骤；
5. 不因黄金路径已存在就把新能力全部塞入；
6. 新增后同步更新数量快照、学习顺序和验收记录；
7. 复验 Runner 集合守恒、Quality 事实和完整框架回归。

框架改动必须按职责补充以下保护：

- 修改 `common/runtime_hooks/`：验证 Noop、Hook 故障 fail-open、线程 ContextVar 和 `common` 独立导入。
- 修改 `quality/metrics/`、`quality/flaky_store/`：验证公开契约、依赖方向、产物等价或数据库事务边界。
- 修改 `run_orchestration/`：验证根入口兼容、collect-only 无副作用、并串行顺序、退出码、环境恢复和质量阶段顺序。
- 修改 Quality 可选加载边界：验证 `quality.__all__` 对象身份、disabled 导入预算、Noop 零文件副作用、enabled pytest/xdist 等价和报告 `NOT_RUN/NO_DATA` 四象限。
- 修改下载/Allure 生命周期：验证 Capture 关闭零网络、多池 raw 隔离、最终一次合并/生成和自定义 `--alluredir`。
- 新增架构边界测试时必须确保文件进入 Git，不能只在本地未跟踪状态下通过。
- 架构测试不得断言 Python 文件集合与白名单完全相等，也不得冻结完整模块 DAG；应验证公共行为、禁止反向依赖和单一所有权。

### 18.6 唯一执行事实规范

Runner 必须先完成一次权威 pytest 收集，再执行最终 nodeid 计划：

```text
target / -k / -m / --ignore
-> 权威收集得到 nodeid + marker
-> scheduling 纯算法分为 parallel / serial
-> 执行池只消费 nodeid，不再次解释选择条件
```

必须满足：

- 最终计划等于并行池与串行池的互斥并集；
- 每个 nodeid 最多执行一次；
- `expected_case_count` 取自最终计划，不取自控制台文本；
- 权威空集合返回 pytest exit 5；
- 在 `reports/execution-result.json` 原子写入成功的前提下，单池 Runner 最终退出码等于 pytest 原始退出码；
- 多池只有所有已执行池都为 0 时才返回 0；
- exit 1 可以继续后续池收集失败证据；exit 2/3/4/5 或池执行异常必须停止所有后续执行池；
- 执行事实写入失败时，Runner 保留终止型原始码 2/3/4/5，其他情况返回 1；
- `reports/execution-result.json` 只记录权威计划、池级原始退出事实和最终退出码，不推导 Jenkins 最终状态；
- Quality、Metrics、Flaky、JUnit 和 Allure 不得改写池级 pytest 原始退出码。

### 18.7 Allure 单一生命周期规范

Runner 与直接 pytest 共用 `run_orchestration/allure_lifecycle.py`：

```text
Runner：每池独立临时 raw -> 合并最终 alluredir -> HTML/history 各生成一次
直接 pytest：session start/finish -> 委托同一生命周期
collect-only：不清理、不创建、不生成制品
```

必须保持默认 `allure-results/`、`allure-report/`、`history_report/` 和自定义 `--alluredir`；Allure 清理、合并或 CLI 失败采用 fail-open，不改写 pytest 原始退出码。`module/conftest.py` 只负责 Hook 适配，不复制文件和 subprocess 实现。

### 18.8 可选 Quality 加载规范

`quality/__init__.py` 必须保留全部公共名称、顺序和对象身份，并通过静态映射按首次访问加载定义模块。`quality.pytest_plugin` 保持稳定注册路径，但关闭或 collect-only 时不得加载 `pytest_plugin_runtime`。

Runner 只能依赖中性的 `QualityRunLifecycle` 和 `RunLifecycleStatus`：关闭时工厂返回 Noop，不创建 run_id、质量目录或质量产物；JUnit 与 Allure 仍按测试执行配置生成，不受 Quality 开关影响。开启时才局部加载 environment、run record 和 Quality Pipeline。`pytest_execution.py` 唯一拥有统一预收集和各池执行，可选扩展不得形成第二个 pytest 生命周期所有者。

Reporting 核心 Source 不得顶层导入 Quality 实现。只有接口测试与 `QUALITY_ENABLE` 同时开启时才加载 `pipeline_reporting/quality_sources.py`；未开启显示 `NOT_RUN`，即使目录中存在陈旧质量产物也不得读取；已开启但本轮产物缺失、损坏、Hash/Schema/版本不可信时显示 `NO_DATA`。两种状态都不得覆盖 pytest 或 Jenkins 事实。

报告事实优先级：

```text
pytest 原始退出码：测试进程事实
Jenkins 显式阶段状态：流水线事实
JUnit：统计和失败详情
Quality / Metrics / Flaky：诊断观察
Pipeline Conclusion：关注等级
```

显式 FAILED/BLOCKED 不得被可解析、全绿或陈旧 JUnit 覆盖。Python Reporting 只解析一次 JUnit，并从同一个 `PipelineReport` 生成 Markdown、机器摘要和邮件内容。

### 18.9 Artifact 信任边界规范

`util/artifact_io.py` 只拥有 UTF-8 JSON/JSONL 读取、原始文件字节 SHA256 和纯字段比较。Metrics、Flaky、Reporting、Aggregator 可复用这些原语，但必须由各自消费者翻译领域错误：

- Metrics 保持既有 `MetricsSourceError` code 和关系校验；
- Flaky 保持既有 `FlakyImportError` code、可导入规则和数据库边界；
- Reporting 保持中文 warning、局部降级及 `NOT_RUN/NO_DATA`；
- Schema、Manifest、版本、状态机和规范 JSON 内容 Hash 不得迁入通用 I/O 原语。

文件 Hash 始终按原始 bytes 计算，不能解析 JSON 后重新序列化。迁移 Artifact 读取实现时必须用相同夹具验证 Hash、错误码、warning 和产物格式完全等价。

## 19. 本地执行与验收

离线用例只访问 `127.0.0.1`，但导入 `config.py` 时仍会校验环境变量。首次运行先复制语法合法的模板；离线 Request 不会使用其中的真实 URL 或 Key：

```powershell
if (-not (Test-Path -LiteralPath '.env')) {
  Copy-Item -LiteralPath '.env.example' -Destination '.env'
}
```

### 19.1 确定性离线门禁

先验证本地服务协议和资源回收：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_offline_service.py -q
```

再验证离线业务分类的收集、分池和 Runner 执行：

```powershell
.\.venv\Scripts\python.exe run_master.py module/offline_framework_example --collect-only -q
.\.venv\Scripts\python.exe run_master.py module/offline_framework_example -n 2
```

截至 2026-08-06，当前收集事实为 23 项并发、0 项串行，黄金 nodeid 只出现一次。该数量用于发现意外丢失，不是后续新增分类时不可改变的永久合同；长期门禁是集合守恒和职责边界不退化。

### 19.2 新模块收集检查

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
没有生成 Quality run_id 或质量产物
没有启动正式执行池或调用真实接口
```

### 19.3 执行指定模块

```powershell
.\.venv\Scripts\python.exe run_master.py module/example_model
```

### 19.4 并发优先、串行收尾

```powershell
.\.venv\Scripts\python.exe run_master.py module/example_model -n 2
```

### 19.5 框架回归

```powershell
.\.venv\Scripts\python.exe -m pytest tests/quality -q
.\.venv\Scripts\python.exe -m pytest tests -q
```

当前 `tests/` 收集基线为 686 项；`module/smoke` collect-only 快照为 40 项（并发池 15、串行池 25）。数量用于发现意外丢失，不替代“最终计划等于并发池与串行池互斥并集”的集合守恒合同。

不带目标执行 `run_master.py` 会收集 `module/` 下全部业务用例，其中包含真实接口、付费调用和共享状态场景。未明确执行真实业务回归时，只运行 collect-only、`tests/` 和 `module/offline_framework_example`。

## 20. 构建后报告使用

每轮 Jenkins 构建按下列顺序查看；若流水线摘要关闭，则从 JUnit 开始：

1. `reports/pipeline-summary.md`：本轮参数、阶段状态、用例结果和直接观测结论。
2. JUnit：用例总数、通过、失败、错误和跳过。
3. Allure：具体用例步骤、请求、响应和附件。
4. `reports/quality/run.json`、`reports/quality/merged/manifest.json` 和 `reports/quality/merged/*.jsonl`：核对运行身份、归并完整性及 Case/请求/失败事实。
5. `reports/quality/semantic/merged/manifest.json`、`reports/quality/semantic/merged/*.jsonl`、`reports/quality/metrics/manifest.json` 和 `reports/quality/metrics/run-metrics.json`：核对逻辑调用语义、指标来源和完整数据。
6. 启用 Flaky 历史与状态机后，使用 `reports/quality/flaky-import.json`、`reports/quality/flaky-evaluation.json` 和 CLI 核对样本导入、状态迁移及治理信息。

`pipeline-summary.md` 适用于框架测试、用例收集、接口测试及其组合。报告不暴露 Smoke 专属参数名，未选择的阶段显示“未执行”，不能解释为失败或数据缺失。其生成由 `GENERATE_PIPELINE_SUMMARY` 控制；质量数据源由 `QUALITY_ENABLE` 独立控制。两者均遵循 Jenkins 参数/进程环境优先于 `.env`，摘要默认开启、Quality 默认关闭。

`pipeline-summary.md` 是唯一人工质量报告。第 4～6 项均为机器证据，只用于来源审计和问题下钻；新用例或框架改动不得再创建并行的人工汇总报告，也不得把可选机器产物缺失解释为零值或测试失败。

`reports/execution-result.json` 和 `reports/pipeline-summary.json` 是机器传递证据：前者保存 Runner 原始执行事实，后者保证 Markdown 与邮件共享同一解析结果。它们不是新的人工报告入口。

注意：

- 请求成功率不等于用例通过率；负向用例和轮询中间状态可能被记为失败请求。
- 逻辑调用失败数不等于测试失败数。
- Metrics 不估算成本，不建立性能基线，缺失值不按零计算。
- Pipeline Summary 不覆盖 pytest/Jenkins 原始结果。

## 21. 新增用例提交检查清单

```text
[ ] 模块包含 __init__.py/request.py/assertions.py/decorators.py/task.py/test_*.py
[ ] 模块类分别继承 BaseRequest/BaseAssertions/BaseDecorators/BaseTask
[ ] 四件套均为真实类，没有使用会改变类身份的简单别名
[ ] __init__.py 正确导出四个模块类
[ ] 测试类没有 __init__
[ ] Request/Assertions/Task 由 setup_method 或 fixture 明确创建
[ ] Request Session 由 teardown_method 或 fixture yield 收尾关闭
[ ] 路径为相对路径，没有硬编码域名
[ ] 没有硬编码 API Key、账号和敏感数据
[ ] payload 使用 Python 类型并由 Task/payloads.py 的参数化公共模板构建
[ ] 模型 ID、提示词、媒体 URL、时长、分辨率、比例和开关等静态测试变量统一声明在 test_*.py 文件开头
[ ] Task/payloads.py 没有保存某条用例专用变量，公共模板包含说明设计意图和调用方责任的简短注释
[ ] request ID、task ID、asset ID 等运行时数据使用局部变量、fixture 或 TestContext，没有写入模块全局变量
[ ] 复用现有 BaseTask 兼容入口或 task capability，没有继续扩张 BaseTask
[ ] 模块 Request 使用中性 runtime_metadata 设置稳定 operation name 和 workload/control 角色
[ ] 复合业务动作具有正确逻辑调用作用域
[ ] 业务模块没有导入 quality 或 run_orchestration 内部实现
[ ] 手动逻辑调用作用域使用 common.runtime_hooks 中性 API
[ ] 线程池使用 submit_with_context
[ ] 共享状态、账单和延迟结算用例标记 serial
[ ] POST 重试具备幂等键或明确 allow_post
[ ] 轮询使用 PollingPolicy 并设置合理 poll_timeout
[ ] 单次 Header 使用请求级参数，没有临时修改共享 Session
[ ] 不需要输入/输出下载时显式使用对应 CapturePolicy
[ ] 流式 Request 使用 SSE runtime_metadata、关闭响应体日志，并在 finally 中关闭响应
[ ] 通用断言和 JSON Schema 已复用
[ ] 金额使用 Decimal，账单调用后等待结算查询
[ ] 用例 nodeid 和参数 ID 稳定，适合 Flaky 历史比较
[ ] collect-only 通过
[ ] 能离线验证的请求生命周期优先复用 offline_framework_example，不复制公共实现
[ ] 新能力先归入职责明确的分类文件
[ ] 黄金路径只包含稳定成功主链，互斥异常仍留在分类用例
[ ] 离线服务状态按实例隔离且不依赖随机 sleep
[ ] 分类用例不导入 Quality、Metrics 或 Flaky 内部实现
[ ] 当前离线收集快照与 README 已同步
[ ] SCM Checkout 能够取得新增分类和黄金路径文件
[ ] 本地、Jenkins、阶段发布和总方案状态分别记录
[ ] 相关离线框架测试通过
[ ] 启用 Pipeline Summary 时无来源告警，或告警已明确定位到对应机器数据
[ ] Quality 关闭路径未加载重实现、未创建质量身份或产物，陈旧产物未污染本轮报告
[ ] 没有新增并行人工质量报告，也没有把可选机器产物缺失按零值处理
```
