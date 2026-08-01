# API Test Framework

基于 `pytest`、`requests` 和 `allure-pytest` 的代码式接口测试框架。

当前框架按“通用基础能力 + 模型模块继承 + pytest nodeid 用例池执行”的方式组织。框架能力服务于现有接口自动化，不引入 YAML、Excel 或隐式 DSL；真实用例仍以 Python 代码表达业务链路。

## 当前状态

- 基础层已实现请求中间件、配置校验与脱敏、契约断言、显式重试、轮询状态机、测试上下文、轻量 Mock 与故障模拟。
- 协议与业务层已覆盖 OpenAI Chat Completions、Responses、Anthropic Messages，以及图片、视频和真实 Smoke 链路。
- P0 已实现质量结果归并、完整性校验、失败分类、请求指标、中文影子门禁报告和机器可读产物。
- P1 已实现逻辑调用语义、HTTP/SSE/异步耗时、Token/媒体用量覆盖、Flaky 最小历史存储、状态机与治理命令。
- Jenkins 已支持参数化执行、并发优先与串行收尾、JUnit/Allure、P0/P1 中文报告、邮件直达链接和构建产物自动清理。
- 当前门禁仍为影子模式；P1 不引入估算成本和性能基线，缺失数据不会按零计算，也不会伪造结论。

推荐的日常使用顺序：

```text
Jenkins 构建结果
-> P0 gate-report.md：确认本轮数据是否完整、是否存在失败或基础稳定性风险
-> P1 p1-observation.md：查看耗时、用量覆盖和 Flaky 状态变化
-> Allure：定位具体用例步骤、请求响应和附件
-> Flaky CLI：对已确认的问题执行纠正、隔离或恢复治理
```

## 目录结构

```text
common/
  base_request.py          # BaseRequest：HTTP 请求、请求中间件、重试、轮询
  base_assertions.py       # BaseAssertions：状态码、JSONPath、JSON Schema 断言
  base_decorators.py       # Allure step、模型结果附件、下载结果挂载
  base_task.py             # 通用业务骨架：创建、轮询、账单/用量查询
  request_context.py       # 单次请求上下文
  request_middleware.py    # Redaction、Logging、MediaResource 中间件
  retry.py                 # RetryPolicy 与重试判定/退避计算
  polling.py               # PollingPolicy、PollingState 与轮询异常
  test_context.py          # 用例级变量传递与清理回调
  __init__.py              # 延迟导出通用对象

util/
  api_call_logger.py       # 请求/响应/异常/重试/轮询日志写入 Allure
  config_validation.py     # 配置校验、类型解析、错误聚合
  curl_builder.py          # 脱敏 cURL 生成
  media_resources.py       # POST 前 input.media.url 异步下载与 Allure 挂载
  redaction.py             # 统一脱敏规则
  __init__.py

module/
  conftest.py              # pytest fixture、Allure 清理、报告生成、test_context fixture
  image_model/             # 图片模型真实用例
  video_model/             # 视频模型真实用例
  smoke/                   # Smoke 用例、响应 Schema、业务 payload builder
  protocol_testing/        # OpenAI/Anthropic 协议兼容性用例

quality/
  collector.py             # P0 Case/请求事实采集
  aggregator.py            # P0 分片归并与完整性校验
  classifier.py            # 失败分类与稳定指纹
  report.py                # P0 摘要、影子门禁及中文 Markdown
  semantic_*.py            # 逻辑调用、请求组、轮询与流式语义
  metrics.py               # P1 单次运行指标聚合
  observation_report.py    # P1 中文观察与 Flaky 报告
  flaky*.py                # Flaky 历史、状态机、投影与治理
  migrations/flaky/        # Flaky SQLite 迁移脚本

tests/
  mock_helpers.py          # 离线响应、故障、流式响应和睡眠记录工具
  quality/                 # P0/P1、Flaky、Jenkins 集成回归测试
  test_*.py                # 框架基础能力单测

dev/                       # 各阶段设计方案
code_history/              # 各阶段独立变更历史
config.py                  # Settings 与环境配置加载
master_service.py          # pytest nodeid 收集服务
run_master.py              # 框架执行入口
Jenkinsfile                # Jenkins 单 Job 参数化流水线
JENKINS_MIGRATION_TEMPLATE.md # Jenkins 环境迁移与复建模板
pytest.ini                 # pytest 与 Allure 默认配置
requirements.txt           # Python 依赖
package.json               # Allure CLI 依赖
.env.example               # 环境变量示例
```

