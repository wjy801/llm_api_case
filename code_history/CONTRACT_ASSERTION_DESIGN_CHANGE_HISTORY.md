# 基础契约断言开发方案变更历史

## 2026-07-25

### 基础契约断言开发方案

根据 `FRAMEWORK_CAPABILITY_ROADMAP.md` 中 P1「基础契约断言」，在当前代码基础上输出第一版开发方案。

变更内容：

- 新增 `dev/contract_assertion_design.md`
  - 明确第一版只实现 `assert_schema(response, schema)`。
  - 基于 `common/base_assertions.py` 现有断言体系设计同步、异步和模块级导出入口。
  - 规划新增 `jsonschema>=4.0.0` 依赖。
  - 规划在 `module/smoke/response_schemas.py` 中维护第一批业务响应 schema。
  - 明确第一批 schema：Chat Completions 成功响应和标准错误响应。
  - 设计 schema 校验失败信息，要求输出 JSON 路径、schema 路径、期望约束、实际类型和脱敏后的实际值。
  - 明确 `required` 缺失字段需要推导到具体字段路径。
  - 规划复用 `util.redaction`，避免 schema 断言失败信息泄露敏感字段。
  - 设计 `tests/test_base_assertions_schema.py` 的单元测试覆盖范围。
  - 明确第一版不做 OpenAPI 自动加载、自动生成用例、全量迁移和 Header/耗时等扩展断言。

验证记录：

- 本次为开发方案输出，只新增设计文档和变更历史，未改动运行时代码，未执行测试。

### 基础契约断言第一版实现

根据 `dev/contract_assertion_design.md` 落地 P1「基础契约断言」第一版。

变更内容：

- 更新 `requirements.txt`
  - 新增 `jsonschema>=4.0.0`。

- 更新 `common/base_assertions.py`
  - 新增 `BaseAssertions.assert_schema(response, schema)`。
  - 新增 `BaseAssertions.async_assert_schema(response, schema)`。
  - 新增模块级 `assert_schema()` 和 `async_assert_schema()`。
  - 使用 `jsonschema` 校验响应 JSON。
  - schema 自身非法时抛出 `AssertionError` 并说明 schema 无效。
  - 响应体不是合法 JSON 时抛出 `AssertionError`。
  - 校验失败信息输出 JSON 路径、schema 路径、validator、期望约束、实际类型和实际值。
  - 对 `required` 缺失字段推导到具体字段路径。
  - 复用 `util.redaction`，对实际值和响应片段脱敏。

- 更新 `common/__init__.py`
  - 导出 `assert_schema`。
  - 导出 `async_assert_schema`。

- 新增 `module/smoke/response_schemas.py`
  - 新增 `CHAT_COMPLETION_SUCCESS_SCHEMA`。
  - 新增 `STANDARD_ERROR_RESPONSE_SCHEMA`。

- 更新 `module/smoke/test_response_body_validation.py`
  - Chat Completions 成功响应结构改用 `CHAT_COMPLETION_SUCCESS_SCHEMA`。
  - 标准错误响应结构改用 `STANDARD_ERROR_RESPONSE_SCHEMA`。
  - 保留具体业务值断言，不用 schema 替代业务语义判断。

- 新增 `tests/test_base_assertions_schema.py`
  - 覆盖 schema 校验成功返回原始 response。
  - 覆盖顶层必填字段缺失、嵌套必填字段缺失、类型错误、const 错误、数组元素错误。
  - 覆盖非法 JSON、非法 schema。
  - 覆盖失败信息脱敏。
  - 覆盖模块级同步和异步导出。
  - 覆盖首批业务 schema 的最小成功样例。

验证记录：

首次执行目标测试时，当前虚拟环境缺少新增依赖 `jsonschema`，收集阶段失败：

```text
ModuleNotFoundError: No module named 'jsonschema'
```

安装依赖：

```powershell
.\.venv\Scripts\python.exe -m pip install jsonschema>=4.0.0
```

重新执行目标单测：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_base_assertions_schema.py -q
```

结果：

```text
13 passed
```

执行相关回归：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_base_assertions_schema.py tests/test_config_validation.py tests/test_request_middleware.py tests/test_base_request_middleware.py -q
```

结果：

```text
44 passed
```

执行全量单测：

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
```

结果：

```text
85 passed, 1 skipped
```

执行 smoke 收集：

```powershell
.\.venv\Scripts\python.exe run_master.py module/smoke --collect-only -q
```

结果：

```text
42 tests collected
```
