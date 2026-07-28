# 代码变更历史

## 2026-07-25

### 请求中间件 V1

根据 `dev/request_middleware_design.md` 完成第一版请求中间件机制开发。

变更内容：

- 新增 `common/request_context.py`
  - 定义单次请求独立的 `RequestContext`。
  - 记录 method、path、URL、请求参数、日志挂载开关、Allure 步骤名和中间件共享属性。

- 新增 `common/request_middleware.py`
  - 定义 `RequestMiddleware` 生命周期协议。
  - 新增 `RedactionMiddleware`。
  - 新增 `LoggingMiddleware`。
  - 新增 `MediaResourceMiddleware`。
  - 定义默认中间件注册顺序。

- 更新 `common/base_request.py`
  - 将 `BaseRequest.request()` 改造为先构建 `RequestContext`，再调用统一 `_send()`。
  - 新增 `_build_request_context()`。
  - 新增 `_send()`，作为统一请求发送管道。
  - 新增中间件执行方法，并在中间件异常时输出明确来源。
  - 保持 `get/post/put/patch/delete` 调用方式兼容。
  - 保持 SSE 场景的 `_attach_log=False` 行为兼容。
  - 通过 `_request_without_attach()` 保持 `poll_get()` 只挂载最后一次轮询日志的行为兼容。
  - 支持显式注入中间件列表；`middlewares=[]` 表示禁用默认中间件。

### 统一脱敏能力

新增日志、cURL 和中间件共享的脱敏能力。

变更内容：

- 新增 `util/redaction.py`
  - 脱敏敏感请求头：`authorization`、`cookie`、`proxy-authorization`、`set-cookie`、`x-api-key`。
  - 脱敏敏感字段：`api_key`、`key`、`token`、`access_token`、`refresh_token`、`secret`、`password`、`authorization`。
  - 支持嵌套 `dict`、`list`、`tuple`、JSON 文本、form-urlencoded 文本、请求 kwargs 和 URL query 参数。
  - 只生成脱敏副本，不修改真实请求数据。

- 更新 `util/api_call_logger.py`
  - Allure 附件生成前，对请求 URL、请求头、请求体、响应头、响应体和 fallback 请求参数进行脱敏。

- 更新 `util/curl_builder.py`
  - 复用统一脱敏常量。
  - 对 cURL 中的敏感 URL query、JSON 请求体字段和表单请求体字段进行脱敏。

- 更新 `util/__init__.py`
  - 导出 `API_RESPONSE_STEP_NAME`。
  - 导出脱敏工具函数和 `REDACTED_VALUE`。

### 测试

补充中间件迁移和脱敏行为相关测试。

变更内容：

- 新增 `tests/test_request_middleware.py`
  - 覆盖 `RedactionMiddleware`。
  - 覆盖 `LoggingMiddleware`。
  - 覆盖 `MediaResourceMiddleware`。

- 新增 `tests/test_base_request_middleware.py`
  - 覆盖中间件执行顺序。
  - 覆盖请求异常时中间件执行和原始异常继续抛出。
  - 覆盖中间件异常来源包装。
  - 覆盖每次请求创建独立上下文。
  - 覆盖 `_attach_log=False`。
  - 覆盖 `poll_get()` 最终响应日志兼容性。
  - 覆盖显式空中间件列表行为。

- 更新 `tests/test_api_call_logger.py`
  - 增加 Allure 附件数据中 URL、Header、Body、Response 脱敏覆盖。

- 更新 `tests/test_curl_builder.py`
  - 增加 cURL 输出中敏感 query、JSON body 和 form body 脱敏覆盖。

### 真实环境测试入口

将图片生成真实环境用例复制到 `tests` 目录。

变更内容：

- 新增 `tests/test_real_env_wan2_7_image.py`
  - 基于 `module/image_model/test_wan2_7_image.py`。
  - 使用 `ImageTask.create_and_poll_media_generation()` 调用 `wan2.7-image-pro` 异步媒体生成。
  - 默认跳过，只有设置 `RUN_REAL_ENV_TESTS=TRUE` 后才执行。

手动运行命令：

```powershell
$env:RUN_REAL_ENV_TESTS="TRUE"
.\.venv\Scripts\python.exe -m pytest tests/test_real_env_wan2_7_image.py -q
```

### 验证记录

执行命令：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_api_call_logger.py tests/test_curl_builder.py tests/test_request_middleware.py tests/test_base_request_middleware.py -q
```

结果：

```text
22 passed
```

执行命令：

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
```

