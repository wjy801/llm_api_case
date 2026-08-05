# 阶段3完成Polling、TestContext、Capture和Assertions分类详细开发方案

> 日期：2026-08-05
> 状态：待执行
> 所属总方案：`dev_plan/离线框架能力分类用例与黄金路径详细执行方案_20260805.md`
> 协议输入：`dev/离线框架能力示例协议与验收合同_20260805.md`
> 前置阶段：阶段1确定性服务、阶段2四件套及Request/Retry分类已完成
> 阶段性质：验证状态机、用例上下文、资源捕获和契约诊断，不实现并发与黄金路径

## 1. 需求复述与阶段目标

阶段3基于现有四件套和确定性本地服务，完成三组分类用例：

```text
Polling终态与严格总deadline
TestContext提取、类型和清理
Capture与Assertions分层契约
```

阶段完成后必须证明：

1. Polling成功、业务失败、未知状态和超时四种互斥终态各自可解释；
2. 成功场景中的HTTP 503由Retry消费，不进入业务状态迁移；
3. Polling异常保留最后状态、最后响应和完整迁移；
4. 总deadline耗尽后不接受成功、不继续启动新请求；
5. TestContext能从真实HTTP响应提取JSON、Header、Cookie和Regex值；
6. transform、expected_type、required、default和require语义清晰；
7. cleanup严格LIFO，单个失败不阻断后续回调，并聚合脱敏错误；
8. DELETE cleanup发生在Request Session和本地服务关闭之前；
9. 输入和输出Capture只访问loopback资源，并写入pytest临时目录；
10. 输出Capture同步完成，输入Capture通过Event和原子文件落点有界等待；
11. Capture超限失败采用fail-open，不改写成功业务响应；
12. Assertions按状态码、关键字段和Schema三层复用公共原语；
13. Schema错误能定位JSON Path和Schema Path，且不泄露认证占位值；
14. 阶段1、阶段2、框架完整回归和Smoke收集保持不退化。

本阶段的成功标准不是“新增12条测试”，而是让状态、上下文、文件和异常诊断共享同一业务结论，并且每个资源都有明确生命周期所有者。

## 2. 第一性原理与TOC判断

### 2.1 问题本质

阶段2已经证明单请求和Retry链路可信。阶段3需要建立有状态流程的完整因果链：

```text
固定服务场景
→ PollingPolicy解释业务状态
→ TestContext保存跨步骤值
→ Capture下载本地输入/输出资源
→ Assertions验证响应合同
→ Cleanup释放本地任务
→ pytest与Allure消费同一执行结果
```

如果生命周期顺序错误，则会形成：

```text
Request提前关闭或服务提前停止
→ TestContext cleanup无法DELETE
→ 业务任务残留

临时目录提前删除
→ Allure teardown找不到附件源文件
→ 用例通过但报告证据缺失

Capture异常覆盖业务响应
→ 文件问题被误判为接口失败
```

因此阶段3的本质是“终态与资源所有权一致”，不是增加更多接口包装。

### 2.2 TOC首要约束

当前首要约束是跨能力生命周期顺序：

```text
Polling结论
→ Context cleanup
→ Request关闭
→ Service停止
→ teardown hook读取仍然存在的附件源文件
→ 临时目录回收
```

TOC处理顺序：

1. 识别约束：终态、cleanup和附件分别由不同公共组件管理；
2. 利用约束：通过fixture依赖建立唯一收尾顺序，不增加全局管理器；
3. 服从约束：先分别验证Polling、Context、Capture，不提前组合黄金路径；
4. 提升约束：用异常对象、服务快照、Event、Hash和Allure原始附件形成闭环证据；
5. 防止惯性：阶段3通过后停止扩张状态测试，把并发传播和组合流程留给阶段4。

### 2.3 决策原则

- Polling必须调用BaseTask/BaseRequest现有入口，不复制while循环；
- 每次Polling显式传入`OFFLINE_POLLING_POLICY`；
- Polling测试使用`CapturePolicy.disabled()`隔离状态机职责；
- Context只使用`TestContext`实例，不使用模块全局变量；
- cleanup只操作当前测试的本地任务和本地回调；
- Capture只使用框架现有MediaResourceMiddleware和BaseDecorators；
- 输入异步下载不使用固定长sleep；
- 文件Hash使用阶段0冻结值，不重新生成期望；
- Assertions只调用BaseAssertions公共方法，不复制JSONPath或Schema算法；
- 业务测试不读取Quality、Metrics、Flaky或Runner内部产物；
- 不新增Marker、报告、DSL、DI容器、事件总线或Task注册器。

## 3. 已确认的实施基线

### 3.1 阶段1服务能力

可直接使用的冻结场景：

| 场景 | Polling序列 | Capture资源 |
|---|---|---|
| `DEFAULT` | succeeded | 70字节正常输出 |
| `POLL_SUCCESS_WITH_RETRY` | HTTP503、queued、running、succeeded | 正常输出 |
| `POLL_FAILURE` | queued、failed | 不下载输出 |
| `POLL_UNKNOWN` | queued、paused | 不下载输出 |
| `POLL_TIMEOUT` | running持续重复 | 不下载输出 |
| `CAPTURE_OVERSIZED` | succeeded | 140字节超限输出 |

服务公开状态可提供：

