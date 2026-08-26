# 第 5 课：为什么必须先形成权威 Case 集合

## 本课在事实链中的位置

第 4 课已经解释了异步图像生成为什么不能只看 HTTP 200：一次创建请求得到 `task_id="job-101"` 后，客户端还要对同一个任务持续查询，并把 `pending`、`running`、`succeeded` 等外部状态归入等待、成功、失败或未知分支。那次业务动作可能包含一个 POST 和多次 GET。

不过，“这些请求为什么继续或停止”与“这些事实属于本次运行中的哪个测试 Case”是两个问题。`job-101` 是外部服务返回的业务任务标识，不是 pytest Case 的身份；一个 Case 也可能创建多个任务，或者在进入 HTTP 调用前就失败。请求数量、任务数量和 Case 数量不能互相替代。

从本课开始，事实链进入 Runner 与身份模块。Runner 是本框架负责组织收集和执行的运行编排器，Worker 是执行具体用例的工作进程。第 5 课先解决最上游的问题：在任何执行池和 Worker 开始工作前，本次运行究竟预期执行哪些 Case？只有先固定这个范围，后续才有依据判断某个 Case 是被执行、被跳过、未启动，还是其观察事实发生了缺失。

本课只讨论权威 Case 集合的形成及三种用途：执行输入、完整性参照和稳定计划基线。串行池与并行池如何改变调度留到第 6 课；跨运行、执行阶段和工作进程的完整身份留到第 7 课；质量归并的全部校验顺序留到第 12～16 课。

---

## 核心问题

本课要回答的矛盾是：

> 如果执行结束后只观察到两份 Case 结果，怎样判断本来就只计划了两个 Case，还是原本计划三个、其中一个没有留下结果？为什么不能让每个 Worker 各自发现用例，再把它们报告的数量相加？

答案依赖一个先后顺序：

```text
先确定“应当有哪些 Case”
→ 再决定“把这些 Case 放到哪里执行”
→ 最后比较“实际观察到了什么”
```

若把“实际观察到的结果”反过来定义成“原本应执行的集合”，缺失项会从分母中一起消失。此时两份结果既可以解释为“计划两个且全部完成”，也可以解释为“计划三个但丢失一个”，框架没有证据在二者之间作出选择。

---

## 从一个具体现象开始

继续使用异步图像生成案例。本课从仓库中的真实 Smoke 文件选择三个真实 pytest Case。教学运行的输入是：

```text
test_path：module/smoke/test_图片生成异步调用.py
选择条件：-k "f8_07 or f8_08 or f8_09"
```

这三个 Case 分别检查提交返回任务标识、查询任务状态，以及创建并轮询到成功结果。对当前仓库执行受控收集时，Runner 得到以下三个 pytest `nodeid`：

```text
A = module/smoke/test_图片生成异步调用.py::TestAsyncImageGeneration::test_f8_07_async_image_generation_submit_returns_task_id

B = module/smoke/test_图片生成异步调用.py::TestAsyncImageGeneration::test_f8_08_async_image_generation_task_status_query

C = module/smoke/test_图片生成异步调用.py::TestAsyncImageGeneration::test_f8_09_async_image_generation_task_succeeds_with_result
```

本课把这个按 pytest 收集顺序保存的计划记为：

```text
P = [A, B, C]
planned_case_count = 3
```

这里的方括号很重要。课程沿用“权威 Case 集合”这个概念名称，但当前实现保存的是有顺序的 `tuple[CollectedTestCase, ...]`，不是无序的 Python `set`。每个成员包含 `nodeid` 和收集时可见的 marker 名称。

这三个 Case 所在文件具有文件级 `serial` marker；收集结果会保存这个 marker。本课不继续推导它们将进入哪个执行池，相关规则留到第 6 课。

一次普通执行的关键时间线如下：

| 时间 | 输入或事件 | 新形成的事实 | 此时还不能得出的结论 |
| --- | --- | --- | --- |
| T0 | Runner 接收目标文件与 `-k` 条件 | 本次选择范围已给定 | 尚不知道最终能收集几项 |
| T1 | Runner 发起一次独立的 pytest `collect-only` | pytest 根据当前代码、配置、插件和选择条件形成 `session.items` | 尚未执行三个测试函数 |
| T2 | 收集成功且 `nodeid` 无重复 | 权威 Case 集合固定为 `P=[A,B,C]`，计划数为 3 | 不代表三个 Case 都会启动、完成或通过 |
| T3 | Runner 从 `P` 派生执行输入 | 后续 pytest 调用收到明确的 `nodeid` 列表 | 不代表执行顺序等于收集顺序 |
| T4 | Case C 执行一次示例业务动作 | 可能取得 `job-101`，并发送一个 POST 与多次 GET | 这些 HTTP 事件不会把 Case 数从 3 改成 4 或更多 |
| T5 | 执行结束并生成可用产物 | 计划列表、计划数量、池级状态以及观察事实可以被对照 | 观察事实缺失时不能把缺失补成通过 |

