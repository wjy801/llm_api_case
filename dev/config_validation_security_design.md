# 配置校验与安全保护开发方案

## 1. 需求理解

根据 `FRAMEWORK_CAPABILITY_ROADMAP.md`，配置校验与安全保护属于 P0 能力，目标是在用例真正执行前发现配置问题，并避免密钥、环境和高成本用例失控。

当前代码已经完成请求中间件和统一脱敏的一部分基础建设：

- 请求日志、cURL、响应体和异常文本已经开始复用 `util.redaction`。
- `tests/test_real_env_wan2_7_image.py` 已经使用 `RUN_REAL_ENV_TESTS=TRUE` 保护真实环境用例。
- `BaseRequest` 仍通过 `config.settings` 获取 `base_url`、`api_key` 和 `timeout`。

因此本阶段开发重点不是重新设计配置系统，而是在现有结构上补齐启动前校验、安全输出和高风险用例开关。

## 2. 第一性原理与 TOC 分析

配置校验的本质是建立测试运行前的最低安全前提：

- 请求必须发往明确、合法的环境地址。
- 请求必须使用明确、非空的 API Key。
- timeout、报告开关、历史报告保留数量等运行参数必须可解析。
- 真实环境、高成本或破坏性用例必须由人显式启用。
- 控制台、异常、日志和报告不能泄露密钥。

当前约束点不在请求发送能力，而在配置错误暴露太晚、错误信息质量低：

1. `config.py` 在 import 阶段直接执行 `.rstrip()`、`.strip()`、`float()` 和 `int()`。
2. 如果环境变量缺失，可能抛出 `AttributeError`，无法直接看到缺失变量名。
3. 如果数值格式错误，只有原始 `ValueError`，没有框架语义。
4. 真实环境和高风险用例开关分散在用例里，后续容易重复实现。
5. 配置错误输出如果直接包含原始值，存在密钥泄露风险。

开发方案应先消除这些约束，而不是提前建设多配置文件、远程配置中心或复杂插件系统。

## 3. 第一版目标

第一版只解决以下问题：

1. 校验当前环境必需的 base URL 和 API Key。
2. 校验 `API_TIMEOUT`、`HISTORY_REPORT_KEEP_LIMIT`、报告开关等基础配置类型。
3. 缺失或格式错误时输出明确变量名和原因。
4. 明确系统环境变量、`.env` 和代码默认值的优先级。
5. 配置摘要、异常和终端输出复用统一脱敏规则。
6. 提供真实环境、高成本和破坏性用例的统一显式开关。
7. 保持现有 `settings.base_url`、`settings.api_key`、`settings.timeout` 等调用方式兼容。
8. 配置校验逻辑具备独立单元测试，不依赖真实 `.env`。

第一版不做：

- 不引入 Pydantic。
- 不引入多配置文件合并。
- 不引入远程配置中心。
- 不实现 Web 配置管理后台。
- 不实现密钥托管或密钥轮换。
- 不改造 `BaseRequest` 对 `settings` 的使用方式。
- 不把用例级账号密钥纳入全局配置模型。

## 4. 当前问题定位

### 4.1 `config.py` import 阶段错误质量低

当前代码：

```python
timeout: float = float(os.getenv("API_TIMEOUT",600))
base_url: str = os.getenv("CHINA_TEST_ENVIRONMENT_BASE_URL").rstrip("/")
api_key: str = os.getenv("CHINA_API_KEY").strip()
```

问题：

- 缺失 `CHINA_TEST_ENVIRONMENT_BASE_URL` 时是 `None.rstrip()`。
- 缺失 `CHINA_API_KEY` 时是 `None.strip()`。
- `API_TIMEOUT=abc` 时是原始 `ValueError`。
- 错误没有聚合，用户需要反复修复、反复运行。

### 4.2 环境变量优先级不显式

当前使用：

```python
load_dotenv()
```

这意味着默认情况下：

- 系统环境变量优先。
- `.env` 只补充不存在的变量。

第一版应保留这个行为，但在配置解析函数和文档中显式说明。

### 4.3 安全保护已有基础但未统一

当前已有：

- `util.redaction`：统一脱敏工具。
- `RUN_REAL_ENV_TESTS`：真实环境用例开关。

下一步应将这些能力收口，避免不同用例各自判断、各自输出错误。

## 5. 建议文件结构

不做整体目录重构，优先放入现有 `util`、根目录和 `tests`：

```text
config.py
util/
  config_validation.py
tests/
  test_config_validation.py
```

职责：

