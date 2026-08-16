# 第 11 课：TestContext 是一次测试的资料袋

> 本课承接第 10 课：一次 SSE 流已经能够被消费并关闭，但真实业务常常需要把前一步产生的 request ID、task ID、资源 ID 和清理动作交给后续步骤。TestContext 提供可选的用例级变量容器与清理栈；它包围多步骤业务流程，但不是每次 Request 的固定处理节点。

## 1. 课程信息

| 项目 | 内容 |
| --- | --- |
| 建议时长 | 60～90 分钟 |
| 核心问题 | 前一步产生的 ID、资源和清理动作，怎样安全交给后续步骤？ |
| 讲解重点 | 用例级所有权、提取漏斗、缺失与空值、LIFO cleanup、ContextVar 传播边界 |
| 代码入口 | `common/test_context.py`、`common/context_executor.py`、`module/conftest.py`；业务证据：`module/material_library/test_seedance_2_5_virtual_asset_library.py`（仅静态阅读，禁止课堂执行） |
| 轻量验证 | `tests/test_test_context.py`、`tests/quality/test_quality_context_executor.py`，共 34 条 |
| 安全边界 | 课堂命令只使用内存中的完整、可缓冲 Response、临时文件和线程池，不访问真实 API；不证明未消费流式 Response 安全 |
| 课后产出 | TestContext 生命周期图、cleanup 顺序表和三分钟复述 |

### 1.1 学完本课，你应该能够

1. 解释 TestContext 为什么是可选的用例级容器，而不是 Request 管线节点。
2. 使用 `set/get/require/extract` 描述动态值的保存、提取和读取，并区分未匹配、默认值、转换值与实际已保存变量。
3. 解释 cleanup 为什么按 LIFO 执行，以及多个 cleanup 失败怎样继续执行后汇总。
4. 区分显式 TestContext 对象与 ContextVar 快照，并说明线程池为什么使用 `submit_with_context()`。
5. 判断一次 Response 提取是否会读取 body，并说明未消费 SSE 的所有权边界。

### 1.2 本课刻意不展开

- 不把 TestContext 加入所有 Test → Task → Request 调用链。
- 不设计跨用例共享状态；跨用例依赖本身应被拆除。
- 不展开异步 cleanup；当前 cleanup 回调是同步调用。
- 不展开真实资源删除接口、账单结算或外部数据恢复。
- 不展开 Runtime Hooks、Quality 产物和 ContextVar 的内部变量清单；第三周学习。
- 不展开 BaseTask 与 Capability 的委托结构；第 12 课学习。
- 不展开 Runner 的并发池与串行池；第 13 课学习。

### 1.3 课堂必讲路径

| 环节 | 对应章节 | 建议时间 |
| --- | --- | ---: |
| 问题、约束与 fixture 所有权 | 第 2～5 节 | 8～10 分钟 |
| 变量、提取与流式边界 | 第 6～10.3 节，不含 9.5 | 16～19 分钟 |
| cleanup 主流程 | 第 13～14.3 节 | 10～12 分钟 |
| 实例隔离与 ContextVar 基本边界 | 第 15.1～15.2、16.1～16.3、16.5 节 | 8～10 分钟 |
| 离线证据结论与 cleanup 活动 | 第 17～18 节 | 7～9 分钟 |
| 三模式总图与必讲误区 | 第 19 节；第 20 节误区一至六、八、十七 | 10～12 分钟 |
| 三分钟复述与统一课堂验收 | 第 21 节、第 22.1 节；第 24.1 节只判定结果 | 7～9 分钟 |
| 过渡、讨论与机动 | 全课 | 4～9 分钟 |

总计约 70～90 分钟。第 9.5、10.4、11、12、14.4、15.3～15.4、16.4 节改为选讲或课后阅读；第 20 节其余误区和第 22.2 节作为题库，不进入必讲时间。第 17 节只讲离线证据结论，不在课堂现场重复执行命令。

### 1.4 课堂最短路径

```text
第 2～5 节：建立“一个用例一个资料袋”
-> 第 6～10.3 节：走通 Response 提取、存储、读取
-> 第 13～14.3 节：预测 LIFO 与失败汇总
-> 第 15.1～15.2 节：区分实例隔离与共享实例线程安全
-> 第 16.1～16.3、16.5 节：分开 TestContext 与 ContextVar
-> 第 17～18 节：确认离线证据并完成一个 cleanup 场景
-> 第 19～20 节：三模式总图与必讲误区
-> 第 21～22.1 节：三分钟复述与同一套课堂验收题
```

课堂只要求掌握主干。null 特殊分支、`extract_first()` 回退细节、脱敏实现、共享实例与进程隔离等内容保留为教师答疑和课后查阅，避免按全文逐行讲授。

---

## 2. 承接第十课：拿到 ID 之后，流程还没有结束

第 10 课的流式调用可以拿到 request ID；异步任务也会返回 task ID。后续步骤可能需要：

```text
创建资源
-> 提取 resource_id
-> 使用 resource_id 查询
-> 使用 resource_id 做断言
-> 测试结束删除资源
```

如果只用局部变量，短流程完全够用：

```python
resource_id = response.json()["id"]
result = task.query_resource(request_client, resource_id)
```

TestContext 不是为了替代所有局部变量。只有当一个用例存在多步骤传值、多个候选来源或统一 cleanup 时，它才提供额外价值。

### 2.1 错误做法：模块全局变量

```text
用例 A 写 global task_id
-> 用例 B 或并发 worker 覆盖
-> 用例 A 读取到别人的 ID
-> 查询或删除错误资源
```

### 2.2 错误做法：依赖用例顺序

```text
test_create 先运行
-> test_query 假设前一用例留下资源
-> 并发、重跑或单独 nodeid 执行
-> 前置状态不存在
```

一个测试用例必须拥有自己的输入、动态状态和清理动作，不能把另一个用例当隐式 fixture。

---

## 3. 当前认知障碍与因果链

### 3.1 把 TestContext 画进每次 Request

```text
看到“测试上下文”
-> 误以为所有请求都必须经过它
-> 把可选状态容器画成固定中间件
-> 单请求用例也被迫增加无意义节点
```

TestContext 由 Test 显式使用；BaseRequest 不调用它。

### 3.2 把缺失、空值和 null 当成一件事

```text
提取结果都是“看起来没内容”
-> 无法区分字段不存在、空字符串、空列表和 JSON null
-> 默认值与 required 判断失真
-> 后续步骤拿到错误语义
```

当前实现对这些值有明确但不同的规则。

### 3.3 把 cleanup 当普通顺序列表

```text
先创建父资源，再创建子资源
-> 按登记顺序先删父资源
-> 子资源仍依赖父资源
-> 清理失败或留下残留
```

cleanup 是栈，不是队列。

