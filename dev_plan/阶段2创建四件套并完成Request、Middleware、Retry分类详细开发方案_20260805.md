# 阶段2创建四件套并完成Request、Middleware、Retry分类详细开发方案

> 日期：2026-08-05
> 状态：待执行
> 所属总方案：`dev_plan/离线框架能力分类用例与黄金路径详细执行方案_20260805.md`
> 协议输入：`dev/离线框架能力示例协议与验收合同_20260805.md`
> 前置阶段：阶段1确定性本地服务、fixture及基础设施门禁已完成
> 阶段性质：建立规范四件套，验证Request、默认Middleware和Retry，不实现Polling分类

## 1. 需求复述与阶段目标

阶段2基于已验证的`OfflineService`，创建规范化离线示例四件套：

```text
OfflineFrameworkRequest
OfflineFrameworkTask
OfflineFrameworkAssertions
OfflineFrameworkDecorators
```

并完成两个分类：

```text
Request与默认Middleware
Retry资格与挽救
```

阶段完成后必须证明：

1. 四件套均为真实类，具有稳定类身份、MRO、`__name__`和导入路径；
2. 所有HTTP请求都由`OfflineFrameworkRequest(BaseRequest)`发出；
3. Offline Request只使用阶段1的loopback base URL和固定无权限占位Key；
4. 默认Middleware链完整保留，没有在业务模块复制日志、脱敏、Capture或观察逻辑；
5. 中性`runtime_metadata()`使用冻结的operation name和workload/control角色；
6. 调用方payload和Session公共Header不被单次请求改变；
7. GET 503、GET 429和幂等POST都能按合同重试并被挽救；
8. 非幂等POST即使传入RetryPolicy也只发送一次；
9. Retry次数和结果以服务计数及请求上下文为证据，不解析日志文本；
10. 阶段1服务、现有框架测试和Smoke收集保持不退化。

本阶段的成功标准不是“7条用例通过”，而是证明业务模块只做领域适配，传输、Middleware、Retry和运行时观察仍由现有框架唯一实现负责。

## 2. 第一性原理与TOC判断

### 2.1 问题本质

阶段1已经建立可信HTTP事实源。阶段2需要建立下一段因果链：

```text
业务四件套调用稳定公共入口
→ BaseRequest构造真实请求上下文
→ 默认Middleware观察同一请求
→ RetryExecutor按显式策略决定是否重试
→ OfflineService产生可核对计数
→ 用例能够从业务响应和服务事实判断框架行为
```

如果业务模块自行实现Session、Retry或日志，则会产生第二套事实：

```text
业务Request复制底层算法
→ 框架公共入口未被真正使用
→ 示例通过不能证明框架通过
→ 后续Quality和报告无法消费同一执行事实
```

因此阶段2的本质是“公共能力接线正确”，不是增加更多封装层。

### 2.2 TOC首要约束

当前首要约束从阶段1的“本地服务是否确定”转移为：

```text
四件套能否在不复制核心算法的前提下，
把稳定业务语义准确映射到BaseRequest、BaseTask和Runtime Hooks。
```

TOC处理顺序：

1. 识别约束：Request配置、metadata、Retry资格和Session生命周期尚未形成业务模块闭环；
2. 利用约束：只调用BaseRequest/BaseTask已有扩展点，不修改核心实现；
3. 服从约束：先完成Request/Middleware/Retry分类，不提前进入Polling和Capture；
4. 提升约束：通过真实loopback请求、服务计数和中性Hooks记录形成可追溯证据；
5. 防止惯性：阶段2通过后停止继续包装Retry，状态类能力转交阶段3。

### 2.3 决策原则

- 四件套必须全部创建，不能用简单别名替代；
- Request只定义路径、单请求参数和中性metadata；
- Task只构建新payload并组织业务动作；
- Assertions只增加领域语义，通用断言委托BaseAssertions；
- Decorators保持真实薄子类，业务步骤优先使用公共`allure_step`；
- RetryPolicy是显式不可变策略，不修改BaseRequest默认行为；
- 测试运行真实BaseRequest与默认Middleware，不用mock transport替代loopback服务；
- Runtime事实记录器必须包装并委托当前Hooks，不能屏蔽Quality后端；
- 不读取Quality、Metrics、Flaky或Runner内部文件。

## 3. 已确认的实施基线

### 3.1 阶段1可直接复用

阶段1已提供：

```text
OfflineService
OfflineServiceScenario及9个冻结场景
offline_service_factory
offline_service
offline_network_guard
offline_capture_dirs
服务计数、任务、审计、Host和Handler错误快照
```

阶段1基础设施门禁当前为18项，并已证明连续20次启停、双实例隔离、并发计数、固定资源和线程回收。

### 3.2 BaseRequest实际语义

已核对当前实现：

- 构造函数接收`Settings`、可选Middleware、RetryExecutor和CapturePolicy；
- 默认创建独立`requests.Session`；
- 默认Header包含Accept、Content-Type、User-Agent和Bearer Authorization；
- 相对路径通过`config.base_url`拼接；
- 请求kwargs尽力深拷贝后再交给Middleware；
- `runtime_metadata`在传输前被移除，不发送给服务端；
- 默认Middleware顺序为RuntimeObservation、MediaResource、Redaction、Logging；
- 每个显式RetryPolicy形成一个请求组；
- POST只有允许方法、`allow_post=True`或幂等Header时才进入重试循环；
- `close()`是Request Session唯一公开收尾入口。

