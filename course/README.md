# 接口测试框架扩展能力课程

## 1. 课程目标

这套课程面向已经掌握初版框架使用方式的学习者。目标不是记住当前有哪些类和参数，而是能够从旧实现暴露的问题出发，重新推导当前架构，并在遇到新需求时判断扩展应该放在哪一层。

课程以当前代码为最终事实，同时使用 Git 历史、`dev/` 和 `code_history/` 还原设计决策：

- 当前代码回答“现在真实怎么运行”。
- Git 历史回答“旧实现原来承担了什么职责”。
- `dev/` 回答“开发时考虑过哪些方案和风险”。
- `code_history/` 回答“最终实际改动了什么”。

当四者冲突时，先确认时间顺序，再以当前代码和测试为准。

课程共 12 个学习日，每天约 2 小时，总计约 24 小时。可以连续学习，也可以分两到三周完成。不追求覆盖所有框架功能，重点掌握以下六个扩展问题：

1. 配置如何从无类型环境变量变成可信的运行时状态。
2. 横切能力如何从请求主流程中分离。
3. 网络瞬态故障和异步业务状态为什么必须分层处理。
4. 用例变量和资源为什么需要明确生命周期。
5. 如何脱离真实接口证明异常路径正确。
6. 如何在并发执行和 CI 中保留正确的结果语义。

本课程不包含 Flaky 治理、失败重跑队列及已经从当前分支删除的相关实现。

## 2. 结课能力标准

完成课程后，应能独立完成以下任务：

- 从公开方法开始画出普通请求、重试请求和异步轮询的真实执行链。
- 找出一个需求的变化轴，并确定状态应该由哪个对象拥有。
- 根据状态所有者和生命周期推导职责边界，而不是凭文件名放置代码。
- 为同一个需求比较至少两种方案，说明收益、代价和失败方式。
- 解释当前实现为什么这样设计，以及它仍然保留了什么限制。
- 使用离线测试验证幂等性、隔离性、时间预算、状态迁移和安全输出。
- 面对仓库中没有实现的新需求，给出有证据的扩展设计。

结课时真正要回答的问题是：

> 一个新需求带来了什么变化，谁应该拥有这些状态，这段状态持续多久，因此职责边界应放在哪里；如果换一种方案，最先破坏的系统不变量是什么？

## 3. 统一学习路线

每个主题都使用同一条推理路线。

```text
观察旧实现
  → 找到变化轴
  → 识别状态所有者
  → 推导职责边界
  → 比较其他方案
```

### 3.1 观察旧实现

不先阅读最终答案。先查看初版或改造前的代码，回答：

- 当前职责集中在哪里？
- 哪些代码因为不同原因发生变化，却被写在同一个函数中？
- 哪些分支难以离线测试？
- 哪些状态没有明确生命周期？

### 3.2 找到变化轴

变化轴是导致代码需要独立演进的原因。例如：

- 日志格式会变化，但 HTTP 发送逻辑不应随之变化。
- 重试次数会变化，但单次请求的中间件生命周期不应变化。
- 不同接口的业务状态集合会变化，但轮询循环骨架不应复制。
- 用例变量会变化，但不能跨用例泄漏。

如果两个逻辑沿不同原因、不同频率变化，它们通常不应该由同一对象同时拥有。

### 3.3 识别状态所有者

对每个状态回答四个问题：

1. 谁创建它？
2. 谁修改它？
3. 谁负责结束或清理它？
4. 它持续一次 HTTP attempt、一次重试序列、一次轮询序列，还是一个测试用例？

### 3.4 推导职责边界

先写出必须保持的不变量，再决定代码位置。例如：

- 脱敏输出不能改变真实发送数据。
- 每次重试必须拥有独立请求上下文。
- 非幂等 POST 默认不能自动重试。
- 未知业务状态不能被悄悄视为成功。
- 测试变量不能跨用例共享。

边界的价值不是让目录更漂亮，而是让这些不变量能够被局部证明。

### 3.5 比较其他方案

每个主题至少完成一张决策表：

| 方案 | 状态放在哪里 | 收益 | 代价/失败方式 | 适用条件 |
| --- | --- | --- | --- | --- |
| 当前方案 |  |  |  |  |
| 备选方案 A |  |  |  |  |
| 备选方案 B |  |  |  |  |

不要把“当前代码这样写”当成设计理由。当前方案只是约束条件下的一次选择。

## 4. 每日两小时结构

每天严格控制在约 120 分钟：