### 3.4 把 TestContext 与 ContextVar 混成同一个机制

```text
看到“context”
-> 以为 submit_with_context 会复制 TestContext 字典
-> worker 没有显式接收资料袋
-> 动态业务变量仍然不可用
```

`submit_with_context()` 传播 ContextVar 快照；TestContext 仍是普通 Python 对象。

### 3.5 TOC：本课真正的约束

本课主要约束是“状态没有明确所有者”：

```text
动态值属于谁？
cleanup 属于谁？
线程池中的运行身份属于谁？
```

解除约束需要三条独立规则：

```text
动态业务值 -> 当前 TestContext 实例
业务资源清理 -> 当前 TestContext cleanup 栈
隐式运行上下文 -> 提交时的 ContextVar 快照
```

---

## 4. 第一性原理：状态必须有范围、所有者和终点

任何测试状态都应回答三个问题。

### 4.1 范围

这个值只属于：

- 当前请求；
- 当前测试用例；
- 当前 worker；
- 还是整个测试运行？

request ID、task ID 和本用例创建的资源 ID 通常属于当前测试用例。

### 4.2 所有者

谁可以创建、读取和删除它？

```text
Test 创建或取得动态值
-> TestContext 保存
-> 同一 Test 的后续步骤显式读取
```

### 4.3 终点

状态何时失效，资源何时清理？

```text
pytest fixture setup 创建 TestContext
-> Test 执行多步骤流程
-> fixture teardown 调用 cleanup
-> 用例生命周期结束
```

没有终点的测试状态会逐渐变成共享污染。

---

## 5. `test_context` fixture：按需创建，不是 autouse

`module/conftest.py`：

```python
@pytest.fixture
def test_context() -> TestContext:
    context = TestContext()
    try:
        yield context
    finally:
        context.cleanup()
```

### 5.1 真实生命周期

```text
Test 函数声明 test_context 参数
-> pytest 调用 fixture
-> 创建新的 TestContext
-> yield 给当前 Test
-> Test 执行
-> 无论 Test 正常还是失败，fixture 进入 finally
-> context.cleanup()
```

### 5.2 它不是 autouse

未声明 `test_context` 参数的用例，不会自动创建 TestContext：

```python
def test_single_request():
    ...
```

需要资料袋时才显式声明：

```python
def test_multi_step(test_context):
    ...
```

fixture 是推荐的所有权模式，但不是当前工作树中唯一的创建方式。现有业务文件还存在 `setup_method()` 手动创建实例、`teardown_method()` 先调用 cleanup 并在 `finally` 中关闭 Request Client 的模式；这种模式不经过 fixture，必须由业务 Test 自己保证业务清理与 Client 关闭成对出现。

### 5.3 fixture 的可用范围

当前 fixture 定义在 `module/conftest.py`，服务于该 pytest 目录树。`tests/test_test_context.py` 对 fixture 的测试是直接驱动其生成器，不应误认为所有仓库目录都自动获得同一个 fixture 实例。

### 5.4 fixture cleanup 与 TestContext cleanup

二者不是同一个对象：

| 角色 | 职责 |
| --- | --- |
| pytest fixture | 决定何时创建与何时调用 cleanup |
| TestContext | 保存变量与 cleanup 回调，并执行 LIFO |

---

## 6. 变量容器：最小读写合同

TestContext 内部变量属于当前实例：

```text
TestContext A -> _variables A
TestContext B -> _variables B
```

### 6.1 `set()`

```python
test_context.set("task_id", "task-001")
```

- 校验变量名；
- 写入或覆盖同名变量；
- 返回原 value，便于链式赋值。

### 6.2 `get()` 与 `require()`

```python
task_id = test_context.require("task_id", expected_type=str)
```

`require()` 委托 `get()`，区别主要在表达意图：调用方明确要求变量必须存在。

`get(name, default=...)` 在变量缺失时返回 default，但不会把 default 写入 TestContext。

当前 `get()` 在缺失分支会直接返回 default，因此不会再对这个 fallback 执行 `expected_type` 校验；`expected_type` 只校验上下文中实际存在的值。它与 `extract(default)` 的“转换、类型检查并保存”语义不同。

### 6.3 `has()`、`delete()` 与 `clear()`

- `has()` 判断变量名是否已保存；
- `delete()` 删除并返回变量，缺失时抛异常；
- `clear()` 只清空变量字典。

重要边界：

```text
clear variables
≠ 清空 cleanup 栈
```

变量被删除，也不代表已经登记的业务资源 cleanup 被取消。

### 6.4 `snapshot()`

`snapshot()` 返回变量字典的浅拷贝：

```python
snapshot = test_context.snapshot()
```

它不包含 cleanup 回调，也不是深拷贝。若 value 本身是可变对象，snapshot 与上下文仍可能引用同一个 value。

### 6.5 变量名规则

当前变量名必须匹配：

```text
[A-Za-z_][A-Za-z0-9_.-]*
```

首字符只能是字母或下划线；后续可包含数字、点和短横线。非法名称抛 `ContextVariableError`。

---

## 7. `extract()`：从 Response 到变量的决策漏斗

`extract()` 不是单纯的 JSONPath 工具。它按固定顺序完成：

```text
校验变量名
-> 校验恰好一个提取来源
-> 从来源提取候选值
-> 判断缺失、空值、default 和 required
-> 判断 None 是否允许
-> 可选 transform
-> expected_type 校验
-> set() 保存
-> 返回最终值
```

### 7.1 恰好一个来源

一次 `extract()` 必须且只能选择：

- `json_path`；
- `header`；
- `cookie`；
- `regex`。

同时传 JSONPath 与 Header 会抛 `ContextExtractionError`；一个来源都不传也会失败。

### 7.2 提取成功后自动保存

```python
request_id = test_context.extract(
    "request_id",
    response,
    header="x-oneapi-request-id",
    expected_type=str,
)
```

返回值与保存值是同一个最终结果：

```text
extract return value
=
test_context.require("request_id")
```

### 7.3 TestContext 不主动发请求

Response 已经由 Test、Task 或 Request 取得，TestContext 不会主动调用 BaseRequest 或发送 HTTP。但“读取传入的 Response”不等于无副作用：读取 body 可能消费尚未缓冲的流。

```text
Test --调用 extract(response)--> TestContext
Response --作为提取输入--> extract
extract --写入变量--> 当前 TestContext
```

不能画成：

```text
TestContext -> BaseRequest -> HTTP
```

### 7.4 流式 Response 的提取边界

当前实现有两类读取会触碰完整 body：

1. JSONPath 调用 `response.json()`；基于 Response 的 Regex 直接读取 `response.text`。它们只适合 body 已完整取得、允许缓冲的普通响应。
2. Header 或 Cookie 成功提取只读取元数据，不读取 body；但当必填值缺失时，错误摘要会调用 `_redact_response_summary(response)`，其中同样读取完整 `response.text`。

