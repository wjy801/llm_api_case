# 基础契约断言开发方案

## 1. 需求理解

根据 `FRAMEWORK_CAPABILITY_ROADMAP.md`，P1「基础契约断言」的目标是在核心业务接口中使用 JSON Schema 统一校验响应结构，减少重复的字段存在性、类型、枚举和范围断言。

结合当前代码，本阶段不是建设完整契约测试平台，而是在现有 `BaseAssertions` 上补齐第一版最小能力：

- 新增 `assert_schema(response, schema)`。
- 使用 JSON Schema 校验响应 JSON。
- 失败信息必须定位到具体字段。
- 至少沉淀一个成功响应 schema 和一个标准错误响应 schema。
- 增加独立单元测试，保证该能力不依赖真实环境。

## 2. 第一性原理与 TOC 分析

契约断言的本质是把“响应是否符合约定”从零散的命令式判断，转成可复用、可解释、可维护的结构化规则。

当前系统已经具备请求发送、日志脱敏、基础断言和真实环境用例保护。真正的约束点不在“能不能写断言”，而在以下链路：

1. 用例通过 `assert_json_path_exists()` 和 `assert_json_value()` 重复描述同一类响应结构。
2. 重复断言只能逐字段表达，无法作为“响应契约”被复用和沉淀。
3. 结构变化时，需要在多个用例中逐个修改，容易遗漏。
4. 当前失败信息能定位单个 JSONPath，但不能表达完整结构期望、类型范围和枚举约束。
5. 如果直接引入 OpenAPI 自动生成或复杂 schema 注册中心，会把第一版瓶颈从“结构校验缺口”转移成“契约来源治理缺口”。

因此第一版 TOC 决策是：先建立一个稳定、可复用、失败可定位的 `assert_schema` 入口；schema 来源先用代码常量维护，不引入自动加载、动态生成和全量业务迁移。

## 3. 当前代码基础

### 3.1 已有断言能力

`common/base_assertions.py` 当前提供：

- `BaseAssertions.assert_status_code(response, expected)`
- `BaseAssertions.assert_json_value(response, json_path, expected)`
- `BaseAssertions.assert_json_path_exists(response, json_path)`
- 对应 async 包装函数。
- 模块级默认断言函数。

`module/smoke/assertions.py`、`module/protocol_testing/*/assertions.py` 都继承或复用 `BaseAssertions`，所以 `assert_schema` 放入 `BaseAssertions` 后可以自然被业务断言类继承。

### 3.2 当前重复结构校验

`module/smoke/test_response_body_validation.py` 中已经出现稳定重复结构：

- Chat Completions 成功响应：
  - `$.model`
  - `$.id`
  - `$.object`
  - `$.choices[0].message`
  - `$.usage.prompt_tokens`
  - `$.usage.total_tokens`
  - `$.usage.completion_tokens`

- 标准错误响应：
  - `$.error`
  - `$.error.message`
  - `$.error.type`
  - `$.error.code`

这两类结构正好满足路线图中“至少一个成功响应 schema 和一个标准错误响应 schema”的第一版验收要求。

### 3.3 当前缺口

- `requirements.txt` 尚未引入 `jsonschema`。
- `common/__init__.py` 尚未导出 `assert_schema`。
- 当前没有面向 `BaseAssertions` 的 schema 单元测试。
- 业务响应结构还没有独立 schema 常量。
- 现有基础断言的部分历史中文错误信息存在编码异常，本阶段不把它作为契约断言的主线问题，但新增错误信息必须保持可读。

## 4. 第一版目标

第一版只交付以下能力：

1. 在 `BaseAssertions` 中新增同步断言：

   ```python
   assertions.assert_schema(response, schema)
   ```

2. 增加对应异步包装：

   ```python
   await assertions.async_assert_schema(response, schema)
   ```

3. 模块级函数同步导出：

   ```python
   from common import assert_schema
   ```

4. 使用 JSON Schema 校验响应 JSON 顶层结构、必填字段、字段类型、枚举值和数值范围。

5. schema 校验失败时输出：

   - 失败 JSON 路径。
   - schema 路径。
   - 违反的校验器。
   - 期望约束。
   - 实际类型。
   - 脱敏后的实际值。
   - jsonschema 原始说明。

6. 提供第一批 schema：

   - Chat Completions 成功响应 schema。
   - 标准错误响应 schema。

7. 增加独立单元测试，并用模拟 `requests.Response` 构造响应，不依赖真实接口。

第一版不做：