`P` 的权威性只针对这一次 Runner 调用。若目标路径、`-k`/`-m` 等选择条件、测试代码、pytest 插件或运行环境发生变化，就必须重新收集；不能把本次的三个成员宣称为仓库永久不变的 Case 总表。

---

## 为什么原有解释不够

只看执行后的结果，很容易采用下面的循环定义：

```text
本次 Case 集合 = 最终写出结果的 Case
```

这个定义无法识别缺失。假定实际只观察到 A 和 B：

| 候选解释 | 原计划 | 实际观察 | 只看结果能否区分 |
| --- | --- | --- | --- |
| 情形一 | `[A, B]` | `[A, B]` | 不能 |
| 情形二 | `[A, B, C]` | `[A, B]` | 不能 |

两种情形拥有同一份观察结果，但含义相反。情形一可以是完整执行；情形二至少缺少 C 的观察事实。必须保留 T2 时形成的计划，才能在 T5 判断是哪一种情况。

“每个 Worker 自行发现，再合并结果”也不能自动解决这个问题。下面是一个反事实方案，不是当前 Runner 的实现：

```text
Worker α 局部发现：[A, B]
Worker β 局部发现：[B, C]

直接相加：2 + 2 = 4        → B 被重复计算
按 nodeid 去重：{A, B, C}  → 得到 3，但仍不知道是否还有双方都没发现的 D
```

再把 Worker β 的环境差异改成只发现 B：

```text
Worker α 局部发现：[A, B]
Worker β 局部发现：[B]
去重后的观察集合：{A, B}
```

如果事前没有 `P=[A,B,C]`，框架无法仅凭 `{A,B}` 证明 C 缺失。把观察集合本身当作分母，只会得到“观察到 2、总数也是 2”的自我确认。

当前 Runner 采用的是另一条路径：先让一次独立收集确定计划，再把计划中的明确 `nodeid` 交给后续 pytest 执行。pytest 和 pytest-xdist（pytest 的并行执行插件）在执行阶段仍可能进行自身的内部收集与分配；本课所说的“一次权威收集”是“一次由 Runner 主持、决定原始执行范围的预收集”，不是“整个进程从此不再发生任何 pytest collection”。

---

## 核心概念

本课新增三个核心概念。

### 1. 权威 Case 集合：Authoritative Case Set

权威 Case 集合是 Runner 在本次执行开始前，根据目标路径、选择参数以及当时的 pytest 环境形成的唯一成员快照。它回答“本次原本要执行谁”，并作为本次执行计划的来源。

当前实现中，每个计划成员是一个冻结的 `CollectedTestCase`：

- `nodeid` 是 pytest 给收集项的执行地址，也是本课范围内的计划成员键。
- `markers` 是该收集项可见的 marker 名集合，用于后续派生调度池。
- 多个成员按 pytest 的 `session.items` 顺序保存在冻结的 `CollectionResult.cases` 元组中。

`nodeid` 不是完整的长期质量身份。它能在本次 pytest 计划中指向具体收集项，但还没有同时表达整体运行、执行阶段和工作进程等归属范围；这些层级由第 7 课展开。`job-101` 更不是 `nodeid`：前者属于外部异步任务，后者属于测试计划。

权威性也不表示 Runner 比 pytest 更会发现测试。收集项仍由 pytest 产生；Runner 的职责是只接受一次成功收集的结果作为后续计划来源，不让各执行阶段重新定义最初范围。当前源码没有另一份独立的“本来应当存在的测试清单”与 pytest 收集结果对照，因此一个因命名、目标路径或选择条件而未被 pytest 收集的 Case，也不会被 Runner 识别成计划缺失。

### 2. 计划事实与观察事实：Planned Facts and Observed Facts

计划事实描述执行前已经决定的内容，例如：

```text
计划成员：[A, B, C]
计划数量：3
某个成员携带哪些 marker
某个执行池计划接收哪些 nodeid
```

观察事实描述执行期间或执行后真正发生的内容，例如：

```text
A 是否启动、完成、通过或失败
C 是否留下可用的 Case 观察记录
Case C 是否发送 POST 和三次 GET
某个执行池的原始 pytest 退出码是什么
```

