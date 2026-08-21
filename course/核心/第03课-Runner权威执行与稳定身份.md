# 第 3 课：Runner 权威执行与稳定身份

> 建议时长：75 分钟
>
> 本课范围：权威 Case 集合、并行/串行分池、pytest 退出事实、五级身份、worker 原始账页与 JUnit 身份。
>
> 本课不展开：Aggregator 的完整归并规则、Metrics、Flaky、Allure 内部生命周期和完整源码调用链。

## 1. 先说结论

Runner 的核心价值不是“把 pytest 跑得更快”，而是在并发开始前冻结一份权威执行计划，并在执行结束后保留可审计的池级事实。

稳定身份的核心价值也不是“多加几个 ID 字段”，而是让每一条分散在不同进程、不同文件和不同产物中的事实，都能回答：

1. 它属于哪一轮运行？
2. 它来自哪个执行阶段？
3. 它由哪个 worker 写出？
4. 它对应哪个长期稳定的测试定义？
5. 它对应本轮哪一次具体参数化调用？

整节课只围绕一条主线展开：

~~~text
先确定“谁应该执行”
-> 再保证并行池和串行池不重、不漏
-> 执行时保留每个池的原始 pytest 结果
-> 用稳定身份标记分散的 Case 事实
-> 为后续 JUnit 与 Aggregator 对账提供可靠输入
~~~

需要先明确一个能力边界：Runner 能证明计划是什么、计划怎样分池、各池返回了什么；它单独不能证明每个 Case 事实都已完整落盘。完整性结论还需要后续 Aggregator 结合 Case 分片、预期数量和 JUnit 进行对账。

## 2. 学完本课应建立的认识

本课不要求记忆文件或函数。重点是理解以下设计判断：

- 为什么并发执行前必须先形成唯一的权威 Case 集合。
- 为什么 `|P| + |S| = |C|` 不能代替真正的集合守恒检查。
- 为什么 pytest 原始退出码、Runner 项目级退出码和质量诊断不是同一层事实。
- `run_id`、`execution_id`、`worker_id`、`case_id`、`invocation_id` 分别解决什么归属问题。
- 参数化用例为什么既需要稳定的 `case_id`，又需要本轮具体的 `invocation_id`。
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
-> 产物才能回到同一份计划上对账
~~~

这里冻结的是 Runner 的**计划目标**，不是对执行阶段最终集合的绝对保证。权威收集显式使用 `-o addopts=` 清空配置中的 addopts；实际执行会重新加载 pytest 配置与插件，未知插件参数也会同时进入收集和执行阶段。因此，执行阶段仍可能受插件或配置行为影响，最终是否与 `C` 一致必须由后续执行证据对账，而不能仅凭 nodeid 参数宣称。

### 4.2 TOC：本课最大的理解约束

本课最大的理解约束不是并行语法，而是学习者容易把“执行了相同数量的测试”误认为“执行了正确的测试集合”。

数量只回答“有几个”，集合才回答“是哪几个”。只要这个约束没有解除，讲 worker、JSONL 或 JUnit 都只是增加字段，无法证明执行正确性。

所以本课的引入顺序是：

~~~text
权威集合
-> 集合守恒
-> 池级执行事实
-> 五级身份
-> worker 账页与 JUnit 证据
~~~

## 5. 整体实现思路

当前实现可以压缩成三个相互衔接的责任层：

| 层次 | 核心输入 | 核心责任 | 输出 |
| --- | --- | --- | --- |
| 权威计划层 | 测试目标、pytest 选择参数 | 用 pytest 正式收集 Case，形成并校验 `C`、`P`、`S` | 权威 nodeid 与分池计划 |
| 执行事实层 | 计划 nodeid、执行参数 | 把计划 nodeid 作为显式目标交给 pytest，保留池状态、原始退出码和项目级退出码 | `execution-result.json`、JUnit 路径 |
| 身份观察层 | 父运行身份、阶段身份、worker 与测试项 | 建立五级身份，写出按 execution/worker 隔离的 Case 分片，并给 JUnit 增加身份 | Case JSONL、JUnit identity properties |

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