```text
endpoint_call_counts
tasks
deleted_task_ids
request_hosts
input/output/oversized Event
handler_errors
```

阶段3只读取公开`state.snapshot()`和Event，不修改服务游标或私有Handler状态。

### 3.2 阶段2四件套能力

可直接复用：

```text
OfflineFrameworkRequest
OfflineFrameworkTask
OfflineFrameworkAssertions
OfflineFrameworkDecorators
OFFLINE_RETRY_POLICY
OFFLINE_POLLING_POLICY
4个响应Schema
offline_request_factory
offline_runtime_recorder
```

Offline Task已经继承：

```text
create_media_generation()
poll_media_generation_result()
create_and_poll_media_generation()
extract_task_id()
```

并已提供：

```text
build_media_generation_payload()
query_context()
delete_media_task()
query_contract()
```

阶段3原则上不修改四件套生产逻辑。

### 3.3 Polling公共语义

当前BaseRequest已经保证：

- HTTP timeout、Retry等待、poll sleep共享一个deadline；
- 每次transport timeout会截断到剩余预算；
- 响应到达后若总预算已耗尽，先抛`PollingTimeoutError`，不接受成功；
- `PollingFailedError`保存`error_value`；
- Polling异常保存`last_status`、`last_response`和`transitions`；
- transition保存attempt index、elapsed、状态分类、raw status和HTTP状态；
- 成功后按`result_json_path`同步执行输出Capture；
- Capture失败只记录附件错误，不替换业务响应。

### 3.4 TestContext公共语义

已确认：

- `extract()`支持JSONPath、Header、Cookie、Regex；
- `extract_first()`支持多来源顺序回退；
- 支持required、default、expected_type、transform、allow_none；
- cleanup按后进先出执行；
- cleanup失败后继续执行剩余回调；
- 最后抛`ContextCleanupError`并保存原始`errors`列表；
- 变量值、响应摘要和cleanup错误通过现有脱敏工具处理。

### 3.5 Capture公共语义

- 输入Capture由POST前的MediaResourceMiddleware异步启动；
- 输入下载使用临时`.part`文件，完成后原子替换最终文件；
- 输出Capture由`poll_get()`装饰器在成功后同步下载；
- `offline_capture_dirs`已将输入目录重定向到`tmp_path/input`；
- `offline_capture_dirs`已将输出目录重定向到`tmp_path/output`；
- module级teardown hook在pytest teardown末尾挂载输入和输出资源；
- `tmp_path`在hook挂载期间仍存在。

## 4. 总方案草案与冻结合同的差异处理

总方案阶段3曾写：

```text
超时测试使用可控服务延迟和短总预算
```

阶段0最终合同已经冻结为：

```text
POLL_TIMEOUT持续返回running
poll_interval = 0.01秒
poll_timeout = 0.03秒
服务不增加后台定时器、随机状态或延迟字段
```

阶段3以阶段0合同为准：

```text
running响应
→ Polling执行0.01秒有界sleep
→ 共享deadline自然耗尽
→ 抛PollingTimeoutError
```

不修改`OfflineServiceScenario`，不添加delay字段，不通过服务sleep制造超时。该处理不是合同变更，而是执行总方案时服从更晚、更具体的冻结输入。

## 5. 阶段边界

### 5.1 阶段内工作

- 扩展委托式Runtime记录器以观察Polling分类状态和终态；
- 增加具有正确依赖顺序的Offline TestContext fixture；
- 实现4条Polling分类用例；
- 实现4条TestContext与cleanup分类用例；
- 实现4条Capture与Assertions分类用例；
- 验证Allure原始输入/输出附件可读取；
- 验证阶段1、阶段2、完整框架和Smoke不退化。

### 5.2 明确不做

- 不实现阶段4并发ContextVar分类；
- 不实现黄金路径组合用例；
- 不增加线程池Request Session逻辑；
- 不读取Quality、Metrics、Flaky数据库和机器文件；
- 不修改`common/`、`quality/`、`run_orchestration/`或Jenkins；
- 不修改Retry、Polling、TestContext、Capture和Assertions公共实现；
- 不新增服务场景、HTTP端点、Marker或环境开关；
- 不增加专用下载器、附件器或Schema错误格式化器；
- 不使用真实外部资源、真实账号或真实接口；
- 不恢复SSE或流式输出。

## 6. 文件级修改清单

### 6.1 新增文件

```text
module/offline_framework_example/test_polling.py
module/offline_framework_example/test_context_cleanup.py
module/offline_framework_example/test_capture_assertions.py
```

### 6.2 修改文件

```text
module/offline_framework_example/conftest.py
```

只扩展测试观察和生命周期fixture，不改变阶段1、2已有fixture语义。

### 6.3 原则上零修改

```text
module/offline_framework_example/offline_service.py
module/offline_framework_example/request.py
module/offline_framework_example/task.py
module/offline_framework_example/assertions.py
module/offline_framework_example/decorators.py
module/offline_framework_example/response_schemas.py
module/offline_framework_example/__init__.py
tests/test_offline_service.py
dev/离线框架能力示例协议与验收合同_20260805.md
common/
quality/
run_orchestration/
Jenkinsfile
```

若现有四件套或公共能力无法表达阶段3合同，应触发停止条件，而不是直接扩张生产API。

### 6.4 工作树保护

执行前记录完整工作树，保护已知用户内容：