| 环节 | 时间 | 产出 |
| --- | ---: | --- |
| 观察旧实现 | 20 分钟 | 职责和问题清单 |
| 阅读演进证据 | 20 分钟 | 改动前后的差异 |
| 变化轴与状态所有者 | 25 分钟 | 状态生命周期表 |
| 边界推导与方案比较 | 25 分钟 | 决策表 |
| 最小实验 | 20 分钟 | 一项可验证证据 |
| 复盘验收 | 10 分钟 | 150 字以内结论 |

现成测试只用于验证推导，不能代替推导。每天最多精读一个核心机制，不要求读完整个仓库。

## 5. 演进证据使用规则

本课程只使用与当前能力直接相关的历史节点：

| 提交 | 用途 |
| --- | --- |
| `56f4f15` | 观察初版框架 |
| `291e6ea` | 观察中间件、重试、轮询、上下文、契约等能力首次集中引入 |
| `fbff62e` | 观察轻量 Mock 的形成 |
| `2748f16` | 观察重试执行器抽离、轮询全面迁移和 Pydantic 重构 |
| `41cf8b5` | 观察 Jenkinsfile 初次接入 |
| `24a3d8c` | 观察并发池与串行池形成 |

常用命令：

```powershell
# 查看旧文件，不修改工作区
git show 56f4f15:common/base_request.py

# 比较一个文件在两个阶段的差异
git diff 56f4f15 291e6ea -- common/base_request.py

# 查看一次提交对目标文件做了什么
git show 2748f16 -- common/base_request.py common/retry_executor.py
```

不要使用会修改工作区的历史切换命令。课程不需要 checkout 到旧提交。

## 6. 开课准备

当前 `requirements.txt` 已声明 `jsonschema`。如果本地虚拟环境未同步，契约断言和部分用例会在导入阶段失败。

```powershell
cd D:\API_CASE
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pytest tests -q
.\.venv\Scripts\python.exe run_master.py module\smoke --collect-only -q
```

不要输出 `.env` 或任何真实密钥。课程实验默认不访问真实接口、不执行收费调用、不进行真实等待。

---

## 第 1 天：从初版框架建立演进问题地图

完整课程内容：[DAY01_EVOLUTION_PROBLEM_MAP.md](DAY01_EVOLUTION_PROBLEM_MAP.md)

### 核心问题

初版框架已经能够发请求、做断言和生成报告，为什么还需要后续扩展？

### 观察旧实现

只阅读初版，不先看当前实现：

```powershell
git show 56f4f15:common/base_request.py
git show 56f4f15:common/base_task.py
git show 56f4f15:run_master.py
```

记录 `BaseRequest` 当时同时承担的职责，并找出以下三类代码：

- 因业务协议变化而变化。
- 因观测和报告需求变化而变化。
- 因执行稳定性需求变化而变化。

### 找到变化轴

从初版代码推导至少四条变化轴：请求发送、横切观测、时间控制、业务链路状态、执行调度。判断哪些变化互相独立。

### 识别状态所有者

先不要使用当前类名，自行回答：请求参数、重试次数、远端任务状态、用例变量分别应该存活多久。

### 推导职责边界

画出你认为合理的边界草图，再与当前目录中的 `RequestContext`、`RetryExecutor`、`PollingPolicy`、`TestContext` 对照。记录哪些边界是你推导出来的，哪些是当前实现额外做出的选择。

### 比较其他方案

比较：继续扩大 `BaseRequest`、按功能拆工具函数、按状态生命周期拆对象。重点说明三种方案的测试成本。

### 最小实验与产出

- 产出一张“初版职责 → 变化轴 → 当前控制点”映射图。
- 执行 `git diff 56f4f15 291e6ea -- common/base_request.py`，用五句话解释改动为什么会如此集中。

### 验收

不引用当前类名，也能解释初版真正的约束不是“不能发请求”，而是什么。

---

## 第 2 天：配置从字符串集合演进为可信状态

### 核心问题

配置校验为什么是框架运行边界，而不只是几个字符串解析函数？

### 观察旧实现

```powershell
git show 56f4f15:config.py
git diff 56f4f15 291e6ea -- config.py util/config_validation.py
git show 2748f16 -- config.py util/config_validation.py
```

观察初版配置在什么时候失败、错误能否同时报告多个变量，以及调用方能否在运行期修改配置。

### 变化轴与状态所有者

变化轴包括：环境选择、原始字符串解析、跨字段约束、错误输出、安全脱敏、运行期只读性。