- 不实现 `assert_headers()`。
- 不实现 `assert_response_time()`。
- 不实现 `assert_json_contains()`。
- 不实现 `assert_json_types()`。
- 不接入 OpenAPI 自动加载。
- 不接入 Schemathesis/Hypothesis 自动生成用例。
- 不建设全局 schema 注册中心。
- 不全量迁移历史用例。
- 不处理 B 账号、zero 账号等用例级账号配置。
- 不把流式 SSE chunk 的完整契约纳入第一版主线。

## 5. 建议文件结构

```text
requirements.txt
common/
  base_assertions.py
  __init__.py
module/
  smoke/
    response_schemas.py
tests/
  test_base_assertions_schema.py
```

职责边界：

- `requirements.txt`
  - 新增 `jsonschema>=4.0.0`。

- `common/base_assertions.py`
  - 实现 `BaseAssertions.assert_schema()`。
  - 实现 `BaseAssertions.async_assert_schema()`。
  - 实现 schema 错误格式化私有辅助函数。
  - 复用 `util.redaction` 对失败输出中的实际值和响应片段脱敏。

- `common/__init__.py`
  - 在 `TYPE_CHECKING`、`__all__`、`__getattr__()` 中补充 `assert_schema` 和 `async_assert_schema`。

- `module/smoke/response_schemas.py`
  - 放置第一批业务响应 schema 常量。
  - 第一版只维护稳定、最小、跨用例可复用的响应结构。
  - 暂不放入 `common`，避免把业务响应契约误建模为框架公共契约。

- `tests/test_base_assertions_schema.py`
  - 覆盖 `assert_schema` 的成功、失败、错误信息和导出入口。

## 6. 依赖选择

推荐新增：

```text
jsonschema>=4.0.0
```

实现建议：

```python
from jsonschema import Draft202012Validator, SchemaError
from jsonschema.validators import validator_for
```

选择逻辑：

1. 如果 schema 声明 `$schema`，使用 `validator_for(schema)` 选择对应 validator。
2. 如果 schema 未声明 `$schema`，默认使用 `Draft202012Validator`。
3. 调用 `Validator.check_schema(schema)`，尽早暴露 schema 自身错误。
4. 调用 `validator.iter_errors(body)`，按路径排序后输出第一个错误。

只输出第一个错误的原因：

- 第一版验收重点是“定位准确”，不是一次性输出所有差异。
- 单个错误信息可以保持短、清晰、适合 Allure 和控制台展示。
- 后续如果真实使用中需要聚合错误，再增加 `max_errors` 参数或内部配置。

## 7. API 设计

### 7.1 类方法

```python
class BaseAssertions:
    def assert_schema(
        self,
        response: requests.Response,
        schema: Mapping[str, Any],
    ) -> requests.Response:
        ...
```

返回值保持与现有断言一致：校验通过后返回原始 `response`，便于链式或后续断言继续使用。

### 7.2 模块级函数

```python
def assert_schema(response: requests.Response, schema: Mapping[str, Any]) -> requests.Response:
    return _default_assertions.assert_schema(response, schema)
```

### 7.3 异步包装

```python
async def async_assert_schema(
    response: requests.Response,
    schema: Mapping[str, Any],
) -> requests.Response:
    return self.assert_schema(response, schema)
```

异步版本只保持调用形态兼容，不引入异步 JSON 解析。

## 8. 响应解析规则

### 8.1 非法 JSON

当 `response.json()` 抛出 `ValueError` 时，抛出 `AssertionError`：

```text
Response body is not valid JSON. Response body: <redacted body>
```

响应体必须先脱敏再输出。

### 8.2 非对象 JSON

JSON Schema 本身可以校验顶层数组、字符串或对象，所以 `assert_schema` 不强制顶层必须是对象。是否要求对象由 schema 决定：

```python
{"type": "object"}
```

### 8.3 schema 自身非法

schema 自身不合法时，抛出 `AssertionError`：

```text
Invalid JSON Schema: <message>
```

这是测试代码或契约常量错误，不应该伪装成接口响应错误。

## 9. 失败信息格式

推荐格式：

```text
JSON Schema assertion failed.
Path: $.usage.prompt_tokens
Schema path: properties/usage/properties/prompt_tokens/type
Validator: type
Expected: integer
Actual type: str
Actual value: '12'
Message: '12' is not of type 'integer'
```

路径格式规则：

- 空路径输出 `$`。
- 对象属性输出 `$.error.message`。
- 数组下标输出 `$.choices[0].message`。
- `required` 缺失字段时，不只输出父节点路径，而是推导出缺失字段路径，例如 `$.usage.prompt_tokens`。