```text
module/material_library/test_volc_cn_assets.py
module/material_library/volc_cn_asset_mgmt_smoke.py
module/material_library/volc_cn_asset_pipeline_smoke.py
module/test/
既有dev与dev_plan文件
```

不得清理、覆盖或暂存无关改动和未跟踪文件。

## 7. 生命周期和所有权设计

### 7.1 正常顺序

```text
OfflineService启动
→ OfflineFrameworkRequest创建
→ OfflineTestContext创建
→ 用例执行与业务断言
→ OfflineTestContext.cleanup
→ OfflineFrameworkRequest.close
→ OfflineService.stop
→ module teardown hook挂载仍然存在的Capture文件
→ pytest临时目录回收
```

pytest fixture依赖必须保证业务资源清理时Request和Service仍可用。当前附件hook是`pytest_runtest_teardown` hookwrapper的post-yield逻辑，因此会在fixture teardown后读取本地文件；Request和Service关闭不删除Capture文件，`tmp_path`也不得在hook读取前被主动删除。

### 7.2 责任表

| 资源/状态 | 创建者 | 正常清理者 | 异常兜底 |
|---|---|---|---|
| 本地HTTP服务 | `offline_service_factory` | 同fixture | 逆序stop并检查Handler错误 |
| Request Session | `offline_request_factory` | 同fixture | 逆序close全部client |
| 本地媒体任务 | TestContext cleanup | DELETE业务动作 | 服务fixture销毁本地state |
| Context变量 | `offline_test_context` | fixture结束 | Context对象释放 |
| 输入文件 | MediaResourceMiddleware | pytest tmp目录 | teardown hook在临时目录回收前附加 |
| 输出文件 | BaseDecorators | pytest tmp目录 | teardown hook在临时目录回收前附加 |
| Runtime Hooks token | `offline_runtime_recorder` | fixture finally | `reset_runtime_hooks(token)` |

### 7.3 异常路径原则

- Polling异常不触发输出成功文件；
- Context cleanup错误必须暴露，但仍关闭Request和Service；
- Capture失败不得替换Polling成功响应；
- Schema断言失败只影响当前断言用例，不改变服务状态；
- teardown任何一步失败时继续尝试后续可执行清理，并聚合报告。

## 8. `conftest.py`扩展设计

## 8.1 `OfflinePollingRecord`

增加只服务测试观察的记录对象：

```text
delegate_handle
states: list[str]
sleep_seconds: list[float]
outcome: RuntimePollingOutcome | None
```

其中`states`记录公共Runtime Hooks收到的分类状态：

```text
pending
success
failure
unknown
```

raw业务状态仍从真实Polling HTTP响应和异常`transitions`读取，不让记录器重新解释响应。

## 8.2 扩展`OfflineRuntimeRecorder`

包装以下公共Hooks方法：

```text
begin_polling_session
observe_polling_state
add_polling_sleep
finish_polling_session
```

规则与阶段2一致：

1. 先创建或更新本地记录；
2. 始终调用原delegate对应方法；
3. delegate返回的native handle保存在record中；
4. 对外返回record作为opaque handle；
5. 后续调用把record还原为delegate handle再委托；
6. 不导入Quality实现，不读取报告文件；
7. 不改变原Hooks的fail-open语义；
8. fixture结束恢复原ContextVar token。

成功场景预期分类状态：

```text
pending → pending → success
```

失败、未知和超时预期终态分别为：

```text
failure
unknown
timeout
```

## 8.3 `offline_test_context`

新增函数级fixture：

```text
offline_test_context(offline_request_factory)
→ 创建TestContext(name="offline-framework-example")
→ yield
→ finally调用cleanup()
```

显式依赖`offline_request_factory`用于建立teardown顺序：

```text
offline_test_context先cleanup
→ offline_request_factory再close
→ offline_service_factory最后stop
```

fixture规则：

- 不复用module级全局Context；
- 每条测试获得独立变量和cleanup栈；
- 用例手动调用cleanup后，fixture再次cleanup应为空操作；
- 自动cleanup失败必须作为teardown错误暴露；
- 不吞掉`ContextCleanupError`。

## 8.4 Capture有界文件等待

不新增通用fixture或生产API。在`test_capture_assertions.py`中定义私有测试辅助函数：

```text
_wait_for_file(path, timeout=1秒)
```

执行规则：

```text
先等待service.input_asset_requested Event
→ 检查最终文件路径
→ 未出现则按极短间隔有界重试
→ deadline到达抛清晰AssertionError
```

该等待不是固定sleep：每次都检查最终条件，且下载器只在完整内容写入`.part`后原子替换最终路径，因此最终文件一旦出现即可安全计算Hash。

## 9. Polling分类公共安排

测试文件：

```text
module/offline_framework_example/test_polling.py
```

稳定类：

```text
TestPolling
```

每条测试：

1. 通过`offline_service_factory`创建独立场景；
2. 通过`offline_request_factory`创建Request；
3. 传入`CapturePolicy.disabled()`，防止状态机测试产生文件；
4. 使用Task构建媒体payload；
5. 使用继承的`create_media_generation()`创建任务；
6. 使用继承的`poll_media_generation_result()`执行Polling；
7. 显式传入`OFFLINE_POLLING_POLICY`；
8. 需要Retry的成功场景显式传入`OFFLINE_RETRY_POLICY`；
9. 使用`poll_interval=0.01`；
10. 成功、失败和未知使用`poll_timeout=1`；
11. 超时使用`poll_timeout=0.03`。