区分两类状态所有者：

- `_EnvironmentSettingsInput` 拥有不可信的外部输入。
- `Settings` 拥有已经验证、可供运行时消费的状态。

### 职责边界

推导为什么原始输入模型和公开运行时模型没有合并；为什么业务账号不应成为所有测试启动时的全局必填配置。

### 方案比较

比较：直接 `os.getenv()`、dataclass + 手写校验、单个 Pydantic model、输入模型 + 公开模型。说明每种方案在启动失败、兼容性和安全输出上的差异。

### 最小实验与产出

- 使用 `load_settings(mapping)` 构造两个同时缺失的变量，证明错误可以聚合。
- 尝试修改 `Settings.timeout`，观察 frozen model 的边界。
- 产出配置两阶段模型决策表。

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_config_validation.py -q
```

### 验收

能解释“尽早失败”和“离线单测不应被真实 `.env` 阻塞”之间的冲突，以及当前代码如何部分解决它。

---

## 第 3 天：从请求主流程推导 Middleware 边界

完整课程内容：[DAY03_REQUEST_MIDDLEWARE_BOUNDARY.md](DAY03_REQUEST_MIDDLEWARE_BOUNDARY.md)

### 核心问题

哪些扩展属于一次 HTTP 请求，哪些看起来像横切能力却不应该进入 Middleware？

### 观察旧实现

```powershell
git show 56f4f15:common/base_request.py
git diff 56f4f15 291e6ea -- common/base_request.py common/request_context.py common/request_middleware.py
```

在旧实现中标出 URL 构造、参数复制、媒体处理、日志、真实发送和异常处理。

### 变化轴与状态所有者

日志、脱敏、媒体资源处理和未来 trace 会独立变化，但都观察同一次 HTTP attempt。这个 attempt 的状态所有者是独立 `RequestContext`，而不是 `BaseRequest` 全局字段。

### 职责边界

用当前协议推导 Middleware 能做和不能做的事情：

- 能观察或补充一次请求上下文。
- 能处理成功响应或请求异常。
- 不能返回 retry decision。
- 不应自行再次调用 transport。

### 方案比较

比较：在 `request()` 中持续加分支、装饰器嵌套、事件回调、显式 Middleware 列表。说明为什么当前阶段没有建设动态插件注册中心。

### 最小实验与产出

实现只存在于学习测试中的 `AttemptTagMiddleware`，证明连续两次请求的 `attributes` 不共享。不要把它加入默认中间件。

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_request_middleware.py tests\test_base_request_middleware.py -q
```

### 验收

给出认证头注入、trace、重试、轮询四个需求的归属判断，并说明不能只凭“它们都发生在请求附近”就放入 Middleware。

---

## 第 4 天：日志与脱敏为什么是数据流问题

完整课程内容：[DAY04_LOGGING_REDACTION_DATA_FLOW.md](DAY04_LOGGING_REDACTION_DATA_FLOW.md)

### 核心问题

为什么“把敏感字段替换掉”可能破坏真实请求？日志正确性由谁负责？

### 观察旧实现

```powershell
git show 56f4f15:util/api_call_logger.py
git diff 56f4f15 291e6ea -- util/api_call_logger.py util/redaction.py util/curl_builder.py common/request_middleware.py
```

追踪一份 payload 的两条数据流：发送给服务端的原始数据，以及交给 logger 的安全副本。

### 变化轴与状态所有者

敏感字段集合、日志格式、cURL 格式和 HTTP 发送分别变化。真实请求由 `RequestContext.kwargs` 持有；脱敏副本属于观测数据，存放在 `attributes` 中供 logger 使用。

### 职责边界

推导以下不变量：

- 脱敏不修改调用方对象。
- 脱敏不修改 transport 输入。
- 请求、响应、异常和 cURL 使用一致的安全出口。
- SSE 场景不能因日志读取 `response.text` 而提前消费流。

### 方案比较

比较：发送前原地脱敏、logger 内各自脱敏、统一脱敏工具 + 安全副本。分析一致性、遗漏风险和调用副作用。

### 最小实验与产出

