# 配置校验与安全保护改动历史

## 2026-07-25

### 背景

根据 `FRAMEWORK_CAPABILITY_ROADMAP.md` 中 P0「配置校验与安全保护」以及 `dev/config_validation_security_design.md`，完成配置校验与安全保护第一版实现。

本次目标：

- 在请求发出前发现核心配置错误。
- 缺失或非法配置输出明确变量名。
- 保持现有 `settings.base_url`、`settings.api_key`、`settings.timeout` 调用方式兼容。
- 复用已有脱敏规则，避免密钥进入配置摘要、异常或日志。
- B 账号和 zero 账号只属于用例级输入，不进入全局配置。

### 运行时代码变更

#### 新增 `util/config_validation.py`

新增能力：

- `ConfigValidationError`
- `parse_bool()`
- `parse_positive_float()`
- `parse_positive_int()`
- `require_non_empty()`
- `require_http_url()`
- `aggregate_config_errors()`
- `is_enabled()`
- `redact_config_summary()`

关键行为：

- 布尔值只接受 `TRUE` / `FALSE`，大小写不敏感。
- `API_TIMEOUT` 必须是大于 0 的数字。
- `HISTORY_REPORT_KEEP_LIMIT` 必须是大于等于 1 的整数。
- base URL 必须是 `http://` 或 `https://`。
- 配置错误支持聚合输出。
- 配置摘要复用 `util.redaction.redact_sensitive_data()`。

#### 更新 `config.py`

主要变化：

- 新增 `load_settings(env=None)`。
- 保留 `settings = load_settings()`。
- 保留 `USE_CHINA_ENVIRONMENT`。
- 保留现有字段：
  - `base_url`
  - `api_key`
  - `timeout`
  - `generate_allure_report`
  - `generate_history_report`
  - `history_report_keep_limit`
- 新增字段：
  - `environment_name`

修复的问题：

- 不再在 dataclass 字段定义阶段直接执行 `.strip()`、`.rstrip()`、`float()`、`int()`。
- 缺失配置不再抛出模糊的 `AttributeError`。
- 数值非法不再只抛出裸 `ValueError`。
- 当前环境 base URL 和主 API Key 会在 `load_settings()` 阶段校验。

全局配置范围：

- 中国环境全局必填：
  - `CHINA_TEST_ENVIRONMENT_BASE_URL`
  - `CHINA_API_KEY`
- 海外环境全局必填：
  - `OVERSEAS_TEST_BASE_URL`
  - `OVERSEAS_API_KEY`

明确不进入全局配置：

- `B_ACCOUNT_API_KEY`
- `B_ACCOUNT_CONTROL_KEY`
- `ZERO_BALANCE_API_KEY`
- `ZERO_BALANCE_CONTROL_KEY`

这些账号只由具体用例或任务按需读取、校验并 `skip`。

#### 更新 `util/__init__.py`

新增导出：

- `ConfigValidationError`
- `is_enabled`
- `parse_bool`
- `parse_positive_float`
- `parse_positive_int`
- `redact_config_summary`
- `require_http_url`
- `require_non_empty`

#### 更新 `tests/test_real_env_wan2_7_image.py`

变化：

- 原先直接读取 `os.getenv("RUN_REAL_ENV_TESTS")`。
- 现在改为使用统一开关函数：

```python
is_enabled("RUN_REAL_ENV_TESTS")
```

效果：

- 真实环境用例默认跳过。
- 设置 `RUN_REAL_ENV_TESTS=TRUE` 后才执行。
- 开关判断逻辑与后续高成本、破坏性用例保护保持一致。

### 测试变更

#### 新增 `tests/test_config_validation.py`

覆盖内容：

- 中国环境配置加载。
- 海外环境默认配置加载。
- 缺失中国环境 base URL 和 API Key 时聚合报错。
- 缺失海外环境 base URL 和 API Key 时聚合报错。
- 非法 URL 被拒绝。
- 非法 `API_TIMEOUT` 被拒绝。
- `API_TIMEOUT=0` 被拒绝。
- 非法 `HISTORY_REPORT_KEEP_LIMIT` 被拒绝。
- 非法 `USE_CHINA_ENVIRONMENT` 被拒绝。
- 布尔值大小写不敏感。
- `is_enabled()` 开关判断。
- 配置摘要脱敏。
- B 账号和 zero 账号不进入全局 `Settings`。

### 验证记录

执行命令：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_config_validation.py -q
```

结果：

```text
13 passed
```

执行命令：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_config_validation.py tests/test_api_call_logger.py tests/test_curl_builder.py tests/test_request_middleware.py tests/test_base_request_middleware.py -q
```

结果：

```text
43 passed
```

执行命令：

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
```

结果：

```text
72 passed, 1 skipped
```

执行命令：

```powershell
.\.venv\Scripts\python.exe run_master.py module/smoke --collect-only -q
```

结果：

```text
42 tests collected
```

### 当前边界

- 第一版没有引入 Pydantic。
- 第一版没有引入多配置文件合并。
- 第一版没有引入远程配置中心。
- 第一版没有把 B 账号或 zero 账号纳入全局配置。
- 第一版没有注册 pytest marker 或新增命令行参数。