不得参数化四种互斥终态，保证nodeid和失败归因稳定。

## 10. `test_polling.py`详细用例

## 10.1 `test_polling_reaches_success_with_complete_transitions`

场景：`POLL_SUCCESS_WITH_RETRY`。

固定流程：

```text
POST创建任务
→ 第一次Polling transport返回503
→ Retry同组挽救得到queued
→ 下一轮得到running
→ 最后一轮得到succeeded
```

必须断言：

- 创建响应HTTP 202；
- 创建调用1次；
- 最终响应HTTP 200；
- 最终task ID为`offline-task-001`；
- 最终status为`succeeded`；
- 最终响应满足`OFFLINE_POLLING_SUCCESS_SCHEMA`；
- `media_poll_calls == 4`；
- Polling协议响应HTTP序列为`[503, 200, 200, 200]`；
- HTTP 200业务状态为`queued → running → succeeded`；
- 503不出现在业务状态序列；
- 第一个Polling请求组attempt index为`[1, 2]`；
- Runtime分类状态为`pending → pending → success`；
- Polling outcome为SUCCESS；
- Capture目录未创建输出文件，因为本分类禁用了Capture；
- 服务任务仍存在，清理由服务fixture本地兜底，本用例不混入TestContext职责。

## 10.2 `test_polling_reports_business_failure`

场景：`POLL_FAILURE`。

使用`pytest.raises(PollingFailedError)`，必须断言：

- `last_status == "failed"`；
- `last_response`存在且响应status为failed；
- `error_value`等于冻结error对象；
- transition raw status为`["queued", "failed"]`；
- transition状态分类为PENDING、FAILURE；
- 服务`media_poll_calls == 2`；
- Runtime Polling outcome为FAILURE；
- 异常文本包含路径、failed和迁移；
- 异常文本不包含认证占位值；
- 没有输出文件。

## 10.3 `test_polling_rejects_unknown_state`

场景：`POLL_UNKNOWN`。

使用`pytest.raises(PollingUnknownStateError)`，必须断言：

- `last_status == "paused"`；
- `last_response`存在且status为paused；
- transition raw status为`["queued", "paused"]`；
- transition状态分类为PENDING、UNKNOWN；
- 服务`media_poll_calls == 2`；
- Runtime outcome为UNKNOWN；
- 未知状态没有被当作pending继续等待；
- 没有输出文件。

## 10.4 `test_polling_enforces_total_deadline`

场景：`POLL_TIMEOUT`。

使用`pytest.raises(PollingTimeoutError)`，必须断言：

- 至少一次真实Polling请求；
- `last_status == "running"`；
- `last_response`存在且status为running；
- transitions非空；
- 所有transition raw status均为running；
- 所有transition分类均为PENDING；
- transition attempt index单调递增；
- Runtime outcome为TIMEOUT；
- 异常返回后服务调用计数不再变化；
- 单用例目标耗时小于0.5秒；
- 不断言精确poll次数和精确毫秒；
- 没有输出文件。

“调用计数不再变化”在同步`poll_get()`抛出后读取两次快照确认，不增加固定等待。公共严格deadline单元测试继续负责“超时后到达成功也不得接受”的底层边界，阶段3不新增延迟场景重复该算法测试。

## 11. TestContext分类公共安排

测试文件：

```text
module/offline_framework_example/test_context_cleanup.py
```

稳定类：

```text
TestContextAndCleanup
```

测试使用：

```text
offline_service或offline_service_factory
offline_request或offline_request_factory
offline_test_context
OfflineFrameworkTask
OfflineFrameworkAssertions
```

需要创建媒体任务的cleanup用例使用`CapturePolicy.disabled()`，避免Context分类产生文件副作用。

## 12. `test_context_cleanup.py`详细用例

## 12.1 `test_context_extracts_json_header_cookie_and_regex`

使用`Task.query_context()`取得真实`GET /v1/context`响应。

依次提取：

| 变量 | API | 来源 | 期望 |
|---|---|---|---|
| `task_id` | `extract` | `$.data.task_id` | `offline-task-001` |
| `request_id` | `extract` | Header `X-Request-ID` | `offline-request-001` |
| `session_id` | `extract` | Cookie `offline_session` | `offline-session-001` |
| `trace_id` | `extract` | Regex group 1 | `offline-trace-001` |

每次设置`expected_type=str`。必须断言：

- 四个返回值准确；
- `context.require()`取得相同值；
- `snapshot()`只包含当前四个变量；
- 服务`context_calls == 1`；
- 响应Header和Cookie来自真实本地HTTP；
- 不通过测试模块变量跨步骤传值。

## 12.2 `test_context_applies_type_and_transform_contracts`

仍使用真实Context响应，验证：

1. task ID经`str.upper` transform后存储为大写；
2. 缺失`$.count`时使用default字符串`"2"`；
3. default经`int` transform后满足`expected_type=int`；
4. `require("count", expected_type=int)`返回2；
5. `required=False`且无default的缺失值返回None且不存储；
6. 错误expected_type抛`ContextVariableTypeError`；
7. 类型错误信息包含变量名和类型，不包含认证占位值。