二者的生命周期不同。计划事实先于执行形成，执行失败不能倒过来删除计划成员；观察事实必须由实际执行和实际产物提供，计划中有 C 也不能补写成“C 已执行”。

这种区分让“未观察到”保持为未知或缺失，而不是被改写为零、通过或不存在问题。

### 3. 完整性基线与稳定分母：Completeness Baseline and Stable Denominator

稳定分母是在比较计划与观察时不随观察缺失而缩小的基线。对本课的三个 Case：

```text
planned_count = |P| = 3
observed_count = 2
```

若业务需要表达“观察覆盖比例”，概念上的分母必须是计划数 3，而不能把已经观察到的 2 同时当成分子和分母。于是只能得到 `2/3`，不能得到伪造的 `2/2`。

当前实现没有在第 5 课这条链上直接产出一个 `2/3` 指标。它采取更保守的动作：可选质量扩展（Quality）启用且归并真正运行时，后续归并组件将 `expected_case_count=3` 与合并得到的独立 Case 执行数量比较；若实际为 2，就记录 `expected_case_count_mismatch` 完整性错误。

因此，“稳定分母”在当前实现中的落点是计划基线和后续指标的可信度门槛，不能扩大为“所有质量指标已直接以收集 Case 数为分母”。各指标如何选择自己的业务语义分母，留到第 17～20 课解释。

---

## 完整运行过程

先用一张数据流图把三个用途放在同一条链上：

```mermaid
flowchart TD
    A[目标路径 + 选择参数] -->|Runner 发起权威预收集| B[pytest collect-only]
    B -->|退出码 0 且 nodeid 无重复| C[权威 Case 集合 P<br/>实现为有序元组]
    B -->|退出码非 0 或收集异常| X[停止，不进入 Case 执行]
    C -->|明确 nodeid| D[后续调度与执行]
    C -->|完整列表与数量| E[runner-execution.v1]
    C -->|仅传 len(P)，且 Quality 启用| F[后续完整性参照]
    D --> G[实际 Case 观察事实]
    F --> H{观察数量是否等于计划数量}
    G --> H
    H -->|否| I[记录完整性错误]
    H -->|是| J[只通过数量关卡]
```

图中的每条边承担不同责任：

1. **A → B：选择范围进入收集。** `test_path` 和 `-k`、`-m`、`--ignore`、`--deselect` 等选择参数先交给权威预收集。报告输出和并发等已识别的执行参数不会被误当成选择条件。
2. **B → C：只有成功收集才能建立计划。** pytest 退出码必须为 0，收集到的 `nodeid` 还必须无重复。退出码 5 表示没有可执行 Case；其他收集错误和重复 `nodeid` 同样阻止计划进入执行。
3. **C → D：集合成为执行输入。** Runner 从同一集合派生明确的 `nodeid` 序列，再交给后续调度与执行。本课只确认输入来源，具体分池和先后顺序留到第 6 课。
4. **C → E：计划成为审计事实。** 正常的非 `collect-only` 流程在执行后写出顶层计划数量、完整 `nodeid` 列表和各池计划。
5. **C → F：计划数量成为完整性参照。** 这条边有两个条件：Quality 已启用且其最终化链能够运行。当前只传数量，不传完整计划成员表。
6. **D → G：执行产生观察事实。** 计划只规定输入；实际是否启动、完成、写出 Case 分片，要由运行事实说明。
7. **F、G → H：期望与观察相互核对。** 数量不一致是错误；数量一致只说明通过这一道数量关卡，不等于其他完整性、来源或业务校验全部通过。具体归并规则由后续课程展开。

下面沿真实控制流逐步展开。

### 第一步：先分离选择参数与执行参数

Runner 首先处理调用者传入的 pytest 参数。`-k`、`-m`、`--ignore`、`--ignore-glob` 和 `--deselect` 会进入 `collection_args`，因为它们会改变“哪些 Case 属于本次计划”。`--junitxml`、`--alluredir`、`-n` 和 `--dist` 等进入 `execution_args`，因为它们描述怎样执行或输出。

不能识别的插件参数会同时传给收集和执行。Runner 没有尝试重新实现 pytest 的完整参数语义，因此插件仍可能影响两个阶段；这也是权威 Case 集合只对当前参数与插件环境有效的原因。

### 第二步：执行权威预收集

收集函数构造的核心参数是：

```text
--collect-only -q -o addopts= <collection_args> <test_path>
```