结果：

```text
51 passed, 1 skipped
```

执行命令：

```powershell
.\.venv\Scripts\python.exe run_master.py module/smoke --collect-only -q
```

结果：

```text
42 tests collected
```

### 重试策略与轮询状态机开发方案

根据 `FRAMEWORK_CAPABILITY_ROADMAP.md` 中 P1「重试策略与轮询状态机」，在当前代码基础上输出开发方案。

变更内容：

- 新增 `dev/retry_polling_state_machine_design.md`
  - 梳理当前 `BaseRequest.request()`、`_send()`、`_request_without_attach()` 和 `poll_get()` 的现状。
  - 明确第一版目标：显式 `RetryPolicy`、默认不重试、显式 `PollingPolicy`、保留旧 `poll_get()` 参数兼容。
  - 设计 `RetryPolicy` 字段、可重试异常、可重试状态码、幂等方法、POST 幂等键判断、指数退避、jitter、`Retry-After` 和 `max_elapsed`。
  - 明确 POST 无幂等保证时不自动重试，避免重复创建任务或重复扣费。
  - 设计 `PollingPolicy`、`PollingState`、`PollingTransition` 和轮询异常。
  - 明确 pending/success/failure/unknown 状态处理，未知状态默认失败。
  - 规划 `BaseRequest` 接入方式：`retry_policy`、`polling_policy`、`_send_with_retry()` 和每次尝试独立 `RequestContext`。
  - 规划日志策略：普通请求保持现状，重试保留摘要记录，轮询仍只挂载最终响应并补状态迁移摘要。
  - 补充单元测试设计、实施顺序、验证命令、风险处理和完成标准。

- 新增 `code_history/RETRY_POLLING_STATE_MACHINE_DESIGN_CHANGE_HISTORY.md`
  - 单独记录本次重试策略与轮询状态机开发方案的文档变更。

验证记录：

- 本次为开发方案输出，只新增设计文档和变更历史，未改动运行时代码，未执行测试。

### 重试策略与轮询状态机第一版实现

根据 `dev/retry_polling_state_machine_design.md` 落地 P1「重试策略与轮询状态机」第一版。

变更内容：

- 新增 `common/retry.py`
  - 定义 `RetryPolicy` 和 `RetryAttemptRecord`。
  - 支持 429、500、502、503、504 和 `requests.ConnectionError`、`requests.Timeout`。
  - 排除 `SSLError` 和 `TooManyRedirects`。
  - GET/HEAD 在显式启用策略后可重试。
  - POST 默认不重试；只有 `Idempotency-Key` 或 `allow_post=True` 时允许重试。
  - 支持 fixed/exponential backoff、jitter、数字和 HTTP 日期格式 `Retry-After`。

- 新增 `common/polling.py`
  - 定义 `PollingPolicy`、`PollingState`、`PollingEvaluation`、`PollingTransition`。
  - 定义 `PollingFailedError`、`PollingUnknownStateError`、`PollingTimeoutError`。
  - 支持 pending/success/failure/unknown 状态分类。
  - 支持 `result_json_path` 和 `error_json_path`。
  - 未知状态默认失败。
  - 失败和超时异常携带最后状态、最后响应和 transitions。

- 更新 `common/base_request.py`
  - `request()` 支持显式 `retry_policy`。
  - 未传 `retry_policy` 时保持原单次请求行为。
  - 新增 `_send_with_retry()`。
  - 每次重试重新构建独立 `RequestContext`。
  - 重试受 `max_attempts` 和 `max_elapsed` 限制。
  - `poll_get()` 支持 `polling_policy` 和 `retry_policy`。
  - 未传 `polling_policy` 时保持旧 `success_json_path` / `failure_json_path` 行为。
  - 轮询请求内部启用重试时，最终响应日志和状态迁移绑定到最后一次真实请求 logger。

- 更新 `util/api_call_logger.py`
  - 新增重试记录附件。
  - 新增轮询状态迁移附件。
  - 重试和轮询附件复用脱敏输出。

- 更新 `common/__init__.py`
  - 导出 `RetryPolicy`、`PollingPolicy`、`PollingState`、`PollingTimeoutError`。

- 更新 `common/base_task.py`
  - `poll_media_generation_result()` 和 `create_and_poll_media_generation()` 增加可选 `polling_policy` / `retry_policy` 并透传。
  - 默认参数和旧调用方式保持不变。