以下目录为本地生成产物，不应提交到仓库：

```text
allure-results/
allure-report/
history_report/
reports/
node_modules/
.pytest_cache/
__pycache__/
data/
```

## 安装依赖

安装 Python 依赖：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

安装 Allure CLI 本地依赖：

```powershell
npm install
```

说明：

- `allure-pytest` 负责生成 Allure 原始结果。
- `allure-commandline` 负责把原始结果生成 HTML 报告。
- `pytest-xdist` 支持用例级并发执行。
- Allure CLI 依赖 Java。框架会优先使用系统 `java`，找不到时会尝试常见 IDE 自带的 JBR/JRE。

## 环境配置

创建或修改 `.env`，按 `config.py` 当前读取的变量配置：

```text
USE_CHINA_ENVIRONMENT=TRUE

CHINA_TEST_ENVIRONMENT_BASE_URL=https://pre.juhemoxing.com
CHINA_API_KEY=your-china-api-key
CHINA_CONTROL_API_KEY=your-china-control-api-key

OVERSEAS_TEST_BASE_URL=https://pre.tokensave.pro
OVERSEAS_API_KEY=your-overseas-api-key
OVERSEAS_CONTROL_API_KEY=your-overseas-control-api-key

API_TIMEOUT=600
GENERATE_ALLURE_REPORT=TRUE
GENERATE_HISTORY_REPORT=FALSE
HISTORY_REPORT_KEEP_LIMIT=30
```

账单、余额及用量查询需要对应环境的 `*_CONTROL_API_KEY`。特殊账号 Key 继续按 `.env.example` 配置，不写入代码或 Jenkinsfile。

本地启用完整 P0/P1 数据链路时，可在 PowerShell 当前进程中设置：

```powershell
$env:QUALITY_ENABLE = '1'
$env:QUALITY_SEMANTIC_ENABLE = '1'
$env:QUALITY_METRICS_ENABLE = '1'
$env:QUALITY_P1_REPORT_ENABLE = '1'
$env:QUALITY_OUTPUT_DIR = 'reports/quality'
$env:QUALITY_SHADOW_GATE = '1'
```

启用 Flaky 历史与状态机还需要：

```powershell
$env:QUALITY_FLAKY_HISTORY_ENABLE = '1'
$env:QUALITY_FLAKY_STATE_ENABLE = '1'
$env:QUALITY_FLAKY_DB_PATH = 'D:\your-persistent-path\flaky-history.db'
```

`QUALITY_FLAKY_DB_PATH` 必须是可写的绝对持久路径，父目录需要提前创建；同一个数据库只允许一个 Jenkins Job 独占写入。Jenkins 真实 Smoke 会自动打开 P0、语义指标和 P1 报告，并在检测到有效数据库路径时启用 Flaky 历史。

环境开关：

```text
USE_CHINA_ENVIRONMENT=TRUE   # 国内环境
USE_CHINA_ENVIRONMENT=FALSE  # 海外环境
```

`load_settings()` 会在执行前校验 URL、API Key、超时和报告开关配置。配置错误会聚合为明确的变量名错误，不再等到请求阶段暴露模糊异常。

当前配置校验由 Pydantic 模型承接，但对外仍保持原有接口：

- `load_settings()` 返回 `Settings`，字段名和默认值保持不变。
- 配置缺失、类型错误、非法 URL 等仍通过 `ConfigValidationError` 暴露。
- 多个必填配置缺失时仍聚合输出，不只报第一个错误。