`--collect-only` 要求 pytest 建立收集项但不执行测试函数；`-o addopts=` 清空配置文件中的默认 `addopts`，避免默认执行输出参数混入这次受控预收集。调用者明确传入的收集参数仍然保留。

收集期间，插件在 `pytest_collection_finish` 获得最终的 `session.items`。它按顺序提取每个 item 的 `nodeid` 与所有可见 marker，并形成 `CollectedTestCase`。

### 第三步：拒绝不唯一或失败的收集

同一个 `nodeid` 若第二次出现，收集器会记录重复项，结束后抛出 `RuntimeError`；它不是静默保留一份然后继续。

pytest 收集退出码也决定能否继续：

| 收集结果 | Runner 动作 | 能否形成可执行计划 |
| --- | --- | --- |
| 退出码 0，且无重复 `nodeid` | 接受 `CollectionResult.cases` | 能 |
| 退出码 5 | 报告没有收集到可执行 Case，停止 | 不能进入执行 |
| 其他非零退出码 | 输出收集诊断，停止 | 不能进入执行 |
| 收集函数抛异常 | 报告权威收集失败，返回非零 | 不能进入执行 |

因此，空集合不是“零个 Case 全部通过”。它保留 pytest 的“未收集到测试”退出事实。

### 第四步：从同一计划派生执行输入

收集成功后，Runner 只从 `collection.cases` 生成一次 `case_nodeids`。后续无论采用一个执行阶段，还是先把集合派生成不同执行池，成员来源都必须回到这一个序列。具体 marker 规则、分池条件和执行顺序留到第 6 课。

每个非空池调用 pytest 时，参数开头是该池明确的 `nodeid`：

```text
<nodeid A> <nodeid B> ... <execution_args>
```

这证明当前 Runner 没有把一个宽泛目录分别交给多个 Worker，让它们各自决定原始范围。它不证明 pytest-xdist 内部不再收集，也不证明任意第三方插件绝不会改变执行期行为。

### 第五步：分别保留计划事实和执行事实

每个执行阶段的结果保存自己的 `planned_nodeids`、状态、原始 pytest 退出码、开始与结束时间、异常类型和报告路径。Runner 随后尝试写出 `runner-execution.v1`，其中顶层继续保存最初的 `planned_case_count` 与完整 `planned_nodeids`。

该产物描述“Runner 计划了什么，以及每个池发生了什么”。它在执行后写出，不会反向驱动已经发生的本次执行；若写入失败，也不能把缺失产物解释成执行成功的证据。

### 第六步：把计划数量交给可选的 Quality 完整性链

Quality 开启时，Runner 在最终化阶段传入：

```text
expected_case_count = len(case_nodeids)
```

即使某个后续执行阶段没有运行，这个值也仍来自最初计划，不会缩减成“实际写出了多少结果”。后续归并组件把它与观察到的独立 Case 执行数量比较；数量不等就记录 ERROR。至于独立执行身份怎样形成、分片怎样归并，将在身份课和账本课解释。

Quality 关闭时不执行这项数量核对。Quality 初始化或最终化失败时也可能没有完整性产物。因此，“没有看到 `expected_case_count_mismatch`”只有在确认 Quality 已启用、归并已运行且产物可信时，才具有相应含义。观察路径怎样隔离自身异常属于第 9～11 课。

---

## 正常路径

回到 `P=[A,B,C]`。本例增加以下条件：

```text
权威预收集退出码：0
重复 nodeid：无
执行方式：本例只使用一个执行阶段，不增加并发变量
三个 Case 的 pytest 执行均完成
Quality：已启用并完成最终化
合并后的独立 Case 执行数量：3
其他完整性校验：均未产生 ERROR 或 WARN
```

### 1. 计划形成

T1 的收集器按顺序得到 A、B、C，并把文件级 `serial` marker 记录到三个成员。T2 形成：

```text
CollectionResult.cases = (A, B, C)
case_nodeids = (A.nodeid, B.nodeid, C.nodeid)
planned_case_count = 3
```

这里的 A、B、C 是为了缩短展示使用的别名；真实计划保存完整 `nodeid`，不会只保存字母或测试函数的显示名称。

### 2. 计划成为执行输入

本例不增加并行变量，Runner 把三个完整 `nodeid` 一次性交给一个执行阶段。pytest 的实际输入范围来自 `case_nodeids`，而不是执行结束后才从报告里猜测。

Case C 在一次示例执行中可以取得 `task_id="job-101"`，随后发送一个创建请求和多次状态查询。无论它发送四次还是更多次 HTTP 请求，它在权威 Case 集合中仍是一个 pytest 收集项 C。

