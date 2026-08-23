# 第 3 课：Runner 权威执行与稳定身份

> 建议时长：75 分钟
>
> 本课范围：权威 Case 集合、并行/串行分池、pytest 退出事实、五级身份、worker 原始账页与 JUnit 身份。
>
> 本课不展开：Aggregator 的完整归并规则、Metrics、Flaky、Allure 内部生命周期和完整源码调用链。

## 1. 先说结论

Runner 的核心价值不是“把 pytest 跑得更快”，而是在并发开始前冻结一份权威执行计划，并在执行结束后保留可审计的池级事实。

稳定身份的核心价值也不是“给每个产物都加满五个 ID”，而是为分散事实建立一套五级坐标。不同产物只携带自身职责所需的身份子集：Case/Request 记录可以获得五级上下文，JUnit testcase 只有 Case 与逻辑参数实例（invocation）两级，`execution-result.json` 当前甚至没有 `run_id`。因此，下面五个问题属于整套坐标模型，不要求每一条事实单独全部回答：

1. 它属于哪一轮运行？
2. 它来自哪个执行阶段？
3. 它由哪个 worker 写出？
4. 它对应哪个长期稳定的测试定义？
5. 它对应本轮哪个逻辑参数实例？

整节课只围绕一条主线展开：

~~~text
先确定“谁应该执行”
-> 再保证并行池和串行池不重、不漏
-> 执行时保留每个池的原始 pytest 结果
-> 用稳定身份标记分散的 Case 事实
-> 为后续 JUnit 与 Aggregator 的有限数量/身份对账提供可靠输入
~~~

需要先明确一个能力边界：Runner 能证明计划是什么、计划怎样分池、已返回的池给出了什么结果，但当前实现还不能证明最终实际集合等于计划集合。所缺证据与当前有限对账能力在第 8.4 节再展开。

## 2. 学完本课应建立的认识

本课不要求记忆文件或函数。重点是理解以下设计判断：

- 为什么并发执行前必须先形成唯一的权威 Case 集合。
- 为什么 `|P| + |S| = |C|` 不能代替真正的集合守恒检查。
- 为什么 pytest 原始退出码、Runner 项目级退出码和质量诊断不是同一层事实。
- `run_id`、`execution_id`、`worker_id`、`case_id`、`invocation_id` 分别解决什么归属问题。
- 参数化用例为什么既需要稳定的 `case_id`，又需要标识本轮逻辑参数实例的 `invocation_id`。
- 当前实现在哪些情况下停止后续执行、降级质量观察或保留原始错误。

## 3. 从一个并发场景开始

假设一次回归测试收集到 8 个 pytest 收集项：

- 6 个收集项可以并行执行。
- 2 个收集项带有 `serial` 标记，只能串行执行。
- 并行池由两个 xdist worker 执行。
- 其中一个测试定义经过参数化展开，形成两个带不同参数后缀的收集项。
- 并行池出现普通断言失败，串行池仍然需要执行。

先解释四个初学者容易陌生的词：

| 术语 | 本课中的含义 |
| --- | --- |
| 测试定义 | 源码中的测试函数或测试方法，例如 `test_chat` |
| pytest 收集项 | pytest 应用参数化和选择规则后得到的可执行项；每个收集项具有完整 nodeid |
| Case 调用 | 本轮实际执行一个 pytest 收集项所形成的用例调用；它可能产生 setup、call、teardown 多条 phase 事实 |
| nodeid | pytest 为一个可执行测试项生成的定位字符串，例如 `module/test_chat.py::test_chat[model-a]` |
| marker | pytest 附加在测试项上的标记；本框架用 `serial` 标记决定是否进入串行池 |
| xdist worker | pytest-xdist 启动的执行进程，常见身份为 `gw0`、`gw1` |
| JUnit | pytest 输出的 XML 测试证据，记录 Case 名称、状态、耗时和错误等信息 |
| JSONL | 一行保存一条 JSON 记录的文件格式；不同 worker 用它分别写原始 Case 账页 |
| Integrity | Quality 对缺失、冲突或采集失败等可信度问题留下的诊断记录 |

下文未加限定的“Case”指本轮的一次 Case 调用；“测试定义”指源码中的稳定定义；`C`、`P`、`S` 的直接元素则是参数展开后的 pytest 收集项 nodeid。三者不能混用。

如果没有权威计划和稳定身份，执行后会立即出现四类问题：

### 3.1 计划问题

到底是哪 8 个收集项被纳入计划？某个计划项没有产物，是没有被选中、被分池时遗漏，还是执行时丢失？

### 3.2 归属问题

`gw0` 和 `gw1` 都写出 Case 记录时，怎样判断它们属于同一轮运行、同一个执行池，而不是上一次残留的数据？

### 3.3 参数化问题

`test_chat[model-a]` 和 `test_chat[model-b]` 是同一个测试定义的两个调用。长期比较时它们应该共享稳定 Case 身份；本轮执行时又必须能够区分。

### 3.4 结论问题

并行池失败、串行池通过、Quality 采集失败时，谁有权决定最终退出码？如果诊断模块覆盖 pytest 结果，执行事实就会被观察系统篡改。

因此，真正的问题不是“怎样启动两个 pytest 进程”，而是：

> 怎样让执行计划、运行过程和机器产物共享同一套可复核的事实坐标？

## 4. 第一性原理：并发正确性先需要一个不变的参照物

### 4.1 任务本质

并发把一份工作拆到多个进程中，但验证并发是否正确，必须依赖拆分前的一份不变参照物。

在本框架中，这份参照物就是 pytest 权威收集得到的计划集合 `C`。Runner 不是自己扫描文件、猜测测试函数，也不是让每个执行池自行发现目标。它先调用 pytest 的正式收集机制，再把收集结果中的 nodeid 作为后续执行的显式目标。

因果链如下：

~~~text
选择条件、marker 和 pytest 插件可能改变实际收集结果
-> 目录扫描或函数名推断无法代表 pytest 真正会执行的集合
-> Runner 必须先取得 pytest 的权威收集结果 C
-> 并行池 P 与串行池 S 只能从 C 中派生
-> 后续执行以已冻结的 nodeid 作为显式计划目标
-> 产物才具备回到同一份计划核验的坐标；当前实现尚未闭合 nodeid 集合对账
~~~

这里冻结的是 Runner 的**计划目标**，不是对执行阶段最终集合的绝对保证。把计划 nodeid 交给 pytest，不等于已经证明最终实际集合与 `C` 一致；当前实现尚未闭合该证明，所缺证据与能力边界留到第 8.4 节说明。

### 4.2 TOC：本课最大的理解约束

本课最大的理解约束不是并行语法，而是学习者容易把“执行了相同数量的测试”误认为“执行了正确的测试集合”。

数量只回答“有几个”，集合才回答“是哪几个”。只要这个约束没有解除，讲 worker、JSONL 或 JUnit 都只是增加字段，无法证明执行正确性。

所以本课的引入顺序是：

~~~text
权威集合
-> 计划分池集合守恒
-> 池级执行事实
-> 五级身份
-> worker 账页与 JUnit 证据
~~~

## 5. 整体实现思路

当前实现可以压缩成三个相互衔接的责任层：

| 层次 | 核心输入 | 核心责任 | 输出 |
| --- | --- | --- | --- |
| 权威计划层 | 测试目标、pytest 选择参数 | 用 pytest 正式收集 Case，形成并校验 `C`、`P`、`S` | 权威 nodeid 与分池计划 |
| 执行事实层 | 计划 nodeid、执行参数 | 把计划 nodeid 作为显式目标交给 pytest，保留池状态、原始退出码和项目级退出码 | 到达写入点且原子提交成功时产生本轮 `execution-result.json`；pytest 阶段可能产生 JUnit |
| 身份观察层 | 父运行身份、阶段身份、worker 与测试项 | 建立五级身份，写出按 execution/worker 隔离的 Case 分片，并给 JUnit 增加身份 | Case JSONL、JUnit identity properties |

`execution-result.json` 不是每次调用 Runner 都必然产生。参数规划异常、`collect-only`、写入点之前的外层异常，以及真正逃出 `pytest.main()` 的 `KeyboardInterrupt` / `SystemExit` 都可能不写本轮文件；Runner 启动时也不会先删除默认路径中的旧文件。因此，“路径上存在文件”不能单独证明它属于本轮。

~~~mermaid
flowchart TD
    A[测试目标与选择参数] --> B[pytest 权威收集]
    B --> C{收集退出码是否为 0}
    C -- 否 --> D[保留收集退出事实并停止]
    C -- 是 --> E[权威集合 C]
    E --> F[按 serial marker 分池]
    F --> G[并行池 P]
    F --> H[串行池 S]
    G --> I[池级 pytest 执行]
    H --> I
    I --> J[原始池退出码与项目级 final_exit_code]

    I --> K[run_id / execution_id]
    K --> L[worker_id]
    L --> M[case_id / invocation_id]
    M --> N[worker Case JSONL]
    M --> O[JUnit identity properties]

    E --> P[后续 expected Case count]
    J --> Q[Runner 执行事实]
    N --> R[后续 Aggregator 对账输入]
    O --> R
    P --> R