构造同时含 header、query、JSON 嵌套字段和异常文本的 secret，证明 transport 看到原值而附件看不到原值。

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_api_call_logger.py tests\test_curl_builder.py tests\test_request_middleware.py -q
```

### 验收

画出原始数据流和观测数据流，指出二者最迟应在哪个位置分叉。

---

## 第 5 天：从业务副作用推导 RetryPolicy

### 核心问题

重试判断的第一条件为什么不是状态码，而是业务操作能否安全重复？

### 观察旧实现

```powershell
git show 56f4f15:common/base_request.py
git show 291e6ea:common/retry.py
git diff 56f4f15 291e6ea -- common/base_request.py common/retry.py
```

确认初版没有显式重试策略，再观察第一版策略引入了哪些决策数据。

### 变化轴与状态所有者

变化轴包括方法幂等性、异常类型、状态码、退避方式、服务端 `Retry-After`、次数上限和总时间预算。它们描述“是否以及何时再次尝试”，因此由不可变 `RetryPolicy` 拥有，而不由某次响应临时决定全部规则。

### 职责边界

先判断方法是否允许重复，再判断本次结果是否属于瞬态故障，最后判断预算。明确 POST 的 `Idempotency-Key` 是业务安全证据，而不是一个普通 header 技巧。

### 方案比较

比较：全局自动重试、requests adapter 默认重试、调用点手写循环、显式 `RetryPolicy`。特别分析可观察性和 POST 副作用。

### 最小实验与产出

建立重试决策表，覆盖 GET/POST、400/429/503、Timeout/SSLError、带/不带幂等键。只测试策略函数，不进入完整请求链。

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_retry_policy.py -q
```

### 验收

面对一个收费 POST 接口，能列出允许重试所需的业务证据，而不是只修改 `allow_post=True`。

---

## 第 6 天：重试循环为什么从 BaseRequest 再次抽离

### 核心问题

已经有 `RetryPolicy` 后，为什么还要把执行循环抽成 `RetryExecutor`？

### 观察旧实现

这一天重点比较两个已经支持重试的阶段：

```powershell
git show 291e6ea:common/base_request.py
git show 2748f16:common/base_request.py
git show 2748f16:common/retry_executor.py
git show 2748f16 -- common/base_request.py common/retry_executor.py
```

列出抽离前 `_send_with_retry()` 同时知道的事情。

### 变化轴与状态所有者

`BaseRequest` 拥有请求构造、session、middleware 和单次发送；`RetryExecutor` 拥有 attempt 序号、累计记录、sleep 和时间预算。两者变化原因不同。

### 职责边界

推导 executor 为什么通过回调获得 `context_factory`、`send_once` 和 `attach_records`，而不是直接依赖 `BaseRequest` 和 Allure。

必须保持：每次 attempt 独立 context、最终异常类型不变、时间测试不真实等待。

### 方案比较

比较：循环留在 `BaseRequest`、继承式 RetryRequest、普通 RetryMiddleware、独立 executor。说明当前 Middleware 协议为什么无法正确拥有重试控制流。

### 最小实验与产出

使用假时钟推演 `503 → 503 → 200`，记录每一步的 context、record、sleep 和剩余预算。

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_retry_executor.py -q
```

### 验收

能从抽离前代码推导出 executor，而不是从 executor 类名反向解释设计。

---

## 第 7 天：从“字段出现”演进为业务状态机

### 核心问题

异步任务为什么不能用“结果字段出现则成功，否则继续等待”表示？

### 观察旧实现

```powershell
git show 56f4f15:common/base_request.py
git show 291e6ea:common/polling.py
git show 2748f16 -- common/polling.py common/base_request.py common/base_task.py
```

找出旧 `poll_get()` 如何判断成功、失败和超时，再确认当前代码已经删除旧 `success_json_path` / `failure_json_path` 入口。

### 变化轴与状态所有者

不同业务接口的状态值和 JSONPath 会变化；循环、deadline、transition 记录和最终异常结构相对稳定。状态集合属于 `PollingPolicy`，实际迁移历史属于一次 polling 执行。

### 职责边界

明确外层 polling 和内层 HTTP retry：一次 poll GET 可以重试，但重试成功只代表得到响应，不代表业务任务成功。

### 方案比较

比较：布尔 success path、回调 predicate、显式状态集合、完整通用状态机引擎。说明当前策略为何选择有限集合而没有建设通用工作流系统。

### 最小实验与产出

设计一个假想任务的状态表，并模拟 `queued → running → succeeded`、`queued → failed` 和 unknown。检查最后状态、最后响应及 transitions。

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_polling_state_machine.py tests\test_base_request_retry_polling.py -k "polling" -q
```

### 验收

画出 retry 内循环与 polling 外循环，分别标注它们的成功条件和时间预算。

---