### 3. 计划成为执行产物的一部分

执行完成后，`reports/execution-result.json` 的相关内容可表示为：

```json
{
  "schema_version": "runner-execution.v1",
  "planned_case_count": 3,
  "planned_nodeids": ["A 的完整 nodeid", "B 的完整 nodeid", "C 的完整 nodeid"],
  "collection_exit_code": 0,
  "pool_results": [
    {
      "stage_id": "serial-pool",
      "planned_nodeids": ["A 的完整 nodeid", "B 的完整 nodeid", "C 的完整 nodeid"],
      "status": "COMPLETED",
      "raw_pytest_exit_code": 0
    }
  ],
  "final_exit_code": 0
}
```

这是按本例输入删减后的结构展示；真实产物还包含测试目标、选择参数、时间、异常类型和报告路径等字段。`COMPLETED` 表示执行阶段调用完成，不应脱离 `raw_pytest_exit_code` 推导每个 Case 的业务结果。

### 4. 计划成为完整性参照

Quality 最终化收到 `expected_case_count=3`。合并结果也有三个独立 Case 执行，因此数量关卡满足：

```text
expected_case_count = 3
observed invocation count = 3
3 == 3 → 不产生 expected_case_count_mismatch
```

由于本例还明确给出其他完整性校验均未产生问题，质量事实集合才能得到 `complete`。如果只知道 `3 == 3`，最多只能说“数量关卡通过”，不能单独推出来源、成员或内容都正确。

正常路径最终形成三类不同输出：

| 输出层次 | 本例结果 | 它回答的问题 |
| --- | --- | --- |
| 权威 Case 集合 | A、B、C，共 3 项 | 本次原本要执行谁 |
| 执行事实 | 一个池完成，原始退出码为 0 | pytest 执行阶段发生了什么 |
| 质量完整性 | 预期 3，观察到 3，且其他关卡通过 | 当前质量事实能否作为可信来源 |

这三类输出互有关联，但不能互相覆盖。

---

## 复杂路径

### 路径一：选择条件没有收集到任何 Case

只改变一个输入：`-k` 条件与任何测试名都不匹配。pytest 返回“没有收集到测试”的退出码 5。

完整推导是：

```text
输入范围确定
→ 权威预收集得到 0 项并返回退出码 5
→ Runner 不生成执行池调用
→ 原始最终退出码保持为 5
→ pool_results 为空
```

在非 `collect-only` 模式下，Runner 会尝试把收集退出码 5、计划数 0 和空池结果写入执行产物；在用户明确要求 `collect-only` 时，当前分支直接返回，不写这份执行产物。两种情况下都没有 Case 执行，不能把“没有失败结果”写成“全部通过”。

还有一种更隐蔽的情况：作者心中预期 A、B、C，但由于目标路径写窄、测试命名不符合 pytest 发现规则或选择条件排除了 C，pytest 以退出码 0 只交出 A、B。当前 Runner 会把 `[A,B]` 接受为本次权威 Case 集合，因为它没有独立的“应有 A、B、C”清单。这里的“权威”表示后续组件只能以同一份已接受集合为准，不表示这份集合已经与人的全部测试意图核对。

### 路径二：计划三个，但只合并到两个 Case 的观察事实

恢复计划 `P=[A,B,C]`，只改变执行后的观察输入：假定 A、B 有可合并的观察记录，而 C 没有留下可用记录。缺失原因可能是 Case 未启动、进程终止、观察路径失败、分片丢失或其他基础设施问题；仅凭“少一份记录”不能擅自选择其中一种解释。

```text
T2：planned_case_count = 3
T3：执行输入仍包含 A、B、C
T5：只合并到 A、B 两个独立 Case 执行
判断：2 != 3
输出：expected_case_count_mismatch，severity=ERROR
结果：质量完整性失败
```

关键状态变化发生在最后两步：观察数量从未知变为 2，但计划数量仍保持 3。后续归并组件（Aggregator）不会把期望数改写为 2，也不会把 C 的缺失补成通过。

当前实现不会继续把这组事实包装成看似正常的质量结果，也不会直接生成概念上的 `2/3` 覆盖率；本课能确定的是数量不完整。后续指标怎样拒绝不可信来源，将在质量账本与指标课程说明。

当前数量核对还有一个边界：若计划是 `[A,B,C]`，观察结果却来自 A、B 和计划外的 D，数量仍是 3。这一道关卡不会发现成员替换，因为当前 Quality 链只接收计划数量，不接收完整 `planned_nodeids`。Runner 保存了完整成员表，不等于归并组件已经使用它逐项对账。