~~~

图中的执行事实和质量事实是并行关系：Quality 观察 pytest，但无权改写 pytest 的原始退出事实。

## 6. 模块级精简教学代码

下面是一份**教学重构代码**，不是仓库中的完整文件，也不能直接替换当前实现。

原实现需要解决的约束包括：pytest 参数在收集期与执行期的边界、权威收集、marker 分池、并行与串行阶段编排、原始退出码保留、Quality 可选接入、xdist 身份传播、Case 分片和 JUnit 身份。

这份教学代码：

- 保留了参数规划与收集异常、权威集合、计划分池集合守恒、池级状态、Runner 总出口、中断状态、五级身份、写盘隔离、JSONL/JUnit 输出和下游边界。
- 把实际分散在 Runner、调度器、pytest 执行封装和 Quality pytest 插件中的职责放到一个教学骨架里。
- 省略了 CLI、Allure、路径后缀处理、完整异常文本、Pydantic 校验、原子写实现、Semantic 采集和失败分类。
- 省略内容不会改变“谁拥有执行事实”“身份怎样形成”以及“正常与失败怎样退出”等架构事实。

真实职责分散在多个模块；这里把它们重组为同一个代码块中的两条转换主线，以便连续看到 `pytest_main()` 之后由 pytest 驱动的插件与 hook 链。这不是仓库中的单一文件。

~~~python
# 教学重构：只保留“执行事实”和“身份观察”两条主线。

TERMINATING_EXIT_CODES = {2, 3, 4, 5}


@dataclass(frozen=True)
class PoolExecutionResult:
    stage_id: str
    planned_nodeids: tuple[str, ...]
    status: PoolExecutionStatus
    raw_pytest_exit_code: int | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    exception_type: str | None = None
    junit_path: Path | None = None


# 主线一：collect -> C -> P/S -> execute -> merge exit -> Runner 产物
def build_execution_plan(cases, serial_marker="serial"):
    C = tuple(case.nodeid for case in cases)
    if len(C) != len(set(C)):
        raise ValueError("duplicate nodeid")
    P = tuple(case.nodeid for case in cases if serial_marker not in case.markers)
    S = tuple(case.nodeid for case in cases if serial_marker in case.markers)
    if set(P) & set(S):
        raise ValueError("parallel and serial pools overlap")
    if set(P) | set(S) != set(C):
        raise ValueError("pool union differs from authoritative plan")
    return C, P, S


def execute_pool(stage_id, planned_nodeids, pytest_args):
    nodeids = tuple(planned_nodeids)
    junit_path = extract_junit_path(pytest_args)
    started_at = now_utc()
    try:
        raw_exit = int(pytest_main([*nodeids, *pytest_args]))
    except Exception as error:
        return PoolExecutionResult(
            stage_id, nodeids, PoolExecutionStatus.ERROR,
            started_at=started_at,
            completed_at=now_utc(),
            exception_type=type(error).__name__,
            junit_path=junit_path,
        )
    return PoolExecutionResult(
        stage_id, nodeids, PoolExecutionStatus.COMPLETED, raw_exit,
        started_at=started_at,
        completed_at=now_utc(),
        junit_path=junit_path,
    )


def execute_stage(quality, stage_id, planned_nodeids, pytest_args):
    if not planned_nodeids:
        return PoolExecutionResult(
            stage_id, (), PoolExecutionStatus.NOT_RUN,
            junit_path=extract_junit_path(pytest_args),
        )
    with quality.stage_environment(stage_id):
        return execute_pool(stage_id, planned_nodeids, pytest_args)


def run(test_target, pytest_args, *, numprocesses=None, dist=None):
    try:
        argument_plan = partition_pytest_args(pytest_args)
    except ValueError:
        return PYTEST_EXIT_USAGE_ERROR

    try:
        collection = pytest_collect_only(test_target, argument_plan.collection_args)
    except Exception:
        return PYTEST_EXIT_TESTS_FAILED

    if collection.raw_pytest_exit_code != PYTEST_EXIT_OK:
        final_exit = collection.raw_pytest_exit_code
        if not argument_plan.collect_only:
            final_exit = write_runner_fact_or_fail(
                collection=collection, pool_results=(),
                final_exit_code=final_exit,
            )
        return final_exit

    C, P, S = build_execution_plan(collection.cases)
    if argument_plan.collect_only:
        return PYTEST_EXIT_OK

    quality = create_quality_lifecycle()  # 关闭或配置失败时得到 Noop
    start_time = now_utc()
    quality.prepare(start_time)           # 内部 fail-open
    pool_results = []
    run_status = FINISHED

    try:
        if not numprocesses:
            args = quality.ensure_junit_args(argument_plan.execution_args)
            # 真实非并行分支在 with 内追加：即使环境恢复失败，
            # 已完成的池结果仍保留给 finally 中的 finalize()。
            with quality.stage_environment("serial-pool"):
                pool_results.append(execute_pool("serial-pool", C, args))
        else:
            parallel_args = build_parallel_args(
                quality.ensure_junit_args(argument_plan.execution_args),
                numprocesses=numprocesses, dist=dist, junit_suffix="parallel",
            )
            parallel_result = execute_stage(
                quality, "parallel-pool", P, parallel_args
            )
            pool_results.append(parallel_result)

            must_stop = (
                parallel_result.status is PoolExecutionStatus.ERROR
                or parallel_result.raw_pytest_exit_code in TERMINATING_EXIT_CODES
            )
            serial_args = build_serial_args(
                quality.ensure_junit_args(argument_plan.execution_args),
                junit_suffix="serial",
            )
            serial_result = (
                PoolExecutionResult(
                    "serial-pool", tuple(S), PoolExecutionStatus.NOT_RUN,
                    junit_path=extract_junit_path(serial_args),
                )
                if must_stop or not S
                else execute_stage(quality, "serial-pool", S, serial_args)
            )
            pool_results.append(serial_result)

        final_exit = _final_exit_code(pool_results)
        if any(result.status is PoolExecutionStatus.ERROR for result in pool_results):
            run_status = PARTIAL
        return write_runner_fact_or_fail(
            collection=collection, pool_results=tuple(pool_results),
            final_exit_code=final_exit,
        )
    except (KeyboardInterrupt, SystemExit):
        run_status = INTERRUPTED
        raise
    except Exception:
        run_status = PARTIAL
        return PYTEST_EXIT_TESTS_FAILED
    finally:
        quality.finalize(
            start_time=start_time, expected_case_count=len(C),
            pool_results=tuple(pool_results),
            status=run_status,
        )


# 主线二：run/execution/worker -> Case identity -> JSONL/JUnit
@pytest.hookimpl(optionalhook=True)
def pytest_configure_node(node):
    state = get_state(node.config)
    if state is None or not state.config.enabled:
        return
    node.workerinput["quality_runtime"] = {
        "enabled": True,
        "run_id": state.config.run_id,
        "execution_id": state.config.execution_id,
        "output_dir": str(state.config.output_dir),
    }


def pytest_configure(config):  # runtime 插件
    if config.option.collectonly:
        return
    try:
        runtime_config = resolve_runtime_config(config)
    except Exception as error:
        warn("quality collection disabled", error)
        return

    state = PluginState(config=runtime_config)
    config._quality_plugin_state = state
    if not runtime_config.enabled or is_xdist_controller(config):
        return

    run_context = QualityRunContext(
        run_id=runtime_config.run_id,
        execution_id=runtime_config.execution_id,
        worker_id=xdist_worker_id_or_master(config),
        output_dir=runtime_config.output_dir,
    )
    try:
        state.run_context = run_context
        state.run_token = set_run_context(run_context)
        state.collector = configure_collector(run_context)
    except Exception as error:
        rollback_plugin_state(state)
        warn("quality collector initialization failed", error)


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_protocol(item, nextitem):
    state = get_state(item.config)
    collector = state.collector if state is not None else None
    if collector is None:
        yield
        return

    token = build_and_bind_case_context_fail_open(item, collector)
    try:
        yield  # pytest 执行 setup -> call -> teardown
    finally:
        if token is not None:
            reset_case_context(token)


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_logreport(report):
    record_phase_fail_open(report)  # Case JSONL + JUnit identity；异常不阻止 pytest
~~~

后续代码块优先从这份主骨架抽取；对于骨架中抽象掉的关键判断和真实接入边界，使用保持相同核心语义的最小源码摘录补充证明。局部片段可以省略不影响当前解释的外围上下文，但不替代模块级骨架，也不构成第二套实现。

为避免形成两套对象模型，骨架直接使用真实名称 `PoolExecutionResult`、`PoolExecutionStatus`、`QualityRunContext` 和 `extract_junit_path`；`C`、`P`、`S` 是权威集合、并行子集和串行子集的数学简称。`rollback_plugin_state()`、`build_and_bind_case_context_fail_open()` 与 `record_phase_fail_open()` 是教学辅助名，用来收束真实实现中的捕获、回滚和 Integrity 记录；它们不是仓库新增的 API，具体分支在第 10～11 节就地展开。