因此即使选择 Header，也不能推导出当前 `extract()` 对未消费 SSE 安全：

```text
未消费的 stream=True Response
-> 提取必填 Header 但未匹配
-> 构造错误摘要并读取 response.text
-> 整体消费或阻塞流，也可能抛传输异常
-> 若完整读取成功，Response._content_consumed: False -> True
-> 若读取阻塞或抛传输异常，不保证该状态已经转换
-> Task 尚未接管原始流，流所有权被诊断路径改变
```

流式场景应选择以下边界之一：

- 由拥有 Response 生命周期的代码执行流安全 Header 检查；失败消息只使用状态码、Header 名等元数据，并在同一个 `try/finally` 中保证关闭；
- 先由 Task 消费并关闭流，再从 Task 已收集的 chunks 或领域结果中提取变量；
- 不对未消费 SSE 直接使用 JSONPath、基于 Response 的 Regex，或会生成完整 Response 错误摘要的必填提取路径。

本课 34 条离线测试中，涉及 Response 提取的测试均使用内存构造的完整、可直接缓冲 Response；其余测试覆盖变量、cleanup、fixture 与 ContextVar。它们没有证明 TestContext 对未消费流式 Response 安全。

---

## 8. 四种提取来源

### 8.1 JSONPath

```python
test_context.extract("task_id", response, json_path="$.task_id")
```

- JSONPath 必须以 `$` 开头；
- Response 必须能解析 JSON；
- 会读取并缓冲 body，不用于尚待逐行消费的 SSE Response；
- 默认返回第一个匹配；
- `multiple=True` 时返回所有匹配组成的 list。

### 8.2 Header

```python
test_context.extract(
    "request_id",
    response,
    header="x-oneapi-request-id",
)
```

requests 的 headers 大小写不敏感；字符串值会执行 `strip()`。成功路径只读取 headers，但必填 Header 缺失时，当前错误摘要仍会读取完整 `response.text`。

### 8.3 Cookie

```python
test_context.extract("session_id", response, cookie="session_id")
```

从 `response.cookies` 读取；字符串值同样会 strip。成功路径不读取 body，但必填 Cookie 缺失时同样会进入读取 `response.text` 的错误摘要。

### 8.4 Regex

可以从 `response.text`，这会读取并缓冲 body，只适合完整响应：

```python
test_context.extract(
    "image_url",
    response,
    regex=r"https://[^\s]+",
)
```

也可以显式传 `source_text`，并使用数字或命名 group：

```python
test_context.extract(
    "task_id",
    regex=r"task=(?P<task_id>task-\d+)",
    group="task_id",
    source_text="task=task-123",
)
```

Regex 仍是四种来源之一；`source_text` 只是 Regex 的输入，不是第五种独立来源。

若数据来自 SSE，应优先把 Task 已收集的 chunk 内容显式转换成 `source_text`，不要让 Regex 重新读取未消费 Response。

---

## 9. 缺失、空值、默认值与 null

这是本课最容易产生错误测试的部分。

### 9.1 当前什么算“没有提取到值”

`_has_extracted_value()` 把以下情况视为没有值：

- 没有任何匹配；
- 空字符串 `""`；
- 空列表 `[]`。

数值 `0` 和布尔值 `False` 不属于缺失。

### 9.2 `required=True`

默认 `required=True`。没有值且没有 default 时，抛 `ContextExtractionError`，不会保存变量。

### 9.3 `required=False` 且没有 default

```text
返回 None
-> 不保存变量
-> has(name) 仍为 False
```

返回 None 不等于上下文中存在一个值为 None 的变量。

### 9.4 default 的两种不同语义

`get()` 的 default：

```text
变量缺失 -> 返回 default -> 不保存
```

`extract()` 的 default：

```text
来源没有值 -> 使用 default -> transform/type check -> 保存
```

不能把这两个 default 混为一谈。

### 9.5 JSON null（选讲）

JSON 字段存在但值为 null 时，提取结果是 Python `None`：

```text
allow_none=False 且 required=True
-> ContextExtractionError
```

`required=True` 时显式设置 `allow_none=True`：

```text
保存 name -> None
has(name) == True
get(name) is None
```

当前源码只在 `required=True` 时执行“不允许 None”的门禁。因此：

```text
required=False + 来源明确返回 JSON null
-> 即使 allow_none 保持默认 False
-> 仍保存 name -> None
```

这与“没有匹配且 required=False”不同：后者返回 None 但不保存。现有课堂测试覆盖默认 required 下拒绝 null，以及 `allow_none=True` 保存 null；没有直接覆盖 `required=False + null`，该分支结论来自当前源码。

“变量不存在”与“变量存在且值为 None”是两个状态。

---

## 10. transform 与 expected_type 的顺序

当前顺序是：

```text
提取原始值
-> 应用 default（如需要）
-> transform(value)
-> expected_type 校验
-> 保存
```

例如：

```python
count = test_context.extract(
    "count",
    response,
    json_path="$.count",
    required=False,
    default="2",
    transform=int,
    expected_type=int,
)
```

最终保存的是整数 `2`，不是字符串 `"2"`。

### 10.1 transform 失败

transform 抛出的普通 Exception 会包装成 `ContextExtractionError`，并保留原异常为 cause。

### 10.2 expected_type 失败

值存在但类型不匹配时，抛 `ContextVariableTypeError`。检查可发生在：

- `get()/require()` 读取时；
- `extract()/extract_first()` 保存前。

### 10.3 为什么 transform 先于类型检查

因为 transform 的目的就是把外部表示转换成用例需要的类型：

```text
"2" -> int -> 2 -> expected_type=int
```

### 10.4 transform 不是数据清洗万能入口（选讲）

transform 应保持小而确定。若包含网络请求、资源创建或复杂业务分支，会把 TestContext 变成隐藏 Task，破坏职责边界。

---

## 11. `extract_first()`：多个兼容来源的优先级（选讲）

不同上游可能把同一 ID 放在不同位置：

```python
task_id = test_context.extract_first(
    "task_id",
    response,
    sources=[
        {"json_path": "$.task_id"},
        {"json_path": "$.id"},
        {"json_path": "$.request_id"},
    ],
    expected_type=str,
)
```

### 11.1 当前决策顺序

```text
按 sources 顺序尝试
-> 第一个不属于“未匹配、空字符串、空列表”的结果
-> transform/type check
-> 保存并立即返回
```

当前 `_has_extracted_value(None)` 为 True，所以 JSON null 会停止来源搜索，再进入 `required/allow_none` 处理；它不会被当成“继续尝试下一个来源”。

### 11.2 它解决什么问题

它适合兼容已知的响应差异，避免在 Test 中重复：