- `util/config_validation.py`
  - 提供纯函数，不依赖 `config.Settings`。
  - 负责解析 bool、float、int、URL、非空字符串。
  - 提供 `ConfigValidationError`。
  - 提供真实环境/高风险开关判断函数。
  - 提供脱敏配置摘要函数。

- `config.py`
  - 继续加载 `.env`。
  - 定义 `Settings`。
  - 新增 `load_settings(env=None)`。
  - 保留 `settings = load_settings()`。
  - 保留 `USE_CHINA_ENVIRONMENT`，但由校验函数解析。

- `tests/test_config_validation.py`
  - 只测配置解析和校验，不访问真实环境。

## 6. 配置读取优先级

第一版保持当前 `python-dotenv` 行为：

```python
load_dotenv()
```

优先级：

1. 系统环境变量优先。
2. `.env` 补充系统环境变量中不存在的值。
3. 代码默认值只用于非敏感、低风险配置。
4. base URL 和 API Key 不允许静默默认。

必填规则：

- `USE_CHINA_ENVIRONMENT=TRUE` 时必填：
  - `CHINA_TEST_ENVIRONMENT_BASE_URL`
  - `CHINA_API_KEY`

- `USE_CHINA_ENVIRONMENT=FALSE` 时必填：
  - `OVERSEAS_TEST_BASE_URL`
  - `OVERSEAS_API_KEY`

## 7. Settings 设计

继续使用 dataclass。

```python
@dataclass(frozen=True)
class Settings:
    timeout: float
    generate_allure_report: bool
    generate_history_report: bool
    history_report_keep_limit: int
    base_url: str
    api_key: str
    environment_name: str
```

说明：

- `base_url`、`api_key`、`timeout` 等现有字段必须保留。
- `environment_name` 可新增，用于配置摘要和报告定位。
- 如果担心影响现有代码，`environment_name` 可以先作为新增字段，不替代 `USE_CHINA_ENVIRONMENT`。

新增入口：

```python
def load_settings(env: Mapping[str, str | None] | None = None) -> Settings:
    ...
```

默认：

```python
settings = load_settings()
```

这样测试可以传入模拟 env，避免依赖本机 `.env`。

## 8. 校验规则

### 8.1 布尔配置

适用：

- `USE_CHINA_ENVIRONMENT`
- `GENERATE_ALLURE_REPORT`
- `GENERATE_HISTORY_REPORT`
- `RUN_REAL_ENV_TESTS`
- `RUN_HIGH_COST_TESTS`
- `RUN_DESTRUCTIVE_TESTS`

规则：

- 接受 `TRUE` / `FALSE`，大小写不敏感。
- 空值可使用默认值，仅限有默认值的字段。
- 非法值抛出 `ConfigValidationError`。

示例：

```text
Invalid config USE_CHINA_ENVIRONMENT='yes'. Expected TRUE or FALSE.
```

### 8.2 URL 配置

适用：

- `CHINA_TEST_ENVIRONMENT_BASE_URL`
- `OVERSEAS_TEST_BASE_URL`

规则：

- 必填。
- 去除首尾空白。
- 必须以 `http://` 或 `https://` 开头。
- 保存时去除末尾 `/`。

示例：

```text
Missing required config CHINA_TEST_ENVIRONMENT_BASE_URL.
Invalid config OVERSEAS_TEST_BASE_URL='pre.example.com'. Expected http(s) URL.
```

### 8.3 API Key 配置

全局适用：

- `CHINA_API_KEY`
- `OVERSEAS_API_KEY`

用例级适用，不进入全局配置：

- `CHINA_CONTROL_API_KEY`
- `OVERSEAS_CONTROL_API_KEY`
- `B_ACCOUNT_API_KEY`
- `B_ACCOUNT_CONTROL_KEY`
- `ZERO_BALANCE_API_KEY`
- `ZERO_BALANCE_CONTROL_KEY`

规则：

- 当前环境主 API Key 全局必填。
- 控制台密钥、B 账号、zero 账号等只在对应业务用例中按需读取和校验，不作为全局配置项。
- B 账号和 zero 账号不进入 `Settings`、`load_settings()`、全局启动校验和全局配置摘要。
- 错误信息只输出变量名，不输出原始值。
- 配置摘要中所有 key/token/secret/password/authorization 字段显示 `<redacted>`。

### 8.4 数字配置

适用：

- `API_TIMEOUT`
- `HISTORY_REPORT_KEEP_LIMIT`

规则：

- `API_TIMEOUT` 转为 `float`，必须大于 0。
- `HISTORY_REPORT_KEEP_LIMIT` 转为 `int`，必须大于等于 1。