`required` 路径推导建议：

1. 当 `error.validator == "required"` 时，读取 `error.validator_value`。
2. 将 `error.instance` 已有字段与 required 字段做差集。
3. 对第一个缺失字段拼接到 `error.absolute_path` 后面。
4. 输出最终缺失字段路径。

实际值输出规则：

- 对当前失败字段的实际值调用 `redact_sensitive_data()`。
- 如果实际值不存在，输出 `<missing>`。
- 如果需要输出响应体片段，使用脱敏后的 `response.text`，并限制长度，避免大响应污染报告。

## 10. 第一批 schema 设计

### 10.1 Chat Completions 成功响应

建议常量名：

```python
CHAT_COMPLETION_SUCCESS_SCHEMA
```

建议 schema：

```python
CHAT_COMPLETION_SUCCESS_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["id", "object", "model", "choices", "usage"],
    "properties": {
        "id": {"type": "string", "minLength": 1},
        "object": {"const": "chat.completion"},
        "created": {"type": "integer", "minimum": 0},
        "model": {"type": "string", "minLength": 1},
        "choices": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["message"],
                "properties": {
                    "index": {"type": "integer", "minimum": 0},
                    "finish_reason": {"type": ["string", "null"]},
                    "message": {
                        "type": "object",
                        "properties": {
                            "role": {"type": "string"},
                            "content": {"type": ["string", "array", "null"]},
                        },
                    },
                },
            },
        },
        "usage": {
            "type": "object",
            "required": ["prompt_tokens", "completion_tokens", "total_tokens"],
            "properties": {
                "prompt_tokens": {"type": "integer", "minimum": 0},
                "completion_tokens": {"type": "integer", "minimum": 0},
                "total_tokens": {"type": "integer", "minimum": 0},
            },
        },
    },
}
```

设计取舍：

- `object` 使用 `const`，因为这是协议层稳定字段。
- `model` 只要求非空字符串，不在 schema 中写死 `glm-5`，具体模型值仍由业务用例断言。
- 不设置 `additionalProperties: false`，避免服务端新增字段导致无意义失败。
- `message.content` 保持宽松，兼容字符串、多模态数组和空值场景。

### 10.2 标准错误响应

建议常量名：

```python
STANDARD_ERROR_RESPONSE_SCHEMA
```

建议 schema：

```python
STANDARD_ERROR_RESPONSE_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["error"],
    "properties": {
        "error": {
            "type": "object",
            "required": ["message", "type", "code"],
            "properties": {
                "message": {"type": "string", "minLength": 1},
                "type": {"type": "string", "minLength": 1},
                "code": {"type": ["string", "null"]},
            },
        },
    },
}
```

设计取舍：

- `error.code` 允许 `null`，避免某些错误场景只返回错误类型和消息时产生误报。
- 具体错误码如 `model_not_found` 仍由业务用例用 `assert_json_value()` 校验。
- 不限制 `error` 内额外字段，便于后续兼容 `param`、`request_id` 等扩展字段。

## 11. 用例迁移策略

第一版只迁移最稳定、重复最高的用例，不做全量替换。

建议先改：

- `module/smoke/test_response_body_validation.py::test_chat_completions_response_body`
  - 保留 `assert_status_code(response, 200)`。
  - 使用 `assert_schema(response, CHAT_COMPLETION_SUCCESS_SCHEMA)`。
  - 如仍要校验具体模型，继续保留 `assert_json_value(response, "$.model", "glm-5")`。

- `module/smoke/test_response_body_validation.py` 中标准错误结构校验。
  - 使用 `assert_schema(response, STANDARD_ERROR_RESPONSE_SCHEMA)`。
  - 具体错误码、错误消息和业务语义断言继续留在各自用例中。

暂不迁移：

- 账单金额计算断言。
- B 账号、zero 账号相关用例。
- 流式 SSE chunk 完整结构校验。
- 协议兼容性模块中带有特殊内容判断的断言。

## 12. 单元测试设计

新增：

```text
tests/test_base_assertions_schema.py
```

建议覆盖：