- 保留了参数规划与收集异常、权威集合、集合守恒、池级状态、Runner 总出口、中断状态、五级身份、写盘隔离、JSONL/JUnit 输出和下游边界。
- 把实际分散在 Runner、调度器、pytest 执行封装和 Quality pytest 插件中的职责放到一个教学骨架里。
- 省略了 CLI、Allure、路径后缀处理、完整异常文本、Pydantic 校验、原子写实现、Semantic 采集和失败分类。
- 省略内容不会改变“谁拥有执行事实”“身份怎样形成”以及“正常与失败怎样退出”等架构事实。

代码中的文件分隔注释表示不同真实模块。它们被放进同一个代码块，是为了显式展示 `pytest_main()` 之后由 pytest 驱动的插件与 hook 链；这不是把这些函数伪装成仓库中的单一文件。

~~~python
# 教学重构：用于表达 Runner + 稳定身份的端到端主控制流

from dataclasses import dataclass
from datetime import timedelta
from enum import Enum
from pathlib import Path


TERMINATING_EXIT_CODES = {2, 3, 4, 5}


@dataclass(frozen=True)
class CollectedCase:
    nodeid: str
    markers: frozenset[str]


class PoolStatus(str, Enum):
    NOT_RUN = "NOT_RUN"
    COMPLETED = "COMPLETED"
    ERROR = "ERROR"


@dataclass(frozen=True)
class PoolResult:
    stage_id: str
    planned_nodeids: tuple[str, ...]
    status: PoolStatus
    raw_pytest_exit_code: int | None = None
    junit_path: str | None = None


@dataclass(frozen=True)
class RunIdentity:
    run_id: str
    execution_id: str
    worker_id: str
    output_dir: Path


@dataclass(frozen=True)
class CaseIdentity:
    case_id: str
    invocation_id: str
    nodeid: str
    param_hash: str


class EnabledQualityLifecycle:
    """仅保留本课需要看到的 JUnit 参数边界。"""
    enabled = True

    def ensure_junit_args(self, pytest_args):
        args = list(pytest_args)
        if junit_path_from(args) is not None:
            return args
        return args + [f"--junitxml={self.output_dir}/junit/quality.xml"]


class NoopQualityLifecycle:
    enabled = False

    def ensure_junit_args(self, pytest_args):
        return list(pytest_args)


def build_execution_plan(cases: tuple[CollectedCase, ...]):
    """从同一份权威收集结果派生 P 和 S。"""
    seen: set[str] = set()
    parallel: list[str] = []
    serial: list[str] = []

    for case in cases:
        if case.nodeid in seen:
            raise ValueError("duplicate nodeid")
        seen.add(case.nodeid)
        target = serial if "serial" in case.markers else parallel
        target.append(case.nodeid)

    P, S = tuple(parallel), tuple(serial)
    if set(P) & set(S):
        raise ValueError("parallel and serial pools overlap")
    if set(P) | set(S) != seen:
        raise ValueError("pool union differs from authoritative plan")
    return P, S


def execute_pool(stage_id, planned_nodeids, pytest_args):
    """把计划 nodeid 交给 pytest；这里只隔离 pytest 调用本身的异常。"""
    nodeids = tuple(planned_nodeids)
    junit_path = junit_path_from(pytest_args)  # 位于池内 try 之外
    if not nodeids:
        return PoolResult(
            stage_id, (), PoolStatus.NOT_RUN, junit_path=junit_path
        )

    args = [*nodeids, *pytest_args]
    try:
        # pytest 在这里重新加载配置和 module/conftest.py；下半段展示
        # Quality 插件怎样由 pytest 加载并通过 hooks 观察执行。
        raw_exit = int(pytest_main(args))
        return PoolResult(
            stage_id=stage_id,
            planned_nodeids=nodeids,
            status=PoolStatus.COMPLETED,
            raw_pytest_exit_code=raw_exit,
            junit_path=junit_path,
        )
    except Exception as error:
        return PoolResult(
            stage_id=stage_id,
            planned_nodeids=nodeids,
            status=PoolStatus.ERROR,
            raw_pytest_exit_code=None,
            junit_path=junit_path,
        )