### 3.3 BaseTask实际语义

BaseTask现有媒体能力已经拥有：

```text
create_media_generation()
poll_media_generation_result()
create_and_poll_media_generation()
extract_task_id()
```

对应operation name已经冻结为：

```text
media_generation_create
media_generation_polling
media_generation
```

阶段2只能复用或通过类路径常量对接，不得复制创建、轮询、task_id提取或ASYNC_TASK作用域。

### 3.4 Runtime Hooks实际语义

`runtime_metadata()`、`bind_runtime_hooks()`、`get_runtime_hooks()`和`reset_runtime_hooks()`均是公共中性入口。

测试如果需要观察metadata，必须：

```text
取得当前Runtime Hooks
→ 用记录包装器继续委托当前Hooks
→ 记录需要的中性事实
→ 用例结束恢复原ContextVar token
```

禁止用Noop记录器直接替换当前Hooks，否则Quality开启时会丢失本轮业务事实。

## 4. 阶段边界

### 4.1 阶段内工作

- 创建Request、Task、Assertions、Decorators四个真实类；
- 创建稳定响应Schema常量；
- 更新模块包导出；
- 扩展模块fixture，统一管理Offline Request Session；
- 提供只用于断言中性事实的委托式Runtime记录fixture；
- 实现3条Request/Middleware分类用例；
- 实现4条Retry分类用例；
- 验证四件套导入、nodeid、Runner收集和现有回归。

### 4.2 明确不做

- 不实现阶段3的Polling成功、失败、未知和超时用例；
- 不验证TestContext提取和cleanup；
- 不验证输入/输出Capture和附件；
- 不实现阶段4并发ContextVar用例和黄金路径；
- 不读取或断言Quality、Metrics、Flaky数据库和机器文件；
- 不修改`common/`、`quality/`、`run_orchestration/`和Jenkins；
- 不新增Marker、报告、配置环境变量或场景DSL；
- 不添加SSE和流式输出；
- 不在四件套中创建第二套Session、Retry循环、日志或脱敏算法；
- 不更改阶段1HTTP路径、场景序列和固定标识。

## 5. 文件级修改清单

### 5.1 新增文件

```text
module/offline_framework_example/request.py
module/offline_framework_example/task.py
module/offline_framework_example/assertions.py
module/offline_framework_example/decorators.py
module/offline_framework_example/response_schemas.py
module/offline_framework_example/test_request_pipeline.py
module/offline_framework_example/test_retry.py
```

### 5.2 修改文件

```text
module/offline_framework_example/__init__.py
module/offline_framework_example/conftest.py
```

### 5.3 原则上零修改

```text
module/offline_framework_example/offline_service.py
tests/test_offline_service.py
dev/离线框架能力示例协议与验收合同_20260805.md
common/
quality/
run_orchestration/
Jenkinsfile
```

如果阶段2必须修改上述服务、合同或核心框架才能成立，应触发停止条件，不得把缺口伪装成普通四件套开发。

### 5.4 工作树保护

执行前记录`git status --short`，保护以下已知用户内容：

```text
module/material_library/test_volc_cn_assets.py
module/material_library/volc_cn_asset_mgmt_smoke.py
module/material_library/volc_cn_asset_pipeline_smoke.py
module/test/
既有dev与dev_plan文件
```

只允许修改第5.1和5.2节文件。无关改动、未跟踪文件和报告产物不得清理、覆盖或纳入本阶段。

## 6. 四件套职责和依赖方向

### 6.1 依赖关系

```text
test_request_pipeline.py / test_retry.py
                 │
                 ├── OfflineFrameworkTask
                 ├── OfflineFrameworkAssertions
                 └── OfflineFrameworkRequest
                            │
                            └── BaseRequest

OfflineFrameworkTask ──→ BaseTask
OfflineFrameworkAssertions ──→ BaseAssertions
OfflineFrameworkDecorators ──→ BaseDecorators
```

规则：

- Request不导入Task或Assertions；
- Task可以依赖Request类型；
- Assertions不发请求、不修改服务状态；
- Decorators不导入具体测试类；
- 测试优先从模块包导入四件套；
- response_schemas不导入Request、Task或测试代码；
- 业务模块不导入任何Quality或Runner内部实现。

### 6.2 所有权表

| 责任 | 唯一所有者 | 阶段2做法 |
|---|---|---|
| URL拼接和Session | BaseRequest | Offline Request只注入显式Settings |
| 默认Header | BaseRequest | 不覆盖`_build_default_headers()` |
| Middleware链 | BaseRequest | 不覆盖`_default_middlewares()` |
| Retry循环 | RetryExecutor | Request只传RetryPolicy |
| Retry资格 | `is_method_retry_allowed()` | POST只传或不传幂等Header |
| 单请求metadata | Offline Request | 每个业务方法传`runtime_metadata()` |
| 媒体复合流程 | BaseTask/MediaGenerationCapability | Offline Task继承复用 |
| payload构建 | Offline Task | 每次返回新字典 |
| 通用断言 | BaseAssertions | Offline Assertions委托调用 |
| 领域断言 | Offline Assertions | 只判断冻结业务字段 |
| Allure业务步骤 | 公共`allure_step` | Task方法使用稳定标题 |
| HTTP事实 | OfflineService | 用服务快照确认计数 |