示例：

```text
Invalid config API_TIMEOUT='abc'. Expected positive number.
Invalid config HISTORY_REPORT_KEEP_LIMIT='0'. Expected integer >= 1.
```

## 9. 异常与错误聚合

新增：

```python
class ConfigValidationError(RuntimeError):
    pass
```

建议一次聚合多个错误：

```text
Configuration validation failed:
- Missing required config CHINA_TEST_ENVIRONMENT_BASE_URL.
- Missing required config CHINA_API_KEY.
- Invalid config API_TIMEOUT='abc'. Expected positive number.
```

价值：

- 用户一次看到完整配置缺口。
- 避免修一个变量、跑一次、再暴露下一个。
- 错误信息可以直接用于面试讲解“失败前移”和“可定位性”。

## 10. 建议 API

`util/config_validation.py` 建议提供：

```python
class ConfigValidationError(RuntimeError):
    ...


def parse_bool(name: str, value: str | None, *, default: bool | None = None) -> bool:
    ...


def parse_positive_float(name: str, value: str | None, *, default: float | None = None) -> float:
    ...


def parse_positive_int(name: str, value: str | None, *, default: int | None = None) -> int:
    ...


def require_non_empty(name: str, value: str | None) -> str:
    ...


def require_http_url(name: str, value: str | None) -> str:
    ...


def is_enabled(name: str, env: Mapping[str, str | None] | None = None) -> bool:
    ...


def redact_config_summary(summary: Mapping[str, object]) -> dict[str, object]:
    ...
```

`config.py` 负责组装：

```python
def load_settings(env: Mapping[str, str | None] | None = None) -> Settings:
    env = os.environ if env is None else env
    use_china = parse_bool("USE_CHINA_ENVIRONMENT", env.get("USE_CHINA_ENVIRONMENT"), default=False)
    ...
```

## 11. 真实环境与高风险用例保护

统一开关：

```text
RUN_REAL_ENV_TESTS=TRUE
RUN_HIGH_COST_TESTS=TRUE
RUN_DESTRUCTIVE_TESTS=TRUE
```

第一版提供工具函数：

```python
def is_enabled(name: str, env: Mapping[str, str | None] | None = None) -> bool:
    ...
```

用例使用：

```python
pytestmark = pytest.mark.skipif(
    not is_enabled("RUN_REAL_ENV_TESTS"),
    reason="Set RUN_REAL_ENV_TESTS=TRUE to run real environment tests.",
)
```

迁移建议：

- `tests/test_real_env_wan2_7_image.py` 改为复用 `is_enabled()`。
- 后续高成本模型调用加 `RUN_HIGH_COST_TESTS`。
- 破坏性或影响余额/资源状态的用例加 `RUN_DESTRUCTIVE_TESTS`。

第一版不需要注册 pytest marker，也不需要新增大量命令行参数。

## 12. 与现有脱敏能力的集成

必须复用 `util.redaction`。

建议：

```python
def redact_config_summary(summary: Mapping[str, object]) -> dict[str, object]:
    return redact_sensitive_data(summary)
```

配置摘要示例：

```python
{
    "environment_name": "china",
    "base_url": "https://pre.example.com",
    "api_key": "<redacted>",
    "timeout": 600.0,
}
```

禁止：

- 在异常中输出 API Key 原文。
- 在终端输出完整 Authorization。
- 为配置校验再维护一套敏感字段列表。

## 13. pytest 启动接入

推荐方式：

- 核心校验放在 `load_settings()` 中。
- `settings = load_settings()` 保证任意入口都能尽早失败。
- `module/conftest.py` 只负责可选地输出脱敏配置摘要。

可选输出：

```text
Config loaded: environment=china, base_url=https://pre.example.com, api_key=<redacted>, timeout=600.0
```

注意：

- `--collect-only` 是否强制校验需要权衡。
- 当前业务用例收集通常会 import `config.settings`，因此基础配置错误会在收集期暴露。
- 纯单元测试应通过 `load_settings(env=...)` 避免依赖真实 `.env`。

## 14. 实施顺序

1. 新增 `util/config_validation.py`
   - 实现 `ConfigValidationError`。
   - 实现 bool、float、int、URL、非空字符串解析。
   - 实现 `is_enabled()` 和 `redact_config_summary()`。

2. 改造 `config.py`
   - 新增 `load_settings(env=None)`。
   - 用解析函数替代直接 `.strip()`、`.rstrip()`、`float()`、`int()`。
   - 保留 `settings = load_settings()`。
   - 保留现有字段名。