不重复测试TestContext所有底层分支，只证明业务响应能够使用这些公共合同。

## 12.3 `test_context_runs_cleanup_in_lifo_order`

流程：

```text
创建本地媒体任务
→ 注册first记录回调
→ 注册DELETE并记录delete的回调
→ 注册last记录回调
→ context.cleanup()
```

必须断言：

- 调用顺序为`last → delete → first`；
- DELETE返回HTTP 204；
- `media_delete_calls == 1`；
- `deleted_task_ids`包含固定task ID；
- `tasks`最终为空；
- 第二次`cleanup()`为空操作；
- Request在cleanup完成后仍可检查服务状态；
- fixture结束后才关闭Request和Service。

DELETE包装函数可以记录顺序，但业务删除必须委托`Task.delete_media_task()`，不能直接改服务state。

## 12.4 `test_context_continues_cleanup_and_reports_errors`

流程：

```text
创建本地媒体任务
→ 注册first回调
→ 注册DELETE回调
→ 注册一个受控失败回调
→ 注册last回调
→ context.cleanup()
```

预期执行顺序：

```text
last → fail → delete → first
```

必须断言：

- 抛`ContextCleanupError`；
- `errors`列表只有受控失败；
- DELETE即使位于失败回调之后仍执行；
- 所有回调均执行一次；
- 服务tasks最终为空；
- cleanup栈已经清空，fixture再次cleanup不重复执行；
- 聚合错误包含回调类型和数量；
- 完整`Authorization: Bearer offline-example-key`不在错误文本；
- `offline-example-key`不在错误文本；
- 错误文本出现统一脱敏占位符。

## 13. Capture与Assertions分类公共安排

测试文件：

```text
module/offline_framework_example/test_capture_assertions.py
```

稳定类：

```text
TestCaptureAndAssertions
```

全部用例使用`offline_capture_dirs`。Request根据职责显式使用：

```text
CapturePolicy.output_only()
CapturePolicy.input_only()
CapturePolicy.output_only(max_bytes=100)
CapturePolicy.disabled()
```

每条用例只开启需要验证的Capture方向，避免输入与输出证据互相污染。

## 14. `test_capture_assertions.py`详细用例

## 14.1 `test_output_capture_and_contract_assertions`

场景：`DEFAULT`。Request使用`CapturePolicy.output_only()`。

流程：

```text
构建含loopback输入URL的媒体payload
→ BaseTask create_and_poll_media_generation
→ 创建任务202
→ Polling立即succeeded
→ 输出装饰器同步下载output.png
→ 三层Assertions
```

必须断言：

- 最终HTTP 200；
- 状态码断言返回原response；
- task ID和status领域断言返回原response；
- `OFFLINE_POLLING_SUCCESS_SCHEMA`通过并返回原response；
- 结果URL通过`offline_network_guard`；
- `output_asset_requested` Event已设置；
- `output_asset_calls == 1`；
- 输出目录只存在完整`output.png`；
- 文件长度70字节；
- SHA-256等于`OUTPUT_PNG_SHA256`；
- 输入Capture未启用，input目录为空；
- 业务原payload不被改变。

## 14.2 `test_input_capture_uses_only_loopback_resource`

场景：`DEFAULT`。Request使用`CapturePolicy.input_only()`。

流程：

```text
Task构建payload
→ 调用offline_network_guard检查input URL
→ POST创建任务
→ MediaResourceMiddleware异步下载input.png
→ 等待service input Event
→ 有界等待最终文件原子出现
```

必须断言：

- 创建响应HTTP 202；
- 创建响应满足`OFFLINE_CREATE_TASK_SCHEMA`；
- task ID正确；
- payload资源主机为`127.0.0.1`；
- `input_asset_requested.wait(timeout)`成功；
- `input_asset_calls == 1`；
- input目录只存在完整`input.png`，不存在残留`.part`；
- 文件长度70字节；
- SHA-256等于`INPUT_PNG_SHA256`；
- output目录为空；
- 不使用固定长sleep。

## 14.3 `test_capture_limit_failure_does_not_override_response`

场景：`CAPTURE_OVERSIZED`。Request使用：

```python
CapturePolicy.output_only(max_bytes=100)
```

服务返回140字节并声明Content-Length。必须断言：

- 最终Polling业务响应仍为HTTP 200；
- status仍为`succeeded`；
- 最终响应仍满足成功Schema；
- result URL指向`/assets/oversized-output.png`且通过loopback guard；
- `oversized_asset_requested` Event已设置；
- `oversized_asset_calls == 1`；
- output目录不存在成功文件和`.part`残留；
- Capture错误没有替换response或抛到业务用例；
- pytest原始结论为PASSED；
- 不读取Allure内部错误附件作为业务断言。

## 14.4 `test_schema_error_has_path_and_redacted_diagnostics`

场景：`DEFAULT`，Request使用`CapturePolicy.disabled()`。

调用：

```text
Task.query_contract(request, "invalid_schema")
```

服务返回HTTP 200但缺少创建任务Schema必填字段。使用：

```text
OfflineFrameworkAssertions.assert_schema(
    response,
    OFFLINE_CREATE_TASK_SCHEMA,
)
```

必须断言`AssertionError`包含：

```text
JSON Schema assertion failed
Path: $.task_id
Schema path: required
Validator: required
Actual type: <missing>
```