### 6.1 这段代码的输入与输出

输入不是“一个测试目录”这么简单，而是：

- 测试目标。
- 影响选择结果的 pytest 参数。
- 只影响执行方式的 pytest 参数。
- 是否启用并行。
- Quality 是否启用及其父运行身份。

输出也不是一个整数，而是多类不同所有权的事实：

- pytest 权威收集结果。
- Runner 的计划、池状态和 `final_exit_code`。
- pytest 的池级原始退出码。
- Quality 成功启用、插件被加载且 collector 初始化成功时，产生 worker Case 分片。
- Quality 成功启用时，Runner 只会识别并保留规范形式 `--junitxml PATH` 或 `--junitxml=PATH`；未识别到时注入默认 JUnit 路径，pytest 再把 Case 身份写入该 JUnit 证据。
- 提供给后续 Aggregator 的预期 Case 数量与 execution 集合。

主骨架把 JUnit 参数处理收束为三个真实 helper。Enabled 生命周期先保留可识别的已有路径，否则注入默认路径；`extract_junit_path()` 的识别范围如下：

~~~python
def ensure_junit_args(self, pytest_args):
    args = list(pytest_args)
    if extract_junit_path(args) is not None:
        return args
    return args + [
        f"--junitxml={self._config.output_dir / 'junit' / 'quality.xml'}"
    ]


def extract_junit_path(pytest_args):
    for index, arg in enumerate(pytest_args):
        if arg == "--junitxml" and index + 1 < len(pytest_args):
            return resolve_report_path(pytest_args[index + 1])
        if arg.startswith("--junitxml="):
            return resolve_report_path(arg.split("=", 1)[1])
    return None
~~~

进入双池模式后，并行池改写 JUnit 后缀并追加 xdist 参数；串行池先移除 `-n`、`--numprocesses`、`--dist`，再改写后缀：

~~~python
parallel_args = replace_junitxml_suffix(list(pytest_args), "parallel")
parallel_args.extend(["-n", numprocesses])
if dist:
    parallel_args.extend(["--dist", dist])

serial_args = replace_junitxml_suffix(
    remove_xdist_args(list(pytest_args)), "serial"
)
~~~

这里的“保留已有 JUnit 参数”不能泛化到 pytest 的全部等价拼写。参数规划器虽然接受 `--junit-xml`，但当前 `extract_junit_path()` 与分池后缀处理只识别 `--junitxml`。调用方使用 `--junit-xml` 时，Enabled 生命周期还会追加默认的 `--junitxml`，原别名路径也不会被正确追加 parallel/serial 后缀。

### 6.2 为什么把实际代码重组为这条主线

真实实现分文件是为了维护边界，教学重组则是为了看清转换过程：

~~~text
pytest 选择语义
-> 权威 Case 集合
-> 分池计划
-> Runner 注入 JUnit 参数与阶段环境
-> pytest_main 重新加载 module/conftest.py
-> 轻量 Quality 插件按开关注册 runtime hooks
-> xdist controller 转发 run / execution 配置
-> worker 建立 run / execution / worker 上下文
-> pytest_runtest_protocol 建立 Case 调用上下文
-> pytest_runtest_logreport 观察真实 phase
-> 安全写入 Case JSONL，并把身份交给 JUnit
~~~

如果按文件逐个介绍，学习者会先看到大量辅助函数，却看不到“输入怎样变成可复核输出”。教学重构只改变展示方式，不改变真实职责。

当前这条自动加载链成立，是因为真实业务测试位于 `module` 下，而 `module/conftest.py` 声明了 `quality.pytest_plugin`。若 Runner 改为执行不受该 conftest 管辖的自定义测试路径，不能仅凭 `QUALITY_ENABLE=1` 推断插件一定被加载。

~~~python
# module/conftest.py
pytest_plugins = ("quality.pytest_plugin",)

# 轻量插件被 pytest 加载后，才可能注册 runtime hooks。
def pytest_configure(config):
    if config.option.collectonly:
        return
    try:
        enabled = quality_enabled_from_env_or_workerinput(config)
    except Exception as error:
        warn("quality collection disabled", error)
        return
    if enabled:
        runtime = import_module("quality.pytest_plugin_runtime")
        if not config.pluginmanager.has_plugin("quality-runtime"):
            config.pluginmanager.register(runtime, "quality-runtime")
~~~

## 7. 权威集合：先回答“谁应该执行”

### 7.1 权威来源必须是 pytest

当前实现通过 `pytest.main()` 执行一次正式的 `--collect-only`，并由收集插件读取 `session.items`。每个收集项保留：

- 精确 `nodeid`。
- 从函数、类和文件层级继承到的 marker 集合。

主骨架中的 `pytest_collect_only()` 对应下面这段最小源码路径。`-o addopts=` 清空配置中的默认 `addopts`，收集插件只接受 pytest 实际交付的 `session.items`：

~~~python
collector = _CaseCollector()
args = ["--collect-only", "-q", "-o", "addopts=", *pytest_args, str(test_path)]
exit_code = int(pytest.main(args, plugins=[collector]))

# _CaseCollector.pytest_collection_finish 内
seen = set()
for item in session.items:
    if item.nodeid in seen:
        self.duplicate_nodeids.add(item.nodeid)
        continue
    seen.add(item.nodeid)
    self.items.append(CollectedTestCase(
        nodeid=item.nodeid,
        markers=frozenset(marker.name for marker in item.iter_markers()),
    ))

if collector.duplicate_nodeids:
    raise RuntimeError("pytest collection produced duplicate nodeids")
~~~

这意味着 Runner 尊重 pytest 已经形成的选择语义，而不是重新实现一套测试发现器。

选择参数和执行参数还会先被分开：

| 参数类型 | 示例 | 进入权威收集 | 进入实际执行 |
| --- | --- | --- | --- |
| 选择参数 | `-k`、`-m`、`--ignore`、`--deselect` | 是 | Runner 不再传这些选择参数，而是传入计划 nodeid；执行期配置与插件仍会重新加载 |
| 执行参数 | `-n`、`--dist`、`--junitxml`、`--alluredir` | 否 | 是 |
| 未识别的插件参数 | 自定义插件参数 | 为保留插件语义，会同时传递 | 是 |

主骨架中的 `partition_pytest_args()` 由这些分支决定参数去向；带独立值的参数会连同下一项一起加入对应列表：

~~~python
# partition_pytest_args 的循环体；列表游标递增等外围样板省略。
if arg in COLLECT_ONLY_ARGS:
    collect_only = True
elif arg in _SELECTION_OPTIONS_WITH_VALUE:
    pair = [arg, args[index + 1]]
    collection_args.extend(pair)
    selection_args.extend(pair)
elif arg.startswith(_SELECTION_OPTION_PREFIXES):
    collection_args.append(arg)
    selection_args.append(arg)
elif arg in _EXECUTION_OPTIONS_WITH_VALUE:
    execution_args.extend([arg, args[index + 1]])
elif arg.startswith(_EXECUTION_OPTION_PREFIXES) or arg in _EXECUTION_ONLY_FLAGS:
    execution_args.append(arg)
else:
    # 未识别的插件参数同时进入收集期和执行期。
    collection_args.append(arg)
    execution_args.append(arg)
~~~

核心不是机械分类参数，而是先把 Runner 认可的计划目标固定下来。由于收集期清空 configured addopts、执行期重新加载配置，且未知插件参数会同时进入两阶段，这个计划不能自动等同于最终实际集合。逐 nodeid 的执行证据才能闭合该证明，而当前 Aggregator 尚未接收 `planned_nodeids`，只做有限数量/身份核验。

### 7.2 收集失败不能继续分池

权威收集的原始退出码不是 0 时，Runner 不应拿到一个不完整集合后继续运行：

- 退出码 5：没有收集到测试。
- 其他非零：收集错误或 pytest 失败。
- 收集阶段发现重复 nodeid：当前实现直接视为错误。

Runner 在分池前就收口这三类失败：

~~~python
try:
    collection = pytest_collect_only(test_target, argument_plan.collection_args)
except Exception:
    return PYTEST_EXIT_TESTS_FAILED

if collection.raw_pytest_exit_code != PYTEST_EXIT_OK:
    final_exit = collection.raw_pytest_exit_code
    if not argument_plan.collect_only:
        final_exit = write_runner_fact_or_fail(
            planned_nodeids=tuple(case.nodeid for case in collection.cases),
            collection_exit_code=collection.raw_pytest_exit_code,
            pool_results=(),
            final_exit_code=final_exit,
        )
    return final_exit

C = tuple(case.nodeid for case in collection.cases)  # 只在成功后形成权威集合
~~~

没有可信的 `C`，就不存在可信的 `P` 和 `S`。

## 8. 计划分池的集合守恒：不是“数量对上了”就算正确

设：