def run(test_target, pytest_args, *, numprocesses=None, dist=None):
    """Runner：保留参数、收集、池执行和中断的真实失败归属。"""
    try:
        argument_plan = partition_pytest_args(pytest_args)
    except ValueError:
        return PYTEST_EXIT_USAGE_ERROR  # 4：参数规划失败，尚未收集

    try:
        collection = pytest_collect_only(
            test_target,
            argument_plan.collection_args,
        )  # 内部固定传入 -o addopts=
    except Exception:
        return PYTEST_EXIT_TESTS_FAILED  # 1：权威收集调用本身异常

    if collection.raw_pytest_exit_code != PYTEST_EXIT_OK:
        final_exit = collection.raw_pytest_exit_code
        if not argument_plan.collect_only:
            final_exit = write_runner_fact_or_fail(
                planned_nodeids=tuple(
                    case.nodeid for case in collection.cases
                ),
                collection_exit_code=collection.raw_pytest_exit_code,
                pool_results=(),
                final_exit_code=final_exit,
            )
        return final_exit

    C = tuple(case.nodeid for case in collection.cases)
    P, S = build_execution_plan(collection.cases)
    if argument_plan.collect_only:
        return PYTEST_EXIT_OK  # 不创建新的 Quality 身份和产物

    quality = create_quality_lifecycle()  # 关闭或配置失败时得到 Noop
    start_time = now_utc()
    quality.prepare(start_time)           # 内部 fail-open
    pool_results: list[PoolResult] = []
    run_status = FINISHED

    try:
        if not numprocesses:
            # Enabled 生命周期在未提供 --junitxml 时注入默认路径；
            # Noop 生命周期原样返回参数。
            serial_args = quality.ensure_junit_args(
                argument_plan.execution_args
            )

            # 环境上下文故障属于 Runner 总出口，不属于 execute_pool 的 ERROR。
            with quality.stage_environment("serial-pool"):
                pool_results.append(
                    execute_pool("serial-pool", C, serial_args)
                )
        else:
            parallel_args = quality.ensure_junit_args(
                argument_plan.execution_args
            )
            parallel_args = build_parallel_args(
                parallel_args,
                numprocesses=numprocesses,
                dist=dist,
                junit_suffix="parallel",
            )
            if P:
                with quality.stage_environment("parallel-pool"):
                    parallel_result = execute_pool(
                        "parallel-pool", P, parallel_args
                    )
            else:
                parallel_result = not_run_pool(
                    "parallel-pool", P, parallel_args
                )
            pool_results.append(parallel_result)

            must_stop = (
                parallel_result.status is PoolStatus.ERROR
                or parallel_result.raw_pytest_exit_code
                in TERMINATING_EXIT_CODES
            )
            serial_args = quality.ensure_junit_args(
                argument_plan.execution_args
            )
            serial_args = build_serial_args(
                serial_args, junit_suffix="serial"
            )
            if must_stop:
                serial_result = not_run_pool(
                    "serial-pool", S, serial_args
                )
            elif S:
                # 普通测试失败 exit 1 不阻止串行池继续执行。
                with quality.stage_environment("serial-pool"):
                    serial_result = execute_pool(
                        "serial-pool", S, serial_args
                    )
            else:
                serial_result = not_run_pool(
                    "serial-pool", S, serial_args
                )
            pool_results.append(serial_result)

        final_exit = merge_raw_pytest_exit_codes(pool_results)
        if any(r.status is PoolStatus.ERROR for r in pool_results):
            run_status = PARTIAL
        return write_runner_fact_or_fail(
            planned_nodeids=C,
            collection_exit_code=collection.raw_pytest_exit_code,
            pool_results=tuple(pool_results),
            final_exit_code=final_exit,
        )
    except (KeyboardInterrupt, SystemExit):
        run_status = INTERRUPTED
        raise  # 中断保持中断，不伪装成普通测试失败
    except Exception:
        run_status = PARTIAL
        return PYTEST_EXIT_TESTS_FAILED
    finally:
        # Enabled 生命周期从非 NOT_RUN 池提取 execution_id 和 JUnit 路径；
        # finalize 自身 fail-open，不覆盖上面的退出事实。
        quality.finalize(
            start_time=start_time,
            expected_case_count=len(C),
            pool_results=tuple(pool_results),
            status=run_status,
        )


# ----- module/conftest.py -----
# pytest_main() 执行 module 下的 nodeid 时，pytest 加载这个轻量插件入口。
pytest_plugins = ("quality.pytest_plugin",)