```text
if task_id else id else request_id
```

### 11.3 它不解决什么问题

- 不判断哪个字段在业务上更可信；
- 不合并多个来源；
- 不遍历多个 Response；
- 不替代 Schema 断言。

优先级由调用方给出的 sources 顺序决定。

### 11.4 单个来源错误会继续回退（课后边界）

每个 source 在规范化或提取时若抛 `ContextExtractionError`，当前实现会先把错误文本记录到 `source_errors`，再继续尝试下一来源：

```text
来源一 -> ContextExtractionError -> 记录，继续
来源二 -> 成功 -> 保存并返回
```

只要后续来源成功，前面记录的错误不会再抛出，也不会进入返回值；所有来源均失败时，最终 `ContextExtractionError` 才会包含各来源的错误摘要。这意味着兼容回退可以容忍前一来源失败，但也可能让错误配置在后续来源成功时保持不可见，调用方仍需谨慎安排 sources 顺序。

---

## 12. 错误类型与脱敏边界（课后阅读）

### 12.1 当前异常族

```text
TestContextError (AssertionError)
├─ ContextVariableError
│  ├─ ContextVariableNotFound
│  └─ ContextVariableTypeError
├─ ContextExtractionError
└─ ContextCleanupError
```

这些异常属于测试上下文合同失败，不是 HTTP 传输异常。

### 12.2 提取错误会提供什么

根据场景，错误信息可能包含：

- 变量名；
- JSONPath、Header、Cookie 或 Regex 描述；
- Response status_code；
- Response header 名称；
- 脱敏并截断的 body。

### 12.3 当前脱敏回退

当前目标是错误摘要不泄露：

- Authorization 值；
- Cookie 值；
- api_key 等敏感字段值；
- cleanup 异常中的敏感文本。

响应摘要只列 Header 名称，不列 Header 值；合法 JSON 按敏感 key 结构化脱敏。若 body 看起来是 JSON 但解析失败，`redact_text_body()` 现在采用 fail-closed 回退，整个 malformed JSON body 替换为 `<redacted>`，不再把无法可靠解析的原文写入异常。回归测试覆盖带引号的 malformed `api_key` 不泄露；body 最终仍受最大长度限制。

---

## 13. cleanup 是后进先出的资源栈

### 13.1 为什么是 LIFO

假设资源依赖关系：

```text
先创建 Group
-> 再在 Group 中创建 Asset
```

登记顺序：

```python
test_context.add_cleanup(delete_group, group_id)
test_context.add_cleanup(delete_asset, asset_id)
```

清理顺序必须反过来：

```text
delete_asset
-> delete_group
```

这与函数调用栈、`with` 嵌套和事务补偿的逆序思想一致：后创建的依赖资源先释放。

### 13.2 登记时机

推荐：

```text
资源成功创建并取得 ID
-> 立即 add_cleanup
-> 再进入后续步骤
```

若等到所有断言结束才登记，中间失败会让资源失去清理机会。

### 13.3 callback 合同

`add_cleanup(callback, *args, **kwargs)`：

- callback 必须 callable；
- 参数在登记时保存；
- cleanup 时同步调用；
- callback 返回值会被忽略；
- callback 必须自己在失败时抛异常。

如果删除接口返回 500 但 callback 不断言也不抛异常，TestContext 不会自动识别清理失败。

### 13.4 当前不支持的自动能力（选讲）

- 不自动 await coroutine；
- 不根据 Response status_code 判断 callback 成功；
- 不自动重试 cleanup；
- 不自动生成资源依赖图。

---

## 14. cleanup 失败：继续清理，最后汇总

`cleanup()` 的核心算法：

```text
errors = []
while 栈非空:
    pop 最后登记的 callback
    try 执行
    若抛 BaseException:
        保存原异常
        继续下一个 callback
若 errors 非空:
    抛 ContextCleanupError(errors)
```

### 14.1 第一个失败不会阻止后续 cleanup

登记：

```text
first
fail
last
```

实际执行：

```text
last
fail -> 记录错误
first
-> 最后统一抛 ContextCleanupError
```

### 14.2 聚合保留什么

`ContextCleanupError.errors` 保存原异常列表；消息包含异常类型和脱敏后的文本。

### 14.3 cleanup 是幂等收口，但失败 callback 不会自动重试

callback 在执行前已经从栈中 pop。第一次 cleanup 后栈被耗尽：

```text
第二次 cleanup()
-> 没有 callback
-> 直接返回
```

这证明收口幂等，不表示第一次失败的删除动作已经成功，也不表示会重试。

### 14.4 “不掩盖前面的错误”的准确边界（选讲）

TestContext 自己只负责：

```text
不让第一个 cleanup 失败阻止后续 cleanup
-> 汇总所有 cleanup 异常
```

它不会接收或合并 Test body 的原始异常。fixture 在 pytest teardown 阶段调用 cleanup；当 call 阶段与 teardown 阶段都失败时，如何同时展示由 pytest 报告生命周期负责。当前 34 条课堂测试没有直接证明最终报告如何呈现双重失败。

---

## 15. 隔离：一个实例属于一个用例

### 15.1 多实例隔离

```text
context A: request_id = request-001
context B: request_id = request-002
```

二者拥有不同变量字典，不会互相覆盖。

### 15.2 线程测试证明了什么

当前测试在线程池的每个 worker 内新建一个 TestContext：

```text
worker 0 -> context 0
worker 1 -> context 1
...
```

它证明“不同实例在不同线程中互不共享变量”。它不证明同一个 TestContext 实例可以被多个线程安全地并发修改；锁与共享写入细节保留到 15.3 选讲。

### 15.3 它没有证明什么（选讲）

它没有让一个 TestContext 变成线程安全对象。当前 `dict` 与 cleanup `list` 没有锁：

```text
不要把同一个 TestContext 实例交给多个线程并发写入
```

若 worker 需要业务变量，优先显式传入不可变参数，或为每个 worker 创建独立对象。

### 15.4 TestContext 也不是跨 worker 存储（课后阅读）

pytest-xdist worker 是独立进程。普通 Python 对象不会自动跨进程共享；本课不引入进程间状态。

---

## 16. ContextVar 与 TestContext 是两条独立机制

### 16.1 TestContext

```text
显式 Python 对象
-> Test 通过 fixture 参数取得
-> Test 显式调用 set/extract/require/add_cleanup
```

保存的是动态业务值和 cleanup 回调。

### 16.2 ContextVar

```text
隐式绑定在当前执行上下文
-> 例如当前 case、operation、Runtime Hooks
-> 普通函数可在不增加参数时读取
```

线程池不会自动继承提交线程当前的 ContextVar 值。

### 16.3 `submit_with_context()`

当前实现：