## 7. `request.py`详细设计

## 7.1 类与构造函数

创建真实类：

```python
class OfflineFrameworkRequest(BaseRequest):
    ...
```

构造参数建议固定为：

```text
base_url: str
timeout: float = 1
capture_policy: CapturePolicy | None = None
```

构造顺序：

```text
assert_loopback_url(base_url)
→ 构造显式Offline Settings
→ BaseRequest.__init__
→ session.trust_env = False
```

Offline Settings必须显式设置：

```text
base_url = 阶段1服务base URL
api_key = offline-example-key
timeout = 1秒
environment_name = offline
generate_allure_report = False
generate_history_report = False
history_report_keep_limit = 1
```

约束：

- 不使用`settings.model_copy()`继承真实环境base URL或Key；
- 不从`.env`读取Offline base URL；
- 不在构造函数中修改全局环境变量；
- 不允许`localhost`、外部域名或HTTPS；
- 不覆盖默认Middleware、RetryExecutor算法和Header构建；
- CapturePolicy只透传给BaseRequest，阶段2用例不触发媒体下载。

## 7.2 路径常量

Request统一拥有路径字符串：

```text
echo_path = /v1/echo
transient_path = /v1/transient
idempotent_operation_path = /v1/idempotent-operation
media_generations_path = /v1/media/generations
media_task_path_template = /v1/media/tasks/{task_id}
context_path = /v1/context
audit_path = /v1/audit
contract_path_template = /v1/contracts/{mode}
```

Task引用这些常量对接BaseTask，禁止在多个文件重复维护同一路径。

## 7.3 Request方法与metadata

| 方法 | HTTP | path | operation name | role |
|---|---|---|---|---|
| `echo(payload)` | POST | echo | `offline_echo` | workload |
| `get_transient(retry_policy)` | GET | transient | `offline_transient_request` | workload |
| `commit_idempotent_operation(payload, retry_policy, idempotency_key)` | POST | idempotent | `offline_idempotent_operation` | workload |
| `get_context()` | GET | context | `offline_context_query` | control |
| `get_audit(audit_name)` | GET | audit | `offline_audit_query` | control |
| `delete_media_task(task_id)` | DELETE | media task | `offline_task_cleanup` | control |
| `get_contract(mode)` | GET | contract | `offline_contract_query` | control |

每个方法必须使用：

```python
runtime_metadata=runtime_metadata(
    RuntimeOperationKind.HTTP,
    name="稳定名称",
    role=RuntimeTrafficRole.WORKLOAD或CONTROL,
)
```

要求：

- 不使用旧`_quality_operation_name`和`_quality_traffic_role`；
- operation name不包含端口、task ID、attempt、线程名和时间戳；
- `runtime_metadata`不得进入HTTP请求参数；
- audit的`X-Audit-Name`作为单次Header传入；
- 不调用`session.headers.update()`处理单次Header；
- idempotency key为`None`时完全不发送`Idempotency-Key`；
- 幂等键存在时只在当前POST的`headers`中发送；
- payload原对象直接作为业务输入交给BaseRequest，由公共层负责防变异拷贝。

## 7.4 媒体路径与BaseTask边界

阶段2不为媒体创建和Polling复制Request包装方法。`OfflineFrameworkTask`把自身媒体路径类属性指向Request路径常量，让继承的BaseTask能力继续调用：

```text
request_client.post(media_generations_path)
request_client.poll_get(media_task_path_template)
```

这样`media_generation_create`、`media_generation_polling`和`media_generation`仍由BaseTask现有作用域拥有，不形成重复顶层operation。

## 8. `task.py`详细设计

## 8.1 固定策略

定义一个不可变Retry策略常量：

```text
OFFLINE_RETRY_POLICY
├─ max_attempts = 2
├─ base_delay = 0
├─ max_delay = 0
├─ jitter = False
├─ respect_retry_after = True
├─ max_elapsed = 1
└─ allow_post = False
```

定义阶段3可直接复用的标准PollingPolicy：

```text
OFFLINE_POLLING_POLICY
├─ status_json_path = $.status
├─ pending = {queued, running}
├─ success = {succeeded}
├─ failure = {failed, cancelled}
├─ result_json_path = $.result.url
├─ error_json_path = $.error
└─ unknown = fail
```

策略是Pydantic frozen model，可安全作为默认参数。不得在用例中临时修改策略字段。

## 8.2 类定义和BaseTask复用

创建真实类：

```python
class OfflineFrameworkTask(BaseTask):
    media_generations_path = OfflineFrameworkRequest.media_generations_path
    media_task_path_template = OfflineFrameworkRequest.media_task_path_template
```

不覆盖以下方法：

```text
create_media_generation
poll_media_generation_result
create_and_poll_media_generation
extract_task_id
```

阶段3和黄金路径直接调用继承方法，确保BaseTask仍是媒体生命周期唯一所有者。

## 8.3 payload builder

至少提供：