- `C`：pytest 权威收集的全部 Case nodeid。
- `P`：并行池 nodeid。
- `S`：串行池 nodeid。

正确分池必须同时满足：

~~~text
P ∩ S = ∅
P ∪ S = C
~~~

第一条保证同一 Case 不会同时进入两个池；第二条保证没有 Case 在分池时丢失。

### 8.1 为什么数量相等不够

假设：

~~~text
C = {A, B}
P = {A}
S = {A}
~~~

表面上：

~~~text
|P| + |S| = 2 = |C|
~~~

但真实结果是：

- `A` 重复执行。
- `B` 完全丢失。

数量相等掩盖了两个相反错误。只有 nodeid 集合比较才能发现。

### 8.2 当前实现怎样分池

调度器遍历权威收集结果：

- nodeid 已出现过：拒绝重复计划。
- marker 中包含配置的 `serial`：进入 `S`。
- 其他 Case：进入 `P`。
- 分池结束后再验证交集为空、并集等于权威集合。

第 6 节骨架中的 `build_execution_plan()` 对应真实的 `split_test_cases()`；它把“分类”和“守恒校验”放在同一个边界内：

~~~python
def split_test_cases(cases, serial_marker="serial"):
    seen = set()
    parallel, serial = [], []

    for case in cases:
        if case.nodeid in seen:
            raise ValueError("duplicate nodeid")
        seen.add(case.nodeid)
        target = serial if serial_marker in case.markers else parallel
        target.append(case.nodeid)

    P, S = tuple(parallel), tuple(serial)
    if set(P) & set(S):
        raise ValueError("parallel and serial pools overlap")
    if set(P) | set(S) != seen:
        raise ValueError("pool union differs from authoritative plan")
    return P, S
~~~

这里没有根据文件名、测试类或执行耗时猜测串并行属性。

### 8.3 关闭并行时的真实行为

即使代码已经计算出 `P` 和 `S`，当前 Runner 在没有设置 `numprocesses` 时，会把整个 `C` 作为一次 `serial-pool` 执行，而不是先执行 `P` 再执行 `S`。

~~~python
if not numprocesses:
    serial_args = quality.ensure_junit_args(argument_plan.execution_args)
    with quality.stage_environment("serial-pool"):
        pool_results.append(
            execute_pool("serial-pool", C, serial_args)
        )
~~~

这是一个典型的“代码中存在分池能力，不等于当前执行模式使用了两个池”的例子。

### 8.4 计划分池守恒不等于实际执行集合已对账

当前精确成立的是计划层合同：`P ∩ S = ∅` 且 `P ∪ S = C`。Runner 也会把这些计划 nodeid 显式传给 pytest，但这仍没有形成下面这条闭环：

~~~text
计划集合 C
-> pytest 实际产生的 nodeid 集合 A
-> 按 nodeid 检查 A = C
~~~

`execution-result.json` 保存 `planned_nodeids`；当前 `QualityMergeRequest` 却只接收 `expected_case_count`、`expected_execution_ids`、JUnit 路径等信息，Aggregator 不会拿实际 Case 的 nodeid 与 `planned_nodeids` 做集合比较。因此，若 `C={A,B}`，实际证据却是 `{A,D}`，数量仍为 2，当前预期数量检查可能通过。课程必须把“Runner 已证明分池计划守恒”和“系统尚未证明实际集合等于计划集合”分开。

两个数据合同的字段差异直接显示了当前缺口：Runner 产物保存完整计划，Quality 归并请求只接收数量、execution 和 JUnit，没有 `planned_nodeids` 字段。

~~~python
# _write_execution_result() 构造的 payload；仅列本节相关字段
payload = {
    "planned_nodeids": [case.nodeid for case in collection.cases],
    "pool_results": pool_results,
    "final_exit_code": final_exit_code,
}

@dataclass(frozen=True)
class QualityMergeRequest:
    run_id: str
    output_dir: Path
    expected_execution_ids: tuple[str, ...] = ()
    expected_case_count: int | None = None
    junit_files: tuple[Path, ...] = ()
~~~

`pool_results` 不会原样交给 Aggregator。Quality 生命周期先排除 `NOT_RUN`，再把真正执行过的池转换成预期 execution 与 JUnit 输入：

~~~python
executed = tuple(
    result
    for result in pool_results
    if result.status.value != "NOT_RUN"
)
finalize_quality_run(
    self._config,
    start_time=start_time,
    expected_execution_ids=tuple(result.stage_id for result in executed),
    expected_case_count=expected_case_count,
    junit_files=tuple(result.junit_path for result in executed),
    status=RunStatus(status.value),
)
~~~

因此参数桥是：排除未运行池 → 用已执行池的 `stage_id` 形成 `expected_execution_ids` → 收集这些池的 `junit_path` → 交给 Aggregator。被跳过的池不会进入预期 execution 和 JUnit 文件集合；但 `expected_case_count` 仍来自完整的 `C`，而且请求仍未携带计划 nodeid 集合。

## 9. 池级执行：保留原始事实，再形成项目级结论

### 9.1 PoolExecutionResult 记录什么

一个已经形成的 `PoolExecutionResult` 在内存中至少记录：

- `stage_id`：当前实现为 `parallel-pool` 或 `serial-pool`。
- `planned_nodeids`：这个池原计划执行的精确 Case。
- `status`：`NOT_RUN`、`COMPLETED` 或 `ERROR`。
- `raw_pytest_exit_code`：pytest 实际返回的原始退出码；未运行或基础设施异常时可能为空。
- 开始与完成时间。
- 异常类型。
- 对应 JUnit 路径。

这里要区分两种失败：

| 情况 | Pool status | raw pytest exit code | 含义 |
| --- | --- | --- | --- |
| pytest 正常结束，但测试断言失败 | `COMPLETED` | `1` | pytest 完成了执行，测试失败 |
| 调用 pytest 的基础设施发生异常 | `ERROR` | `None` | 没有获得正常 pytest 退出事实 |

这两个结果来自 `execute_pool()` 的两个互斥出口；`raw_pytest_exit_code` 只在 `pytest_main()` 正常返回后写入：

~~~python
started_at = now_utc()
try:
    raw_exit = int(pytest_main([*planned_nodeids, *pytest_args]))
    return PoolExecutionResult(
        stage_id=stage_id,
        planned_nodeids=tuple(planned_nodeids),
        status=PoolExecutionStatus.COMPLETED,
        raw_pytest_exit_code=raw_exit,
        started_at=started_at,
        completed_at=now_utc(),
        junit_path=junit_path,
    )
except Exception as error:
    return PoolExecutionResult(
        stage_id=stage_id,
        planned_nodeids=tuple(planned_nodeids),
        status=PoolExecutionStatus.ERROR,
        raw_pytest_exit_code=None,
        started_at=started_at,
        completed_at=now_utc(),
        exception_type=type(error).__name__,
        junit_path=junit_path,
    )
~~~

不能把二者都压成一个 `failed=true`，否则后续无法判断是产品测试失败还是执行器故障。

### 9.2 普通测试失败为什么不停止串行池

并行池返回退出码 1，表示测试已正常运行但存在失败。当前 Runner 仍会继续执行串行池，让本轮尽量获得完整测试事实。

只有以下情况会停止后续串行池：

- 并行池状态为 `ERROR`。
- 原始退出码为 2、3、4、5，即中断、pytest 内部错误、用法错误或没有测试。

停止条件在主骨架中是一个精确布尔表达式；退出码 1 不在 `TERMINATING_EXIT_CODES` 中：

~~~python
TERMINATING_EXIT_CODES = {2, 3, 4, 5}

must_stop = (
    parallel_result.status is PoolExecutionStatus.ERROR
    or parallel_result.raw_pytest_exit_code in TERMINATING_EXIT_CODES
)
if must_stop:
    serial_result = not_run_pool("serial-pool", S, serial_args)
elif S:
    serial_result = execute_pool("serial-pool", S, serial_args)
else:
    serial_result = not_run_pool("serial-pool", S, serial_args)
~~~

这里的退出码 2 通常表示 pytest 已经把用户中断转换成正常返回的 raw exit。此时 `pytest.main()` 返回 2，池状态仍是 `COMPLETED`，Runner 记录并返回 2，同时不再运行后续池。它不会进入 Runner 外层的 `except (KeyboardInterrupt, SystemExit)`。

只有 `KeyboardInterrupt` 或 `SystemExit` 真正逃出 `pytest.main()` 时，Runner 才把运行生命周期标为 `INTERRUPTED` 并重新抛出；该路径在本轮 `execution-result.json` 写入点之前退出，因此不能假定已经写出本轮池级文件。

~~~python
except (KeyboardInterrupt, SystemExit):
    run_status = INTERRUPTED
    raise
~~~

因果关系是：

~~~text
断言失败只否定测试结果
-> 不必否定 Runner 继续执行其他独立 Case 的能力

基础设施错误或终止性退出否定继续执行的前提
-> 后续池标记 NOT_RUN
-> 不能伪装成已执行或通过
~~~