```python
def submit_with_context(executor, function, /, *args, **kwargs):
    context = copy_context()
    return executor.submit(context.run, function, *args, **kwargs)
```

真实顺序：

```text
调用 submit_with_context
-> 在提交线程执行 copy_context()
-> 得到当前 ContextVar 快照
-> executor.submit(context.run, function, ...)
-> worker 在该快照内执行 function
-> 返回 Future
```

### 16.4 快照边界（选讲）

每次提交都创建自己的 Context：

```text
value = first -> submit task A -> A 捕获 first
value = second -> submit task B -> B 捕获 second
```

它传播提交时的 ContextVar 绑定快照，不是持续共享的全局映射，也不会把 worker 内的重新绑定自动写回提交线程。

`copy_context()` 不会深拷贝绑定值。若某个 ContextVar 绑定的是可变 dict 或 list，复制后的 Context 仍可能引用同一个可变对象；“每个任务有独立 Context”指绑定映射独立，不代表绑定的数据对象完全隔离。当前课堂测试使用不可变字符串，没有证明可变对象被深拷贝。

### 16.5 不会被自动解决的问题

`submit_with_context()`：

- 不复制 TestContext 的 `_variables`；
- 不自动把 `test_context` fixture 注入 worker；
- 不让同一个 requests.Session 变得线程安全；
- 不为每个线程创建 Request Client；
- 不吞掉 worker 的返回值或异常。

用例内部使用线程池时，每个线程仍应创建并关闭自己的 Request Client。

---

## 17. 轻量验证：34 条离线测试

### 17.1 为什么安全

- Response 在内存中构造；
- JSON、Header、Cookie 和 Regex 都读取本地对象；
- cleanup 使用 list callback 或 pytest 临时文件；
- ContextVar 测试只创建本地线程池；
- 不需要真实账号、API Key 或外部资源。

### 17.2 安全命令

```powershell
$hadDotenvPath = Test-Path Env:API_CASE_DOTENV_PATH
$previousDotenvPath = $env:API_CASE_DOTENV_PATH
$hadQualityEnable = Test-Path Env:QUALITY_ENABLE
$previousQualityEnable = $env:QUALITY_ENABLE
$pytestExitCode = 1
$evidenceRoot = $null
try {
  $env:API_CASE_DOTENV_PATH = (Resolve-Path -LiteralPath '.env.example' -ErrorAction Stop).Path
  $env:QUALITY_ENABLE = '0'
  $evidenceRoot = Join-Path ([System.IO.Path]::GetTempPath()) ('api-case-lesson11-' + [guid]::NewGuid().ToString('N'))
  New-Item -ItemType Directory -Path $evidenceRoot -ErrorAction Stop | Out-Null
  & .\.venv\Scripts\python.exe -m pytest tests/test_test_context.py tests/quality/test_quality_context_executor.py --basetemp "$evidenceRoot\pytest-temp" --alluredir "$evidenceRoot\allure-results" -p no:cacheprovider -q
  $pytestExitCode = $LASTEXITCODE
}
finally {
  if ($hadDotenvPath) {
    $env:API_CASE_DOTENV_PATH = $previousDotenvPath
  }
  else {
    Remove-Item Env:API_CASE_DOTENV_PATH -ErrorAction SilentlyContinue
  }
  if ($hadQualityEnable) {
    $env:QUALITY_ENABLE = $previousQualityEnable
  }
  else {
    Remove-Item Env:QUALITY_ENABLE -ErrorAction SilentlyContinue
  }
}
if ($null -ne $evidenceRoot) {
  Write-Host "Lesson evidence: $evidenceRoot"
}
if ($pytestExitCode -ne 0) {
  throw "Lesson 11 offline tests failed with exit code $pytestExitCode"
}
```

### 17.3 当前结果

```text
34 passed
```

其中：

- `tests/test_test_context.py`：31 条；
- `tests/quality/test_quality_context_executor.py`：3 条。

### 17.4 明确证明范围

31 条 TestContext 测试覆盖：

- 变量读写、删除、clear 与 snapshot；
- 变量名、缺失变量与类型错误；
- JSONPath、Header、Cookie、Regex；
- multiple、extract_first、default、required、默认拒绝 null、`allow_none=True` 保存 null、transform；
- 错误摘要脱敏；
- malformed JSON 使用 fail-closed 脱敏且不泄露带引号的敏感值；
- cleanup LIFO、继续执行、错误聚合和二次调用；
- 不同实例与每线程独立实例隔离；
- fixture teardown 调用 cleanup；
- 临时文件 cleanup。

3 条 ContextExecutor 测试覆盖：

- 每次提交捕获自己的 ContextVar 快照；
- 40 个并发任务不复用同一个 Context；
- Future 保留函数返回值和原异常。

### 17.5 不能证明什么

这些测试不证明：

- 真实资源删除一定成功；
- 一个共享 TestContext 实例可以被多线程并发修改；
- TestContext 会自动进入 worker；
- requests.Session 可以跨线程共享；
- cleanup callback 返回非 2xx Response 会自动失败；
- async callback 会被 await；
- call 失败与 teardown 失败在最终报告中的具体展示；
- Runtime Hooks 与 Quality 的全部 ContextVar 都已正确归属。
- `copy_context()` 会深拷贝 ContextVar 绑定的可变对象。
- TestContext 对未消费流式 Response 安全；JSONPath、基于 Response 的 Regex 与失败摘要均可能读取 body，当前测试没有覆盖 `_content_consumed=False` 的 SSE 输入。

---

## 18. 课堂活动：一组包含三个 callback 的 cleanup

### 18.1 题目

```python
calls = []

test_context.add_cleanup(calls.append, "delete-group")
test_context.add_cleanup(fail_delete_asset)
test_context.add_cleanup(calls.append, "delete-temp-file")

test_context.cleanup()
```

`fail_delete_asset` 会先向 calls 追加 `"delete-asset"`，再抛 RuntimeError。

请预测：

1. calls 的最终顺序；
2. 第一个失败后是否还执行 `delete-group`；
3. cleanup 最终抛什么；
4. 再调用一次 cleanup 会怎样。

### 18.2 参考答案

```text
执行顺序：
delete-temp-file
-> delete-asset（失败并记录）
-> delete-group

最终：
抛 ContextCleanupError
errors 中包含原 RuntimeError

第二次 cleanup：
栈已耗尽，直接返回
```

### 18.3 验收重点

学习者必须同时说出：

- LIFO；
- 失败后继续；
- 最后汇总；
- 不自动重试失败 callback。

---

## 19. 第十一版累积链路总图：fixture、当前手动模式与不使用模式

当前工作树中的业务 Test 尚未声明 `test_context` fixture，也没有使用 `extract/require` 组成完整变量链；因此图中的 fixture 与变量提取部分仍是由框架源码和单元测试支撑的推荐模式。