### `build_echo_payload()`

每次返回新字典：

```python
{
    "model": "offline-media-model",
    "prompt": "offline framework example",
    "metadata": {"case": "request_pipeline"},
}
```

### `build_idempotent_operation_payload()`

每次返回：

```python
{
    "operation": "offline-write",
    "value": 1,
}
```

### `build_media_generation_payload(base_url)`

为阶段3和黄金路径准备：

```python
{
    "model": "offline-media-model",
    "prompt": "offline framework example",
    "input": {
        "media": {
            "type": "image",
            "url": f"{base_url}/assets/input.png",
        }
    },
}
```

构建前调用loopback校验；不得复用模块级可变payload，不得就地修改调用方传入字典。

## 8.4 业务动作

Task提供薄业务动作并使用公共`allure_step`：

| Task方法 | 委托目标 | 阶段 |
|---|---|---|
| `submit_echo()` | Request.echo | 阶段2使用 |
| `request_transient()` | Request.get_transient | 阶段2使用 |
| `commit_idempotent_operation()` | Request.commit_idempotent_operation | 阶段2使用 |
| `query_context()` | Request.get_context | 阶段3使用 |
| `query_audit()` | Request.get_audit | 阶段4使用 |
| `delete_media_task()` | Request.delete_media_task | 阶段3/4使用 |
| `query_contract()` | Request.get_contract | 阶段3使用 |

Task只传递业务参数和固定策略，不捕获异常、不重试循环、不解析日志、不操作服务state。

Allure标题表达业务动作，不包含完整payload、API Key、端口和动态task ID。

## 9. `assertions.py`详细设计

## 9.1 类身份

```python
class OfflineFrameworkAssertions(BaseAssertions):
    ...
```

不得改成：

```python
OfflineFrameworkAssertions = BaseAssertions
```

## 9.2 领域断言

阶段2实现并供后续复用：

| 方法 | 委托的通用断言 | 领域语义 |
|---|---|---|
| `assert_echo_accepted(response, expected_payload)` | status、JSON value | Echo接受且收到原payload |
| `assert_transient_recovered(response)` | status、JSON value | 最终ok、attempt为2 |
| `assert_idempotent_committed(response)` | status、JSON value | 写操作最终committed |
| `assert_task_id(response, expected)` | JSONPath exists/value | task_id符合合同 |
| `assert_task_status(response, expected)` | JSON value | 任务状态符合预期 |
| `assert_audit_names(responses, expected)` | status、JSON value | 审计名称集合准确 |

要求：

- 通用状态码、JSONPath和Schema必须调用继承方法；
- 不复制JSONPath解析器和Schema校验器；
- 单响应领域断言返回原`requests.Response`；
- 多响应领域断言返回原响应列表或tuple；
- 错误文本指出字段路径、期望与实际；
- 不在错误文本中拼接Authorization、Offline API Key或完整Session Header；
- 阶段2测试仍以服务计数作为Retry次数的主要证据。

## 10. `decorators.py`详细设计

保持真实薄子类：

```python
class OfflineFrameworkDecorators(BaseDecorators):
    pass
```

原因：

- 四件套规范要求稳定类对象和导入路径；
- 当前没有离线模块专属装饰算法；
- Task业务步骤直接使用`common.allure_step`；
- 不复制BaseDecorators的结果下载、文件记录和附件逻辑。

不得为了让类“看起来有内容”增加无业务意义的转发方法。

## 11. `response_schemas.py`详细设计

定义以下Draft 2020-12 Schema常量：

```text
OFFLINE_CREATE_TASK_SCHEMA
OFFLINE_POLLING_SUCCESS_SCHEMA
OFFLINE_BUSINESS_ERROR_SCHEMA
OFFLINE_AUDIT_RESPONSE_SCHEMA
```

### 11.1 创建任务Schema

要求字段：

```text
task_id: 非空字符串
status: queued
model: offline-media-model
trace_id: 非空字符串
```

### 11.2 Polling成功Schema

要求字段：

```text
task_id: 非空字符串
status: succeeded
model: offline-media-model
trace_id: 非空字符串
result.url: 非空字符串
```

Schema只验证URL是字符串；是否loopback由`offline_network_guard`负责，避免在Schema中复制URL安全逻辑。

### 11.3 业务错误Schema

要求：

```text
error.code
error.type
error.message
```

均为非空字符串。Schema不只服务某一个错误code。

### 11.4 审计Schema

要求：

```text
audit_name: 非空字符串
task_id: 非空字符串
status: recorded
```

所有对象使用`additionalProperties: true`，只约束稳定合同，不阻断未来无害扩展。文件通过`__all__`显式导出四个Schema。

## 12. `__init__.py`导出设计

阶段1的包说明文件更新为稳定导出：

```python
from module.offline_framework_example.assertions import OfflineFrameworkAssertions
from module.offline_framework_example.decorators import OfflineFrameworkDecorators
from module.offline_framework_example.request import OfflineFrameworkRequest
from module.offline_framework_example.task import OfflineFrameworkTask
```

`__all__`只列四件套类。测试优先从包入口导入，服务场景继续从`offline_service.py`明确导入，避免把测试基础设施误当成业务公共API。

导出要求：