并断言错误文本不包含：

```text
offline-example-key
Bearer offline-example-key
Authorization完整值
```

该用例只消费BaseAssertions现有诊断，不复制Schema错误排序、路径格式化或脱敏算法。

## 15. Assertions分层规则

阶段3成功响应统一按以下顺序：

```text
状态码
→ 关键业务字段
→ JSON Schema
→ 领域关系或文件事实
```

具体入口：

| 层次 | 公共/领域入口 |
|---|---|
| 状态码 | `assert_status_code()` |
| task ID | `assert_task_id()` |
| task status | `assert_task_status()` |
| JSON字段 | `assert_json_value()` / `assert_json_path_exists()` |
| Schema | `assert_schema()` |
| 文件 | 标准库长度与SHA-256 |

禁止：

- 用一个宽泛`assert response.json()`代替分层断言；
- 在Offline Assertions中复制BaseAssertions逻辑；
- 将完整响应、Authorization或Cookie值拼入自定义错误；
- 通过Schema约束随机端口；loopback URL由网络守卫验证。

## 16. Allure附件生命周期验收

阶段3定向测试必须显式使用系统临时Allure raw目录，并关闭HTML和历史生成。

运行结束后只读检查raw目录：

- 至少存在输入PNG附件；
- 至少存在输出PNG附件；
- 附件Hash集合包含冻结输入Hash；
- 附件Hash集合包含冻结输出Hash；
- 不要求固定Allure UUID、文件名或JSON顺序；
- 超限Capture只产生失败文本证据，不产生140字节成功PNG附件。

该验收证明：

```text
临时源文件在module teardown hook读取时仍然存在
→ Allure完成附件复制
→ tmp_path之后才可回收
```

不在业务测试中解析Allure结果文件；附件检查属于阶段执行后的运行级门禁。

## 17. 实施任务分解

## 17.1 任务3.0：保护工作树并复验阶段1、2

执行：

1. 记录分支、HEAD和工作树；
2. 确认3个目标测试文件不存在用户内容；
3. 运行阶段1基础设施门禁；
4. 运行阶段2的7条分类用例；
5. 确认离线模块当前collect-only为7项全并发；
6. 确认四件套、策略和Schema可稳定导入。

门禁：任何前置阶段失败都先归因，不进入阶段3实现。

## 17.2 任务3.1：扩展Polling观察和Context fixture

执行：

1. 增加`OfflinePollingRecord`；
2. 扩展Runtime recorder的4个Polling方法；
3. 保持对原Hooks完整委托；
4. 增加`offline_test_context`；
5. 核验Context→Request→Service teardown依赖。

门禁：阶段2Runtime记录用例继续通过，Quality开启/关闭均不被截断。

## 17.3 任务3.2：实现Polling分类

按第9～10节实现4个稳定nodeid。所有Polling测试禁用Capture。

门禁：四种终态独立，成功计数准确，异常属性完整，超时不冻结精确次数。

## 17.4 任务3.3：实现TestContext分类

按第11～12节实现4个稳定nodeid。

门禁：真实HTTP提取完整；LIFO准确；cleanup失败后DELETE仍执行；错误脱敏。

## 17.5 任务3.4：实现Capture与Assertions分类

按第13～15节实现4个稳定nodeid，并增加私有有界文件等待辅助函数。

门禁：输入/输出Hash准确；超限fail-open；Schema诊断路径准确；目录只在tmp_path。

## 17.6 任务3.5：定向验证与附件核验

执行12条阶段3测试，检查Allure raw附件Hash，不检查内部Quality文件。

门禁：12条全绿；输入/输出附件均可读；无仓库`data/`污染。

## 17.7 任务3.6：完整回归与范围审查

执行阶段1、阶段2、完整`tests`、离线collect-only、Smoke collect-only及diff检查。

不得把临时Allure、下载文件、pytest缓存或报告目录纳入改动。

## 18. 验证命令

以下命令从仓库根目录执行。

### 18.1 语法检查

```powershell
.\.venv\Scripts\python.exe -m py_compile `
  module/offline_framework_example/conftest.py `
  module/offline_framework_example/test_polling.py `
  module/offline_framework_example/test_context_cleanup.py `
  module/offline_framework_example/test_capture_assertions.py
```

### 18.2 前置阶段回归

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_offline_service.py -q

$env:GENERATE_ALLURE_REPORT = "FALSE"
$env:GENERATE_HISTORY_REPORT = "FALSE"
$stage2Allure = Join-Path ([System.IO.Path]::GetTempPath()) "api-case-stage2-regression-$PID"
.\.venv\Scripts\python.exe -m pytest `
  module/offline_framework_example/test_request_pipeline.py `
  module/offline_framework_example/test_retry.py `
  --alluredir=$stage2Allure -q
```

当前快照分别为18项和7项，长期门禁以全部通过为准。

### 18.3 阶段3定向测试

```powershell
$env:GENERATE_ALLURE_REPORT = "FALSE"
$env:GENERATE_HISTORY_REPORT = "FALSE"
$stage3Allure = Join-Path ([System.IO.Path]::GetTempPath()) "api-case-stage3-allure-$PID"
.\.venv\Scripts\python.exe -m pytest `
  module/offline_framework_example/test_polling.py `
  module/offline_framework_example/test_context_cleanup.py `
  module/offline_framework_example/test_capture_assertions.py `
  --alluredir=$stage3Allure -q
```