当前业务代码已经存在 TestContext 清理链：`module/material_library/test_seedance_2_5_virtual_asset_library.py` 的 `setup_method()` 手动创建 TestContext 与 MaterialLibraryRequest，资源创建后把同一个 Request Client 作为 cleanup callback 参数登记；pytest 进入 `teardown_method()` 后先尝试 `cleanup()`，再由 `finally` 关闭 Request Client。该文件会访问真实素材库和模型接口，本课只做静态阅读，绝不加入课堂执行命令。

```mermaid
flowchart TD
    PYTEST["pytest 收集到测试项"]
    USE{"本测试是否启用 TestContext?"}

    FIXTURE_MODE["fixture 模式（推荐）<br/>Test 声明 test_context 参数"]
    FIXTURE_SETUP["pytest fixture setup<br/>创建 TestContext"]
    FIXTURE_TEST["多步骤 Test<br/>接收同一个 TestContext 对象"]
    STEP1["步骤一：Task / Request"]
    RESPONSE["Response<br/>包含候选动态 ID"]
    BUFFERED{"body 是否已完整且允许缓冲?"}
    EXTRACT["Test 调用 extract / extract_first"]
    STREAM_OWNER["Task 消费并关闭 SSE"]
    STREAM_RESULT["chunks / 领域结果"]
    STREAM_TEST["Test 接收流处理结果"]
    SET_CONTEXT["Test 调用 TestContext.set"]
    STORE["当前 TestContext 变量字典"]
    REQUIRE["Test 调用 require(name)"]
    VALUE["动态 ID"]
    STEP2["步骤二：后续 Task / Request"]
    RESOURCE["本用例创建的业务资源"]
    REGISTER["Test 调用 add_cleanup(callback, id)"]
    FIXTURE_CALL_END["Test call 阶段结束<br/>正常返回或抛异常"]
    FIXTURE_TEARDOWN["pytest 恢复 fixture<br/>进入 finally"]

    MANUAL_MODE["当前手动模式<br/>业务文件"]
    MANUAL_SETUP["setup_method<br/>创建 Request 与 TestContext"]
    MANUAL_REQUEST["MaterialLibraryRequest<br/>self.request"]
    MANUAL_TEST["业务 Test<br/>使用局部变量，不调用 extract / require"]
    MANUAL_REGISTER["self.test_context.add_cleanup<br/>callback 参数包含 self.request"]
    MANUAL_CALL_END["Test call 阶段结束<br/>正常返回或抛异常"]
    MANUAL_TEARDOWN["pytest 调用 teardown_method"]
    CLIENT_CLOSE["finally: self.request.close()<br/>HTTP Client 关闭"]
    FIXTURE_CLEANUP_EXIT["fixture teardown<br/>正常结束或报告 cleanup 异常"]
    MANUAL_CLEANUP_EXIT["手动 teardown<br/>close 后结束或继续抛 cleanup 异常"]

    NO_CONTEXT["不使用 TestContext"]
    ORDINARY["普通 Test<br/>调用 Task / Request，随后 Assertions"]

    CLEANUP["TestContext.cleanup"]
    STACK["当前实例的 cleanup 栈"]
    HAS_CALLBACK{"栈中还有 callback?"}
    CALLBACK["最后登记的 callback"]
    AGG{"callback 是否失败?"}
    CLEAN_OK["cleanup 完成"]
    CLEAN_ERROR["ContextCleanupError<br/>汇总 cleanup 异常"]

    PYTEST -->|"根据测试代码与 fixture 声明判断"| USE
    USE -->|"是：声明 fixture"| FIXTURE_MODE
    USE -->|"是：手动创建"| MANUAL_MODE
    USE -->|"否"| NO_CONTEXT

    FIXTURE_MODE -->|"pytest 调用 fixture"| FIXTURE_SETUP
    FIXTURE_SETUP -->|"yield TestContext 对象"| FIXTURE_TEST
    FIXTURE_TEST -->|"调用"| STEP1
    STEP1 -->|"返回 Response"| RESPONSE
    RESPONSE -->|"判断响应形态"| BUFFERED
    BUFFERED -->|"是：Response 作为提取输入"| EXTRACT
    FIXTURE_TEST -->|"调用 extract / extract_first"| EXTRACT
    BUFFERED -->|"否：未消费 SSE"| STREAM_OWNER
    STREAM_OWNER -->|"返回"| STREAM_RESULT
    STREAM_RESULT -->|"对象返回给"| STREAM_TEST
    STREAM_TEST -->|"调用 set(name, value)"| SET_CONTEXT
    SET_CONTEXT -->|"写入变量"| STORE
    EXTRACT -->|"写入变量"| STORE
    FIXTURE_TEST -->|"调用 require"| REQUIRE
    STORE -->|"提供已保存值"| REQUIRE
    REQUIRE -->|"返回"| VALUE
    FIXTURE_TEST -->|"调用后续步骤"| STEP2
    VALUE -->|"作为请求参数"| STEP2
    STEP1 -->|"创建资源"| RESOURCE
    STEP2 -->|"也可能创建资源"| RESOURCE
    FIXTURE_TEST -->|"调用 add_cleanup"| REGISTER
    RESOURCE -->|"资源 ID 作为 callback 参数"| REGISTER
    REGISTER -->|"压入 callback"| STACK
    FIXTURE_TEST -->|"正常返回或抛异常"| FIXTURE_CALL_END
    FIXTURE_CALL_END -->|"pytest 进入 teardown"| FIXTURE_TEARDOWN
    FIXTURE_TEARDOWN -->|"调用 context.cleanup()"| CLEANUP

    MANUAL_MODE -->|"pytest 调用 setup_method"| MANUAL_SETUP
    MANUAL_SETUP -->|"保存 self.request"| MANUAL_REQUEST
    MANUAL_SETUP -->|"保存 self.test_context"| MANUAL_TEST
    MANUAL_TEST -->|"资源创建后调用"| MANUAL_REGISTER
    MANUAL_REQUEST -->|"作为 callback 参数"| MANUAL_REGISTER
    MANUAL_REGISTER -->|"压入 callback"| STACK
    MANUAL_TEST -->|"正常返回或抛异常"| MANUAL_CALL_END
    MANUAL_CALL_END -->|"pytest 进入 teardown"| MANUAL_TEARDOWN
    MANUAL_TEARDOWN -->|"try：调用 self.test_context.cleanup()"| CLEANUP
    MANUAL_REQUEST -->|"关闭同一 Client 实例"| CLIENT_CLOSE

    NO_CONTEXT -->|"运行原有测试链"| ORDINARY

    CLEANUP -->|"检查当前栈"| HAS_CALLBACK
    STACK -->|"提供栈状态"| HAS_CALLBACK
    HAS_CALLBACK -->|"是：LIFO pop"| CALLBACK
    CALLBACK -->|"执行后判断"| AGG
    AGG -->|"否：继续"| HAS_CALLBACK
    AGG -->|"是：记录原异常并继续"| HAS_CALLBACK
    HAS_CALLBACK -->|"否且无已记录错误"| CLEAN_OK
    HAS_CALLBACK -->|"否且有已记录错误"| CLEAN_ERROR
    CLEAN_OK -->|"fixture 调用：返回 fixture teardown"| FIXTURE_CLEANUP_EXIT
    CLEAN_OK -->|"手动调用：返回 teardown 后进入 finally"| CLIENT_CLOSE
    CLEAN_ERROR -->|"fixture 调用：抛 ContextCleanupError"| FIXTURE_CLEANUP_EXIT
    CLEAN_ERROR -->|"手动调用：异常沿调用栈返回，finally 先 close"| CLIENT_CLOSE
    CLIENT_CLOSE -->|"close 完成"| MANUAL_CLEANUP_EXIT

    PARENT["提交线程<br/>当前 ContextVar 值"]
    COPY["submit_with_context<br/>copy_context 快照"]
    WORKER["worker<br/>context.run(function)"]
    FUTURE["Future<br/>返回值或原异常"]
    PARENT -->|"调用 submit_with_context"| COPY
    COPY -->|"executor.submit(context.run, ...)"| WORKER
    WORKER -->|"完成或抛异常写入"| FUTURE

    NO_COPY["TestContext 变量字典<br/>不会被 submit_with_context 自动复制"]
    COPY -. "不包含显式资料袋复制语义" .-> NO_COPY

    CAPABILITY["第 12 课<br/>BaseTask 门面与窄 Capability"]
    STEP2 -. "后续课程接口" .-> CAPABILITY
```