1. 合法响应通过校验，并返回原始 `response` 对象。
2. 缺失顶层必填字段时，错误信息包含精确路径。
3. 缺失嵌套必填字段时，错误信息包含 `$.usage.prompt_tokens` 这类路径。
4. 字段类型错误时，错误信息包含路径、期望类型、实际类型和实际值。
5. 枚举或 `const` 失败时，错误信息包含 validator 名称。
6. 数组元素字段错误时，错误信息包含 `$.choices[0].message` 这类路径。
7. 响应体不是合法 JSON 时，抛出 `AssertionError`。
8. schema 自身非法时，抛出 `AssertionError`，并说明 schema 无效。
9. 敏感字段出现在失败实际值或响应体中时，错误信息必须脱敏。
10. 模块级 `assert_schema()` 可用。
11. `async_assert_schema()` 可用。
12. `CHAT_COMPLETION_SUCCESS_SCHEMA` 可校验一份最小成功样例。
13. `STANDARD_ERROR_RESPONSE_SCHEMA` 可校验一份标准错误样例。

构造响应建议使用 `requests.Response`：

```python
def make_response(body: object, status_code: int = 200) -> requests.Response:
    response = requests.Response()
    response.status_code = status_code
    response._content = json.dumps(body).encode("utf-8")
    response.headers["Content-Type"] = "application/json"
    return response
```

## 13. 实施顺序

1. 增加依赖
   - 在 `requirements.txt` 中加入 `jsonschema>=4.0.0`。

2. 实现核心断言
   - 修改 `common/base_assertions.py`。
   - 新增 `assert_schema()`、`async_assert_schema()`。
   - 新增 schema validator 构建、路径格式化、错误格式化和脱敏辅助函数。

3. 补充导出
   - 修改 `common/__init__.py`。
   - 补充 `TYPE_CHECKING`、`__all__` 和 `__getattr__()`。

4. 增加第一批 schema
   - 新增 `module/smoke/response_schemas.py`。
   - 定义 `CHAT_COMPLETION_SUCCESS_SCHEMA`。
   - 定义 `STANDARD_ERROR_RESPONSE_SCHEMA`。

5. 补充单元测试
   - 新增 `tests/test_base_assertions_schema.py`。
   - 覆盖成功、失败、脱敏、导出和 async 包装。

6. 小范围迁移业务用例
   - 只迁移 `module/smoke/test_response_body_validation.py` 中最稳定的响应结构断言。
   - 保留业务值断言，不用 schema 替代所有语义判断。

7. 更新历史记录
   - 将本次实现写入 `code_history`。

## 14. 验证命令

目标单测：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_base_assertions_schema.py -q
```

相关回归：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_base_assertions_schema.py tests/test_config_validation.py tests/test_request_middleware.py tests/test_base_request_middleware.py -q
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

### 15.1 schema 过严导致服务端新增字段失败

风险：如果第一版大量使用 `additionalProperties: false`，服务端新增兼容字段也会让用例失败。

处理：第一版 schema 默认不禁止额外字段，只校验稳定的必填字段、核心类型和协议枚举。

### 15.2 `required` 错误定位只到父对象

风险：`jsonschema` 原始错误路径对缺失字段通常停留在父对象，例如 `$.usage`。

处理：对 `required` validator 做专项格式化，推导缺失字段路径，确保满足“失败消息定位精确字段”的验收标准。

### 15.3 失败信息泄露响应中的敏感字段

风险：schema 失败时输出实际值或响应体片段，可能带出 token、key、authorization 等敏感内容。

处理：复用 `util.redaction`，所有失败输出中的实际值和响应片段先脱敏。

### 15.4 把 schema 误用成业务语义断言

风险：把具体模型名、具体错误消息、账单金额等业务判断写进通用 schema，会降低复用性。

处理：schema 只表达稳定结构和通用类型；业务精确值继续由 `assert_json_value()` 或业务断言类表达。

### 15.5 依赖引入影响离线环境

风险：开发机或 CI 没有安装 `jsonschema` 时新增测试失败。

处理：明确加入 `requirements.txt`，实现前先安装依赖；不在代码中做静默降级，避免契约断言实际未执行。

## 16. 第一版完成标准

- `assert_schema(response, schema)` 可用，并返回原始 `response`。
- `async_assert_schema(response, schema)` 可用。
- `from common import assert_schema` 可用。
- JSON Schema 校验能覆盖必填字段、字段类型、枚举值和数值范围。
- 缺失字段、类型错误、数组元素错误都能输出精确 JSON 路径。
- 失败信息中敏感字段已脱敏。
- 至少存在 `CHAT_COMPLETION_SUCCESS_SCHEMA` 和 `STANDARD_ERROR_RESPONSE_SCHEMA`。
- `tests/test_base_assertions_schema.py` 覆盖核心成功和失败场景。
- 相关回归测试通过。
- `module/smoke` 可正常 collect。