# ----- quality/pytest_plugin.py：真实 hook 名 pytest_configure -----
def pytest_configure(config):
    if config.option.collectonly:
        return
    try:
        enabled = quality_enabled_from_env_or_workerinput(config)
    except Exception as error:
        warn("quality collection disabled", error)
        return
    if not enabled:
        return

    # 只有启用时才加载并注册较重的 runtime hooks。
    runtime = import_module("quality.pytest_plugin_runtime")
    if not config.pluginmanager.has_plugin("quality-runtime"):
        config.pluginmanager.register(runtime, "quality-runtime")


# ----- quality/pytest_plugin_runtime.py：真实 hook 名 pytest_configure -----
def pytest_configure(config):
    try:
        runtime_config = resolve_runtime_config(config)
    except Exception as error:
        warn("quality collection disabled", error)
        return
    state = PluginState(config=runtime_config)
    config._quality_plugin_state = state
    if not runtime_config.enabled:
        return

    # controller 保留 state，供 pytest_configure_node 转发配置；
    # controller 自己不建 collector，因此不会写 master Case 分片。
    if is_xdist_controller(config):
        return

    try:
        state.run_context = RunIdentity(
            run_id=runtime_config.run_id,
            execution_id=runtime_config.execution_id,
            worker_id=xdist_worker_id_or_master(config),
            output_dir=runtime_config.output_dir,
        )
        state.run_token = set_run_context(state.run_context)
        state.collector = configure_collector(state.run_context)
    except Exception as error:
        reset_partially_created_context_and_collector(state)
        warn("quality collector initialization failed", error)


# ----- xdist controller hook：真实 hook 名 pytest_configure_node -----
@pytest.hookimpl(optionalhook=True)
def pytest_configure_node(node):
    state = getattr(node.config, "_quality_plugin_state", None)
    if state is None or not state.config.enabled:
        return
    node.workerinput["quality_runtime"] = {
        "enabled": True,
        "run_id": state.config.run_id,
        "execution_id": state.config.execution_id,
        "output_dir": str(state.config.output_dir),
    }
    # worker 启动后再次加载轻量插件；上面的 pytest_configure
    # 从 workerinput 取回同一 run_id / execution_id，再建立 worker 上下文。


def build_case_context(item, run_id):
    normalized = normalize_nodeid(item.nodeid)
    callspec = getattr(item, "callspec", None)
    parameter_view = None
    if callspec is not None:
        parameter_view = {
            "parameter_id": normalized.parameter_id,
            "params": callspec.params,
        }
    param_hash = build_param_hash(parameter_view)  # 先脱敏、规范化，再截断
    case_id = build_case_id(item.nodeid)
    return CaseIdentity(
        case_id=case_id,
        invocation_id=build_invocation_id(run_id, case_id, param_hash),
        nodeid=item.nodeid,
        param_hash=param_hash,
    )


# ----- worker hookwrapper：真实 hook 名 pytest_runtest_protocol -----
@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_protocol(item, nextitem):
    state = item.config._quality_plugin_state
    collector = state.collector
    if collector is None:
        yield
        return

    token = None
    try:
        case_context = build_case_context(
            item, collector.run_context.run_id
        )
        token = set_case_context(case_context)
    except Exception as error:
        collector.capture_integrity(
            source="pytest_plugin",
            code="case_context_build_failed",
            message=str(error),
            severity="ERROR",
        )

    try:
        yield  # pytest 在这里执行 setup -> call -> teardown
    finally:
        if token is not None:
            reset_case_context(token)


# ----- worker phase hook：真实 hook 名 pytest_runtest_logreport -----
@pytest.hookimpl(tryfirst=True)
def pytest_runtest_logreport(report):
    collector = get_collector()
    if collector is None or report.when not in {"setup", "call", "teardown"}:
        return

    case_context = get_case_context()
    if case_context is None:
        collector.capture_integrity(
            source="pytest_plugin",
            code="case_context_build_failed",
            message=f"case context missing for {report.when}",
            severity="ERROR",
        )
        return

    try:
        end_time = now_utc()
        duration_ms = max(report.duration * 1000, 0)
        status = status_from_pytest(report)
        result = CaseResult(
            run_id=collector.run_context.run_id,
            execution_id=collector.run_context.execution_id,
            worker_id=collector.run_context.worker_id,
            case_id=case_context.case_id,
            invocation_id=case_context.invocation_id,
            nodeid=case_context.nodeid,
            param_hash=case_context.param_hash,
            phase=report.when,
            raw_status=status,
            final_status=status,
            duration_ms=duration_ms,
            start_time=end_time - timedelta(milliseconds=duration_ms),
            end_time=end_time,
        )

        # pytest 的 JUnit 插件把 user_properties 写入已注入路径的 XML。
        add_junit_property(report, "quality_case_id", case_context.case_id)
        add_junit_property(
            report, "quality_invocation_id", case_context.invocation_id
        )
        collector.record_case(result)  # 安全入口，不直接 append_jsonl
    except Exception as error:
        collector.capture_integrity(
            source="pytest_plugin",
            code="case_write_failed",
            related_id=case_context.invocation_id,
            message=str(error),
            severity="ERROR",
        )