安全边界：

- 旧的 `BASE_URL`、`API_KEY` 变量当前不会被 `config.py` 读取。
- B 账号、zero 账号等特殊账号不进入全局统一配置，使用范围限制在明确用例或局部 fixture 中。
- 日志、异常、cURL、Allure 附件和配置摘要共用 `util/redaction.py` 的脱敏规则。

## 已实现框架能力

### 请求中间件

`BaseRequest` 的请求生命周期已经抽象为中间件管线：

```python
RequestMiddleware.before_request(context)
RequestMiddleware.after_response(context, response)
RequestMiddleware.on_exception(context, error)
```

默认中间件包括：

- `RedactionMiddleware`：对请求参数建立脱敏副本。
- `LoggingMiddleware`：输出请求、响应、异常、重试记录和轮询迁移日志。
- `MediaResourceMiddleware`：在 POST 前收集 `input.media.url` 前置资源下载任务。

每次请求使用独立 `RequestContext`，请求参数会尽量深拷贝，避免中间件污染调用方 payload。`on_exception()` 中间件自身失败时不会覆盖原始网络异常。

### 配置校验与安全保护

`util/config_validation.py` 提供：

```python
parse_bool()
parse_positive_float()
parse_positive_int()
require_http_url()
require_non_empty()
aggregate_config_errors()
redact_config_summary()
is_enabled()
```

`config.py` 使用 Pydantic `Settings` 模型保存当前环境配置，并在导入时完成基础校验。脱敏能力集中在 `util/redaction.py`，`api_call_logger.py` 与 `curl_builder.py` 都复用同一套规则。

后续如果从 `.env` 扩展到 YAML/JSON/TOML 等文件型配置，继续优先使用 Pydantic 做结构化校验，用模型约束替代散落的手写属性类和字段解析逻辑。

当前已迁移到 Pydantic 的校验模型包括：

- `Settings`
- `RetryPolicy`
- `RetryAttemptRecord`
- `PollingPolicy`
- `PollingEvaluation`
- `PollingTransition`

这些模型均使用 frozen 配置，避免运行中被意外改写。纯内部记录结构和测试辅助结构没有强制迁移，避免为了统一形式引入不必要的模型成本。

### 基础契约断言

`BaseAssertions` 已支持：

```python
assert_status_code(response, expected)
assert_json_value(response, json_path, expected)
assert_json_path_exists(response, json_path)
assert_schema(response, schema)
async_assert_status_code(response, expected)
async_assert_json_value(response, json_path, expected)
async_assert_json_path_exists(response, json_path)
async_assert_schema(response, schema)
```

`assert_schema()` 使用 JSON Schema 校验响应结构，并在失败信息中输出 JSONPath、Schema path、期望、实际类型和值；敏感值会先脱敏。`module/smoke/response_schemas.py` 提供成功响应和标准错误响应 Schema 示例。

### 重试策略

`common/retry.py` 提供显式启用的 Pydantic `RetryPolicy`。默认请求不自动重试，避免掩盖真实服务问题。

能力边界：

- 支持 429、指定 5xx、连接异常、超时异常的重试判定。
- 支持固定/指数退避、jitter、`Retry-After` 和最大总耗时。
- GET/HEAD 默认允许重试；POST 只有带幂等键或显式 `allow_post=True` 时才允许重试。
- 每次重试原因、等待时间、响应状态或异常类型会写入 Allure 附件。
- `RetryPolicy(...)` 的公开构造参数保持兼容，非法参数仍以 `ValueError` 语义暴露。

示例：

```python
from common import RetryPolicy

response = client.get(
    "/v1/models",
    retry_policy=RetryPolicy(max_attempts=3),
)
```

### 轮询状态机

`common/polling.py` 提供 Pydantic `PollingPolicy`、`PollingState` 和轮询异常：

- `PollingFailedError`
- `PollingUnknownStateError`
- `PollingTimeoutError`

