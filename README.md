# API Test Framework

基于 `pytest`、`requests` 和 `allure-pytest` 的代码式接口测试框架。

当前框架按“通用基础能力 + 模型模块继承 + pytest nodeid 用例池执行”的方式组织。框架能力服务于现有接口自动化，不引入 YAML、Excel 或隐式 DSL；真实用例仍以 Python 代码表达业务链路。

## 当前状态

- 基础层已实现请求中间件、配置校验与脱敏、契约断言、显式重试、轮询状态机、测试上下文，以及单元级 Mock 与 loopback 集成级离线验证。
- `common` 不静态依赖 `quality`，质量观察通过中性 Runtime Hooks 注入；指标、Flaky Store 和运行编排分别位于职责独立的目录。
- 协议与业务层已覆盖 OpenAI Chat Completions、Responses、Anthropic Messages，以及图片、视频和真实 Smoke 链路。
- 质量链已实现事实归并、完整性校验、失败分类、请求指标和机器可读产物。
- 语义与指标链已实现逻辑调用、HTTP/SSE/异步耗时、Token/媒体用量覆盖；Flaky 历史、状态机与治理命令完整保留。
- 已建立确定性离线学习模块：标准库 loopback 服务提供 9 个固定场景，18 项基础设施门禁验证协议、隔离、并发计数和线程回收；截至 2026-08-06，23 条业务用例覆盖 6 组能力分类和 1 条黄金路径，Runner 计划为 23 条并发、0 条串行。23 条是日期化快照，长期合同是最终计划与并发/串行池集合守恒。
- 截至 2026-08-06，本地 Runner、Quality、Metrics、Flaky 和完整框架回归均已通过；Jenkins Build #76 从提交 `c0954ba` Checkout 并通过 23 条离线用例，阶段5发布门禁为 `PASS`，离线框架能力总方案为 `COMPLETE`。详细证据见“本地验收”和 Build #76，不以本段替代后续实际验收。
- Jenkins 已支持参数化执行、并发优先与串行收尾、每轮 Pipeline 执行摘要、JUnit/Allure、邮件直达链接和构建产物自动清理。
- `reports/pipeline-summary.md` 是唯一人工质量报告；缺失的 Metrics 或 Flaky 数据只降级对应章节，不按零计算，也不覆盖 pytest/Jenkins 结果。

推荐的日常使用顺序：

```text
Jenkins 构建结果
-> pipeline-summary.md：确认本轮参数、阶段、用例、请求、重试和 Flaky 变化
-> JUnit：确认用例总数、通过、失败、错误和跳过
-> Allure：定位具体用例步骤、请求响应和附件
-> reports/quality/**：需要审计时查看 merged、metrics 和 Flaky 机器数据
-> Flaky CLI：对已确认的问题执行纠正、隔离或恢复治理
```

## 目录结构