### 9.3 原始退出码与 final_exit_code 的所有权

| 事实 | 所有者 | 当前实现的处理 |
| --- | --- | --- |
| 单个 pytest 进程的原始退出码 | pytest | Runner 原样记录在池结果中 |
| 哪些 Case 属于哪个池 | Runner | 来自权威集合与分池计划 |
| 项目级 `final_exit_code` | Runner | 按池结果合并；终止性退出优先，任一测试失败最终为 1 |
| Quality 完整性或诊断 | Quality | 可以降级或失败，但不覆盖 pytest/Runner 执行事实 |

主骨架中的 `_final_exit_code()` 对应下面的真实 Runner helper。池级 `ERROR` 先映射为 1；其余 raw exit 再按终止性退出、测试失败、其他非零、全零的顺序合并：

~~~python
def _final_exit_code(pool_results):
    if any(
        result.status is PoolExecutionStatus.ERROR
        for result in pool_results
    ):
        return PYTEST_EXIT_TESTS_FAILED
    exit_codes = [
        result.raw_pytest_exit_code
        for result in pool_results
        if result.raw_pytest_exit_code is not None
    ]
    if not exit_codes:
        return PYTEST_EXIT_OK
    for exit_code in exit_codes:
        if exit_code in TERMINATING_EXIT_CODES:
            return exit_code
    if any(exit_code == PYTEST_EXIT_TESTS_FAILED for exit_code in exit_codes):
        return PYTEST_EXIT_TESTS_FAILED
    if any(exit_code != PYTEST_EXIT_OK for exit_code in exit_codes):
        return PYTEST_EXIT_TESTS_FAILED
    return PYTEST_EXIT_OK
~~~

只有流程到达 `_write_execution_result()`，Runner 才尝试提交本轮 `execution-result.json`。参数规划异常、`collect-only`、写入点之前的外层异常或逃逸控制异常不会提交本轮文件，默认路径上的旧文件还可能继续存在。

已经到达写入点、但原子提交失败时，当前实现不会制造“成功”：

- 原本是终止性 pytest 退出码时，保留该退出码。
- 原本为 0 或普通非终止结果时，返回 1，表明 Runner 执行事实未能可靠提交。

对应的写入失败出口没有吞掉提交失败：

~~~python
try:
    write_execution_result_atomic(payload)
except Exception:
    if final_exit_code in TERMINATING_EXIT_CODES:
        return final_exit_code
    return PYTEST_EXIT_TESTS_FAILED
return final_exit_code
~~~

这不是 Quality 改写 pytest，而是 Runner 对自己拥有的执行产物负责。

## 10. 五级身份：一套坐标，不是每个产物都有五个字段

权威集合解决计划归属，但并发产物还需要稳定坐标。当前核心身份模型分为五级；Case / Request 记录使用完整上下文，JUnit 与 Runner 产物只携带各自需要的子集：

| 身份 | 稳定范围 | 回答的问题 | 当前来源 |
| --- | --- | --- | --- |
| `run_id` | 一轮完整运行 | 事实属于哪一轮运行 | Runner 父进程生成或读取配置，并向各池传播 |
| `execution_id` | 一轮运行中的执行阶段 | 事实来自并行池、串行池还是手工 pytest | 当前 Runner 使用 `parallel-pool`、`serial-pool`；直接运行默认为 `manual-pytest` |
| `worker_id` | 一个具体 pytest 执行进程 | 哪个 worker 写出了事实 | xdist 使用 `gw0` 等；非 xdist 使用 `master` |
| `case_id` | 跨运行稳定的测试定义 | 长期比较时这是哪个 Case | nodeid 规范化后移除末尾参数部分 |
| `invocation_id` | 本轮逻辑参数实例 | 本轮是哪个测试定义与规范化参数组合 | `run_id + case_id + param_hash` 的稳定摘要 |

### 10.1 run_id：先把多个阶段锁进同一轮运行

Quality 开启时，Runner 在父进程只确定一次 `run_id`：

- Jenkins 同时提供 `JOB_NAME` 和 `BUILD_NUMBER` 时，把它们纳入运行身份。
- 本地运行使用 UTC 时间与随机 UUID 片段形成身份。

运行身份只在父配置解析时补建一次；两个 Jenkins 字段必须同时存在才进入构造函数：

~~~python
def new_parent_run_id():
    job_name = os.environ.get("JOB_NAME")
    build_number = os.environ.get("BUILD_NUMBER")
    if job_name and build_number:
        return build_run_id(job_name=job_name, build_number=build_number)
    return build_run_id()

# create_quality_run_lifecycle() 先处理关闭分支：
if not preview.enabled:
    return NoopQualityRunLifecycle()

# resolve_parent_quality_config() 只在启用分支补建身份：
run_id = configured.run_id or new_parent_run_id()
~~~

同一轮的并行池和串行池共享这个 `run_id`。如果两个池各自生成一个运行身份，后续就会被误认为两轮独立测试。

Quality 关闭时使用 Noop 生命周期，不应因为 Runner 执行而创建新的 Quality 身份或质量产物。

### 10.2 execution_id：区分同一轮中的执行阶段

当前 Runner 真实传入的是：

~~~text
parallel-pool
serial-pool
~~~

这两个字符串由 Runner 直接作为阶段环境的 `execution_id` 传入，而不是调用 `build_execution_id()` 生成：

~~~python
with quality.stage_environment("parallel-pool"):
    parallel_result = execute_pool("parallel-pool", P, parallel_args)

with quality.stage_environment("serial-pool"):
    serial_result = execute_pool("serial-pool", S, serial_args)

# 不经过 Runner、环境中没有 execution_id 时：
execution_id = loaded.execution_id or "manual-pytest"
~~~

`stage_environment()` 不是只有函数名的一跳。Quality 开启时，它把父进程的运行身份和当前阶段身份写入 pytest 将要读取的环境变量，并在阶段退出时逐项恢复原值：