3. 改造真实环境用例开关
   - `tests/test_real_env_wan2_7_image.py` 改用 `is_enabled("RUN_REAL_ENV_TESTS")`。

4. 补充单元测试
   - 新增 `tests/test_config_validation.py`。
   - 覆盖缺失配置、非法配置、脱敏摘要、开关判断。

5. 回归验证
   - 跑配置校验单测。
   - 跑现有请求中间件和脱敏测试。
   - 跑全量 `tests`。
   - 跑 `run_master.py module/smoke --collect-only -q`。

## 15. 单元测试设计

新增：

```text
tests/test_config_validation.py
```

建议覆盖：

1. 中国环境缺失 `CHINA_TEST_ENVIRONMENT_BASE_URL` 时，错误包含变量名。
2. 中国环境缺失 `CHINA_API_KEY` 时，错误包含变量名但不包含密钥值。
3. 海外环境缺失 `OVERSEAS_TEST_BASE_URL` / `OVERSEAS_API_KEY` 时，错误准确。
4. 非法 URL 被拒绝。
5. `API_TIMEOUT=abc` 被拒绝。
6. `API_TIMEOUT=0` 被拒绝。
7. `HISTORY_REPORT_KEEP_LIMIT=0` 被拒绝。
8. `USE_CHINA_ENVIRONMENT=yes` 被拒绝。
9. `TRUE` / `FALSE` 大小写不敏感。
10. `load_settings(env=...)` 返回正确 `Settings`。
11. base URL 自动去除末尾 `/`。
12. 配置摘要中 API Key 被脱敏。
13. `RUN_REAL_ENV_TESTS=TRUE` 时开关启用，否则关闭。
14. 控制台密钥、B 账号、zero 账号不作为全局必填。
15. B 账号和 zero 账号不进入 `Settings`、`load_settings()` 和全局配置摘要。

测试必须使用传入 env 字典，不依赖本机 `.env`。

## 16. 验证命令

目标单测：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_config_validation.py -q
```

相关回归：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_config_validation.py tests/test_api_call_logger.py tests/test_curl_builder.py tests/test_request_middleware.py tests/test_base_request_middleware.py -q
```

全量单测：

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
```

用例收集：

```powershell
.\.venv\Scripts\python.exe run_master.py module/smoke --collect-only -q
```

## 17. 风险与处理

### 17.1 离线单测被真实 `.env` 阻塞

风险：全局 `settings = load_settings()` 变严格后，缺 `.env` 的机器跑 `tests` 可能失败。

处理：

- 配置校验测试使用 `load_settings(env=...)`。
- 当前测试环境应保留 `.env` 或在 CI 中注入基础 env。
- 如果后续需要完全离线单测，可将涉及 `BaseRequest()` 默认配置的测试统一传入 `DummyConfig`。

### 17.2 误把用例级账号纳入全局配置

风险：把 `B_ACCOUNT_API_KEY`、`B_ACCOUNT_CONTROL_KEY`、`ZERO_BALANCE_API_KEY` 或 `ZERO_BALANCE_CONTROL_KEY` 纳入全局配置，会导致不相关用例无法运行，也会把业务用例数据前置误建模为框架级配置。

处理：

- 第一版只全局校验当前环境主 API Key。
- B 账号和 zero 账号由对应任务或用例按需读取、校验并 skip。
- B 账号和 zero 账号不得加入 `Settings` 字段、`load_settings()` 必填项或配置摘要。

### 17.3 错误信息泄露密钥

风险：聚合错误或配置摘要输出原始值。

处理：

- API Key 缺失只输出变量名。
- 配置摘要统一调用 `redact_config_summary()`。
- 不在异常中输出敏感字段原文。

### 17.4 破坏现有调用方式

风险：业务代码依赖 `settings.base_url`、`settings.api_key`。

处理：

- 保持字段名不变。
- 保持 `settings = load_settings()`。
- 不改变 `BaseRequest(config=settings)` 默认签名。

## 18. 第一版完成标准

- 缺失 base URL 或 API Key 时，在请求发出前失败，并明确指出变量名。
- 非法 timeout、历史报告保留数量、布尔开关有清晰错误。
- 控制台、异常、配置摘要不泄露 API Key。
- 真实环境、高成本和破坏性用例有统一显式开关。
- 配置校验逻辑有独立单元测试。
- 现有请求中间件、日志脱敏和 `BaseRequest` 调用方式不回退。
- `tests` 全量单测通过。
- `run_master.py module/smoke --collect-only -q` 可正常收集。