```text
common/
  base_request.py          # BaseRequest：HTTP 请求、请求中间件、重试、轮询
  base_assertions.py       # BaseAssertions：状态码、JSONPath、JSON Schema 断言
  base_decorators.py       # Allure step、模型结果附件、下载结果挂载
  base_task.py             # Task 公共 API 的稳定兼容门面
  task_capabilities/       # 可组合的媒体生成与账单领域能力
  capture.py               # 输入/输出下载 CapturePolicy
  request_context.py       # 单次请求上下文
  request_middleware.py    # Redaction、Logging、MediaResource 中间件
  retry.py                 # RetryPolicy 与重试判定/退避计算
  polling.py               # PollingPolicy、PollingState 与轮询异常
  context_executor.py      # 在线程池提交边界传播 ContextVar
  runtime_hooks/           # 中性观察协议、RuntimeObserver、Noop 与生命周期
  test_context.py          # 用例级变量传递与清理回调
  __init__.py              # 延迟导出通用对象

util/
  api_call_logger.py       # 请求/响应/异常/重试/轮询日志写入 Allure
  config_validation.py     # 配置校验、类型解析、错误聚合
  curl_builder.py          # 脱敏 cURL 生成
  media_resources.py       # POST 前 input.media.url 异步下载与 Allure 挂载
  downloads.py             # 下载、命名、限额与附件类型单一实现源
  redaction.py             # 统一脱敏规则
  __init__.py

module/
  conftest.py              # pytest fixture 与直接 pytest 的 Allure 生命周期适配
  image_model/             # 图片模型真实用例
  video_model/             # 视频模型真实用例
  smoke/                   # Smoke 用例、响应 Schema、业务 payload builder
  material_library/        # 素材管理领域四件套、真实用例与独立 CLI
  protocol_testing/        # OpenAI/Anthropic 协议兼容性用例
  offline_framework_example/
    offline_service.py     # 127.0.0.1 随机端口确定性 HTTP 服务与冻结场景
    request.py             # 显式离线 Settings、相对路径与中性 metadata
    task.py                # 离线 payload、业务动作与 Retry/Polling 策略
    assertions.py          # 离线领域断言，委托 BaseAssertions
    decorators.py          # 保留真实类身份的薄 Decorators 子类
    response_schemas.py    # 创建、Polling、错误与审计响应 Schema
    conftest.py            # 服务、Request、Capture 与 Runtime 观察 fixture
    test_request_pipeline.py # Request 与默认 Middleware 分类
    test_retry.py          # GET/POST Retry 资格与挽救分类
    test_polling.py        # Polling success/failure/unknown/timeout 分类
    test_context_cleanup.py # TestContext 提取、转换与 LIFO cleanup 分类
    test_capture_assertions.py # Capture、Schema 与脱敏诊断分类
    test_concurrency_context.py # ContextVar、Session 与 Header 隔离分类
    test_full_framework_flow.py # 稳定成功主链黄金路径

quality/
  collector.py             # Case/请求事实采集
  aggregator.py            # 事实分片归并与完整性校验
  classifier.py            # 失败分类与稳定指纹
  semantic_*.py            # 逻辑调用、请求组、轮询与流式语义
  runtime_adapter.py       # Runtime Hooks 到质量采集器的适配层
  metrics/                 # 来源校验、调用/请求粒度聚合与指标写入
  flaky_store/             # SQLite 仓储、迁移、投影、治理及事务门面
  flaky*.py                # Flaky 模型、规则、历史导入与配置

tests/
  mock_helpers.py          # 离线响应、故障、流式响应和睡眠记录工具
  test_offline_service.py  # 确定性本地服务协议、隔离、资源与回收门禁
  quality/                 # 质量事实、Metrics、Flaky、Jenkins 集成回归测试
  test_*.py                # 框架基础能力单测

dev/                       # 架构审查、协议合同与开发思路
dev_plan/                  # 可执行的阶段开发方案
code_history/              # 分阶段代码变更与验收记录
config.py                  # Settings 与环境配置加载
master_service.py          # 收集与分池公共导入的稳定兼容门面
run_master.py              # 本地与 Jenkins 共用的稳定执行入口
pipeline_reporting/        # Jenkins 全场景执行摘要、阶段状态与兜底输入
run_orchestration/
  cli.py                   # CLI 参数解析和 pytest 参数透传
  runner.py                # 测试执行状态机与异常收口
  pytest_execution.py      # pytest 权威收集、池执行、参数分相和原始退出码
  scheduling.py            # 不依赖 pytest 的纯分池与集合守恒算法
  artifacts.py             # JUnit 路径与 Runner 执行事实产物
  allure_lifecycle.py      # 多池 Allure raw 合并、HTML/history 单一生命周期
  environment.py           # Quality 配置、run_id 和阶段环境变量
  quality_*_stage.py       # 事实归并、语义、指标与 Flaky 阶段
  quality_pipeline.py      # 质量阶段顺序编排
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

## 架构边界与依赖方向

框架按请求执行、测试编排和质量观察三个变化方向划分职责。`reports/pipeline-summary.md` 是人工查看入口，JUnit、Allure 和 `reports/quality/**` 分别提供测试结果、用例明细与机器证据。当前调用关系如下：

```text
module 业务用例
-> common 请求、重试、轮询和中性 Runtime Hooks
-> quality.pytest_plugin 轻量入口按开关加载 pytest_plugin_runtime
-> 开启后注入 quality.runtime_adapter
-> quality 采集并归并质量事实、语义、Metrics 和 Flaky 状态

run_master.py 稳定入口
-> pytest_execution 完成一次权威收集并形成最终 nodeid/marker 计划
-> scheduling 从同一计划派生并行池和串行池
-> runner 只依赖中性 QualityRunLifecycle 并执行已分配 nodeid
-> Quality 关闭返回 Noop；开启后才加载质量阶段实现
-> master_service 通过兼容委托提供稳定公共导入
```

离线学习链路复用同一公共入口，不建立第二套测试框架：

```text
OfflineService（127.0.0.1 + 随机端口 + 固定响应序列）
-> OfflineFrameworkTask 构建 payload 和业务动作
-> OfflineFrameworkRequest 复用 BaseRequest/Middleware/Retry
-> OfflineFrameworkAssertions 复用 BaseAssertions
-> Request/Middleware/Retry/Polling/TestContext/Capture 分类
-> ContextVar/Session/Header 并发隔离分类
-> 稳定成功黄金路径组合能力
-> pytest / Runner / Allure / 可选 Quality 消费同一执行事实
```

当前离线业务模块已经实现 Request、默认 Middleware、Retry、Polling、TestContext、Capture、Assertions、并发隔离和黄金路径。所有示例复用框架公共实现，不建立第二套 Request、Retry、Polling、Capture、Runner 或 Quality 实现。

依赖方向必须保持：

```text
quality -> common.runtime_hooks
common -X-> quality
```

未启用 Quality 时，`quality` 根包按符号懒加载，pytest 只注册轻量兼容入口，Runner 使用 `NoopQualityRunLifecycle`；Collector、Semantic、Metrics、Flaky 不进入核心导入和执行路径，也不创建质量身份、目录或产物。启用后才加载 `quality.pytest_plugin_runtime`，并在当前 pytest worker 中绑定 `QualityRuntimeHooks`。观察器异常采用 fail-open，不覆盖业务响应和原始异常。

修改能力时按以下边界落位：

| 变化类型 | 修改位置 |
| --- | --- |
| HTTP、Retry、Polling、SSE 执行语义 | `common/` |
| 通用观察事件和生命周期 | `common/runtime_hooks/` |
| 跨模块媒体/账单领域能力 | `common/task_capabilities/` |
| 输入/输出捕获策略与下载原语 | `common/capture.py`、`util/downloads.py` |
| 观察事件映射到质量模型 | `quality/runtime_adapter.py` |
| 指标来源、校验和分粒度聚合 | `quality/metrics/` |
| Flaky SQLite、迁移、投影和治理 | `quality/flaky_store/` |
| pytest 调度、产物和质量阶段顺序 | `run_orchestration/` |

业务线程池不会自动继承 ContextVar。用例内部使用 `ThreadPoolExecutor` 时必须通过 `common.submit_with_context()` 提交任务，保证 run、case、operation 和 Runtime Hooks 归属不丢失。

## 安装依赖

Python 最低版本为 3.11（框架使用 `datetime.UTC` 等 Python 3.11 标准库能力）；截至 2026-08-06，本轮阶段6本地验收使用 Python 3.14.6。

从仓库根目录创建独立虚拟环境：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
```

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

先从模板创建本地 `.env`：

```powershell
Copy-Item .env.example .env
```

再按 `config.py` 当前读取的变量修改配置：

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
GENERATE_PIPELINE_SUMMARY=TRUE
QUALITY_ENABLE=FALSE
QUALITY_SEMANTIC_ENABLE=FALSE
QUALITY_METRICS_ENABLE=FALSE
QUALITY_FLAKY_HISTORY_ENABLE=FALSE
QUALITY_FLAKY_STATE_ENABLE=FALSE
QUALITY_FLAKY_DB_PATH=
```

账单、余额及用量查询需要对应环境的 `*_CONTROL_API_KEY`。特殊账号 Key 继续按 `.env.example` 配置，不写入代码或 Jenkinsfile。

离线分类用例只访问 `127.0.0.1` 随机端口，`OfflineFrameworkRequest` 会创建自己的离线 `Settings`，不会使用 `.env` 中的真实 URL 或 Key。不过，框架导入 `config.py` 时仍会校验所选环境配置，因此 `.env` 至少要保留语法合法的 URL、非空 Key 和有效开关值；直接复制 `.env.example` 已满足离线运行要求。

本地启用完整质量事实与 Metrics 数据链路时，可在 PowerShell 当前进程中设置：

```powershell
$env:QUALITY_ENABLE = '1'
$env:QUALITY_SEMANTIC_ENABLE = '1'
$env:QUALITY_METRICS_ENABLE = '1'
$env:QUALITY_OUTPUT_DIR = 'reports/quality'
```

启用 Flaky 历史与状态机还需要：

```powershell
$env:QUALITY_FLAKY_HISTORY_ENABLE = '1'
$env:QUALITY_FLAKY_STATE_ENABLE = '1'
$env:QUALITY_FLAKY_DB_PATH = 'D:\your-persistent-path\flaky-history.db'
```

`QUALITY_FLAKY_DB_PATH` 必须是可写的绝对持久路径，父目录需要提前创建；同一个数据库只允许一个 Jenkins Job 独占写入。Jenkins 接口测试会自动启用质量事实、Semantic 和 Metrics，并在检测到有效数据库路径时启用 Flaky 历史与状态评估。

环境开关：

```text
USE_CHINA_ENVIRONMENT=TRUE   # 国内环境
USE_CHINA_ENVIRONMENT=FALSE  # 海外环境
```

`load_settings()` 会在执行前校验 URL、API Key、超时和报告开关配置。配置错误会聚合为明确的变量名错误，不再等到请求阶段暴露模糊异常。

配置规则由 `util.config_validation.validate_settings_values()` 统一编排，`config.py` 只把规范值转换为 frozen Pydantic `Settings`。对外仍保持原有接口：

- `load_settings()` 返回 `Settings`，字段名和默认值保持不变。
- 配置缺失、类型错误、非法 URL 等仍通过 `ConfigValidationError` 暴露。
- 多个必填配置缺失时仍聚合输出，不只报第一个错误。

安全边界：

- `BASE_URL`、`API_KEY` 不属于 `config.py` 的有效配置变量。
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

- `RuntimeObservationMiddleware`：通过中性 Runtime Hooks 观察请求开始、成功和异常；Quality 关闭时为空操作。
- `MediaResourceMiddleware`：在 POST 前收集 `input.media.url` 前置资源下载任务。
- `RedactionMiddleware`：对请求参数建立脱敏副本。
- `LoggingMiddleware`：输出请求、响应、异常、重试记录和轮询迁移日志。

默认注册与执行顺序固定为 `RuntimeObservation → MediaResource → Redaction → Logging`；自定义中间件按传入顺序执行。

每次请求使用独立 `RequestContext`，请求参数会尽量深拷贝，避免中间件污染调用方 payload。`on_exception()` 中间件自身失败时不会覆盖原始网络异常。

单次协议或控制请求通过请求级 `headers` 覆盖，不临时 clear/update/reset 共享 `Session.headers`。输入媒体和输出结果下载由同一个 `CapturePolicy` 控制；`CapturePolicy.disabled()` 会同时关闭两类外部下载，下载失败不会覆盖接口响应。

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

`util/config_validation.py` 是环境选择、布尔/正数解析、当前环境 URL/Key 校验和错误聚合顺序的唯一规则源。`config.py` 使用 Pydantic `Settings` 模型保存规范值，并在导入时完成初始化；不得在 `config.py` 或调用方复制第二套配置判断。脱敏能力集中在 `util/redaction.py`，`api_call_logger.py` 与 `curl_builder.py` 都复用同一套规则。

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

同步方法是断言算法的唯一实现源；`async_assert_*`、模块级同步/异步函数和领域 Assertions 子类都通过委托或继承复用同步实现。四件套中的 Assertions 继续保持真实类身份、MRO 和导入路径，不能为了去重替换为简单别名。

### Artifact 基础原语与领域翻译

`util/artifact_io.py` 只提供 UTF-8 JSON/JSONL 读取、原始文件字节 SHA256 和精确字段比较等无领域原语。Metrics、Flaky 和 Pipeline Reporting 复用这些原语，但分别保留自己的错误码、导入规则和 warning/降级语义；Schema、版本、状态机和可信度结论不下沉到通用工具层。

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

状态机能够记录状态迁移序列，区分等待、成功、失败、未知状态和超时。`poll_timeout` 是整个 Polling 的总 deadline，单次 HTTP timeout、Retry backoff/`Retry-After` 与轮询 sleep 都只能消费这一个预算。

媒体生成类任务默认使用 `DEFAULT_MEDIA_POLLING_POLICY`，由 `BaseTask.poll_media_generation_result()` 和 `BaseTask.create_and_poll_media_generation()` 自动传入。直接调用 `BaseRequest.poll_get()` 时必须显式传入 `PollingPolicy`。

兼容性边界：

- `PollingPolicy(...)` 的公开构造参数保持兼容。
- `PollingTransition(1, 0.0, state, status, 200)` 的旧位置参数写法仍可使用。
- 非法 JSONPath、未知状态策略等仍以 `ValueError` 语义暴露。
- `poll_get()` 不接受 `success_json_path` / `failure_json_path` 参数。

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

### 三层离线回归与故障模拟

框架按验证成本分成三层离线能力。第一层位于 `tests/mock_helpers.py`，用于快速、精确地验证核心分支，不启动网络服务：

已提供：

- `make_response()`：构造 `requests.Response`。
- `SequenceTransport`：按顺序返回响应或抛出异常，并记录调用。
- `SleepRecorder`：记录退避和轮询等待。
- `FakeApiCallLogger` / `create_fake_logger()`：验证日志挂载行为。
- `connection_error()`、`connect_timeout()`、`read_timeout()`、`timeout_error()`。
- `polling_responses()`：快速生成轮询状态序列。
- `FakeStreamResponse`：模拟 SSE/流式响应中断和 chunk 行为。

第二层位于 `module/offline_framework_example/offline_service.py`，使用 Python 标准库在 `127.0.0.1` 随机端口启动确定性 HTTP 服务。它冻结 9 个场景，覆盖 Echo、瞬时 429/503、幂等 POST、Polling 成功/失败/未知/超时和超限下载；`tests/test_offline_service.py` 的 18 项门禁验证协议、实例隔离、并发计数、线程回收和 IPv4 loopback 边界。

第三层是 `module/offline_framework_example/test_*.py` 的真实四件套业务验证。截至 2026-08-06，共 23 条用例：Request/Middleware 3 条、Retry 4 条、Polling 4 条、TestContext/cleanup 4 条、Capture/Assertions 4 条、并发隔离 3 条、黄金路径 1 条；它们全部属于并发池，不依赖真实服务、账号或凭据。

三层能力用于框架核心回归和学习，不替代真实环境业务用例。推荐按以下顺序阅读：

```text
offline_service.py 与 conftest.py
-> request.py / task.py / assertions.py / decorators.py
-> test_request_pipeline.py
-> test_retry.py
-> test_polling.py
-> test_context_cleanup.py
-> test_capture_assertions.py
-> test_concurrency_context.py
-> test_full_framework_flow.py
```

| 类型 | 回答的问题 | 不承担的职责 |
| --- | --- | --- |
| 分类用例 | 哪一项能力或边界失效 | 不证明全部能力组合 |
| 并发分类 | Context、Session 和请求级状态是否隔离 | 不验证全部业务异常 |
| 黄金路径 | 已验证能力能否沿稳定成功主链组合成立 | 不容纳互斥 failure/unknown/timeout |
| Runner/Quality 验收 | 同一执行事实能否被可信消费 | 不替代业务断言 |

Polling failure、unknown、timeout、非幂等 POST 禁止重试、Capture 超限和 cleanup 多错误等互斥异常继续保留在分类用例，不扩张黄金路径。

## 分层规范

`common/` 只放所有模型都能复用的通用能力。具体模型路径、payload builder、模型 ID 和真实测试数据应放在对应 `module/<model_name>/` 中。

每个模型目录必须创建四件套，并以真实类保留类身份、MRO、`__name__` 和稳定导入路径；即使当前职责很薄，也不能替换为简单别名：

```text
request.py
assertions.py
decorators.py
task.py
test_*.py
__init__.py
```

其中 `request.py`、`assertions.py`、`decorators.py`、`task.py` 是强制四件套；`test_*.py` 是业务用例入口，`__init__.py` 负责稳定导出。`response_schemas.py`、`payload_builders.py`、`conftest.py` 等文件按实际 Schema、数据构造和 fixture 职责增设，不要求机械创建。

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

`task.py` 只服务当前目录下的测试用例，用于封装本模块独有的业务方法。`BaseTask` 保留现有创建、轮询和账单入口作为兼容门面，真实实现委托 `common/task_capabilities/`；新领域逻辑进入对应模块 Task，确有跨模块复用时再建立窄能力对象，不再扩张 `BaseTask`。不同模型目录的 `task.py` 不应互相引用。

独立业务 CLI 也必须复用所属模块四件套：脚本只保留 argparse、控制台展示和退出码翻译，端点归 Request，payload/流程/轮询归 Task，响应规则归 Assertions。`material_library` 的两个联调 CLI 已按此边界实现；`--insecure` 只影响当前 Request Session，`--quiet` 只影响当前脚本输出。

## 用例写法

测试类中不要定义 `__init__`，pytest 会跳过带自定义 `__init__` 的测试类。

推荐先从不依赖外部环境的离线用例学习。下面的写法可直接放入 `module/offline_framework_example/test_*.py`，`conftest.py` 会负责启动和回收本地服务与 Request：

```python
from module.offline_framework_example import (
    OfflineFrameworkAssertions,
    OfflineFrameworkRequest,
    OfflineFrameworkTask,
)


class TestOfflineEcho:
    def test_echo(self, offline_request: OfflineFrameworkRequest) -> None:
        task = OfflineFrameworkTask()
        assertions = OfflineFrameworkAssertions()
        payload = task.build_echo_payload()

        response = task.submit_echo(offline_request, payload)

        assertions.assert_echo_accepted(response, payload)
```

完整的已实现示例见 `module/offline_framework_example/test_request_pipeline.py` 和 `test_retry.py`。真实业务模块沿用相同的 Request → Task → Assertions 调用方向，只替换领域端点、payload 和响应规则。

约束：

- 用例文件放在 `module/<model_name>/test_*.py`。
- 用例中统一通过模块 `Task` 调用业务动作。
- payload 使用 Python 字典，不使用 YAML。
- 不在用例中硬编码完整环境域名。
- 不在用例中硬编码 API Key。
- 新增或修改用例后先执行 `--collect-only -q` 确认可收集。

## 执行入口

`master_service.py` 提供稳定的收集与分池公共导入；实际 pytest 收集由 `run_orchestration/pytest_execution.py` 唯一拥有，分池算法由 `scheduling.py` 唯一拥有。直接执行该入口可输出收集结果：

```powershell
.\.venv\Scripts\python.exe master_service.py
```

`run_master.py` 是本地与 Jenkins 共用的执行入口，只导出 `run()`、`main()` 和路径常量。Runner 将 `-k`、`-m`、`--ignore` 等选择条件放入同一次权威收集，形成不可变 nodeid/marker 计划；正式执行只消费计划，不再二次选测。

关键职责保持单一所有者：

- 只有 `pytest_execution.py` 调用 `pytest.main()`。
- 只有 `allure_lifecycle.py` 清理、合并并生成 Allure 制品；Runner 每池写独立临时 raw，最终只合并和生成一次。
- `quality_lifecycle.py` 是 Runner 接入可选质量能力的唯一边界；关闭时为 Noop，开启时兼容委托原有质量阶段。
- `environment.py` 负责 Quality 阶段环境变量设置与恢复。
- `quality_pipeline.py` 只决定质量阶段顺序，不承载聚合算法和报告渲染。

架构测试保护上述公共行为、依赖方向和单一所有权，不锁定 Python 文件的精确集合或完整导入 DAG。新增职责明确的小文件本身不应导致门禁失败；只有形成反向依赖、重复所有者或公共合同回退时才失败。

执行全部业务用例：

```powershell
.\.venv\Scripts\python.exe run_master.py
```

未指定目标时会收集 `module/` 下全部业务用例，其中包含可能调用真实接口、产生费用或修改共享状态的用例。学习和离线验收应始终显式指定 `module/offline_framework_example`。

完全离线验证本地服务合同：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_offline_service.py -q
```

只收集离线业务分类，不发起请求：

```powershell
.\.venv\Scripts\python.exe run_master.py module/offline_framework_example --collect-only -q
```

通过 Runner 并发执行离线业务分类：

```powershell
.\.venv\Scripts\python.exe run_master.py module/offline_framework_example -n 2
```

### 离线学习模块运行级验收

Quality 关闭时使用唯一临时目录运行，避免历史报告污染：

```powershell
& .\course\scripts\run_offline_quality_evidence.ps1 -QualityMode Disabled
```

当前预期为 23 条通过、Quality 文件为 0；脚本会输出本轮唯一证据目录。它先逐项保存调用者环境，确认 Runner 备份存在后才删除原文件，关键文件操作使用 fail-fast，并通过嵌套 `finally` 独立恢复 Runner 产物、环境变量和工作目录。实现见 [run_offline_quality_evidence.ps1](course/scripts/run_offline_quality_evidence.ps1)。

Quality、Semantic 和 Metrics 开启时使用全新的 run ID 与输出目录：

```powershell
& .\course\scripts\run_offline_quality_evidence.ps1 -QualityMode Enabled
```

Enabled 模式使用全新的 run ID 和 Quality 输出目录，同时临时关闭 Flaky 持久写入、Allure HTML 与 history；结束后逐值恢复调用者原有环境，不把原值统一删除。

完整 23 条模块当前允许 Metrics 因 `usage_incomplete` 进入受控 `degraded`；黄金路径的 usage 完整，Metrics 必须为 `aggregated`。

黄金路径稳定 nodeid：

```powershell
$goldenNodeId = 'module/offline_framework_example/test_full_framework_flow.py::TestFullFrameworkFlow::test_offline_async_media_flow'
.\.venv\Scripts\python.exe -m pytest $goldenNodeId -q
```

当前运行级关系为 `4 operations / 7 request groups / 1 polling session / 8 request events`，并包含一次 `503 -> 200` Retry 挽救和 `pending -> pending -> success` Polling。该数量是当前快照，业务断言不直接读取 Quality 内部模型。

Flaky 需要同一黄金 nodeid、稳定 case 身份、独占绝对 SQLite 数据库以及逐轮新 run ID。执行轮数按 `stable_min_samples + 1` 动态决定；当前阈值为 3，阶段5的 4 轮真实样本状态为 `OBSERVING -> OBSERVING -> STABLE -> STABLE`。

本地示例把 JUnit、Allure 和 Quality 机器产物写入唯一 `$evidenceRoot`；Jenkins 使用 `reports/smoke-tests*.xml`、`allure-results/**`、`reports/quality/**` 和 `reports/pipeline-summary.md`。Flaky 数据库必须位于 Workspace 外的 Job 独占绝对本地路径。

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
使用 target、-k、-m、--ignore 等条件权威收集最终 nodeid 与 marker
-> 未标记 serial 的用例使用 pytest-xdist 并发执行
-> 并发池结束后，标记 serial 的用例单进程执行
-> pytest 2/3/4/5 或 Runner 异常立即停止后续池；pytest 1 可继续收集失败证据
-> 两个用例池分别生成 JUnit 文件
-> Runner 原子写入 reports/execution-result.json
-> Jenkins 归档报告，邮件消费与 Markdown 相同的 Python 结构化摘要
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

`--collect-only` 在加载 Quality 配置前短路，不生成 run_id、不写质量产物，也不调用真实接口。

Runner 始终保留池级 pytest 原始退出事实：权威空集合返回 5；在 `reports/execution-result.json` 原子写入成功的前提下，单池最终退出码等于 pytest 原始码，多池只有全部已执行池均为 0 时才返回 0。exit 2/3/4/5 或池执行异常会停止所有后续执行池。若执行事实写入失败，Runner 保留终止型原始码 2/3/4/5，其他情况返回 1；Quality、JUnit、Allure 和报告不得改写池级原始退出码。

直接执行框架单测：

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
```

## 质量事实、Metrics 与 Flaky

质量能力由 `run_orchestration.quality_pipeline` 在测试结束时统一收口，根 `run_master.py` 仅保留兼容入口。测试代码仍按原方式编写，不需要直接调用聚合器：

```text
pytest Case/请求事实分片
-> 事实归并与完整性校验
-> 最终运行记录、失败分类与请求指标
-> 逻辑调用/请求组/轮询语义归并
-> 耗时、重试与资源用量聚合
-> Flaky 历史导入和状态评估
-> pipeline-summary.md 读取本轮可信事实
```

事实归并失败时后续质量阶段提前结束，避免基于不可信输入继续聚合；Semantic、Metrics 和 Flaky 阶段分别 fail-open，不改变 pytest 的原始退出结果。

### 可信质量事实

基础事实链解决“这轮结果属于谁、是否完整、为什么失败”：

- 对并发池和串行池的 Case、请求及失败事实统一归并。
- 校验执行分片、预期用例数量、文件哈希和运行 ID，完整性异常不会被包装成正常结论。
- 按产品、测试、框架、环境、配置、瞬时故障和未知原因聚合失败。
- 统计请求总量、HTTP 5xx、超时和接口耗时。

请求成功率与用例通过率不是同一指标：负向用例和轮询中的中间业务状态可能计为请求失败，但用例仍可按预期通过。人工判断应优先看用例结果、5xx、超时和失败分类，而不是孤立使用请求成功率。

主要产物：

| 文件 | 用途 |
| --- | --- |
| `reports/quality/run.json` | 本次运行身份、时间、状态及完整性 |
| `reports/quality/merged/manifest.json` | 归并版本、输入分片、输出计数与哈希校验 |
| `reports/quality/merged/*.jsonl` | 归并后的 Case、请求、失败和完整性事实 |

以上文件是机器数据；人工查看统一进入 `reports/pipeline-summary.md`。

### 单次运行指标与用量覆盖

Metrics 解决“真实调用慢在哪里、重试是否挽救、资源数据是否完整”：

- 以逻辑调用而不是单个 HTTP 请求为主要观察单位。
- 区分 HTTP、SSE、异步任务、轮询等待和控制流量。
- 聚合调用总耗时、响应头等待、轮询总耗时和轮询休眠时间。
- 聚合输入/输出 Token、媒体数量及对应样本量。
- 用 `complete/partial/no_data/not_applicable` 表达完整性；缺失值不按零计算。
- 当前不估算成本，也不建立性能基线或耗时门禁。

逻辑调用失败数同样不等于测试失败数：验证错误响应、失败任务或流式中断的负向场景可能产生预期失败调用。Metrics 用于观察真实工作负载，不替代 pytest/JUnit 用例结论。

主要产物：

| 文件 | 用途 |
| --- | --- |
| `reports/quality/metrics/run-metrics.json` | 完整单次运行指标 |
| `reports/quality/metrics/manifest.json` | Metrics 输入来源、完整性和指标文件索引 |
| `reports/quality/semantic/merged/*.jsonl` | 逻辑调用、请求组和轮询会话事实 |

Pipeline Summary 只展示最直接的请求成功率、重试挽救率、接口耗时 Top 和 Flaky 状态迁移；完整 JSON/JSONL 保留指标键、状态码、问题代码和版本号，用于机器消费和问题追踪。

### Flaky 状态机与治理

Flaky 历史只导入可信、已完成运行中的可比较用例；skip/xfail/xpass 不进入波动判断。状态需要区分自动检测与人工治理。自动检测流转为：

```text
OBSERVING -> STABLE | SUSPECTED
STABLE -> SUSPECTED
SUSPECTED -> STABLE | CONFIRMED
CONFIRMED -> CONFIRMED（检测结果保持粘性，等待人工治理）
```

人工纠正与治理流转为：

```text
SUSPECTED -> CONFIRMED（人工确认）
SUSPECTED | CONFIRMED -> STABLE（人工纠正为非 Flaky）
CONFIRMED -> QUARANTINED（人工隔离）
QUARANTINED -> RECOVERING（开始恢复观察）
QUARANTINED -> CONFIRMED（取消隔离）
RECOVERING -> STABLE | CONFIRMED（恢复证据判定）
```

默认规则要求至少 3 个一致样本才能判定稳定；确认 Flaky 需要至少 4 个样本、至少 2 次通过、2 次失败和 2 次结果切换。单次失败不会直接判定 Flaky。

启用 Flaky 历史与状态机后，每轮接口测试会保留 `reports/quality/flaky-import.json` 和 `reports/quality/flaky-evaluation.json`，分别记录可信样本导入结果与本轮状态迁移；SQLite 历史库继续位于 Job 外部持久路径。

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
testpaths = module
python_files = test_*.py
python_classes = Test*
python_functions = test_*
```

执行 pytest 后：

- `allure-results/` 保存 Allure 原始结果。
- `run_orchestration/allure_lifecycle.py` 是清理、合并、HTML 与 history 的唯一实现源。
- Runner 为 parallel/serial 池分配独立临时 raw，合并到最终目录后只生成一次报告；自定义 `--alluredir` 仍作为最终目录。
- 直接运行 pytest 时，`module/conftest.py` 只负责把 session start/finish 委托给同一个生命周期。
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
- `Real Smoke`：按 `SMOKE_TARGET` 执行真实业务用例，支持并发优先、串行收尾，并在结束时生成质量事实、Metrics 和 Flaky 机器产物。
- `post`：按开关生成本轮 `pipeline-summary.md`，再统一发布 JUnit/Allure、归档产物并发送邮件；Python 生成器不可用时写入最小兜底摘要。

### 构建参数

| 参数 | 默认值 | 作用 |
| --- | --- | --- |
| `RUN_FRAMEWORK_TESTS` | `true` | 执行 `tests/` 框架测试 |
| `RUN_COLLECT_ONLY` | `true` | 收集 `module/smoke` 用例，不真实调用接口 |
| `RUN_REAL_SMOKE` | `false` | 是否执行真实业务 Smoke |
| `GENERATE_PIPELINE_SUMMARY` | `true` | 是否为本轮构建生成 `reports/pipeline-summary.md` |
| `ALWAYS_SEND_REPORT_EMAIL` | `false` | 成功构建也发送报告邮件；失败和不稳定始终发送 |
| `USE_CHINA_ENVIRONMENT` | `TRUE` | 选择国内或默认环境配置 |
| `SMOKE_TARGET` | `module/smoke` | 真实 Smoke 的目标目录、文件或 nodeid |
| `TEST_PARALLEL_WORKERS` | `off` | `off/auto/2/4/8`，控制 pytest-xdist worker 数量 |

默认参数只执行框架测试和 Smoke 收集，不执行真实接口，避免因外部服务、账号余额和调用成本造成非预期影响。

`GENERATE_PIPELINE_SUMMARY` 默认开启，也可在 `.env` 中使用同名变量。本轮 Jenkins 参数/进程环境优先于 `.env`；关闭时不生成主摘要、兜底摘要和邮件摘要链接，但不影响 JUnit、Allure、质量事实、Metrics 与 Flaky 产物。

`QUALITY_ENABLE` 独立控制质量采集和报告质量数据源。Jenkins 的真实接口阶段会显式开启它，并在生成摘要时传递同一执行事实；本地未开启时，报告中的“质量观测”为 `NOT_RUN`，且不会读取上轮遗留的 `reports/quality`。已开启但本轮核心质量产物缺失、损坏或版本不匹配时才显示 `NO_DATA`。

`Jenkinsfile` 还配置了每日 7 点时段的参数化真实 Smoke，使用 `H 7 * * *` 分散同一小时内的节点负载。真实接口会产生模型调用和账单数据；不需要定时执行时，应在 Jenkins Job 的 Build Triggers 中停用对应触发器。

### 推荐构建模式

安全门禁构建：

```text
RUN_FRAMEWORK_TESTS=true
RUN_COLLECT_ONLY=true
RUN_REAL_SMOKE=false
GENERATE_PIPELINE_SUMMARY=true
TEST_PARALLEL_WORKERS=off
```

真实业务回归：

```text
RUN_FRAMEWORK_TESTS=true
RUN_COLLECT_ONLY=true
RUN_REAL_SMOKE=true
GENERATE_PIPELINE_SUMMARY=true
SMOKE_TARGET=module/smoke
TEST_PARALLEL_WORKERS=2
```

离线框架示例运行级验收：

```text
RUN_FRAMEWORK_TESTS=false
RUN_COLLECT_ONLY=false
RUN_REAL_SMOKE=true
GENERATE_PIPELINE_SUMMARY=true
ALWAYS_SEND_REPORT_EMAIL=false
USE_CHINA_ENVIRONMENT=FALSE
SMOKE_TARGET=module/offline_framework_example
TEST_PARALLEL_WORKERS=2
```

阶段4并发分类和黄金路径文件已经进入提交 `c0954ba707dba258a08d7cfdd623e5628acf5ea8`。Jenkins Build #76 使用上述参数 Checkout 同一提交并以 `SUCCESS` 结束，JUnit 为 23 passed，Allure、Quality、Flaky、Pipeline Summary 和归档入口均可用。业务测试 HTTP 只访问本轮 fixture 提供的 `127.0.0.1` 地址；`Prepare Python Env` 仍可能访问 pip/npm 镜像，因此这里证明的是“业务接口零外部请求”，不是整个构建物理断网。目标提交或关键文件变化后必须重新验收，不能自动沿用 Build #76。

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

- `reports/pipeline-summary.md`：本轮 Jenkins 参数、阶段和执行效果的默认人工入口。
- `reports/execution-result.json`：权威计划、池级 pytest 原始退出码、JUnit 路径和 Runner 最终退出码。
- `reports/pipeline-summary.json`：Markdown 与邮件共享的结构化摘要；不是第二份人工报告。
- JUnit 测试结果。
- Allure 报告入口。
- `allure-results/**` 原始结果。
- `reports/unit-tests.xml`。
- `reports/smoke-collect.txt`。
- 真实 Smoke 的 `smoke-tests.xml`，或并发模式下的 `smoke-tests-parallel.xml` 与 `smoke-tests-serial.xml`。
- `reports/quality/**` 下的 merged、semantic、metrics 和 Flaky JSON/JSONL 机器证据。

Jenkins 只保留最近 4 天的归档产物；构建编号、结果、参数和控制台历史不按天数或数量删除。Flaky SQLite 位于 Job 外部持久路径，不受产物清理影响。

查看任意一轮流水线时，先打开 `Artifacts -> reports -> pipeline-summary.md`。报告统一使用“框架测试、用例收集、接口测试”等通用字段，区分阶段通过、失败、未执行、被阻断和产物缺失；只有接口测试与 Quality 同时启用时才展示请求成功率、重试挽救率、接口耗时 Top 和 Flaky 状态迁移。

需要继续下钻接口测试时，使用 Allure 定位具体请求、响应或附件；需要核对口径或排查数据来源时，再进入 `Artifacts -> reports -> quality` 查看机器产物。

### 邮件通知

当前使用 Jenkins Email Extension Plugin 发送 HTML 摘要邮件：

```text
FAILURE  -> FAILED 邮件
UNSTABLE -> UNSTABLE 邮件
SUCCESS 且 ALWAYS_SEND_REPORT_EMAIL=true -> SUCCESS 邮件
SUCCESS 且上一轮为 FAILURE/UNSTABLE -> FIXED 邮件
其他连续 SUCCESS -> 不发送
```

邮件正文包含构建状态、JUnit 汇总、用例收集数量和报告入口，不附带完整 Console 日志、`.env`、API Key、请求体或响应体。JUnit 只由 Python Reporting 业务解析一次；Markdown、`pipeline-summary.json`、邮件主题和邮件 HTML 均由同一个 `PipelineReport` 生成，Jenkins 不再用 Groovy 正则重复解析 XML。

邮件报告入口包括：

- 流水线执行摘要（开关开启且文件生成时，位于首位）。
- Allure 报告。
- JUnit 报告（存在 JUnit 结果时）。
- 构建产物列表。

邮件不展示“构建详情”和“控制台日志”链接，也不嵌入完整 Console、请求、响应、`.env` 或凭据。

SMTP 授权码只配置在 Jenkins 中，不写入 `Jenkinsfile` 或仓库。

### 当前执行判定

当前 Jenkins 阻断逻辑：

- 框架测试失败会使构建失败。
- Smoke 收集失败会使构建失败。
- 真实 Smoke 返回非零退出码会使构建失败。
- 真实 Smoke 的 pytest 2/3/4/5 会保留原码并停止后续池。
- Jenkins 超时和节点执行异常会反映到构建状态。

事实优先级固定为：pytest 原始退出码决定测试进程事实，Jenkins 阶段状态决定流水线事实，JUnit 提供统计与失败详情，Quality/Metrics/Flaky 仅提供诊断。Pipeline Summary 汇总这些事实，不修改构建结果。当前明确不做：

- 基于测试通过率的强制阻断。
- 性能基线、P95/P99 趋势门禁。
- 估算成本或账本价格对账。
- Flaky 自动跳过或自动重跑。
- MR 精准选测、覆盖率/契约矩阵和智能测试生成。

Jenkins 环境复建和迁移参考：

- `JENKINS_MIGRATION_TEMPLATE.md`
- `dev_plan/P1迭代三Flaky状态机与治理详细开发方案.md`

### 本地验收

CI 变更前建议按“确定性离线门禁 → 全部框架回归 → 真实用例收集”的顺序执行：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_offline_service.py -q
.\.venv\Scripts\python.exe run_master.py module/offline_framework_example --collect-only -q
.\.venv\Scripts\python.exe run_master.py module/offline_framework_example -n 2
.\.venv\Scripts\python.exe -m pytest tests -q
.\.venv\Scripts\python.exe run_master.py module/smoke --collect-only -q
```

截至 2026-08-06，当前工作树使用 Python 3.14.6，验收快照如下。数量用于识别意外丢失，不替代集合守恒和零失败发布合同：

- 离线服务合同：`18 passed`。
- 离线业务模块：`23 passed`（并发池 `23`、串行池 `0`）。
- Quality、Semantic、Metrics 与 Flaky：本地运行级门禁通过；完整模块 Metrics 唯一受控降级原因为 `usage_incomplete`。
- 黄金路径：`4 operations / 7 request groups / 1 polling session / 8 request events`。
- Smoke collect-only：`40` 项（并发池 `15`、串行池 `25`），只验证收集和分池。
- 完整框架回归：`686 passed / 0 failed / 0 errors / 0 skipped`。
- 实际 Jenkins：Build #76 `SUCCESS`，Checkout `c0954ba`，JUnit 23 passed，Allure、Quality、Flaky、Pipeline Summary 与归档完整。
- 阶段5发布门禁：`PASS`。
- 阶段6文档与发布基线：`PASS`。
- 整体发布：`PASS`；离线框架能力分类用例与黄金路径总方案为 `COMPLETE`。