~~~python
@contextmanager
def quality_stage_environment(quality_config, execution_id):
    if not quality_config.enabled:
        yield
        return

    values = {
        QUALITY_ENABLE_ENV: "1",
        QUALITY_RUN_ID_ENV: str(quality_config.run_id),
        QUALITY_EXECUTION_ID_ENV: execution_id,
        QUALITY_OUTPUT_DIR_ENV: str(quality_config.output_dir),
    }
    previous = {name: os.environ.get(name) for name in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
~~~

这补齐了父进程到 pytest 的代码桥：`quality_config.run_id → QUALITY_RUN_ID_ENV`，当前池的 `execution_id → QUALITY_EXECUTION_ID_ENV`。随后 runtime 插件从环境读取它们；若启用 xdist，再由 controller 通过 `workerinput` 转交各 worker。恢复旧值同样重要，否则下一池或同进程后续 pytest 调用可能继承错误身份。

仓库中虽然存在 `build_execution_id(stage_name, index)`，但当前 Runner 主调用链没有用它生成 `parallel-pool-1` 或 `serial-pool-1`。因此课程不能把“方法存在”描述成“当前 Runner 正在使用”。

直接运行 pytest 而不经过 Runner 时，Quality 插件当前使用 `manual-pytest` 作为默认 execution 身份。

### 10.3 worker_id：让并发写入物理隔离

在 xdist 模式下：

- controller 负责把 `run_id`、`execution_id` 和配置传给 worker。
- controller 自己不创建 Case collector，避免重复写一份 master 分片。
- 每个 worker 使用自己的 `worker_id`，例如 `gw0`、`gw1`。

controller 与 worker 的分支，以及物理分片命名，可以在同一条代码链中看到：

~~~python
# worker/controller 共用的 pytest_configure：controller 在这里停止初始化 collector
if is_xdist_controller(config):
    return

# controller 的 pytest_configure_node：向每个 worker 转发父级配置
node.workerinput["quality_runtime"] = {
    "enabled": True,
    "run_id": state.config.run_id,
    "execution_id": state.config.execution_id,
    "output_dir": str(state.config.output_dir),
}

# worker 再次执行 pytest_configure；这里只列身份与 collector 分支。
run_context = QualityRunContext(
    run_id=runtime_config.run_id,
    execution_id=runtime_config.execution_id,
    worker_id=xdist_worker_id_or_master(config),
    output_dir=runtime_config.output_dir,
)
try:
    state.run_context = run_context
    state.run_token = set_run_context(run_context)
    state.collector = configure_collector(run_context)
except Exception as error:
    if state.run_token is not None:
        reset_run_context(state.run_token)
        state.run_token = None
    reset_collector()
    state.run_context = None
    state.collector = None
    _write_warning(
        config,
        f"quality collector initialization failed: {type(error).__name__}: {error}",
    )

# pytest_unconfigure 最终使用 token 恢复进入插件前的上下文。
if state.run_token is not None:
    reset_run_context(state.run_token)
~~~

真实异常分支还会回滚已经绑定的 runtime hooks；上面只保留本课所需的 RunContext 与 collector 边界。关键点是先用 `set_run_context()` 把 `QualityRunContext` 绑定到 `ContextVar`，Request/runtime hooks 才能取得同一组 run、execution、worker 身份。配置解析失败会停用采集并告警；绑定或 collector 初始化失败会回滚已绑定状态并告警，而不是中止 pytest。

Case 分片文件名包含：

~~~text
cases-{execution_id}-{worker_id}.jsonl
~~~

例如：

~~~text
cases-parallel-pool-gw0.jsonl
cases-parallel-pool-gw1.jsonl
cases-serial-pool-master.jsonl
~~~

这解决的是并发写入隔离和来源定位，不等于已经证明每个 worker 的所有预期分片都存在。当前后续 Aggregator 只明确检查预期 execution 是否至少存在 Case 分片，不能把它夸大为完整 worker 清单验证。

### 10.4 case_id：稳定测试定义

考虑两个 nodeid：

~~~text
module/test_chat.py::test_chat[model-a]
module/test_chat.py::test_chat[model-b]
~~~

规范化后，它们共享：

~~~text
case_id = module/test_chat.py::test_chat
~~~

主骨架中的 `normalize_nodeid()` 对应下面的最小源码摘录。它从字符串末尾反向配平方括号，因此参数 ID 内还有方括号时，也只移除最外层的末尾参数部分：

~~~python
def _find_parameter_start(nodeid):
    depth = 0
    for index in range(len(nodeid) - 1, -1, -1):
        if nodeid[index] == "]":
            depth += 1
        elif nodeid[index] == "[":
            depth -= 1
            if depth == 0:
                return index
    return None


normalized = _WHITESPACE_PATTERN.sub(" ", nodeid.replace("\\", "/")).strip()
parameter_start = _find_parameter_start(normalized) if normalized.endswith("]") else None
stable_nodeid = (
    normalized[:parameter_start].rstrip()
    if parameter_start is not None
    else normalized
)
case_id = stable_nodeid
~~~

当前实现还会把路径分隔符统一为 `/`、压缩多余空白，并正确处理参数 ID 中的嵌套方括号。

`case_id` 有意忽略参数实例，因为它用于表达长期稳定的测试定义。如果把 `[model-a]` 永久焊进 Case 身份，参数展示名的小变化就可能把同一测试误判为新 Case。

这里的“稳定”以 nodeid 的测试路径和定义名保持稳定为前提。移动测试文件、重命名测试函数或改变 pytest 根路径仍可能改变 `case_id`；框架不会把代码重构前后的两个定义自动猜成同一个 Case。

### 10.5 invocation_id：本轮逻辑参数实例

只用 `case_id` 会把同一轮中的两个参数实例合并。因此插件还会构造参数视图并计算 `param_hash`，再生成：

~~~text
invocation_id = hash(run_id, case_id, param_hash)
~~~

代码先对参数视图做脱敏和规范化，再计算截断摘要；`invocation_id` 的输入中没有 execution、worker 或物理执行次数：

~~~python
parameter_view = None if callspec is None else {
    "parameter_id": normalized.parameter_id,
    "params": callspec.params,
}
param_hash = hashlib.sha256(
    canonicalize_for_hash(parameter_view).encode("utf-8")
).hexdigest()[:16]
parts = (run_id, case_id, param_hash)
digest = hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()[:24]
invocation_id = f"inv-{digest}"
~~~

由此得到两个性质：

- 同一轮、同一测试定义、同一脱敏与规范化后的参数视图会得到相同 invocation 身份。
- `run_id`、`case_id` 或 `param_hash` 变化时会重新计算 invocation 身份；原始参数只有在脱敏、规范化后的 `param_hash` 发生变化时，才会进入这次重新计算。

`param_hash` 和 `invocation_id` 都使用截断摘要，因此这里表达的是工程上的稳定归属规则，不是数学上的绝对无碰撞保证。不能把“原始参数不同”直接写成“invocation_id 必然不同”。

`invocation_id` 不包含 `execution_id`、`worker_id` 或物理执行次数。若同一轮中同一测试定义与同一规范化参数被重复执行，它会复用同一个 `invocation_id`，不能用来区分“第一次执行”和“第二次执行”。

因此 `invocation_id` 不是跨运行长期稳定键，也不是物理执行次数键。跨运行比较使用 `case_id`；本轮逻辑参数实例归属使用 `invocation_id`。

## 11. 身份怎样穿过父进程、worker 和 Case

~~~mermaid
sequenceDiagram
    participant R as Runner 父进程
    participant P as parallel-pool pytest controller
    participant Q as Quality 插件与 runtime hooks
    participant W as worker gw0
    participant C as pytest Case hooks
    participant O as JSONL / JUnit

    R->>R: 确定 run_id 并注入默认 JUnit 参数
    R->>P: stage_environment + pytest_main(计划 nodeid)
    P->>Q: module/conftest 加载轻量插件
    Q->>Q: QUALITY_ENABLE 开启后注册 runtime hooks
    Q->>W: pytest_configure_node 转发 run / execution 配置
    Note over P: controller 不创建 Case 分片
    W->>Q: worker 再加载插件并建立 RunContext
    Q->>C: pytest_runtest_protocol 建立 CaseContext
    C->>C: pytest 执行 setup / call / teardown
    C->>O: pytest_runtest_logreport 安全写 Case JSONL
    C->>O: user_properties 进入已配置的 JUnit
~~~

### 11.1 RunContext 与 CaseContext

当前实现把身份拆成两层上下文：

- `QualityRunContext`：`run_id`、`execution_id`、`worker_id` 和输出目录。
- `QualityCaseContext`：`case_id`、`invocation_id`、原始 nodeid 和 `param_hash`。

它们通过 `ContextVar` 保存。可以先把 `ContextVar` 理解为“随当前执行上下文传递的变量”，它比一个全局变量更适合区分并发执行上下文。

Case 上下文只包住当前 `pytest_runtest_protocol` 调用，并在 `finally` 中恢复；这正是“当前执行上下文”的代码边界：

~~~python
token = None
try:
    case_context = _build_case_context(item, collector.run_context.run_id)
    token = set_case_context(case_context)
except Exception as error:
    collector.capture_integrity(
        source="pytest_plugin",
        code="case_context_build_failed",
        message=f"{type(error).__name__}: {error}",
        related_id=None,
        severity=IssueSeverity.ERROR,
    )

try:
    yield  # pytest 执行 setup -> call -> teardown
finally:
    if token is not None:
        reset_case_context(token)
~~~

但它不是无限传播保证：裸线程切换可能没有自动携带当前上下文。身份字段设计正确，不代表所有自建并发边界都已经正确传播身份。

### 11.2 CaseResult 保留 pytest phase

pytest 对一个测试调用可能产生：

~~~text
setup -> call -> teardown
~~~

当前插件对真实出现的 phase 分别写 `CaseResult`，每条记录共享同一个 `invocation_id`，同时保留自己的 `raw_status`。主骨架的 `record_phase_fail_open()` 对应下面的真实安全边界：状态转换、JUnit 属性和 Case 写入在同一个 `try` 内，失败时记录 Integrity，不把观察异常抛回 pytest。

~~~python
collector = _active_collector()
if collector is None or report.when not in {"setup", "call", "teardown"}:
    return

case_context = get_case_context()
if case_context is None:
    collector.capture_integrity(
        source="pytest_plugin",
        code="case_context_build_failed",
        message=f"case context missing for {report.when} report",
        related_id=None,
        severity=IssueSeverity.ERROR,
    )
    return

try:
    end_time = datetime.now(UTC)
    duration_ms = max(float(report.duration) * 1000, 0.0)
    status = _case_status(report)
    result = CaseResult(
        run_id=collector.run_context.run_id,
        execution_id=collector.run_context.execution_id,
        worker_id=collector.run_context.worker_id,
        case_id=case_context.case_id,
        invocation_id=case_context.invocation_id,
        nodeid=case_context.nodeid,
        param_hash=case_context.param_hash,
        phase=CasePhase(report.when),
        raw_status=status,
        final_status=status,
        duration_ms=duration_ms,
        start_time=end_time - timedelta(milliseconds=duration_ms),
        end_time=end_time,
    )
    _add_junit_identity_properties(
        report, case_context.case_id, case_context.invocation_id
    )
    collector.record_case(result)
except Exception as error:
    collector.capture_integrity(
        source="pytest_plugin",
        code="case_write_failed",
        message=f"{type(error).__name__}: {error}",
        related_id=case_context.invocation_id,
        severity=IssueSeverity.ERROR,
    )
~~~

如果 setup 失败，pytest 可能根本没有 call 报告。当前实现不会为了让数据看起来完整而伪造一个 call 记录；它只记录真实出现的 setup 和 teardown 事实。

这体现了一条重要原则：

> 缺失的执行阶段不能用虚构事实补齐。

### 11.3 JUnit 为什么只写两级身份

当前 JUnit properties 写入：

- `quality_case_id`
- `quality_invocation_id`

两个属性来自同一个 `CaseContext`，并由 pytest 的 JUnit 插件写入已配置的 XML：

~~~python
add_junit_property(report, "quality_case_id", case_context.case_id)
add_junit_property(
    report,
    "quality_invocation_id",
    case_context.invocation_id,
)
~~~

JUnit properties 同时携带这两个值，但当前 Aggregator 实际以 `invocation_id` 建立索引，并只按 `invocation_id` 查找 Case 分片；它没有校验 JUnit 与 CaseResult 两侧的 `case_id` 是否一致。因此，`case_id` 已写入 JUnit 不等于当前对账把它作为一致性条件。`run_id`、`execution_id` 和 `worker_id` 仍保存在 Quality Case 记录及分片来源中。

~~~python
state.junit_evidence[evidence.invocation_id] = evidence

for invocation_id, evidence in state.junit_evidence.items():
    cases = [
        case for case in state.cases.values()
        if case.invocation_id == invocation_id
    ]
    # 当前对账继续比较状态，但没有比较 evidence.case_id 与 case.case_id。
~~~

不能因为五级身份模型存在，就声称 JUnit 单个 testcase 已经携带全部五级身份。

## 12. 从身份到原始账页

当前主要事实流及其能力边界可以表示为：

~~~text
pytest 权威收集的 C
-> Runner 派生 P / S
-> stage_environment 注入 run_id / execution_id
-> xdist 给 worker 分配 worker_id
-> pytest 插件为测试项生成 case_id / invocation_id
-> setup / call / teardown 写入 worker Case JSONL
-> JUnit testcase 写入 case_id / invocation_id
-> 仅当流程到达写入点且提交成功，Runner 才保存本轮 planned_nodeids、池结果与 final_exit_code
-> Quality Enabled 且 finalize 到达时，向 Aggregator 提供预期数量、execution 与 JUnit
-> Aggregator 对分片与 JUnit 做有限数量/身份对账，不对 planned_nodeids 做端到端集合对账
~~~

这里有三种不同的“账”：

| 账目 | 回答的问题 | 当前所有者 |
| --- | --- | --- |
| 权威计划 | 本轮应该执行谁 | Runner，来源是 pytest 权威收集 |
| 执行账 | 每个池是否运行、pytest 怎样退出 | pytest 原始事实 + Runner 编排事实 |
| 观察账 | 每个 worker 观察到了哪些 Case phase | Quality collector |

三者需要身份关联，但不能互相替代。

worker 原始账页的文件隔离与 fail-open 写入在 collector 中同时收口：

~~~python
# QualityCollector.__init__ 内
suffix = f"{run_context.execution_id}-{run_context.worker_id}.jsonl"
self.paths = ShardPaths(
    cases=run_context.output_dir / "shards" / f"cases-{suffix}",
    integrity=run_context.output_dir / "shards" / f"integrity-{suffix}",
)

def record_case(self, result):
    try:
        append_jsonl(self.paths.cases, result)
        return True
    except Exception as error:
        self.capture_integrity(
            source="collector",
            code="case_write_failed",
            related_id=result.invocation_id,
            message=str(error),
            severity="ERROR",
        )
        return False
~~~

## 13. 正常终态、失败出口和降级行为

下表中的 Runner 级出口来自同一条外层控制流。参数与收集在 Quality 生命周期之前收口；执行阶段的普通异常返回 1，真正逃逸的控制异常重新抛出；`finalize()` 位于 `finally`，但它自身按 Quality 的 fail-open 合同处理失败：

~~~python
try:
    argument_plan = partition_pytest_args(pytest_args)
except ValueError:
    return PYTEST_EXIT_USAGE_ERROR

try:
    collection = pytest_collect_only(test_target, argument_plan.collection_args)
except Exception:
    return PYTEST_EXIT_TESTS_FAILED

if collection.raw_pytest_exit_code != PYTEST_EXIT_OK:
    final_exit = collection.raw_pytest_exit_code
    if not argument_plan.collect_only:
        final_exit = write_runner_fact_or_fail(
            collection=collection,
            pool_results=(),
            final_exit_code=final_exit,
        )
    return final_exit

C, P, S = build_execution_plan(collection.cases)
if argument_plan.collect_only:
    return PYTEST_EXIT_OK

try:
    # ...执行各池、合并退出码并写 execution-result.json...
    return final_exit
except (KeyboardInterrupt, SystemExit):
    run_status = INTERRUPTED
    raise
except Exception:
    run_status = PARTIAL
    return PYTEST_EXIT_TESTS_FAILED
finally:
    quality.finalize(
        start_time=start_time,
        expected_case_count=len(C),
        pool_results=tuple(pool_results),
        status=run_status,
    )
~~~

| 场景 | 当前行为 | 事实边界 |
| --- | --- | --- |
| 参数计划非法 | Runner 返回 pytest usage error 4 | 不进入权威收集，也不写本轮 `execution-result.json`；路径上可能仍有旧文件 |
| `collect-only` | 完成收集与分池展示后返回 0 | 不创建 Quality 运行，也不写本轮 `execution-result.json` |
| 权威收集成功 | 冻结计划 `C`，再分池 | 后续把计划 nodeid 作为 pytest 显式目标；当前 Aggregator 尚不能证明最终 nodeid 集合等于 `C` |
| 没有收集到测试 | 返回 pytest 原始退出码 5，不执行池 | 不把空集合解释成成功回归 |
| 收集错误 | 返回收集阶段原始非零退出码 | 不在不可信 `C` 上继续执行 |
| 收集产生重复 nodeid | 视为权威收集异常 | 不允许含糊身份进入计划 |
| 关闭并行 | 整个 `C` 一次进入 `serial-pool` | 分池能力存在，但本模式不执行两个池 |
| 并行池普通测试失败 | 记录 raw exit 1，继续串行池 | 尽量保留完整执行事实 |
| 并行池终止性退出 | 串行池为 `NOT_RUN` | 不伪造串行执行结果 |
| 池调用发生异常 | 池状态为 `ERROR`、无 raw exit，项目级返回非零 | 区分执行器故障与测试失败 |
| `stage_environment` 进入或恢复失败 | 异常进入 Runner 外层 `except`，运行状态为 `PARTIAL` 并返回 1 | 不归类为池内 `ERROR`；若尚未到达写入点，不写本轮 `execution-result.json` |
| pytest 捕获用户中断并返回 exit 2 | 池状态为 `COMPLETED`，记录 raw exit 2，Runner 返回 2 | 这是正常 pytest 退出事实，不进入 Runner 的逃逸中断分支 |
| `KeyboardInterrupt` / `SystemExit` 真正逃出 `pytest.main()` | Runner 标记生命周期 `INTERRUPTED` 并重新抛出 | 不静默改写为普通测试失败，也不保证写出本轮 `execution-result.json` |
| Quality 关闭 | 使用 Noop，不创建新的 Quality 身份和产物 | pytest 与 Runner 仍可正常执行 |
| Quality 开启且调用方提供规范 `--junitxml` 参数 | 保留调用方路径，并在分池时添加 parallel/serial 后缀 | 只保证 `--junitxml PATH` 与 `--junitxml=PATH` 两种形式 |
| Quality 开启且调用方未提供当前 helper 可识别的 JUnit 参数 | Enabled 生命周期注入默认 `quality.xml`，分池时改成 parallel/serial 后缀 | “可识别”仅含 `--junitxml` 两种形式；`--junit-xml` 会被漏识别并触发额外默认参数，且原别名路径不会正确加分池后缀 |
| Quality 初始化或最终归并失败 | 告警并 fail-open | 不覆盖 pytest/Runner 结论 |
| Case 分片写入失败 | 尽力记录 Integrity，pytest 结果保持原样 | Integrity 本身也可能写失败，不能保证所有观察故障可见 |
| Runner 执行结果写入失败 | 不返回虚假成功；非终止结果转为 1，终止性 pytest 退出码保留 | Runner 对自身执行事实提交负责 |

Quality 的 `prepare()` 与 `finalize()` 都在各自入口捕获异常并告警；`stage_environment()` 只在构造上下文管理器时捕获异常：

~~~python
def prepare(self, start_time):
    try:
        write_initial_run_record(self._config, start_time)
    except Exception as error:
        _warn("Quality initialization failed open", error)


def stage_environment(self, execution_id):
    try:
        return quality_stage_environment(self._config, execution_id)
    except Exception as error:
        _warn("Quality stage environment failed open", error)
        return nullcontext()

# finalize() 内：先排除未运行池，再形成 Aggregator 输入
executed = tuple(
    result
    for result in pool_results
    if result.status.value != "NOT_RUN"
)
try:
    finalize_quality_run(
        self._config,
        start_time=start_time,
        expected_execution_ids=tuple(result.stage_id for result in executed),
        expected_case_count=expected_case_count,
        junit_files=tuple(result.junit_path for result in executed),
        status=RunStatus(status.value),
    )
except Exception as error:
    _warn("Quality finalization failed open", error)
~~~

上下文管理器真正执行 `__enter__` / `__exit__` 发生在调用方的 `with quality.stage_environment(...)`，不在上面构造期的 `try` 中；因此进入或恢复环境时的逃逸异常仍会进入 Runner 的 `PARTIAL` 出口。

结果追加顺序也属于当前实现边界：关闭并行时，`pool_results.append(...)` 位于 `with` 内，因此 pytest 已返回后即使环境恢复失败，已完成的池结果仍会进入 `finalize()`；双池路径先在 `with` 内取得结果、退出后再追加，若环境恢复失败，该池结果可能尚未进入列表。课程保留这种现状，不把两条路径虚构成完全相同的事务边界。

## 14. 五个容易产生错误结论的混淆点

### 14.1 “收集了 100 个，两个池加起来也是 100 个，所以没问题”

错误原因：数量不能识别重复与遗漏同时发生。

准确结论：必须检查 nodeid 交集为空、并集等于权威集合。

### 14.2 “有 run_id 就能定位所有事实”

错误原因：同一轮中还有多个阶段、多个 worker、多个 Case 和参数调用。

准确结论：五级身份概念分别消除不同层级的歧义，不能互相代替；不同产物只携带职责所需的身份子集。

### 14.3 “case_id 应该区分参数实例”

错误原因：这会破坏跨运行稳定的测试定义身份。

准确结论：`case_id` 表示稳定定义，`invocation_id` 表示本轮逻辑参数实例；后者不区分同一逻辑实例的重复物理执行。

### 14.4 “并行池只要失败就不该再跑串行池”

错误原因：普通测试失败不等于执行基础设施已经失效。

准确结论：raw exit 1 继续串行；终止性退出或 `ERROR` 才停止。

### 14.5 “Quality 记录了 final_status，所以它拥有最终测试结论”

错误原因：Quality 是诊断与证据层，不是第二个 pytest。

准确结论：pytest 拥有原始执行与退出事实，Runner 拥有编排和项目级 `final_exit_code`。Quality 的 prepare、finalize 与 collector 写盘故障按当前安全层降级；若阶段环境上下文本身在进入或恢复时仍有异常逃逸，它进入 Runner 的 `PARTIAL` 总出口，而不是被伪装成 pytest 测试结论或池内 `ERROR`。

## 15. 当前实现能保证什么，不能保证什么

### 15.1 当前能够提供的保证

- 权威计划来自 pytest 的正式收集结果，而不是文件扫描猜测。
- 分池时拒绝重复 nodeid，并显式检查 `P ∩ S = ∅`、`P ∪ S = C`。
- Runner 把冻结后的计划 nodeid 作为各池的显式执行目标。
- 对已经形成的 `PoolExecutionResult`，内存中保留计划、状态、时间、异常类型和原始 pytest 退出码；只有到达写入点且提交成功，才持久化为本轮 `execution-result.json`。
- 同一轮并行池与串行池共享 `run_id`，但拥有不同 `execution_id`。
- xdist worker 使用独立 `worker_id` 和独立 Case 分片。
- 参数化调用共享稳定 `case_id`，每个本轮逻辑参数实例由 `invocation_id` 归属；相同逻辑实例重复执行会复用该 ID。
- 已生成的 JUnit 当前可以按 `invocation_id` 关联质量记录；虽然两侧也有 `case_id`，Aggregator 尚未校验其一致性。
- Quality 的 prepare、finalize 和 collector 写盘故障不会覆盖 pytest/Runner 的原始执行事实；阶段环境逃逸异常则按 Runner `PARTIAL` 处理。

### 15.2 当前不能据此声称的能力

- 权威计划存在，不等于所有计划 Case 都已经执行并落盘。
- 池返回 0，不等于每个 worker 分片都完整存在。
- 文件名包含 worker_id，不等于系统掌握了一份完整预期 worker 清单。
- 五级身份正确，不等于裸线程或所有自建并发边界都自动传播上下文。
- `case_id` 稳定，不等于 `invocation_id` 跨运行稳定。
- `invocation_id` 相同，不等于只有一次物理执行；其生成输入不包含 execution、worker 或执行次数。
- JUnit 写入两个身份，不等于 JUnit testcase 携带完整五级身份。
- JUnit 与 CaseResult 都含 `case_id`，不等于当前 Aggregator 已经检查两侧 `case_id` 一致。
- 存在 `build_execution_id()`，不等于当前 Runner 使用该方法生成阶段身份。
- Quality fail-open，不等于所有采集故障都有可见 Integrity 证据。
- 计划 nodeid 被显式传给 pytest，不等于执行阶段重新加载的配置和插件绝不会改变最终实际集合。
- 当前 Aggregator 校验预期数量和 execution，不等于它能发现“漏一个计划 nodeid、增加一个非计划 nodeid、总数不变”。
- 默认路径存在 `execution-result.json`，不等于该文件由本轮生成；未到写入点时旧文件可能残留。
- 调用方使用 pytest 的 `--junit-xml` 别名，不等于当前 JUnit helper 会识别、保留并正确添加分池后缀。

## 16. 设计收益、代价与适用边界

| 维度 | 收益 | 代价或边界 |
| --- | --- | --- |
| 权威收集 | 与 pytest 真实选择语义一致 | 需要额外执行一次 collect-only |
| 集合守恒 | 能发现分池重复与遗漏 | 必须长期保持 nodeid 与 marker 合同稳定 |
| 计划 nodeid 显式执行 | 避免各池自行扫描并重新发现目标 | 执行期仍重载 pytest 配置和插件；当前缺少 planned/actual nodeid 集合对账 |
| 池级事实 | 能区分未运行、正常测试失败和执行器错误 | 需要维护项目级退出码合并规则；未到写入点时不产生本轮文件且旧文件可能残留 |
| 五级身份 | 并发事实可归属、可跨产物关联 | 身份规范一旦变化会影响历史对账 |
| worker 独立分片 | 避免多进程竞争写同一 Case 文件 | 后续必须归并并判断完整性 |
| 已隔离的 Quality 观察故障 | prepare、finalize 与 collector 写盘故障不改写 pytest 原始事实 | 阶段环境仍可能有异常逃逸到 Runner 总出口；并非所有诊断故障都可见 |

这套设计适合：

- 测试量较大，需要并行与串行混合执行。
- 需要把多 worker 产物汇总为可审计事实。
- 存在参数化用例和跨运行质量治理。
- 需要区分测试失败、执行器故障和诊断故障。

它对少量一次性同步测试可能偏重。没有并发、没有跨运行比较、也不需要机器证据审计时，五级身份和多层产物会增加维护成本。

## 17. 最小源码证据

下面只列证明本课关键事实所需的代码锚点，不作为源码阅读路线：

| 需要证明的事实 | 当前实现证据 |
| --- | --- |
| 权威 Case 来自 pytest collect-only 和 `session.items` | `run_orchestration/pytest_execution.py` 的 `collect_test_case_items()` 与 `_CaseCollector` |
| 分池检查重复、交集与并集 | `run_orchestration/scheduling.py` 的 `split_test_cases()` |
| Runner 执行池并保存 raw exit / final exit | `run_orchestration/runner.py`、`run_orchestration/pytest_execution.py` |
| 同一 run 向不同 execution 传播 | `run_orchestration/environment.py` 的 `quality_stage_environment()` |
| xdist controller 不写分片、配置转发到 worker | `quality/pytest_plugin_runtime.py` 的 `pytest_configure()` 与 `pytest_configure_node()` |
| Case 与 invocation 身份怎样生成 | `quality/identifiers.py`、`quality/pytest_plugin_runtime.py` 的 `_build_case_context()` |
| worker Case 分片按 execution/worker 隔离 | `quality/collector.py` 的 `QualityCollector` |
| JUnit 写入 Case 与 invocation identity | `quality/pytest_plugin_runtime.py`、`quality/junit.py` |

## 18. 本课收束

Runner 与稳定身份不是两个孤立功能，而是一条连续因果链：

~~~text
pytest 的真实选择语义可能很复杂
-> 必须先形成唯一权威集合 C
-> C 只能被无交叉、无遗漏地派生为 P 与 S
-> 已形成的 PoolExecutionResult 在内存中保留计划、状态、时间、异常类型和原始 pytest 退出事实
-> 只有到达写入点且提交成功才产生本轮 execution-result.json
-> 并发产物必须共享 run 身份并区分 execution 与 worker
-> 稳定 Case 定义必须与本轮逻辑参数实例分开
-> JSONL、JUnit 和 Runner 事实才具备有限数量/身份对账条件
-> 端到端 nodeid 集合守恒仍需要未来把 planned_nodeids 接入实际 Case 集合核验
~~~

最终要记住的不是五个字段名，而是两类所有权：

> Runner 负责回答“本轮计划怎样被编排和执行”；稳定身份模型提供归属坐标，而每种产物只携带并回答自身职责所需的身份子集。

两者共同提供可信执行的坐标系，但不越权宣称账本已经完整。下一步真正需要解决的约束是：Quality 怎样接入这些执行边界进行观察，同时不成为 pytest 和业务调用的控制者。