## 第 8 天：TestContext 与用例生命周期

### 核心问题

为什么几个局部变量最终会演进成测试上下文？又为什么上下文不能自动变成全局数据仓库？

### 观察旧实现

```powershell
git show 56f4f15:common/base_task.py
git diff 56f4f15 291e6ea -- common/test_context.py module/conftest.py common/base_task.py
```

在旧 Task 和业务用例中寻找 `task_id`、`request_id` 等手工提取与传递方式。

### 变化轴与状态所有者

变量来源、字段路径、类型和清理动作会变化，但它们都属于单个测试用例。`TestContext` 持有用例事实和清理栈；pytest fixture 创建并结束其生命周期。

### 职责边界

区分 `RequestContext` 和 `TestContext`。前者属于一次 attempt，后者属于一个 case。`BaseRequest` 不应隐式读写测试上下文。

### 方案比较

比较：局部变量、模块级字典、pytest fixture 字典、显式 `TestContext`、外部 Redis。分析并发隔离、可见依赖和清理责任。

### 最小实验与产出

完成 JSONPath/Header 两种提取、类型校验、候选来源和两个 LIFO 清理回调。暂不学习契约断言。

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_test_context.py -q
```

### 验收

能解释当前业务采用仍较浅意味着什么，并给出把一条真实链路迁移到 `TestContext` 时的最小改动范围。

---

## 第 9 天：从重复字段断言演进为响应契约

### 核心问题

哪些响应规则是稳定契约，哪些只是当前业务示例，不能进入通用 Schema？

### 观察旧实现

```powershell
git show 56f4f15:common/base_assertions.py
git diff 56f4f15 291e6ea -- common/base_assertions.py module/smoke/response_schemas.py
```

观察旧断言如何逐字段验证，以及失败信息能否定位嵌套路径和保护敏感值。

### 变化轴与状态所有者

响应结构随协议版本变化；具体模型名、价格和业务消息随业务数据变化。Schema 只拥有结构性不变量，业务断言继续由模块断言或用例拥有。

### 职责边界

区分三种失败：响应不是 JSON、Schema 自身非法、响应不满足 Schema。三者的责任方和诊断信息不同。

### 方案比较

比较：手写逐字段断言、Pydantic 响应模型、JSON Schema、直接加载 OpenAPI。说明当前业务协议稳定度下，为什么没有直接建设自动用例生成平台。

### 最小实验与产出

为一个最小任务响应写 Schema，分别制造缺失字段、错误类型和敏感值错误，检查错误路径和脱敏结果。

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_base_assertions_schema.py -q
```

### 验收

给定五条响应规则，能判断哪些进入 Schema、哪些保留为业务断言，并说明变化频率依据。

---

## 第 10 天：Mock 是控制不确定性，不是伪造一切

### 核心问题

框架异常分支为什么必须离线验证？Fake 应该模拟到什么程度停止？

### 观察旧实现

```powershell
git show 291e6ea:tests/test_base_request_retry_polling.py
git show fbff62e:tests/mock_helpers.py
git show fbff62e -- tests/test_base_request_retry_polling.py tests/mock_helpers.py tests/test_stream_fault_simulation.py
```

观察 helper 出现前，各测试重复构造了哪些对象；helper 出现后又刻意没有进入哪些运行时代码。

### 变化轴与状态所有者

故障序列、假时间、请求调用记录和流式 chunk 属于单个测试场景。它们不属于生产请求客户端。

### 职责边界

Fake 只模拟被测试逻辑实际依赖的协议表面。Mock 验证框架分支，真实 smoke 验证环境集成，两者不能互相替代。

### 方案比较

比较：直接 monkeypatch、公共 fake helper、`responses/requests-mock`、本地 Mock Server。按 URL 匹配复杂度、Socket 真实性、维护成本选择。

### 最小实验与产出