- 新增/更新测试
  - 新增 `tests/test_retry_policy.py`。
  - 新增 `tests/test_polling_state_machine.py`。
  - 新增 `tests/test_base_request_retry_polling.py`。
  - 更新 `tests/test_base_task.py`，覆盖策略参数透传。
  - 覆盖轮询请求内重试后，最终响应日志挂载到最后一次真实请求 logger。

验证记录：

目标测试首次运行时有 1 个失败：

```text
test_max_elapsed_stops_retry_without_sleep
```

原因是实现将退避等待时间压缩到剩余 `max_elapsed` 后仍继续重试。修正为：如果原始退避等待无法放入剩余总耗时预算，则停止重试，不做压缩等待。

修正后执行目标测试：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_retry_policy.py tests/test_polling_state_machine.py tests/test_base_request_retry_polling.py -q
```

结果：

```text
42 passed
```

执行相关回归：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_base_request_middleware.py tests/test_base_task.py tests/test_api_call_logger.py -q
```

结果：

```text
32 passed
```

执行全量单测：

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
```

结果：

```text
128 passed, 1 skipped
```

执行 smoke 收集：

```powershell
.\.venv\Scripts\python.exe run_master.py module/smoke --collect-only -q
```

结果：

```text
42 tests collected
```

补充轮询内重试最终 logger 归属修正后，重新执行目标测试、相关回归、全量测试和 smoke 收集，结果保持一致：

```text
42 passed
32 passed
128 passed, 1 skipped
42 tests collected
```

### 配置校验与安全保护开发方案重建

在当前代码基础上，重新制定 P0「配置校验与安全保护」开发方案。

变更内容：

- 新增 `dev/config_validation_security_design.md`
  - 基于当前 `config.py`、`util/redaction.py` 和 `tests/test_real_env_wan2_7_image.py` 的实际状态重新规划。
  - 明确当前约束点：配置 import 阶段错误质量低、配置错误暴露晚、真实环境和高风险用例开关未统一。
  - 保留现有 `Settings` 和 `settings = load_settings()` 对外兼容思路。
  - 规划新增 `util/config_validation.py`，承载配置解析、校验、开关判断和脱敏摘要。
  - 规划 `config.py` 改造为 `load_settings(env=None)`，便于单元测试传入模拟环境。
  - 明确第一版只强制校验当前环境 base URL 和主 API Key，控制台密钥、B 账号、zero 账号等按用例需要校验。
  - 规划复用 `util.redaction`，不再维护第二套敏感字段规则。
  - 规划统一开关：`RUN_REAL_ENV_TESTS`、`RUN_HIGH_COST_TESTS`、`RUN_DESTRUCTIVE_TESTS`。
  - 补充实施顺序、测试设计、验证命令、风险处理和完成标准。

验证记录：

- 本次为开发方案输出，只新增设计文档，未改动运行时代码，未执行测试。

### 配置方案账号边界修订

根据反馈修订 `dev/config_validation_security_design.md` 中账号配置边界。

变更内容：

- 明确 B 账号和 zero 账号只属于用例级输入。
- B 账号和 zero 账号不进入 `Settings`。
- B 账号和 zero 账号不进入 `load_settings()`。
- B 账号和 zero 账号不参与全局启动校验。
- B 账号和 zero 账号不进入全局配置摘要。
- 对应密钥由具体用例或任务按需读取、校验并 skip。

验证记录：

- 本次只调整开发方案文档，未改动运行时代码，未执行测试。

### 配置校验与安全保护第一版实现

根据 `dev/config_validation_security_design.md` 落地 P0「配置校验与安全保护」第一版。

变更内容：

- 新增 `util/config_validation.py`
  - 新增 `ConfigValidationError`。
  - 新增布尔、正数、正整数、非空字符串、HTTP URL 解析校验函数。
  - 新增配置错误聚合输出。
  - 新增统一开关判断 `is_enabled()`。
  - 新增配置摘要脱敏 `redact_config_summary()`，复用 `util.redaction`。

- 改造 `config.py`
  - 新增 `load_settings(env=None)`。
  - 保留 `settings = load_settings()`。
  - 保留 `USE_CHINA_ENVIRONMENT` 对外兼容。
  - 保留 `base_url`、`api_key`、`timeout` 等现有字段。
  - 新增 `environment_name`。
  - 将 `.strip()`、`.rstrip()`、`float()`、`int()` 直接解析迁移到配置校验函数。
  - 当前环境 base URL 和主 API Key 全局必填。
  - B 账号和 zero 账号未进入 `Settings`、`load_settings()`、全局启动校验和全局配置摘要。

- 更新 `util/__init__.py`
  - 导出配置校验异常、解析函数、开关函数和配置摘要脱敏函数。

- 更新 `tests/test_real_env_wan2_7_image.py`
  - 使用统一 `is_enabled("RUN_REAL_ENV_TESTS")` 判断真实环境测试开关。

- 新增 `tests/test_config_validation.py`
  - 覆盖中国环境和海外环境配置加载。
  - 覆盖缺失 base URL / API Key 的聚合错误。
  - 覆盖非法 URL、timeout、history keep limit、布尔值。
  - 覆盖 B 账号和 zero 账号不进入全局配置。
  - 覆盖配置摘要脱敏和真实环境开关判断。

验证记录：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_config_validation.py -q
```