# ----- quality/collector.py：写盘失败隔离 -----
class QualityCollector:
    def __init__(self, run_context):
        self.run_context = run_context
        suffix = f"{run_context.execution_id}-{run_context.worker_id}.jsonl"
        self.paths = ShardPaths(
            cases=run_context.output_dir / "shards" / f"cases-{suffix}",
            integrity=run_context.output_dir / "shards" / f"integrity-{suffix}",
        )
        initialize_empty_shards(self.paths)  # 失败由 runtime pytest_configure 隔离

    def record_case(self, result):
        try:
            append_jsonl(self.paths.cases, result)
            return True
        except Exception as error:
            # Integrity 写入也有自己的 try/except；两次写盘都失败时只告警。
            self.capture_integrity(
                source="collector",
                code="case_write_failed",
                related_id=result.invocation_id,
                message=str(error),
                severity="ERROR",
            )
            return False
~~~

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
- Quality 成功启用时，Runner 保留调用方已有 JUnit 参数，或注入默认 JUnit 路径；pytest 再把 Case 身份写入该 JUnit 证据。
- 提供给后续 Aggregator 的预期 Case 数量与 execution 集合。

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

## 7. 权威集合：先回答“谁应该执行”

### 7.1 权威来源必须是 pytest

当前实现通过 `pytest.main()` 执行一次正式的 `--collect-only`，并由收集插件读取 `session.items`。每个收集项保留：

- 精确 `nodeid`。
- 从函数、类和文件层级继承到的 marker 集合。

这意味着 Runner 尊重 pytest 已经形成的选择语义，而不是重新实现一套测试发现器。

选择参数和执行参数还会先被分开：

| 参数类型 | 示例 | 进入权威收集 | 进入实际执行 |
| --- | --- | --- | --- |
| 选择参数 | `-k`、`-m`、`--ignore`、`--deselect` | 是 | Runner 不再传这些选择参数，而是传入计划 nodeid；执行期配置与插件仍会重新加载 |
| 执行参数 | `-n`、`--dist`、`--junitxml`、`--alluredir` | 否 | 是 |
| 未识别的插件参数 | 自定义插件参数 | 为保留插件语义，会同时传递 | 是 |

核心不是机械分类参数，而是先把 Runner 认可的计划目标固定下来。由于收集期清空 configured addopts、执行期重新加载配置，且未知插件参数会同时进入两阶段，这个计划仍需用执行证据复核，不能自动等同于最终实际集合。

### 7.2 收集失败不能继续分池

权威收集的原始退出码不是 0 时，Runner 不应拿到一个不完整集合后继续运行：

- 退出码 5：没有收集到测试。
- 其他非零：收集错误或 pytest 失败。
- 收集阶段发现重复 nodeid：当前实现直接视为错误。

没有可信的 `C`，就不存在可信的 `P` 和 `S`。

## 8. 集合守恒：不是“数量对上了”就算正确

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

这里没有根据文件名、测试类或执行耗时猜测串并行属性。

### 8.3 关闭并行时的真实行为

即使代码已经计算出 `P` 和 `S`，当前 Runner 在没有设置 `numprocesses` 时，会把整个 `C` 作为一次 `serial-pool` 执行，而不是先执行 `P` 再执行 `S`。

这是一个典型的“代码中存在分池能力，不等于当前执行模式使用了两个池”的例子。

## 9. 池级执行：保留原始事实，再形成项目级结论

### 9.1 PoolResult 记录什么

每个执行池至少保留：

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

不能把二者都压成一个 `failed=true`，否则后续无法判断是产品测试失败还是执行器故障。

### 9.2 普通测试失败为什么不停止串行池

并行池返回退出码 1，表示测试已正常运行但存在失败。当前 Runner 仍会继续执行串行池，让本轮尽量获得完整测试事实。