### 路径三：让 Worker 各自决定范围的反事实

最后回到开篇的反事实方案：Worker α 发现 `[A,B]`，Worker β 发现 `[B,C]`。它会产生两种错误诱因：

1. 直接相加得到 4，把 B 重复计数。
2. 去重得到 3，却没有独立基线证明还有没有双方都漏掉的成员。

当前 Runner 通过先形成 `P`、再传递明确 `nodeid`，消除了“由各 Worker 的局部结果定义原始计划”这一步。但执行阶段仍依赖 pytest/xdist 和插件正确处理这些输入；权威 Case 集合不能替外部执行器保证每个 Case 最终被调度和完成。

---

## 对应的框架实现

前面的计划模型建立后，再把它映射到源码。以下片段均是按本课问题截取的真实控制流；为控制篇幅省略了打印、测试报告参数处理和与当前判断无关的分支，未改变输入、状态变化或异常方向。

### 1. Runner 先收集，再读取计划

```python
# run_orchestration/runner.py
argument_plan = pytest_execution.partition_pytest_args(
    extra_pytest_args or ()
)

collection = pytest_execution.collect_test_case_items(
    test_path,
    argument_plan.collection_args,
)

# 只有收集退出码为 0 的分支才会继续到这里。
cases = collection.cases
case_nodeids = tuple(case.nodeid for case in cases)
```

输入是目标路径与调用者参数。`partition_pytest_args()` 先把选择条件放到收集阶段；`collect_test_case_items()` 输出 `CollectionResult`。只有原始收集退出码为 0，状态才从“范围尚未确定”变为“持有 `cases` 与 `case_nodeids`”。任何非零退出码都会在读取执行计划前停止；非 `collect-only` 分支还会先尝试记录空的执行阶段结果，因此这里没有把那段失败处理改写成一次简单的直接返回。

后续调度接收的输入来自同一个 `cases`。本课不讨论执行池细节，只确认调度不能另造一份原始 Case 范围。

### 2. 收集器保存 pytest 的最终收集项并拒绝重复

```python
# run_orchestration/pytest_execution.py
args = [
    "--collect-only",
    "-q",
    "-o",
    "addopts=",
    *pytest_args,
    str(test_path),
]
exit_code = int(pytest.main(args, plugins=[collector]))

if collector.duplicate_nodeids:
    duplicates = ", ".join(sorted(collector.duplicate_nodeids))
    raise RuntimeError(
        f"pytest collection produced duplicate nodeids: {duplicates}"
    )
```

```python
# run_orchestration/pytest_execution.py
def pytest_collection_finish(self, session: pytest.Session) -> None:
    seen: set[str] = set()
    for item in session.items:
        if item.nodeid in seen:
            self.duplicate_nodeids.add(item.nodeid)
            continue
        seen.add(item.nodeid)
        self.items.append(
            CollectedTestCase(
                nodeid=item.nodeid,
                markers=frozenset(
                    marker.name for marker in item.iter_markers()
                ),
            )
        )
```

第一段的输出是 pytest 原始退出码以及收集器内的项目。第二段把每个首次出现的 `nodeid` 与 marker 转成计划成员。若同一 `nodeid` 再次出现，第二份不会悄悄进入计划；收集调用结束后会抛异常，Runner 将这次权威收集视为失败。

当前仓库测试覆盖了函数、类和文件级 marker 的读取，但没有直接命中重复 `nodeid` 的拒绝分支。重复拒绝是当前源码行为，不能描述成已有测试已经证明的回归承诺。

### 3. 后续执行接收明确的计划成员

```python
# run_orchestration/pytest_execution.py
nodeids = tuple(planned_nodeids)
args = [*nodeids, *effective_args]
exit_code = run_pytest(args)
```

`execute_pool()` 的输入是 Runner 从权威 Case 集合派生的 `planned_nodeids`。非空执行阶段把这些地址放到 pytest 参数前面；输出继续保留同一组计划成员和原始退出码。至于一个还是两个执行池、哪些成员进入哪个池以及具体 Worker 怎样分配，都不改变这里的输入来源，细节留到第 6 课。

### 4. 同一计划同时进入执行产物与完整性参照

```python
# run_orchestration/runner.py
payload = {
    "schema_version": artifacts.RUNNER_EXECUTION_SCHEMA_VERSION,
    "planned_case_count": len(collection.cases),
    "planned_nodeids": [case.nodeid for case in collection.cases],
    "collection_exit_code": collection.raw_pytest_exit_code,
    "pool_results": [
        {
            "stage_id": result.stage_id,
            "planned_nodeids": list(result.planned_nodeids),
            "status": result.status.value,
            "raw_pytest_exit_code": result.raw_pytest_exit_code,
        }
        for result in pool_results
    ],
    "final_exit_code": final_exit_code,
}
```