初版预期12项：Polling 4、TestContext 4、Capture/Assertions 4。

### 18.4 Allure raw附件Hash核验

```powershell
$pngAttachments = Get-ChildItem -LiteralPath $stage3Allure -File -Filter '*-attachment.png'
$attachmentHashes = $pngAttachments | Get-FileHash -Algorithm SHA256 | Select-Object -ExpandProperty Hash
$attachmentHashes
```

Hash集合必须包含：

```text
F2BB5BBACA678ECAD746B1FA5ECFA2C8A81DD18817BE19F0187C036D25326317
49E1DAD481E94DFAB7C9573A9A81D56AA2CA629FE15A3F7A910AA4F47601C00D
```

不得断言附件数量或Allure生成文件名完全固定。

### 18.5 离线模块权威收集

```powershell
.\.venv\Scripts\python.exe run_master.py `
  module/offline_framework_example --collect-only -q
```

阶段3快照应满足：

```text
阶段2既有用例 = 7
阶段3新增用例 = 12
总集合 = 19
parallel集合 = 19
serial集合 = 0
```

该数量仅是阶段3执行快照，阶段4新增用例后会变化。

### 18.6 框架完整回归

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
```

要求现有框架测试全部通过，不允许跳过失败或修改pytest默认收集掩盖问题。

### 18.7 Smoke收集回归

```powershell
.\.venv\Scripts\python.exe run_master.py module/smoke --collect-only -q
```

当前基线为40项、并发15、串行25；若执行前仓库已有合法变化，以执行前集合守恒为准。

### 18.8 范围检查

```powershell
git diff --check
git status --short
git diff -- `
  module/offline_framework_example/conftest.py `
  module/offline_framework_example/test_polling.py `
  module/offline_framework_example/test_context_cleanup.py `
  module/offline_framework_example/test_capture_assertions.py
```

检查：

- 无四件套生产逻辑变化；
- 无服务、合同、核心框架、Quality、Runner和Jenkins变化；
- 无外部URL和真实凭证；
- 无直接`requests.Session()`绕过Offline Request；
- 无Quality内部导入；
- 无固定长sleep；
- 无仓库`data/`新增文件；
- 无临时Allure或pytest产物进入改动；
- 用户已有工作树内容未被覆盖。

## 19. 验收矩阵

| 目标 | 主要证据 | 通过条件 |
|---|---|---|
| Polling成功 | 服务计数、HTTP响应、Runtime记录 | 4次transport、3个业务状态、SUCCESS |
| Retry与Polling分层 | 首组attempt和raw状态 | 503不进入业务迁移 |
| Polling失败 | `PollingFailedError` | failed、error、迁移完整 |
| Polling未知 | `PollingUnknownStateError` | paused未被继续等待 |
| 严格超时 | `PollingTimeoutError` | 最后running、非空迁移、短预算 |
| 四源提取 | Context snapshot | JSON/Header/Cookie/Regex准确 |
| 类型与transform | Context值和异常 | int转换、required语义准确 |
| LIFO cleanup | 调用列表和服务state | last→delete→first，tasks为空 |
| 聚合cleanup错误 | 错误列表和服务state | 继续DELETE，错误脱敏 |
| 输入Capture | Event、文件、Hash | 70字节固定输入，仅loopback |
| 输出Capture | 最终响应、文件、Hash | 70字节固定输出，同步完成 |
| 超限fail-open | 成功响应和空目录 | 140>100，业务仍成功 |
| Schema成功 | 原response身份 | 三层断言全部返回原对象 |
| Schema失败 | AssertionError文本 | JSON/Schema路径准确且脱敏 |
| 附件生命周期 | Allure raw PNG Hash | 输入和输出Hash均存在 |
| 阶段1、2不退化 | 定向回归 | 既有用例全绿 |
| Runner集合 | collect-only | 19项全并发、0串行 |
| 框架不退化 | `pytest tests` | 全部通过 |
| Smoke不退化 | collect-only | 集合和分池保持基线 |

## 20. 风险与控制

### 20.1 Polling测试被Capture副作用污染

风险：媒体payload触发输入线程，成功Polling触发输出下载，使状态测试失败原因不纯。

控制：Polling四条测试统一使用`CapturePolicy.disabled()`。

### 20.2 超时测试依赖墙钟精确次数

风险：不同Windows调度造成poll次数差异，形成flaky。

控制：只断言至少一次running、最终异常、迁移单调和总耗时上界，不冻结精确次数。

### 20.3 Runtime记录器截断Quality

风险：扩展Polling观察时忘记把opaque handle还原给delegate。

控制：每个包装方法保存delegate handle并完整委托；阶段2和Quality开关用例复跑。

### 20.4 Context cleanup晚于Request关闭

风险：fixture独立且顺序不明确，DELETE使用已关闭Session或已停止服务。

控制：`offline_test_context`显式依赖`offline_request_factory`，形成pytest逆序收尾链。

### 20.5 输入文件异步竞态

风险：服务Event已设置但下载线程尚未完成落盘，立即Hash失败。

控制：先等Event，再有界等待最终原子文件路径；不检查`.part`内容，不固定长sleep。

### 20.6 临时文件在Allure hook前消失