只有以下情况会停止后续串行池：

- 并行池状态为 `ERROR`。
- 原始退出码为 2、3、4、5，即中断、pytest 内部错误、用法错误或没有测试。

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

如果 Runner 连自身的 `execution-result.json` 都无法写出，当前实现不会制造“成功”：

- 原本是终止性 pytest 退出码时，保留该退出码。
- 原本为 0 或普通非终止结果时，返回 1，表明 Runner 执行事实未能可靠提交。

这不是 Quality 改写 pytest，而是 Runner 对自己拥有的执行产物负责。

## 10. 五级身份：再回答“这条事实属于谁”

权威集合解决计划归属，但并发产物还需要稳定坐标。当前核心身份分为五级：

| 身份 | 稳定范围 | 回答的问题 | 当前来源 |
| --- | --- | --- | --- |
| `run_id` | 一轮完整运行 | 事实属于哪一轮运行 | Runner 父进程生成或读取配置，并向各池传播 |
| `execution_id` | 一轮运行中的执行阶段 | 事实来自并行池、串行池还是手工 pytest | 当前 Runner 使用 `parallel-pool`、`serial-pool`；直接运行默认为 `manual-pytest` |
| `worker_id` | 一个具体 pytest 执行进程 | 哪个 worker 写出了事实 | xdist 使用 `gw0` 等；非 xdist 使用 `master` |
| `case_id` | 跨运行稳定的测试定义 | 长期比较时这是哪个 Case | nodeid 规范化后移除末尾参数部分 |
| `invocation_id` | 本轮具体参数化调用 | 本轮究竟是哪一次调用 | `run_id + case_id + param_hash` 的稳定摘要 |

### 10.1 run_id：先把多个阶段锁进同一轮运行

Quality 开启时，Runner 在父进程只确定一次 `run_id`：

- Jenkins 同时提供 `JOB_NAME` 和 `BUILD_NUMBER` 时，把它们纳入运行身份。
- 本地运行使用 UTC 时间与随机 UUID 片段形成身份。

同一轮的并行池和串行池共享这个 `run_id`。如果两个池各自生成一个运行身份，后续就会被误认为两轮独立测试。

Quality 关闭时使用 Noop 生命周期，不应因为 Runner 执行而创建新的 Quality 身份或质量产物。

### 10.2 execution_id：区分同一轮中的执行阶段

当前 Runner 真实传入的是：

~~~text
parallel-pool
serial-pool
~~~

仓库中虽然存在 `build_execution_id(stage_name, index)`，但当前 Runner 主调用链没有用它生成 `parallel-pool-1` 或 `serial-pool-1`。因此课程不能把“方法存在”描述成“当前 Runner 正在使用”。

直接运行 pytest 而不经过 Runner 时，Quality 插件当前使用 `manual-pytest` 作为默认 execution 身份。

### 10.3 worker_id：让并发写入物理隔离

在 xdist 模式下：

- controller 负责把 `run_id`、`execution_id` 和配置传给 worker。
- controller 自己不创建 Case collector，避免重复写一份 master 分片。
- 每个 worker 使用自己的 `worker_id`，例如 `gw0`、`gw1`。

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

当前实现还会把路径分隔符统一为 `/`、压缩多余空白，并正确处理参数 ID 中的嵌套方括号。

`case_id` 有意忽略参数实例，因为它用于表达长期稳定的测试定义。如果把 `[model-a]` 永久焊进 Case 身份，参数展示名的小变化就可能把同一测试误判为新 Case。

这里的“稳定”以 nodeid 的测试路径和定义名保持稳定为前提。移动测试文件、重命名测试函数或改变 pytest 根路径仍可能改变 `case_id`；框架不会把代码重构前后的两个定义自动猜成同一个 Case。

### 10.5 invocation_id：本轮具体调用

只用 `case_id` 会把同一轮中的两个参数实例合并。因此插件还会构造参数视图并计算 `param_hash`，再生成：

~~~text
invocation_id = hash(run_id, case_id, param_hash)
~~~

由此得到两个性质：

- 同一轮、同一测试定义、同一脱敏与规范化后的参数视图会得到相同 invocation 身份。
- `run_id`、`case_id` 或 `param_hash` 变化时会重新计算 invocation 身份；原始参数只有在脱敏、规范化后的 `param_hash` 发生变化时，才会进入这次重新计算。