这段产物保留完整计划成员与数量。写入采用临时文件加 `os.replace()`，避免把半写入 JSON 当作完整执行事实；磁盘错误仍可能导致产物不可用。

Quality 最终化则只接收计划数量：

```python
# run_orchestration/runner.py
quality_run_lifecycle.finalize(
    start_time=quality_start_time,
    expected_case_count=len(case_nodeids),
    pool_results=tuple(pool_results),
    status=final_status,
)
```

后续归并代码会把该数量与合并到的独立 Case 执行数比较：不相等时新增 `expected_case_count_mismatch` 错误，并把质量完整性判为失败。它没有接收 `planned_nodeids`，所以当前实现的是数量核对，不是成员逐项核对。本课到此只确认计划数的去向；独立执行身份、分片与完整归并链留到后续课程。

### 5. 实现与测试证据索引

本课的重要陈述可追溯到以下位置：

- `run_master.py:22-25`、`run_orchestration/cli.py:11-19`：标准命令行入口怎样进入 `runner.run()`。
- `run_orchestration/pytest_execution.py:39-67,104-181`：选择参数与执行参数分离，以及权威 `collect-only` 调用。
- `master_service.py:15-18`、`run_orchestration/pytest_execution.py:78-83,195-212`：计划成员、冻结结果、`nodeid`、marker 与重复检测。
- `run_orchestration/runner.py:34-79`：收集失败门禁、计划读取与 `collect-only` 分支。
- `run_orchestration/runner.py:99-178`、`run_orchestration/pytest_execution.py:319-367`：明确 `nodeid` 怎样成为执行阶段的 pytest 输入；具体分池行为留到下一课。
- `run_orchestration/runner.py:204-210,244-297`、`run_orchestration/artifacts.py:13-65`：计划数量进入 Quality，以及完整计划写入原子执行产物。
- `run_orchestration/quality_lifecycle.py:92-119`、`run_orchestration/quality_pipeline.py:18-35`、`run_orchestration/quality_fact_merge_stage.py:14-34`：Quality 启用时 `expected_case_count` 的传递链。
- `quality/aggregator.py:378-398,500-525,538-544`：独立执行数量核对、归并清单记录与 ERROR 对完整性状态的影响。
- `quality/metrics/sources.py:49-78`、`quality/metrics/builder.py:43-93`：后续指标拒绝完整性失败的来源，并使用自身语义集合计算；这组证据只用于限定“稳定分母”的范围。
- `module/smoke/test_图片生成异步调用.py:22-70`：本课三个真实 Case、文件级 `serial` marker，以及 Case C 对标准异步创建与轮询入口的调用。
- `Jenkinsfile:16-25,142-174,182-216`：独立 Collect Smoke 与 Real Smoke 的条件入口；Real Smoke 进入 `run_master.py` 并显式开启 Quality，但默认参数并不自动执行真实 Smoke。

相关测试分别覆盖：选择条件进入权威收集、各层 marker 被保存、计划成员成为显式 pytest 输入、`collect-only` 不进入执行、空选择保留退出码 5、执行产物保存计划数量，以及 Quality 归并请求接收期望数量。测试证明这些预期受到当前测试套件覆盖；当前行为的首要依据仍是实现，测试也不能证明任意插件环境或外部服务结果。

---

## 能够保证什么

通过标准 `run_master.py → run_orchestration.runner.run()` 入口，且权威预收集成功时，当前实现能够保证：

1. Runner 在执行池运行前，先以本次目标路径和收集参数形成一份有序计划。
2. 每个被接受的计划成员保存 pytest `nodeid` 与可见 marker；重复 `nodeid` 不会被静默接受为成功计划。
3. 后续池级执行输入从同一计划派生，并以明确 `nodeid` 传给 pytest。
4. 正常的非 `collect-only` 流程会尝试在 `runner-execution.v1` 中保存完整成员、计划数量与执行阶段事实。
5. Quality 启用且最终化成功时，最初计划数量会成为后续归并的 `expected_case_count`；观察数量不足或过多都会产生 ERROR，而不会收缩期望值来制造完整。

这些保证描述 Runner 和可选 Quality 链的控制流，不是对每个业务 Case 结果的保证。

---

## 保证成立的前提

上述保证依赖以下前提：