风险：fixture主动删除tmp目录，附件挂载失败。

控制：`offline_capture_dirs`只恢复patch，不主动删除；文件交由pytest会话临时目录回收。

### 20.7 Capture失败覆盖业务结论

风险：超限下载异常向外抛出，使成功Polling被判失败。

控制：断言原response和成功Schema，同时确认输出目录无文件；不捕获并吞业务Polling异常。

### 20.8 Schema诊断重复实现

风险：业务Assertions自行格式化ValidationError，形成第二套路径与脱敏规则。

控制：直接调用BaseAssertions.assert_schema，只断言公共错误合同。

### 20.9 分类职责膨胀

风险：Context或Capture测试顺手加入并发和黄金路径，失败难以归因。

控制：严格保留12个冻结nodeid；并发和组合能力只在阶段4实现。

## 21. 停止条件

执行中出现以下任一情况必须暂停并询问用户：

1. 必须修改BaseRequest、Polling、TestContext、Capture或BaseAssertions公共实现；
2. 必须修改阶段1服务、场景序列、资源字节或阶段0合同；
3. 必须修改阶段2四件套生产逻辑才能完成分类；
4. Polling成功场景不能稳定形成503、queued、running、succeeded；
5. Polling异常缺少合同规定的最后响应或迁移；
6. 超时必须依赖服务延迟字段、随机等待或固定长sleep；
7. Context cleanup无法在Request关闭前执行；
8. 输入文件无法在不访问私有ContextVar的情况下确定完成；
9. Capture目录无法隔离到tmp_path；
10. Capture错误会覆盖成功业务响应；
11. Schema错误无法通过公共断言得到路径和脱敏诊断；
12. 必须访问外部网络或真实凭证；
13. 必须增加Marker、配置开关、新报告或新DSL；
14. 必须读取Quality、Metrics、Flaky或Runner内部文件；
15. 目标文件已存在用户内容且需要覆盖；
16. 用户工作树与阶段3目标发生实质冲突；
17. 定向用例重复运行不稳定且无法归因。

不得通过删除迁移断言、扩大timeout、忽略cleanup错误、关闭Capture测试或吞掉Schema异常绕过停止条件。

## 22. 阶段完成门禁

阶段3只有在以下条件全部满足时才能关闭：

- [ ] Runtime记录器完整委托Polling Hooks；
- [ ] `offline_test_context`生命周期早于Request和Service收尾；
- [ ] Polling成功场景为4次transport、3个业务状态；
- [ ] 503不进入业务状态迁移；
- [ ] PollingFailedError属性和error value完整；
- [ ] PollingUnknownStateError保留paused；
- [ ] PollingTimeoutError保留最后running响应和非空迁移；
- [ ] 超时用例不冻结精确poll次数；
- [ ] JSON、Header、Cookie、Regex四源提取准确；
- [ ] transform、type、required和default语义通过；
- [ ] cleanup严格LIFO；
- [ ] cleanup失败后剩余回调继续执行；
- [ ] cleanup错误不包含认证占位值；
- [ ] Cleanup后的服务tasks为空；
- [ ] 输入Capture只访问loopback并生成固定Hash；
- [ ] 输出Capture生成固定Hash；
- [ ] 输入和输出目录均位于tmp_path；
- [ ] 超限Capture不产生成功文件且不覆盖response；
- [ ] 状态码、字段、Schema三层断言返回原response；
- [ ] Schema错误包含准确JSON Path和Schema Path；
- [ ] Schema错误保持脱敏；
- [ ] Allure raw附件包含输入和输出固定Hash；
- [ ] 12条阶段3用例全部通过；
- [ ] 阶段1、2用例全部通过；
- [ ] 完整框架回归全绿；
- [ ] 离线模块collect-only为阶段快照19项、全并发、0串行；
- [ ] Smoke收集与分池不退化；
- [ ] `git diff --check`通过；
- [ ] 没有核心框架、服务、合同、报告和Jenkins改动；
- [ ] 没有外部网络、真实凭证、固定长等待和仓库数据污染；
- [ ] 用户已有工作树修改未被触碰。

## 23. 阶段4交接合同

阶段3完成后向阶段4提供：

```text
已证明的四类Polling终态
＋ 完整Retry/Polling迁移证据
＋ 可自动cleanup的Offline TestContext
＋ JSON/Header/Cookie/Regex提取合同
＋ 本地任务DELETE清理动作
＋ 可用的输入/输出Capture临时目录
＋ 固定资源Hash和附件生命周期
＋ 状态码/字段/Schema领域断言
＋ 委托式Runtime Polling观察
```

阶段4只需增加：

```text
test_concurrency_context.py
test_full_framework_flow.py
```

阶段4不得重新实现Polling、Context提取、Capture下载、Schema诊断或服务场景。

## 24. 本阶段最终交付

```text
4条Polling分类用例
+ 4条TestContext与cleanup分类用例
+ 4条Capture与Assertions分类用例
+ 1个Polling观察记录扩展
+ 1个具备正确依赖顺序的Context fixture
+ 0个核心框架改动
+ 0个服务协议改动
+ 0个外部网络请求
```

阶段3通过后，框架学习者可以分别观察状态机、跨步骤上下文、资源清理、输入输出附件和契约诊断，并理解这些能力如何在不互相改写结论的前提下组成可靠接口测试生命周期。