`param_hash` 和 `invocation_id` 都使用截断摘要，因此这里表达的是工程上的稳定归属规则，不是数学上的绝对无碰撞保证。不能把“原始参数不同”直接写成“invocation_id 必然不同”。

因此 `invocation_id` 不是跨运行长期稳定键。跨运行比较使用 `case_id`；本轮精确归属使用 `invocation_id`。

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

但它不是无限传播保证：裸线程切换可能没有自动携带当前上下文。身份字段设计正确，不代表所有自建并发边界都已经正确传播身份。

### 11.2 CaseResult 保留 pytest phase

pytest 对一个测试调用可能产生：

~~~text
setup -> call -> teardown
~~~

当前插件对真实出现的 phase 分别写 `CaseResult`，每条记录共享同一个 `invocation_id`，同时保留自己的 `raw_status`。

如果 setup 失败，pytest 可能根本没有 call 报告。当前实现不会为了让数据看起来完整而伪造一个 call 记录；它只记录真实出现的 setup 和 teardown 事实。

这体现了一条重要原则：

> 缺失的执行阶段不能用虚构事实补齐。

### 11.3 JUnit 为什么只写两级身份

当前 JUnit properties 写入：

- `quality_case_id`
- `quality_invocation_id`

JUnit 用它们把测试证据与 Case 分片对上。`run_id`、`execution_id` 和 `worker_id` 仍保存在 Quality Case 记录及分片来源中。

不能因为五级身份模型存在，就声称 JUnit 单个 testcase 已经携带全部五级身份。

## 12. 从身份到原始账页

完整事实流可以表示为：

~~~text
pytest 权威收集的 C
-> Runner 派生 P / S
-> stage_environment 注入 run_id / execution_id
-> xdist 给 worker 分配 worker_id
-> pytest 插件为测试项生成 case_id / invocation_id
-> setup / call / teardown 写入 worker Case JSONL
-> JUnit testcase 写入 case_id / invocation_id
-> Runner 保存 planned_nodeids、池结果与 final_exit_code
-> 后续 Aggregator 才能对计划、分片与 JUnit 进行对账
~~~

这里有三种不同的“账”：

| 账目 | 回答的问题 | 当前所有者 |
| --- | --- | --- |
| 权威计划 | 本轮应该执行谁 | Runner，来源是 pytest 权威收集 |
| 执行账 | 每个池是否运行、pytest 怎样退出 | pytest 原始事实 + Runner 编排事实 |
| 观察账 | 每个 worker 观察到了哪些 Case phase | Quality collector |

三者需要身份关联，但不能互相替代。

## 13. 正常终态、失败出口和降级行为

| 场景 | 当前行为 | 事实边界 |
| --- | --- | --- |
| 参数计划非法 | Runner 返回 pytest usage error 4 | 不进入权威收集 |
| 权威收集成功 | 冻结计划 `C`，再分池 | 后续把计划 nodeid 作为 pytest 显式目标；最终集合仍需证据对账 |
| 没有收集到测试 | 返回 pytest 原始退出码 5，不执行池 | 不把空集合解释成成功回归 |
| 收集错误 | 返回收集阶段原始非零退出码 | 不在不可信 `C` 上继续执行 |
| 收集产生重复 nodeid | 视为权威收集异常 | 不允许含糊身份进入计划 |
| 关闭并行 | 整个 `C` 一次进入 `serial-pool` | 分池能力存在，但本模式不执行两个池 |
| 并行池普通测试失败 | 记录 raw exit 1，继续串行池 | 尽量保留完整执行事实 |
| 并行池终止性退出 | 串行池为 `NOT_RUN` | 不伪造串行执行结果 |
| 池调用发生异常 | 池状态为 `ERROR`、无 raw exit，项目级返回非零 | 区分执行器故障与测试失败 |
| `stage_environment` 进入或恢复失败 | 异常进入 Runner 外层 `except`，运行状态为 `PARTIAL` 并返回 1 | 不归类为池内 `ERROR`，因为故障发生在 `execute_pool` 边界之外 |
| 用户中断或 `SystemExit` | 原始控制流继续向外传播，同时运行生命周期进入中断收尾 | 不静默改写为普通测试失败 |
| Quality 关闭 | 使用 Noop，不创建新的 Quality 身份和产物 | pytest 与 Runner 仍可正常执行 |
| Quality 开启且调用方未提供 JUnit 参数 | Enabled 生命周期注入默认 `quality.xml`，分池时改成 parallel/serial 后缀 | Runner 保证执行参数中存在 JUnit 目标；实际文件仍取决于对应 pytest 阶段是否运行并完成 |
| Quality 初始化或最终归并失败 | 告警并 fail-open | 不覆盖 pytest/Runner 结论 |
| Case 分片写入失败 | 尽力记录 Integrity，pytest 结果保持原样 | Integrity 本身也可能写失败，不能保证所有观察故障可见 |
| Runner 执行结果写入失败 | 不返回虚假成功；非终止结果转为 1，终止性 pytest 退出码保留 | Runner 对自身执行事实提交负责 |