- 四个对象分别是其模块中定义的原对象；
- `issubclass()`关系正确；
- 不使用延迟字符串映射改变对象身份；
- 不在包导入时启动服务、构造Session或读取场景状态。

## 13. `conftest.py`扩展设计

保留阶段1四个fixture语义不变，新增阶段2资源所有者。

## 13.1 `offline_request_factory`

fixture依赖`offline_service_factory`，即使只通过参数接收Service，也要建立明确teardown顺序。

调用合同：

```text
factory(service, capture_policy=None)
→ assert_loopback_url(service.base_url)
→ 创建OfflineFrameworkRequest
→ 加入本用例Request清单
→ 返回client
```

teardown：

```text
逆序关闭全部Request Session
→ 再由offline_service_factory停止服务
```

一个Request关闭失败时继续关闭其余Request，最后聚合报告。不得在fixture中共享一个Session给多个测试或线程。

## 13.2 `offline_request`

默认fixture：

```text
offline_request_factory(offline_service)
```

供Request pipeline三条DEFAULT场景用例复用。它不自动创建Task或Assertions，避免fixture承担业务编排。

## 13.3 `offline_runtime_recorder`

只用于验证中性metadata和Retry请求组事实。

设计：

```text
delegate = get_runtime_hooks()
recorder = RecordingRuntimeHooks(delegate)
token = bind_runtime_hooks(recorder)
yield recorder
reset_runtime_hooks(token)
```

记录最小事实：

- operation metadata；
- request group的method、path、configured max attempts；
- 每次绑定RequestContext中的attempt index；
- 每次响应状态和Header；
- request group累计retry wait。

硬性要求：

- 未覆盖的方法全部委托原Hooks；
- 覆盖的方法先记录，再调用原Hooks并返回原结果；
- 不导入Quality实现；
- 不读取机器产物；
- 不吞掉原Hooks异常语义；
- fixture结束必须按token恢复ContextVar；
- 不设为autouse，避免改变无关用例。

该记录器是测试观察接缝，不是新的运行时事件总线或报告模型。

## 13.4 fixture清理顺序

```text
用例断言
→ offline_runtime_recorder恢复原Hooks
→ offline_request_factory关闭Session
→ offline_service_factory停止服务
```

阶段2不使用TestContext，因此不引入cleanup回调；阶段3新增TestContext时再调整其依赖层次。

## 14. `test_request_pipeline.py`详细设计

稳定测试类：

```text
TestRequestPipeline
```

使用DEFAULT场景、`offline_service`、`offline_request`、`OfflineFrameworkTask`和`OfflineFrameworkAssertions`。不参数化，保持冻结nodeid。

## 14.1 `test_request_uses_default_middleware_and_runtime_metadata`

执行：

```text
构建全新Echo payload
→ 通过Task调用Offline Request
→ BaseRequest完成真实loopback POST
→ 领域Assertions验证Echo响应
→ 检查Runtime记录和服务计数
```

必须断言：

- HTTP 200；
- Echo服务计数为1；
- Request的base URL等于当前服务base URL；
- `session.trust_env is False`；
- 默认Middleware对象顺序仍为RuntimeObservation、MediaResource、Redaction、Logging；
- operation kind为HTTP；
- operation name为`offline_echo`；
- role为workload；
- request group方法为POST、path为`/v1/echo`、最大attempt为1；
- 传输RequestContext中不存在`runtime_metadata`参数；
- 当前Hooks仍收到委托调用，没有被测试记录器替换成孤立Noop。

## 14.2 `test_sensitive_headers_preserve_business_request`

必须断言：

- 服务返回`authorization_present=true`，证明占位Authorization真实到达服务；
- Request Session中的Authorization仍为固定离线占位值；
- 响应体不包含`offline-example-key`或完整`Bearer`值；
- Echo业务payload完整；
- 默认Redaction/Logging Middleware没有改变实际业务请求；
- 服务只被调用一次。

不读取Allure附件文本证明脱敏；Allure与Quality产物属于阶段5运行级验收。

## 14.3 `test_middleware_does_not_mutate_original_payload`

执行前对payload做`deepcopy`，调用后断言：

- 原payload与深拷贝完全相等；
- 嵌套`metadata`对象未被增删字段；
- 服务返回的`received`等于调用前payload；
- 连续调用`build_echo_payload()`得到内容相等但对象及嵌套字典不相同的新值；
- 服务计数与真实调用次数一致。

不通过Monkeypatch绕过Middleware；必须运行真实默认链。

## 15. `test_retry.py`详细设计

稳定测试类：

```text
TestRetry
```

每条测试通过`offline_service_factory`创建独立场景，通过`offline_request_factory`创建独立Session。Task和Assertions可以在`setup_method`创建，因为它们不持有外部资源。

四条用例统一使用`OFFLINE_RETRY_POLICY`，不修改allow_post、不使用随机等待、不依赖墙钟阈值。

## 15.1 `test_retry_rescues_transient_get_failure`

场景：`GET_503_THEN_200`。

必须断言：

- 最终HTTP 200；
- 领域结果为`status=ok`、`attempt=2`；
- 服务`transient_calls == 2`；
- Runtime请求组配置最大attempt为2；
- RequestContext attempt index为`[1, 2]`；
- 观察响应状态序列为`[503, 200]`；
- operation name为`offline_transient_request`、role为workload。