### 19.1 线型与边标签

- 实线表示分支一旦被选择后真实发生的调用、对象传递、状态读写或 pytest 生命周期控制；每条边都标明关系类型。
- Test 正常返回或抛异常后，pytest 进入对应 teardown 是实线生命周期边，不是可选旁路。
- 虚线只用于“不会自动复制”的概念边界和下一课接口，不表示真实失败或 teardown 路径可选。

### 19.2 当前业务清理链

当前业务文件的静态关系是：

```text
setup_method --创建并保存--> self.test_context
setup_method --创建并保存--> self.request
业务 Test --把 self.request 与资源 ID 作为参数登记--> self.test_context.add_cleanup
Test call 正常结束或失败 --pytest 生命周期--> teardown_method
teardown_method --try 调用--> self.test_context.cleanup
cleanup 正常返回或抛异常 --finally--> self.request.close
```

业务资源 cleanup callback 依赖尚未关闭的同一个 MaterialLibraryRequest；因此 teardown 必须先尝试业务资源清理，再在 `finally` 中关闭 HTTP Client。两层责任彼此不同，而且即使 cleanup 抛出 ContextCleanupError，也仍会尝试关闭 Client。该链没有证明 fixture 注入、`extract()` 或 `require()` 已在业务中使用。该文件访问真实接口，只允许静态阅读。

### 19.3 fixture 与变量提取仍是推荐模式

fixture 分支的实现证据来自 TestContext、`module/conftest.py` 和单元测试。对于完整、可缓冲 Response，可使用 `extract()` 保存变量；对于未消费 SSE，应先由 Task 完成消费和 close，再从 chunks 或领域结果保存动态值。

### 19.4 为什么保留不使用模式

TestContext 不是所有测试的固定节点。未声明 fixture、也未手动创建实例的单请求用例继续沿原有 Test → Task/Request → Response → Assertions 运行。

### 19.5 为什么 ContextVar 单独成图

`copy_context()` 操作的是隐式 ContextVar 上下文；TestContext 是显式对象。二者都涉及“上下文”，但数据结构、传递方式和所有者不同。

---

## 20. 常见误区

课堂必讲误区一至六、误区八和误区十七；null 相关的误区七以及误区九至十六作为选讲或课后自查。

### 误区一：每个 Request 都必须经过 TestContext

不对。它是 Test 按需使用的用例级容器。

### 误区二：有局部变量也必须改成 TestContext

不对。单步骤或短链路使用局部变量更直接。

### 误区三：TestContext 可以跨用例传值

不应这样做。一个实例服务一个用例；跨用例依赖应拆除。

### 误区四：`get(default)` 会保存 default

不会。它只返回 default。

### 误区五：`extract(default)` 也不保存 default

不对。提取缺失时，default 经过转换与类型检查后会保存。

### 误区六：required=False 返回 None 就表示变量已存在

不一定。无 default 且没有值时返回 None，但不保存。

### 误区七：JSON null 与字段缺失相同

不同。null 是已提取的候选值。默认 `required=True` 时需要 `allow_none=True`；当前 `required=False` 分支即使未开启 allow_none 也会保存 None。字段未匹配且 required=False 则返回 None 但不保存。

### 误区八：clear 会同时取消 cleanup

不会。clear 只清空变量字典。

### 误区九：cleanup 按登记顺序执行（课后自查）

不会。它按 LIFO 逆序执行。

### 误区十：第一个 cleanup 失败会立即停止（课后自查）

不会。错误先保存，剩余 callback 继续执行。

### 误区十一：第二次 cleanup 会重试第一次失败动作（课后自查）

不会。callback 已从栈中 pop；第二次调用只是幂等返回。

### 误区十二：callback 返回 500 Response 会被自动识别（课后自查）

不会。callback 必须自己断言或抛异常。

### 误区十三：线程测试证明同一个 TestContext 是线程安全的（课后自查）

没有。测试为每个 worker 创建独立实例。

### 误区十四：`submit_with_context()` 会复制 TestContext（课后自查）

不会。它复制当前 ContextVar 绑定；绑定的可变对象也不会被深拷贝。

### 误区十五：传播 ContextVar 后就能共享 Request Client（课后自查）

不能。每个线程仍应创建并关闭自己的 Request Client。

### 误区十六：ContextCleanupError 会合并 Test body 原异常（课后自查）

不会。它只汇总 cleanup callback 异常；pytest 负责 call 与 teardown 阶段报告。

### 误区十七：Header 提取只读 headers，所以对未消费 SSE 总是安全

不对。Header 成功路径不读取 body，但必填 Header 缺失时，当前错误摘要会读取完整 `response.text`。未消费 SSE 应由资源所有者执行流安全 Header 检查并保证 close，或先由 Task 收集 chunks 后再保存变量。

---

## 21. 三分钟复述

