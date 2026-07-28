# 并发优先与串行标记执行开发方案

## 1. 需求理解

当前 Jenkins 已支持通过 `TEST_PARALLEL_WORKERS` 控制是否启用 pytest-xdist 并发执行。

下一阶段目标是在 master 用例收集阶段识别必须串行执行的用例标记，将用例拆成两个执行池：

- 并发池：未标记串行的用例，优先并发执行。
- 串行池：标记为串行的用例，在并发池执行完毕后同步执行。

期望行为：

```text
TEST_PARALLEL_WORKERS=off:
  全部用例串行执行一次

TEST_PARALLEL_WORKERS=auto/2/4/8:
  收集全部用例
  识别 serial 标记
  先执行未标记 serial 的并发池
  并发池完成后，如果 serial 用例数量 > 0，再串行执行 serial 池
```

## 2. 第一性原理分析

测试并发的本质不是“所有用例都并行”，而是将可并发的计算尽可能并行，同时保护共享外部状态不被并发破坏。

真正需要解决的问题是：

```text
测试集里同时存在两类用例
-> 一类天然可并发
-> 一类依赖账号余额、账单、任务状态、跨账号隔离等共享状态
-> 直接全量并发会破坏确定性
-> 全量串行又浪费执行时间
-> 因此需要基于用例契约拆分执行池
```

结论：

- 是否可以并发是用例契约，应靠近用例声明。
- master 负责识别契约并调度，不应硬编码文件路径。
- Jenkins 只负责传入并发参数，不应承担用例分类逻辑。

## 3. TOC 约束分析

当前约束点不是并发能力本身，pytest-xdist 已经具备并发执行能力。

当前约束点是：

```text
用例池缺少并发安全分层
```

如果不拆分执行池：

- 开启并发会让账单、余额、zero 账号、异步轮询等用例变得不稳定。
- 关闭并发会让大量纯校验和框架测试执行效率下降。

因此 TOC 决策是：

```text
最大化并发池规模
最小化串行池规模
串行池只承载不可并发的共享状态用例
```

## 4. 标记设计

新增 pytest marker：

```ini
markers =
    serial: must run serially after the parallel test pool
```

标记粒度支持三层。

文件级：

```python
import pytest

pytestmark = pytest.mark.serial
```

类级：

```python
import pytest

@pytest.mark.serial
class TestBilling:
    ...
```

方法级：

```python
import pytest

@pytest.mark.serial
def test_balance_change():
    ...
```

优先标记以下类型用例：

- 账单扣费校验。
- 余额变化校验。
- zero 账号调用。
- 跨账号隔离。
- 真实异步任务轮询完整链路。
- 依赖外部任务状态的失败场景。
- 共享账号额度或共享后端状态的真实接口用例。

## 5. master_service 改造方案

### 5.1 新增结构化用例对象

新增数据结构：

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class CollectedTestCase:
    nodeid: str
    markers: frozenset[str]

    @property
    def is_serial(self) -> bool:
        return "serial" in self.markers
```

### 5.2 新增结构化收集接口

新增：

```python
def collect_test_case_items(test_path: str | Path = DEFAULT_TEST_PATH) -> list[CollectedTestCase]:
    ...
```

保留兼容接口：

```python
def collect_test_cases(test_path: str | Path = DEFAULT_TEST_PATH) -> list[str]:
    return [case.nodeid for case in collect_test_case_items(test_path)]
```

这样旧代码仍可使用 `collect_test_cases()`。

### 5.3 marker 识别方式

不建议继续解析 `pytest --collect-only -q` 文本来识别 marker，因为该输出只稳定包含 nodeid，不包含 marker 信息。

建议使用 pytest 插件收集：

```python
class _CaseCollector:
    def __init__(self) -> None:
        self.items: list[CollectedTestCase] = []

    def pytest_collection_finish(self, session):
        for item in session.items:
            markers = frozenset(marker.name for marker in item.iter_markers())
            self.items.append(
                CollectedTestCase(
                    nodeid=item.nodeid,
                    markers=markers,
                )
            )
```

调用：

```python
collector = _CaseCollector()
exit_code = pytest.main(
    [
        "--collect-only",
        "-q",
        str(test_path),
    ],
    plugins=[collector],
)
```

### 5.4 拆分执行池

新增：

```python
def split_test_cases(
    cases: Sequence[CollectedTestCase],
    serial_marker: str = "serial",
) -> tuple[list[str], list[str]]:
    parallel_cases = []
    serial_cases = []

    for case in cases:
        if serial_marker in case.markers:
            serial_cases.append(case.nodeid)
        else:
            parallel_cases.append(case.nodeid)

    return parallel_cases, serial_cases