## 15.2 `test_retry_honors_retry_after`

场景：`GET_429_THEN_200`。

必须断言：

- 最终HTTP 200；
- 服务调用2次；
- 第一次观察响应为429；
- 第一次响应Header含`Retry-After: 0`；
- 请求组累计retry wait为0；
- attempt index为`[1, 2]`；
- 不通过`time.sleep()`或耗时上限反推行为。

公共Retry单元测试已经证明Retry-After解析算法；本分类用例负责证明真实Request链把该Header交给同一RetryExecutor。

## 15.3 `test_idempotent_post_can_retry`

场景：`POST_503_THEN_200`。

调用时只在本次请求传入：

```text
Idempotency-Key: offline-idempotency-001
```

必须断言：

- 最终HTTP 200；
- 业务结果为`committed`；
- 服务调用2次；
- attempt index为`[1, 2]`；
- 观察状态序列为`[503, 200]`；
- Session公共Header在调用后不包含`Idempotency-Key`；
- RetryPolicy的`allow_post`仍为False。

## 15.4 `test_non_idempotent_post_is_not_retried`

场景：`POST_503_THEN_200`，但不发送幂等Header。

必须断言：

- 最终响应保持第一次HTTP 503；
- 用例通过，因为503是本分类的预期业务事实；
- 服务调用只有1次；
- RequestContext attempt index只有`[1]`；
- 服务第二步200仍未被消费；
- Session公共Header没有被临时添加幂等键；
- 不将`allow_post`改为True绕过资格判断。

## 15.5 Retry证据优先级

```text
第一证据：OfflineService端点计数
第二证据：中性Runtime请求上下文attempt index
第三证据：最终HTTP响应
辅助证据：Allure/Quality运行产物（阶段5验收）
```

禁止通过控制台日志出现几次、Allure标题数量或测试耗时猜测重试次数。

## 16. 实施任务分解

## 16.1 任务2.0：保护工作树并复验阶段1

执行：

1. 记录当前分支、HEAD和工作树；
2. 确认阶段2目标文件不存在用户内容；
3. 运行`tests/test_offline_service.py`；
4. 确认阶段1四个fixture仍可发现；
5. 确认合同中场景、固定标识和Retry策略无变化。

门禁：阶段1基础设施未通过时不进入四件套实现。

## 16.2 任务2.1：创建Response Schema和四件套骨架

执行顺序：

```text
response_schemas.py
→ decorators.py
→ assertions.py
→ request.py
→ task.py
→ __init__.py导出
```

门禁：四个类可稳定导入，继承关系正确，不发生循环导入。

## 16.3 任务2.2：实现Offline Request

执行：

1. 显式构造Offline Settings；
2. 校验base URL并关闭代理继承；
3. 定义唯一相对路径；
4. 实现7个单请求方法；
5. 为每个方法附加冻结metadata；
6. 保留默认Middleware和Retry实现。

门禁：无完整外部URL、无真实环境Key、无Session Header临时污染。

## 16.4 任务2.3：实现Offline Task和Assertions

执行：

1. 定义Retry/Polling策略；
2. 把BaseTask媒体路径指向Request常量；
3. 实现3个新payload builder；
4. 实现薄业务动作；
5. 实现领域Assertions并委托通用断言。

门禁：不复制BaseTask媒体流程和BaseAssertions算法。

## 16.5 任务2.4：扩展fixture

执行：

1. 增加Request工厂和默认Request；
2. 确保Session先于Service关闭；
3. 增加委托式Runtime记录器；
4. 验证Quality开启和关闭时包装器都不阻断原Hooks；
5. 保持阶段1fixture名称和语义不变。

门禁：fixture失败仍能关闭全部Session和Service并恢复ContextVar。

## 16.6 任务2.5：实现Request/Middleware分类

按第14节实现3条稳定nodeid用例。测试必须从包入口导入四件套并运行真实loopback HTTP。

门禁：payload不变、metadata准确、默认Middleware未被替换、服务计数可解释。

## 16.7 任务2.6：实现Retry分类

按第15节实现4条稳定nodeid用例。每条测试使用独立服务和Session。

门禁：GET/POST资格、attempt、状态序列和服务计数完全一致。

## 16.8 任务2.7：回归与范围核验

执行语法检查、定向测试、基础设施回归、框架完整回归、离线模块collect-only、Smoke collect-only及diff审查。

不得把临时Allure、JUnit、下载资源或pytest缓存纳入改动。

## 17. 验证命令

以下命令从仓库根目录执行。

### 17.1 语法检查

```powershell
.\.venv\Scripts\python.exe -m py_compile `
  module/offline_framework_example/request.py `
  module/offline_framework_example/task.py `
  module/offline_framework_example/assertions.py `
  module/offline_framework_example/decorators.py `
  module/offline_framework_example/response_schemas.py `
  module/offline_framework_example/conftest.py `
  module/offline_framework_example/test_request_pipeline.py `
  module/offline_framework_example/test_retry.py
```

### 17.2 四件套身份检查