`poll_get()` 已全面迁移为 `polling_policy` 状态机入口，不再支持 `success_json_path` / `failure_json_path` 旧参数：

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

状态机能够记录状态迁移序列，区分等待、成功、失败、未知状态和超时。

媒体生成类任务默认使用 `DEFAULT_MEDIA_POLLING_POLICY`，由 `BaseTask.poll_media_generation_result()` 和 `BaseTask.create_and_poll_media_generation()` 自动传入。直接调用 `BaseRequest.poll_get()` 时必须显式传入 `PollingPolicy`。

兼容性边界：

- `PollingPolicy(...)` 的公开构造参数保持兼容。
- `PollingTransition(1, 0.0, state, status, 200)` 的旧位置参数写法仍可使用。
- 非法 JSONPath、未知状态策略等仍以 `ValueError` 语义暴露。
- 旧版 `success_json_path` / `failure_json_path` 调用方式已删除。

### 测试上下文与变量传递

`common/test_context.py` 提供用例级 `TestContext`，`module/conftest.py` 提供非 autouse 的 `test_context` fixture。

支持能力：

- `set()`、`get()`、`require()`、`delete()`、`snapshot()`。
- 从 JSONPath、Header、Cookie、Regex 提取变量。
- `extract_first()` 按优先级从多个来源提取。
- 类型校验、默认值、`allow_none`、`transform`。
- 用例结束后的 cleanup 回调。
- 错误信息脱敏。

示例：

```python
def test_chain(self, test_context):
    response = self.smoke_task.create_chat_completion(self.smoke_request, payload)
    test_context.extract("request_id", response, header="x-oneapi-request-id")
    request_id = test_context.require("request_id", expected_type=str)
```

### 轻量 Mock 与故障模拟

第一版离线回归能力放在 `tests/mock_helpers.py`，不引入独立 Mock Server。

已提供：

- `make_response()`：构造 `requests.Response`。
- `SequenceTransport`：按顺序返回响应或抛出异常，并记录调用。
- `SleepRecorder`：记录退避和轮询等待。
- `FakeApiCallLogger` / `create_fake_logger()`：验证日志挂载行为。
- `connection_error()`、`connect_timeout()`、`read_timeout()`、`timeout_error()`。
- `polling_responses()`：快速生成轮询状态序列。
- `FakeStreamResponse`：模拟 SSE/流式响应中断和 chunk 行为。

该能力用于框架核心分支单测，不替代真实环境用例。

## 分层规范

`common/` 只放所有模型都能复用的通用能力。具体模型路径、payload builder、模型 ID 和真实测试数据应放在对应 `module/<model_name>/` 中。

每个模型目录建议固定包含：

```text
request.py
assertions.py
decorators.py
task.py
test_*.py
__init__.py
```

新增模块时，每个独立文件的类应分别继承 `common` 中对应公共基类：

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

`task.py` 只服务当前目录下的测试用例，用于封装本模块独有的业务方法。通用创建、轮询和业务组合骨架优先沉淀到 `BaseTask`。不同模型目录的 `task.py` 不应互相引用。

## 用例写法

测试类中不要定义 `__init__`，pytest 会跳过带自定义 `__init__` 的测试类。

推荐写法：

```python
from module.image_model import ImageAssertions, ImageRequest, ImageTask


class TestImageGenerations:
    def setup_method(self):
        self.image_request = ImageRequest()
        self.image_assertions = ImageAssertions()
        self.image_task = ImageTask()

    def teardown_method(self):
        self.image_request.close()

    def test_create_image_generation(self):
        response = self.image_task.create_and_poll_media_generation(
            self.image_request,
            payload,
        )
        self.image_assertions.assert_status_code(response, 200)
```

约束：

- 用例文件放在 `module/<model_name>/test_*.py`。
- 用例中统一通过模块 `Task` 调用业务动作。
- payload 使用 Python 字典，不使用 YAML。
- 不在用例中硬编码完整环境域名。
- 不在用例中硬编码 API Key。
- 新增或修改用例后先执行 `--collect-only -q` 确认可收集。