结果：

```text
13 passed
```

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_config_validation.py tests/test_api_call_logger.py tests/test_curl_builder.py tests/test_request_middleware.py tests/test_base_request_middleware.py -q
```

结果：

```text
43 passed
```

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
```

结果：

```text
72 passed, 1 skipped
```

```powershell
.\.venv\Scripts\python.exe run_master.py module/smoke --collect-only -q
```

结果：

```text
42 tests collected
```

执行命令：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_real_env_wan2_7_image.py --collect-only -q
```

结果：

```text
1 test collected
```

### 说明

- `AGENTS.md` 和 `dev/request_middleware_design.md` 是用户提供的规划文档，本次实现没有修改它们。
- 第一版请求中间件不实现 `RetryMiddleware`、`TraceMiddleware`、`MetricsMiddleware`、动态发现、热插拔和复杂依赖注入。

### 基础契约断言开发方案

根据 `FRAMEWORK_CAPABILITY_ROADMAP.md` 中 P1「基础契约断言」，在当前代码基础上输出第一版开发方案。

变更内容：

- 新增 `dev/contract_assertion_design.md`
  - 明确第一版只实现 `assert_schema(response, schema)`。
  - 基于 `common/base_assertions.py` 现有断言体系设计同步、异步和模块级导出入口。
  - 规划新增 `jsonschema>=4.0.0` 依赖。
  - 规划第一批业务 schema 放入 `module/smoke/response_schemas.py`，避免过早建设全局 schema 注册中心。
  - 明确第一批 schema：Chat Completions 成功响应和标准错误响应。
  - 设计失败信息输出 JSON 路径、schema 路径、期望约束、实际类型和脱敏后的实际值。
  - 规划复用 `util.redaction`，避免 schema 断言失败信息泄露敏感字段。
  - 补充实施顺序、测试设计、验证命令、风险处理和完成标准。

- 新增 `code_history/CONTRACT_ASSERTION_DESIGN_CHANGE_HISTORY.md`
  - 单独记录本次基础契约断言开发方案的文档变更。

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
  - 校验失败信息输出 JSON 路径、schema 路径、validator、期望约束、实际类型和实际值。
  - 对 `required` 缺失字段推导到具体字段路径。
  - 复用 `util.redaction`，对实际值和响应片段脱敏。

- 更新 `common/__init__.py`
  - 导出 `assert_schema` 和 `async_assert_schema`。

- 新增 `module/smoke/response_schemas.py`
  - 新增 `CHAT_COMPLETION_SUCCESS_SCHEMA`。
  - 新增 `STANDARD_ERROR_RESPONSE_SCHEMA`。

- 更新 `module/smoke/test_response_body_validation.py`
  - Chat Completions 成功响应结构改用 `CHAT_COMPLETION_SUCCESS_SCHEMA`。
  - 标准错误响应结构改用 `STANDARD_ERROR_RESPONSE_SCHEMA`。
  - 保留具体业务值断言，不用 schema 替代业务语义判断。

- 新增 `tests/test_base_assertions_schema.py`
  - 覆盖 schema 校验成功、失败定位、非法 JSON、非法 schema、脱敏、同步导出、异步导出和首批业务 schema。

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

### 配置校验与安全保护开发方案

根据 `FRAMEWORK_CAPABILITY_ROADMAP.md` 中 P0「配置校验与安全保护」输出开发方案。

变更内容：

- 新增 `dev/config_validation_security_design.md`
  - 梳理当前 `config.py` 在 import 阶段直接解析环境变量导致的错误定位问题。
  - 明确第一版目标：启动前校验核心配置、输出具体变量名、复用脱敏规则、保留现有 `settings` 调用方式。
  - 设计配置读取优先级：系统环境变量优先，`.env` 补充，敏感配置不使用静默默认值。
  - 设计 base URL、API Key、timeout、历史报告保留数量和布尔开关的校验规则。
  - 设计 `ConfigValidationError` 和聚合错误输出。
  - 规划 `util/config_validation.py`、`config.py`、`tests/test_config_validation.py` 的职责边界。
  - 规划真实环境、高成本和破坏性用例的统一显式开关。
  - 说明与 `util/redaction.py` 的复用关系，避免密钥进入控制台、异常或报告。
  - 补充单元测试设计、验证命令、实施顺序、风险处理和第一版完成标准。

验证记录：

- 本次为开发方案输出，只新增设计文档，未改动运行时代码，未执行测试。

### 请求中间件审计修复

根据审计结论修复请求中间件第一版验收缺口。

变更内容：

- 修复异常日志脱敏
  - `ApiCallLogger.attach_failure()` 不再原样输出 `str(error)`。
  - 异常文本会复用脱敏规则，覆盖 `api_key=...`、`token='...'`、`Authorization: ...` 等常见泄露形式。

- 修复异常语义
  - `on_exception()` 中间件自身失败时，不再覆盖原始网络异常。
  - 原始 `requests.Timeout` 等异常继续抛出。
  - 中间件故障来源通过异常 note 保留，便于定位。

- 修复请求上下文数据隔离
  - `_build_request_context()` 不再浅拷贝请求 kwargs。
  - 请求参数按字段深拷贝，深拷贝失败时回退为原对象。
  - 防止中间件修改嵌套 JSON 时污染调用方 payload。

- 修复 `poll_get()` 兼容性
  - `_request_without_attach()` 在请求异常时会手动挂载失败日志后继续抛出原异常。
  - `poll_get()` 不再硬依赖 `LoggingMiddleware`，在 `middlewares=[]` 或自定义管线下仍可成功返回。
  - 无 logger 时使用 no-op logger，保持轮询流程兼容。

- 补充回归测试
  - 异常文本脱敏。
  - `on_exception()` hook 故障不覆盖原始异常。
  - 嵌套请求参数深拷贝。
  - 线程并发下请求上下文隔离。
  - `poll_get()` 在空中间件列表下成功。
  - `poll_get()` 网络异常失败日志。
  - `poll_get()` 失败状态和超时日志只挂载最终响应。

验证记录：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_api_call_logger.py tests/test_base_request_middleware.py tests/test_request_middleware.py tests/test_curl_builder.py -q
```