- 调用经过标准 Runner。直接执行 `python -m pytest` 不会自动获得 Runner 的权威预收集、执行计划产物和计划数量传递链。
- pytest 能在当前代码、配置、依赖和插件环境中成功完成收集；`nodeid` 必须唯一。
- 调用者把影响范围的已识别选择条件交给 Runner；未知插件参数可能同时影响收集与执行，其语义仍由插件负责。
- 执行期间使用的是这次计划派生的明确 `nodeid`。若外部代码绕过 Runner 或另起一次无关 pytest 调用，本课结论不能自动扩展过去。
- 需要完整性核对时，Quality 必须启用，运行身份与输出目录必须可用，最终化与归并组件必须真正运行并产出可信清单。
- 后续指标消费还依赖其他来源校验；计划数量相等不是完整质量链的唯一条件。
- Jenkins 中的 Real Smoke 确实调用标准 Runner 并开启 Quality，但该阶段受 `RUN_REAL_SMOKE` 控制，默认值为关闭；独立的 Collect Smoke 只是另一轮预览，不会把它的收集结果直接传给后续 Real Smoke。
- 外部异步图像服务若在 Case 执行时返回 `job-101`，该值只属于那次业务调用。仓库不能在收集阶段保证外部任务标识、终态或图像结果。

---

## 不能保证什么

权威 Case 集合不能保证：

1. **收集到就一定执行。** 后续池可能未启动、被终止或因基础设施错误停止。
2. **执行了就一定完成或通过。** 计划事实不覆盖 pytest 原始结果和业务断言。
3. **每个 Case 都会留下完整观察事实。** 观察钩子、进程或存储失败仍可能产生诊断缺口。
4. **一次预收集消除了 pytest 的所有后续收集。** 每个非空池仍会调用 pytest；xdist 可以在 Worker 内部执行自己的收集与分配。
5. **明确 `nodeid` 能约束任意插件的一切行为。** Runner 提供计划输入，但第三方插件和 pytest/xdist 仍拥有其运行语义。
6. **本次计划是仓库永久 Case 总表。** 目标、选择参数、代码、配置、插件或环境改变后，原计划不能继续充当新运行的权威事实。
7. **计划数量相等就证明成员完整。** 当前 Aggregator 只收到 `expected_case_count`；少一个计划成员并混入一个计划外成员时，纯数量检查可能无法识别替换。
8. **所有后续指标直接以权威 Case 数为分母。** 当前指标使用各自的业务语义集合；本课计划数只提供完整性基线和可信度门槛。
9. **Quality 关闭时仍会完成同样的数量核对。** 关闭状态不运行归并组件；缺少质量告警不能解释为事实完整。
10. **`job-101` 可以代表 Case 身份。** 它是外部业务任务标识；本课的计划成员键是 pytest `nodeid`，完整跨层身份还未建立。
11. **收集过程对任意测试仓库绝对没有副作用。** `collect-only` 不执行测试函数，但 pytest 仍会导入模块并运行收集钩子；这些代码是否有副作用取决于实际实现。
12. **Runner 已掌握具体 Worker 分配表。** 当前 Runner 保存池级计划；启用 xdist 后，具体成员怎样分给具体 Worker 由 pytest-xdist 管理。
13. **pytest 未发现的预期 Case 一定会被报告为缺失。** 当前没有独立应有清单；若 pytest 成功收集到的范围本身就少了成员，Runner 会以实际收集结果建立计划。

缺失必须保留为缺失。没有 Case 观察记录、没有可信质量清单或没有执行产物时，只能说明相应事实不可用，不能写成零次失败、全部成功或没有问题。

---

## 与下一课的关系

本课建立了一个不会随执行结果缩小的起点：

```text
目标路径与选择条件
→ pytest 权威预收集
→ P = [A, B, C]
→ 明确 nodeid 成为执行输入
→ 完整计划写入执行事实
→ 计划数量在 Quality 启用时成为完整性参照
```

因此，当只观察到 A 和 B 时，框架至少保留了“原本计划三个”的依据；它不会因为 C 没有留下结果，就把分母改写成两个。与此同时，本课也明确了当前限制：Quality 只消费计划数量，不做完整计划成员逐项核对；后续指标可以受到这道完整性门槛保护，但不会自动把 Case 计划数用于所有业务公式。

计划固定后，下一个自然问题是：A、B、C 应当怎样进入并行池或串行池？即使它们由不同 Worker 执行、完成顺序发生变化，为什么计划成员和事实归属仍不应改变？第 6 课将解释并行池与串行池只改变调度，不改变权威 Case 集合。