用公共 helper 组合一个 `Timeout → 503 → 200` 场景，断言调用次数、等待记录和最终结果；再用 `FakeStreamResponse` 验证中途断流后 response 被关闭。

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_mock_helpers.py tests\test_stream_fault_simulation.py -q
```

### 验收

能指出当前 fake 与真实 `requests.Response` 的至少三个偏差，以及什么情况下这些偏差会迫使你升级测试方式。

---

## 第 11 天：从全量并发演进为资源约束调度

### 核心问题

并发执行的问题为什么不是“线程是否安全”这么简单？谁拥有串行决策？

### 观察旧实现

```powershell
git show 56f4f15:master_service.py
git show 56f4f15:run_master.py
git show 24a3d8c -- master_service.py run_master.py tests/test_master_service_parallel_serial.py
```

观察初版如何把 nodeid 直接交给 pytest，再分析共享账号、计费状态和固定测试数据为何不能安全全量并发。

### 变化轴与状态所有者

用例 marker 属于用例元数据；并发池和串行池属于一次执行计划；worker 数属于运行参数；共享资源安全条件属于业务用例知识。

### 职责边界

收集器负责获得 nodeid 和 markers，调度器负责拆池和执行顺序，业务作者负责标记共享资源约束。调度器无法仅靠代码自动推断所有业务冲突。

### 方案比较

比较：全串行、全并发、marker 拆池、文件级拆 Job、资源锁。分析吞吐、配置成本、死锁风险和报告合并复杂度。

### 最小实验与产出

推演 5 个并发用例和 2 个串行用例：并发池失败后串行池是否执行、退出码如何合并、JUnit 文件为何拆分、Allure results 为何需要保存和恢复。

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_master_service_parallel_serial.py -q
.\.venv\Scripts\python.exe run_master.py module\smoke --collect-only -q
```

### 验收

能解释 `serial` marker 表达的是资源冲突知识，而不是简单的“这个测试代码写得不线程安全”。

---

## 第 12 天：工程闭环与陌生需求设计

### 核心问题

怎样证明自己掌握的是设计方法，而不是只会复述当前实现？

### 观察演进边界

只阅读 Jenkinsfile 与执行入口之间的接口，不学习 Jenkins 平台的全部配置：

```powershell
git show 41cf8b5:Jenkinsfile
git diff 41cf8b5 24a3d8c -- Jenkinsfile run_master.py
```

识别框架单测、collect-only、真实 smoke、JUnit 和 Allure 分别提供什么证据。注意当前部分 README 文字仍声称 CI 未接入，判断时以代码为准。

### 陌生需求

设计一个仓库当前没有实现的请求追踪能力：

> 一个逻辑 API 调用拥有固定 `trace_id`；发生重试时每个 attempt 拥有不同 `attempt_id`；两者进入安全日志，但不要求服务端接受 `attempt_id`。并发请求之间不得串值。

### 按统一路线完成设计

1. 观察当前 `RequestContext`、Middleware、RetryExecutor 和 logger 的数据流。
2. 找出 trace ID 与 attempt ID 的不同变化轴。
3. 判断两个 ID 分别由谁创建、修改和结束。
4. 推导职责边界以及需要修改的最小接口。
5. 比较至少三种方案：全部放 Middleware、全部放 RetryExecutor、逻辑调用上下文与 attempt 上下文分层。

### 必须提交的课程产出

- 一页状态生命周期图。
- 一张三方案决策表。
- 一份不超过 500 字的设计说明。
- 三个关键测试的名称和 Arrange/Act/Assert，不要求完成生产代码：
  - 无重试时两个 ID 的行为。
  - 两次 retry attempt 的隔离与关联。
  - 两个并发逻辑调用互不串值。

### 最终验收

用 15 分钟讲清：

- 为什么所选方案符合状态生命周期。
- 为什么另外两个方案在当前约束下较差。
- 哪些现有公开调用必须保持兼容。
- 如何证明日志安全和并发隔离。
- 如果未来引入异步客户端，当前设计哪个假设最可能失效。

最后执行当前基线，确认课程实验没有破坏框架：

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
.\.venv\Scripts\python.exe run_master.py module\smoke --collect-only -q
```

## 7. 每日完成标准

每天不以“读完文件”作为完成标准。以下五项全部具备才算完成：

1. 能描述旧实现的具体问题，而不是泛泛说耦合高。
2. 能列出至少两个独立变化轴。
3. 能指出核心状态的创建者、修改者和终结者。
4. 能从不变量推导当前职责边界。
5. 能比较当前方案和至少一个替代方案。

测试通过只是第六项辅助证据。

## 8. 课程后的继续方向

课程结束后，不优先增加新抽象。先选择一条低风险真实链路完成两项迁移：

1. 使用 `TestContext` 取代散落的变量提取和清理逻辑。
2. 为一个只读 GET 接口明确接入 `RetryPolicy`，检查重试记录能否支持真实问题定位。

届时新的主约束将不再是“看不懂框架边界”，而是“已实现能力进入真实业务的成本和收益是否匹配”。再根据这个约束决定下一轮扩展。