## 执行入口

`master_service.py` 负责收集 pytest nodeid 和 marker，并支持把用例拆分为普通并发池与 `serial` 串行池。直接执行可输出收集结果：

```powershell
.\.venv\Scripts\python.exe master_service.py
```

`run_master.py` 是当前框架执行入口。它先调用 `master_service.collect_test_case_items()` 收集 nodeid 和 marker，再按执行参数组织 pytest 调用。

执行全部业务用例：

```powershell
.\.venv\Scripts\python.exe run_master.py
```

执行指定目录：

```powershell
.\.venv\Scripts\python.exe run_master.py module/smoke
```

传递额外 pytest 参数：

```powershell
.\.venv\Scripts\python.exe run_master.py module/smoke -n 2
```

传入 `-n/--numprocesses` 后启用“并发优先、串行收尾”：

```text
收集全部用例及 marker
-> 未标记 serial 的用例使用 pytest-xdist 并发执行
-> 并发池结束后，标记 serial 的用例单进程执行
-> 两个用例池分别生成 JUnit 文件
-> Jenkins 归档报告，邮件摘要汇总统计
```

计费、余额、共享账号或其他依赖共享状态的用例应使用：

```python
pytestmark = pytest.mark.serial
```

未传入 `-n` 时，全部用例按普通 pytest 串行执行。

只验证收集，不执行接口：

```powershell
.\.venv\Scripts\python.exe run_master.py module/smoke --collect-only -q
```