```powershell
.\.venv\Scripts\python.exe -c "from common import BaseAssertions, BaseDecorators, BaseRequest, BaseTask; from module.offline_framework_example import OfflineFrameworkAssertions, OfflineFrameworkDecorators, OfflineFrameworkRequest, OfflineFrameworkTask; assert issubclass(OfflineFrameworkAssertions, BaseAssertions); assert issubclass(OfflineFrameworkDecorators, BaseDecorators); assert issubclass(OfflineFrameworkRequest, BaseRequest); assert issubclass(OfflineFrameworkTask, BaseTask)"
```

### 17.3 阶段1基础设施回归

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_offline_service.py -q
```

当前预期为18项通过；数量只作为阶段2执行时快照，长期验收以全部通过为准。

### 17.4 阶段2定向测试

使用临时Allure目录，避免覆盖仓库已有报告：

```powershell
$allureDir = Join-Path ([System.IO.Path]::GetTempPath()) "api-case-stage2-allure-$PID"
$env:GENERATE_ALLURE_REPORT = "FALSE"
$env:GENERATE_HISTORY_REPORT = "FALSE"
.\.venv\Scripts\python.exe -m pytest `
  module/offline_framework_example/test_request_pipeline.py `
  module/offline_framework_example/test_retry.py `
  --alluredir=$allureDir -q
```

初版预期稳定nodeid共7项：Request/Middleware 3项、Retry 4项。

### 17.5 离线模块权威收集

```powershell
.\.venv\Scripts\python.exe run_master.py `
  module/offline_framework_example --collect-only -q
```

阶段2快照应满足：

```text
总集合 = 7
parallel集合 = 7
serial集合 = 0
```

该数量用于确认本阶段未误收集基础设施测试，不作为阶段3以后永久总数合同。

### 17.6 框架完整回归

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
```

要求现有框架单测全部通过，不允许通过跳过或改pytest配置规避失败。

### 17.7 既有Smoke收集回归

```powershell
.\.venv\Scripts\python.exe run_master.py module/smoke --collect-only -q
```

当前基线为40项、并发15项、串行25项。若仓库在执行前已有其他合法变化，以执行前基线和集合守恒为准。

### 17.8 范围检查

```powershell
git diff --check
git status --short
git diff -- `
  module/offline_framework_example/__init__.py `
  module/offline_framework_example/conftest.py `
  module/offline_framework_example/request.py `
  module/offline_framework_example/task.py `
  module/offline_framework_example/assertions.py `
  module/offline_framework_example/decorators.py `
  module/offline_framework_example/response_schemas.py `
  module/offline_framework_example/test_request_pipeline.py `
  module/offline_framework_example/test_retry.py