```text
TestContext 是一个可选的用例级资料袋。它解决同一个测试内多步骤动态值传递和业务资源清理，不是每次 Request 的必经节点。局部变量能清楚完成的短流程不必使用它；模块全局变量和依赖用例顺序则会破坏隔离。

当前 test_context fixture 不是 autouse。Test 声明参数后，pytest 为该用例创建一个新 TestContext，yield 给 Test，并在 teardown 的 finally 中调用 cleanup。当前工作树还存在手动模式：setup_method 创建 TestContext 与 Request Client，资源创建后 add_cleanup，teardown_method 先尝试 cleanup，再在 finally 中关闭 Client；它尚未使用 fixture、extract 或 require。TestContext 内部有两个独立结构：变量字典和 cleanup 栈。clear 只清变量，不取消 cleanup；snapshot 是变量字典的浅拷贝。

extract 先要求 json_path、header、cookie、regex 四种来源恰好选一个，再提取候选值，处理 required/default，然后执行 transform、expected_type 校验并保存。JSONPath 和基于 Response 的 Regex 会读取 body；Header、Cookie 成功路径不读 body，但必填值缺失时错误摘要仍会读取 response.text。因此当前 extract 不应直接处理未消费 SSE，流应由 Task 消费并关闭，再从 chunks 或领域结果保存变量。没有匹配且 required=False、无 default 时返回 None 但不保存。get 的 default 只返回不保存，extract 的 default 会经过转换和类型检查后保存。

资源成功创建并取得 ID 后应立即 add_cleanup。cleanup 使用 LIFO，后创建的依赖资源先删除。某个 callback 失败时先记录异常，继续执行剩余 callback，最后抛 ContextCleanupError 汇总。callback 返回值被忽略，失败必须自己抛异常。栈在执行时被 pop，所以第二次 cleanup 幂等返回，但不会重试失败动作。

TestContext 与 ContextVar 是两条机制。TestContext 是显式对象；submit_with_context 在提交线程 copy_context，为每个任务传播提交时的 ContextVar 绑定快照，Future 保留返回值和原异常。它不会复制 TestContext 字典，也不会把共享 TestContext 或 requests.Session 变成线程安全对象。
```

---

## 22. 课堂验收与课后题库

### 22.1 统一课堂验收题

1. TestContext 是每个 Request 的必经节点吗？A 是 / B 否，按需使用（B）
2. `extract(..., default="2", transform=int)` 保存什么？A `"2"` / B `2`（B）
3. 必填 Header 在未消费 SSE 中缺失时，当前 `extract()` 是否保证不读取 body？A 保证 / B 不保证，错误摘要会读取 `response.text`（B）
4. cleanup 登记 group、asset、temp-file，执行顺序是什么？A group-asset-temp / B temp-asset-group（B）
5. asset cleanup 失败后是否继续 group cleanup？A 继续并最后汇总 / B 立即停止（A）
6. 手动 `teardown_method()` 中 cleanup 抛异常后是否仍尝试关闭 Request Client？A 是，`finally` 执行 close / B 否（A）
7. 当前业务 TestContext 链采用哪种方式？A fixture + extract/require / B setup_method 手动创建、add_cleanup、teardown cleanup（B）
8. `submit_with_context()` 复制什么？A TestContext 字典 / B ContextVar 绑定快照（B）

第 24.1 节只根据本套题和口头因果链判定是否合格，不再追加第二套开放式课堂问题。

### 22.2 课后题库

1. `get("x", default=1)` 会保存 x 吗？
2. required=False、无匹配且无 default 时会保存 None 吗？
3. clear 会清空 cleanup 栈吗？
4. 默认 required=True 时，JSON null 需要什么条件才能保存？
5. `extract_first()` 的第一个来源返回 JSON null 时是否自动尝试下一来源？
6. 第二次 cleanup 为什么不会重试失败 callback？
7. callback 返回 500 Response 为什么不会自动失败？
8. 每线程独立 TestContext 测试能否证明共享实例线程安全？
9. `copy_context()` 会深拷贝 ContextVar 绑定的 dict 吗？
10. 哪些提取路径会读取 Response body？
11. TestContext 为什么不是 Request 管线节点？
12. 当前业务手动清理链与推荐 fixture 变量链分别已经落地到什么程度？

---

## 23. 课后作业：更新生命周期图，不写代码

### 23.1 必做内容

1. 更新一张生命周期图：包含 fixture、当前手动 cleanup 和不使用三种模式；在变量分支中同时画出普通完整 Response 与未消费 SSE，在手动 teardown 分支中画出 `cleanup -> finally close Request Client`，并标明调用、对象流、状态读写和 pytest 生命周期。
2. 只分析第 18 节这一组纯 callback 场景：写出 LIFO 执行顺序、错误汇总和第二次 cleanup 结果，不扩展 Request Client 或 teardown_method。
3. 完成一次三分钟口头因果链复述，必须区分 TestContext 与 ContextVar；文字稿选做。

### 23.2 不要求完成

- 不调用真实创建或删除接口。
- 不新增跨用例共享状态。
- 不实现异步 cleanup。
- 不修改 ContextVar 或 Quality。
- 不把全部局部变量改成 TestContext。
- 不提交长篇源码抄录。

---

## 24. 验收标准

### 24.1 课堂合格判定

- 第 22.1 节 8 道统一验收题至少答对 6 道；不再追加第二套课堂问题。
- 能口头解释“未消费 SSE → 失败摘要读取 body → 流所有权被改变”的因果链。
- 能口头解释“业务 cleanup 依赖 Request Client → teardown 先 cleanup → finally close Client”的因果链。

第 22.2 节是课后题库，用于补弱和复习，不进入课堂时间预算。

### 24.2 三分钟复述合格要点

合格复述必须包含：

- 可选的用例级容器；
- 变量字典与 cleanup 栈；
- 提取漏斗和空值语义；
- 完整 Response 与未消费 SSE 的提取所有权边界；
- LIFO、继续清理、最终汇总；
- fixture、当前手动 cleanup 与不使用三种模式；
- 业务资源 cleanup 与 HTTP Client close 是两层责任；
- callback 返回值与失败责任；
- 实例隔离但非共享线程安全；
- TestContext 与 ContextVar 分离；
- submit 时快照而非自动共享。

---

## 25. 下一课接口

TestContext 解决“一个用例怎样保存动态状态并可靠清理”，但它不决定业务逻辑应该写在哪个 Task：

```text
某个新动作只属于视频领域？
-> 放入 VideoTask

多个模块确实复用同一种媒体能力？
-> 考虑窄 Capability

为了方便继续往 BaseTask 塞方法？
-> 不允许
```

第 12 课将展开：

```text
BaseTask：兼容门面
-> 委托现有 task_capabilities

领域 Task：新领域逻辑默认落点

窄 Capability：确有跨模块复用时的单一能力
```

TestContext 仍然只包围用例状态与 cleanup，不拥有这些业务实现。