直接执行框架单测：

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
```

## 质量工程 P0/P1

质量能力由 `run_master.py` 在测试结束时统一收口，测试代码仍按原方式编写，不需要直接调用聚合器：

```text
pytest Case/请求事实分片
-> P0 归并与完整性校验
-> 失败分类、请求指标与影子门禁
-> 逻辑调用/请求组/轮询语义归并
-> P1 耗时与资源用量聚合
-> Flaky 历史导入和状态评估
-> P1 单次观察报告
```

### P0：可信结果与影子门禁

P0 解决“这轮结果能不能信、为什么失败”：

- 对并发池和串行池的 Case、请求及失败事实统一归并。
- 校验执行分片、预期用例数量、文件哈希和运行 ID，完整性异常不会被包装成正常结论。
- 按产品、测试、框架、环境、配置、瞬时故障和未知原因聚合失败。
- 统计请求总量、HTTP 5xx、超时和接口耗时。
- 输出影子门禁 `PASS/WARN/BLOCK/NO_DATA`；影子门禁只提供决策证据，不修改 pytest/Jenkins 结果。

请求成功率与用例通过率不是同一指标：负向用例和轮询中的中间业务状态可能计为请求失败，但用例仍可按预期通过。人工判断应优先看用例结果、5xx、超时和失败分类，而不是孤立使用请求成功率。

主要产物：

| 文件 | 用途 |
| --- | --- |
| `reports/quality/run.json` | 本次运行身份、时间、状态及完整性 |
| `reports/quality/summary.json` | P0 完整机器数据 |
| `reports/quality/gate-report.json` | 机器可读门禁规则与证据 |
| `reports/quality/gate-report.md` | 中文 P0 门禁报告，人工查看首选 |
| `reports/quality/merged/*.jsonl` | 归并后的 Case、请求、失败和完整性事实 |

### P1：单次运行指标与用量覆盖

P1 解决“真实调用慢在哪里、资源数据是否完整”：

- 以逻辑调用而不是单个 HTTP 请求为主要观察单位。
- 区分 HTTP、SSE、异步任务、轮询等待和控制流量。
- 聚合调用总耗时、响应头等待、轮询总耗时和轮询休眠时间。
- 聚合输入/输出 Token、媒体数量及对应样本量。
- 用 `complete/partial/no_data/not_applicable` 表达完整性；缺失值不按零计算。
- 当前不估算成本，也不建立性能基线或耗时门禁。

逻辑调用失败数同样不等于测试失败数：验证错误响应、失败任务或流式中断的负向场景可能产生预期失败调用。P1 用于观察真实工作负载，不替代 P0 用例结论。

主要产物：

| 文件 | 用途 |
| --- | --- |
| `reports/quality/metrics/run-metrics.json` | 完整单次运行指标 |
| `reports/quality/p1-observation.json` | P1 机器可读观察报告 |
| `reports/quality/p1-observation.md` | 中文 P1 指标与 Flaky 报告，人工查看首选 |
| `reports/quality/semantic/merged/*.jsonl` | 逻辑调用、请求组和轮询会话事实 |

两份 Markdown 报告优先使用中文，同时保留规则 ID、指标键、状态码、问题代码和版本号；JSON 字段与原始枚举保持不变，便于机器消费和问题追踪。

### Flaky 状态机与治理

Flaky 历史只导入可信、已完成运行中的可比较用例；skip/xfail/xpass 不进入波动判断。状态流转为：

```text
OBSERVING（观察中）
-> STABLE（稳定）或 SUSPECTED（疑似不稳定）
-> CONFIRMED（已确认不稳定）
-> QUARANTINED（已隔离）
-> RECOVERING（恢复观察中）
```

默认规则要求至少 3 个一致样本才能判定稳定；确认 Flaky 需要至少 4 个样本、至少 2 次通过、2 次失败和 2 次结果切换。单次失败不会直接判定 Flaky。

常用命令：

```powershell
# 查看所有治理命令
.\.venv\Scripts\python.exe -m quality.cli --help

# 检查数据库结构与 SQLite 完整性
.\.venv\Scripts\python.exe -m quality.cli flaky-db-check --db D:\path\flaky-history.db

# 查询一个用例的历史与当前状态
.\.venv\Scripts\python.exe -m quality.cli flaky-history --db D:\path\flaky-history.db --case-id "module/smoke/test_xxx.py::TestXxx::test_xxx"
.\.venv\Scripts\python.exe -m quality.cli flaky-state --db D:\path\flaky-history.db --case-id "module/smoke/test_xxx.py::TestXxx::test_xxx"
```

如果用例语义或测试实现已经明确改变，应使用 `flaky-reset-epoch` 开启新样本周期；已知代码修复导致的“失败变通过”不应直接确认为 Flaky。`QUARANTINED` 只是带 owner、原因和到期时间的治理标签，不会自动跳过用例。

### Smoke 中的质量能力

`module/smoke` 已接入真实模型调用、响应契约、账单和异步任务观察：

- 共享余额、计费及其他会互相污染状态的用例标记为 `serial`。
- 模型账单采用实际余额扣减与 usage 金额的区间断言，不引入估算成本。
- 预扣款场景在模型调用后的第二次余额查询前默认等待 5 秒，再读取结算数据。
- 异步任务完成前的余额查询仍立即执行，用于验证未完成任务不会提前扣款。
- 一个用例并发调用多个模型时，按 request ID 分别查询 usage 后求和，与账户余额扣减比较。

## Allure 报告

`pytest.ini` 默认配置：

```ini
addopts =
    --alluredir=allure-results
    --clean-alluredir
testpaths = module
python_files = test_*.py
python_classes = Test*
python_functions = test_*
```

执行 pytest 后：

- `allure-results/` 保存 Allure 原始结果。
- `module/conftest.py` 在 pytest 结束后按配置执行 `allure generate`。
- `allure-report/` 保存 HTML 报告。
- 开启 `GENERATE_HISTORY_REPORT=TRUE` 后会生成历史报告并按 `HISTORY_REPORT_KEEP_LIMIT` 清理。

打开报告：

```powershell
node_modules\.bin\allure.cmd open allure-report
```

## Jenkins CI

当前仓库已通过根目录 `Jenkinsfile` 接入 Jenkins，并使用单个参数化 Pipeline Job 管理框架测试、Smoke 收集和可选真实环境测试。

### 流水线阶段

```text
Checkout
-> Check Runtime Env
-> Prepare Python Env
-> Framework Unit Tests（可选）
-> Collect Smoke Cases（可选）
-> Real Smoke（可选）
-> JUnit / Allure / Artifacts / Email
```

当前阶段职责：

- `Checkout`：从 SCM 拉取当前分支代码和 `Jenkinsfile`。
- `Check Runtime Env`：确认 workspace 中存在 `.env`；当前 Windows Agent 可从本机受控路径复制，不把凭据写入仓库。
- `Prepare Python Env`：创建或复用 `.venv`，安装 Python 和 npm 依赖，并清理本次 `reports/` 目录。
- `Framework Unit Tests`：执行 `tests/` 下的框架测试并发布 `reports/unit-tests.xml`。
- `Collect Smoke Cases`：只收集真实业务用例，不调用真实接口；收集结果以 UTF-8 写入 `reports/smoke-collect.txt`。
- `Real Smoke`：按 `SMOKE_TARGET` 执行真实业务用例，支持并发优先、串行收尾，并在结束时生成 P0/P1/Flaky 产物。
- `post`：统一发布 JUnit/Allure、归档 `allure-results/**` 和 `reports/**`，并按构建状态发送邮件。

### 构建参数

| 参数 | 默认值 | 作用 |
| --- | --- | --- |
| `RUN_FRAMEWORK_TESTS` | `true` | 执行 `tests/` 框架测试 |
| `RUN_COLLECT_ONLY` | `true` | 收集 `module/smoke` 用例，不真实调用接口 |
| `RUN_REAL_SMOKE` | `false` | 是否执行真实业务 Smoke |
| `ALWAYS_SEND_REPORT_EMAIL` | `false` | 成功构建也发送报告邮件；失败和不稳定始终发送 |
| `USE_CHINA_ENVIRONMENT` | `TRUE` | 选择国内或默认环境配置 |
| `SMOKE_TARGET` | `module/smoke` | 真实 Smoke 的目标目录、文件或 nodeid |
| `TEST_PARALLEL_WORKERS` | `off` | `off/auto/2/4/8`，控制 pytest-xdist worker 数量 |

默认参数只执行框架测试和 Smoke 收集，不执行真实接口，避免因外部服务、账号余额和调用成本造成非预期影响。

`Jenkinsfile` 还配置了每日 `00:00` 的参数化真实 Smoke。真实接口会产生模型调用和账单数据；不需要定时执行时，应在 Jenkins Job 的 Build Triggers 中停用对应触发器。

### 推荐构建模式

安全门禁构建：

```text
RUN_FRAMEWORK_TESTS=true
RUN_COLLECT_ONLY=true
RUN_REAL_SMOKE=false
TEST_PARALLEL_WORKERS=off
```

真实业务回归：

```text
RUN_FRAMEWORK_TESTS=true
RUN_COLLECT_ONLY=true
RUN_REAL_SMOKE=true
SMOKE_TARGET=module/smoke
TEST_PARALLEL_WORKERS=2
```

只执行指定业务文件：

```text
RUN_REAL_SMOKE=true
SMOKE_TARGET=module/smoke/test_response_body_validation.py
```

### 并发与串行

当 `TEST_PARALLEL_WORKERS` 不是 `off` 时：

```text
普通用例进入并发池
-> pytest-xdist 执行并发池
-> serial 用例进入串行池
-> 并发池结束后串行执行
```

并发池和串行池分别生成 JUnit 文件，并非物理合并成单个 XML 文件。流水线通过 `reports/smoke-tests*.xml` 统一发布两个用例池的结果，邮件摘要会去重并汇总统计。

以下用例通常应标记为 `serial`：

- 共享账号余额和调用计费校验。
- 修改共享 Header、账号状态或全局资源的用例。
- 依赖前序任务结算完成的业务链路。
- 并发执行会导致断言数据互相污染的场景。

### 报告与产物

流水线当前发布：

- JUnit 测试结果。
- Allure 报告入口。
- `allure-results/**` 原始结果。
- `reports/unit-tests.xml`。
- `reports/smoke-collect.txt`。
- 真实 Smoke 的 `smoke-tests.xml`，或并发模式下的 `smoke-tests-parallel.xml` 与 `smoke-tests-serial.xml`。
- `reports/quality/gate-report.md`：中文 P0 质量门禁报告。
- `reports/quality/p1-observation.md`：中文 P1 单次观察与 Flaky 报告。
- `reports/quality/**` 下的 JSON/JSONL 完整机器证据。

Jenkins 只保留最近 4 天的归档产物；构建编号、结果、参数和控制台历史不按天数或数量删除。Flaky SQLite 位于 Job 外部持久路径，不受产物清理影响。

查看一次真实 Smoke 时，推荐在对应构建的 `Artifacts -> reports -> quality` 中先打开：

1. `gate-report.md`：确认数据完整性、门禁结论和失败分类。
2. `p1-observation.md`：查看耗时、usage 覆盖、Flaky 迁移和待关注事项。
3. Allure：需要定位具体请求、响应或附件时再进入。

### 邮件通知

当前使用 Jenkins Email Extension Plugin 发送 HTML 摘要邮件：

```text
FAILURE  -> FAILED 邮件
UNSTABLE -> UNSTABLE 邮件
SUCCESS 且 ALWAYS_SEND_REPORT_EMAIL=true -> SUCCESS 邮件
SUCCESS 且上一轮为 FAILURE/UNSTABLE -> FIXED 邮件
其他连续 SUCCESS -> 不发送
```

邮件正文包含构建状态、分支、提交、JUnit 汇总、Smoke 收集数量、执行参数和报告入口，不附带完整 Console 日志、`.env`、API Key、请求体或响应体。

邮件报告入口包括：

- Allure 报告。
- JUnit 报告（存在 JUnit 结果时）。
- P0 质量门禁报告（存在 `gate-report.md` 时）。
- P1 观察报告（存在 `p1-observation.md` 时）。
- 构建产物列表。

邮件不再展示“构建详情”和“控制台日志”链接；P0/P1 文件未生成时不会发送无效链接。

SMTP 授权码只配置在 Jenkins 中，不写入 `Jenkinsfile` 或仓库。

### 当前质量门禁

当前 Jenkins 阻断逻辑：

- 框架测试失败会使构建失败。
- Smoke 收集失败会使构建失败。
- 真实 Smoke 返回非零退出码会使构建失败。
- Jenkins 超时和节点执行异常会反映到构建状态。

P0 影子门禁已实现：

- 数据可用性和归并完整性校验。
- 产品缺陷、配置问题、框架缺陷和未知失败分类规则。
- HTTP 5xx 比例和超时比例规则，默认最小请求样本量为 20。
- `PASS/WARN/BLOCK/NO_DATA` 结论及逐规则证据。

影子门禁当前不会覆盖 pytest/Jenkins 结果，目的是先积累可信样本并验证规则。当前明确不做：

- 基于测试通过率的强制阻断。
- 性能基线、P95/P99 趋势门禁。
- 估算成本或账本价格对账。
- Flaky 自动跳过或自动重跑。
- MR 精准选测、覆盖率/契约矩阵和智能测试生成。

Jenkins 环境复建和迁移参考：

- `JENKINS_MIGRATION_TEMPLATE.md`
- `dev/P0质量数据底座详细开发方案.md`
- `dev/P1真实接口成本耗时稳定性整体开发思路.md`
- `dev_plan/P1迭代三Flaky状态机与治理详细开发方案.md`

### 本地验收

CI 变更前仍建议先执行：

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
.\.venv\Scripts\python.exe run_master.py module/smoke --collect-only -q
```