## 14. 五个容易产生错误结论的混淆点

### 14.1 “收集了 100 个，两个池加起来也是 100 个，所以没问题”

错误原因：数量不能识别重复与遗漏同时发生。

准确结论：必须检查 nodeid 交集为空、并集等于权威集合。

### 14.2 “有 run_id 就能定位所有事实”

错误原因：同一轮中还有多个阶段、多个 worker、多个 Case 和参数调用。

准确结论：五级身份分别消除不同层级的歧义，不能互相代替。

### 14.3 “case_id 应该区分参数实例”

错误原因：这会破坏跨运行稳定的测试定义身份。

准确结论：`case_id` 表示稳定定义，`invocation_id` 表示本轮具体参数调用。

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
- 每个池保留计划、状态和原始 pytest 退出码。
- 同一轮并行池与串行池共享 `run_id`，但拥有不同 `execution_id`。
- xdist worker 使用独立 `worker_id` 和独立 Case 分片。
- 参数化调用共享稳定 `case_id`，同时具有本轮独立 `invocation_id`。
- 已生成的 JUnit 可以通过 Case 与 invocation identity 对回质量记录。
- Quality 的 prepare、finalize 和 collector 写盘故障不会覆盖 pytest/Runner 的原始执行事实；阶段环境逃逸异常则按 Runner `PARTIAL` 处理。

### 15.2 当前不能据此声称的能力

- 权威计划存在，不等于所有计划 Case 都已经执行并落盘。
- 池返回 0，不等于每个 worker 分片都完整存在。
- 文件名包含 worker_id，不等于系统掌握了一份完整预期 worker 清单。
- 五级身份正确，不等于裸线程或所有自建并发边界都自动传播上下文。
- `case_id` 稳定，不等于 `invocation_id` 跨运行稳定。
- JUnit 写入两个身份，不等于 JUnit testcase 携带完整五级身份。
- 存在 `build_execution_id()`，不等于当前 Runner 使用该方法生成阶段身份。
- Quality fail-open，不等于所有采集故障都有可见 Integrity 证据。
- 计划 nodeid 被显式传给 pytest，不等于执行阶段重新加载的配置和插件绝不会改变最终实际集合。

## 16. 设计收益、代价与适用边界

| 维度 | 收益 | 代价或边界 |
| --- | --- | --- |
| 权威收集 | 与 pytest 真实选择语义一致 | 需要额外执行一次 collect-only |
| 集合守恒 | 能发现分池重复与遗漏 | 必须长期保持 nodeid 与 marker 合同稳定 |
| 计划 nodeid 显式执行 | 避免各池自行扫描并重新发现目标 | 执行期仍重载 pytest 配置和插件，实际集合需要下游证据复核 |
| 池级事实 | 能区分未运行、正常测试失败和执行器错误 | 需要维护项目级退出码合并规则 |
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
-> 每个池必须保留计划、状态和原始 pytest 退出事实
-> 并发产物必须共享 run 身份并区分 execution 与 worker
-> 稳定 Case 定义必须与本轮参数调用分开
-> JSONL、JUnit 和 Runner 事实才具备后续对账条件
~~~

最终要记住的不是五个字段名，而是两类所有权：

> Runner 负责回答“本轮计划怎样被编排和执行”；稳定身份负责回答“每条观察事实属于谁”。

两者共同提供可信执行的坐标系，但不越权宣称账本已经完整。下一步真正需要解决的约束是：Quality 怎样接入这些执行边界进行观察，同时不成为 pytest 和业务调用的控制者。