结果：

```text
30 passed
```

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
```

结果：

```text
59 passed, 1 skipped
```

```powershell
.\.venv\Scripts\python.exe run_master.py module/smoke --collect-only -q
```

结果：

```text
42 tests collected
```

## 2026-07-26 测试上下文与变量传递开发方案

根据 `FRAMEWORK_CAPABILITY_ROADMAP.md` 中 P1「测试上下文与变量传递」，在当前代码基础上输出开发方案。

变更内容：

- 新增 `dev/test_context_design.md`
  - 梳理当前 `BaseTask.extract_task_id()`、`BaseTask.get_request_id_from_response()`、异步图片用例私有提取函数和并发计费用例手动变量传递的重复点。
  - 明确第一版目标：用例级 `TestContext`、变量读写、响应提取、类型校验、清理回调和并发隔离。
  - 明确第一版只支持 function / test-case scope，不做 class/session/global 上下文，不做跨用例持久化。
  - 设计 JSONPath、Header、Cookie、Regex 四类提取规则。
  - 设计 `extract()`、`extract_first()`、`get()`、`require()`、`set()`、`add_cleanup()`、`cleanup()` 等 API。
  - 设计变量缺失、类型不匹配、提取失败和清理失败异常。
  - 明确上下文不保存 API Key、Authorization、Cookie 等敏感值，错误摘要必须复用脱敏能力。
  - 明确 B 账号、zero 账号仍限定在具体用例中，不纳入全局测试上下文配置。
  - 规划 `common/test_context.py`、`tests/test_test_context.py` 和可选 pytest fixture 的职责边界。
  - 补充单元测试设计、实施顺序、验证命令、风险处理和第一版完成标准。

验证记录：

- 本次为开发方案输出，只新增设计文档和变更历史，未改动运行时代码，未执行测试。