```

## 6. run_master 改造方案

### 6.1 参数设计

保留现有参数：

```text
-n / --numprocesses
--dist
```

新增参数：

```text
--serial-marker serial
--parallel-first
```

建议默认行为：

- 不传 `-n`：全量串行。
- 传 `-n`：自动启用并发池优先、串行池后置。

### 6.2 执行策略

伪代码：

```python
def run(test_path, extra_pytest_args=None, numprocesses=None):
    cases = collect_test_case_items(test_path)
    if not cases:
        return 1

    if not numprocesses:
        return pytest.main([case.nodeid for case in cases] + extra_pytest_args)

    parallel_cases, serial_cases = split_test_cases(cases)

    result_codes = []

    if parallel_cases:
        result_codes.append(
            pytest.main(
                parallel_cases
                + ["-n", numprocesses]
                + parallel_report_args
                + extra_pytest_args
            )
        )
    else:
        print("并发池为空，跳过并发阶段。")

    if serial_cases:
        result_codes.append(
            pytest.main(
                serial_cases
                + serial_report_args
                + extra_pytest_args_without_xdist
            )
        )
    else:
        print("串行池为空，跳过串行阶段。")

    return 1 if any(code not in (0, 5) for code in result_codes) else 0
```

### 6.3 失败处理

建议行为：

- 并发池失败后，仍继续执行串行池。
- 串行池失败后，最终返回失败。
- 并发池为空不失败。
- 串行池为空不失败。
- 总收集用例数为 0 时失败。

原因：

```text
并发池失败不代表串行池不能提供有效反馈
-> 继续执行能一次性暴露更多问题
-> 最终结果仍应保持失败
```

## 7. 报告策略

双阶段执行时不能共用同一个 JUnit 文件，否则后一个阶段会覆盖前一个阶段。

建议报告文件：

```text
reports/unit-parallel.xml
reports/unit-serial.xml
reports/smoke-parallel.xml
reports/smoke-serial.xml
```

Jenkins 归档：

```groovy
junit allowEmptyResults: true, testResults: 'reports/*.xml'
```

Allure 风险点：

当前 `pytest.ini` 配置了：

```ini
--alluredir=allure-results
--clean-alluredir
```

双阶段 pytest 执行时，第二阶段如果继续携带 `--clean-alluredir`，会清掉第一阶段结果。

建议处理方式：

- 第一阶段保留 clean。
- 第二阶段覆盖 pytest 参数，使用同一个 `--alluredir=allure-results`，但不携带 `--clean-alluredir`。

如果实现难度较高，可以先接受 JUnit 作为 CI 判定依据，Allure 后续再做阶段合并优化。

## 8. Jenkinsfile 改造方案

当前 Jenkins 已有：

```text
TEST_PARALLEL_WORKERS=off/auto/2/4/8
```

建议后续让 smoke 执行统一走 master 调度：

```powershell
if ($env:TEST_PARALLEL_WORKERS -eq 'off') {
    ./.venv/Scripts/python.exe run_master.py $target --junitxml=reports/smoke-tests.xml
} else {
    ./.venv/Scripts/python.exe run_master.py $target -n $env:TEST_PARALLEL_WORKERS --parallel-first --junitxml=reports/smoke-tests.xml
}
```

框架测试也可以逐步切换为：

```powershell
./.venv/Scripts/python.exe run_master.py tests -n $env:TEST_PARALLEL_WORKERS
```

但建议分两步：

1. 先改 smoke，因为 smoke 已经通过 `run_master.py` 执行。
2. 再评估是否让 `tests` 目录也统一走 master 调度。

## 9. 测试计划

新增或调整测试覆盖：

1. `collect_test_case_items()` 能识别函数级 `serial`。
2. `collect_test_case_items()` 能识别类级 `serial`。
3. `collect_test_case_items()` 能识别文件级 `pytestmark = pytest.mark.serial`。
4. `split_test_cases()` 能正确拆分并发池和串行池。
5. 未传 `-n` 时，只执行一次全集串行。
6. 传 `-n 2` 时，先执行 `not serial`，再执行 `serial`。
7. `serial_pool=0` 时不失败。
8. `parallel_pool=0` 时只跑串行池。
9. 并发池失败后仍执行串行池，最终失败。
10. 双阶段 JUnit 文件不覆盖。
11. Jenkins `TEST_PARALLEL_WORKERS=off` 行为保持不变。
12. Jenkins `TEST_PARALLEL_WORKERS=2` 时控制台能看到并发池和串行池统计。

## 10. 实施步骤

建议按以下顺序开发：

1. 在 `pytest.ini` 注册 `serial` marker。
2. 改造 `master_service.py`，新增结构化收集和拆分函数。
3. 保留 `collect_test_cases()` 兼容旧调用。
4. 改造 `run_master.py`，实现并发池优先、串行池后置。
5. 给真实共享状态用例补 `serial` 标记。
6. 调整 Jenkinsfile，让 smoke 并发模式调用新的 master 调度。
7. 补充单元测试。
8. 本地验证：

```powershell
python run_master.py module/smoke --collect-only -q
python run_master.py module/smoke -n 2 -q
```

9. Jenkins 验证：

```text
TEST_PARALLEL_WORKERS=off
TEST_PARALLEL_WORKERS=2
```

## 11. 验收标准

- 未启用并发时，行为与当前版本一致。
- 启用并发时，控制台输出总用例数、并发池数量、串行池数量。
- 并发池先执行，串行池后执行。
- 串行池数量为 0 时不额外执行同步阶段。
- 标记 `serial` 的用例不会进入 xdist 并发执行。
- Jenkins 并发参数能控制 master 执行策略。
- JUnit 报告不被双阶段覆盖。