```

检查：

- 无`common/`、Quality、Runner和Jenkins改动；
- 无阶段1服务和合同变化；
- 无真实域名和真实Key；
- 无旧`_quality_*`参数；
- 无全新`requests.Session()`绕过BaseRequest；
- 无直接Quality内部导入；
- 无未解释的`TODO`或`TBD`；
- 无生成产物进入改动；
- 用户已有修改未被覆盖。

## 18. 验收矩阵

| 目标 | 直接证据 | 通过条件 |
|---|---|---|
| 四件套真实身份 | 包导入和`issubclass` | 四类对象身份、MRO和导出正确 |
| Offline配置隔离 | Request config和服务Host | 只使用当前`127.0.0.1:{port}` |
| 无代理外连 | `session.trust_env` | 值为False |
| 默认Middleware | 实例链和真实请求事实 | 顺序未被替换，请求成功 |
| metadata稳定 | 委托式Runtime记录 | name、kind、role符合合同 |
| metadata不下发 | RequestContext kwargs | 不含runtime metadata |
| Header业务完整 | Echo响应 | Authorization到达服务但不回显 |
| payload不变 | deepcopy和Echo received | 调用前后深度相等 |
| GET 503挽救 | 服务计数＋attempt | 503→200，调用2次 |
| GET 429挽救 | Header＋wait＋计数 | Retry-After=0，调用2次 |
| 幂等POST | Header＋计数 | 503→200，调用2次 |
| 非幂等POST | 无Header＋计数 | 返回503，只调用1次 |
| Session回收 | Request factory teardown | 用例失败仍关闭全部Session |
| 阶段1不退化 | 基础设施测试 | 全部通过 |
| 既有框架不退化 | `pytest tests` | 全部通过 |
| Runner集合正确 | collect-only | 7项并发、0项串行 |
| Smoke不退化 | Smoke collect-only | 集合和分池保持基线 |

## 19. 风险与控制

### 19.1 Offline Request误用真实配置

风险：通过全局`settings`复制配置时无意继承真实域名、Key或报告开关。

控制：显式创建完整Offline Settings，只使用服务base URL和固定占位值。

### 19.2 Runtime记录器截断Quality事实

风险：测试为了断言metadata直接绑定Noop Hooks，导致Quality开启时该用例没有业务事实。

控制：记录器包装当前Hooks并完整委托；只在需要观察的测试中局部绑定，finally恢复token。

### 19.3 Retry测试产生假阳性

风险：只断言最终200，无法证明是否真正发生重试。

控制：同时断言服务计数、attempt index和响应状态序列；日志和耗时不作为主要证据。

### 19.4 POST资格被Session Header污染

风险：将Idempotency-Key写入`session.headers`后，后续“非幂等”用例也会获得重试资格。

控制：仅使用单请求Header，并在调用后断言Session公共Header不存在该键；每条测试独立Request。

### 19.5 Task复制BaseTask媒体生命周期

风险：为了适配离线路径重写创建、Polling和ASYNC_TASK作用域，形成多个所有者。

控制：Offline Task只引用Request路径常量并继承BaseTask方法，不重写流程。

### 19.6 Session或服务清理顺序错误

风险：服务先停止，Request仍持有连接；或用例失败后Session未关闭。

控制：Request factory依赖Service factory，pytest按依赖逆序先关闭Request再停止Service。

### 19.7 测试职责越界

风险：在阶段2顺手验证Polling、Capture或Quality文件，使失败无法归因。

控制：阶段2只保留7个冻结nodeid；状态、资源和报告事实分别留给阶段3和阶段5。

### 19.8 Allure产物覆盖

风险：pytest.ini默认`allure-results`覆盖其他运行产物。

控制：定向测试显式使用系统临时Allure目录并关闭HTML/历史生成。

## 20. 停止条件

执行中出现以下任一情况必须暂停并询问用户：

1. 必须修改BaseRequest、RetryExecutor、BaseTask或Runtime Hooks才能完成分类；
2. 阶段1服务的HTTP路径、固定响应或计数语义与合同不一致；
3. 必须修改`offline_service.py`或阶段0合同才能让阶段2通过；
4. POST不设置`allow_post=True`且携带幂等Header后仍无法重试；
5. 非幂等POST发生了第二次请求；
6. 无法在不屏蔽当前Hooks的情况下观察metadata；
7. 必须导入Quality或Runner内部实现才能断言业务行为；
8. 必须访问外部网络、真实`.env`域名或真实凭证；
9. 必须增加Marker、配置开关、报告或DSL；
10. 需要提前实现Polling、Capture或TestContext才能让Request/Retry分类成立；
11. 阶段2目标文件已存在用户内容且需要覆盖；
12. 用户已有工作树修改与目标文件发生实质冲突；
13. 定向用例重复运行不稳定且无法归因到阶段1服务或当前公共能力。

不得通过删除断言、增大重试次数、添加sleep、改成mock transport或忽略Session清理绕过停止条件。

## 21. 阶段完成门禁

阶段2只有在以下条件全部满足时才能关闭：

- [ ] Request、Task、Assertions、Decorators均为真实类；
- [ ] 包入口稳定导出四件套；
- [ ] Offline Request只接受loopback base URL；
- [ ] Offline Settings不继承真实域名和Key；
- [ ] `session.trust_env=False`；
- [ ] 默认Middleware链未被覆盖；
- [ ] 7个Request方法使用冻结metadata；
- [ ] Task payload builder每次返回新对象；
- [ ] Task没有复制BaseTask媒体能力；
- [ ] Assertions委托BaseAssertions；
- [ ] Decorators保持真实薄子类；
- [ ] 四个稳定Schema已定义并导出；
- [ ] Request Session由fixture统一关闭；
- [ ] Runtime记录器继续委托原Hooks；
- [ ] 3条Request/Middleware分类通过；
- [ ] 4条Retry分类通过；
- [ ] GET 503和429均调用2次并最终200；
- [ ] 幂等POST调用2次并最终200；
- [ ] 非幂等POST只调用1次并返回503；
- [ ] payload和Session公共Header没有被污染；
- [ ] 阶段1基础设施门禁保持全绿；
- [ ] 现有框架完整回归保持全绿；
- [ ] 离线模块collect-only只收集本阶段7个业务用例；
- [ ] 本阶段7个用例全部进入并发池，串行池为空；
- [ ] Smoke收集与分池不退化；
- [ ] `git diff --check`通过；
- [ ] 没有核心框架、服务协议和Jenkins改动；
- [ ] 没有真实外部请求、固定长等待和生成产物；
- [ ] 用户已有工作树修改未被触碰。

## 22. 阶段3交接合同

阶段2完成后向阶段3提供：

```text
可稳定导入的四件套
＋ 显式Offline Settings与独立Session
＋ 稳定Request路径和metadata
＋ OFFLINE_RETRY_POLICY
＋ OFFLINE_POLLING_POLICY
＋ 媒体、Context、Contract和Cleanup业务入口
＋ 4个响应Schema
＋ Request工厂和清理顺序
＋ 已证明的默认Middleware与Retry链
```

阶段3应直接增加：

```text
test_polling.py
test_context_cleanup.py
test_capture_assertions.py
```

阶段3不得再次创建Offline Request、复制Retry策略或重新实现本地服务。

## 23. 本阶段最终交付

```text
4个真实业务类
+ 4个稳定响应Schema
+ 2个Request生命周期fixture
+ 1个委托式Runtime观察fixture
+ 3条Request/Middleware分类用例
+ 4条Retry分类用例
+ 0个核心框架改动
+ 0个外部网络请求
```

阶段2通过后，框架学习者能够沿着一条清晰路径理解：业务Task如何构造输入、Request如何映射协议、BaseRequest如何拥有Middleware和Retry、Assertions如何复用公共原语，以及真实服务计数如何证明最终行为。
