# 第 16 课：Quality 开关、运行身份与生命周期

> 第 15 课建立了中性 Runtime Hooks：业务代码不依赖 Quality，外部观察者也不拥有业务出口。第 16 课继续解决外层控制问题：什么时候启用 Quality，怎样为一次 Runner 运行和各执行池建立可关联身份，以及关闭 Quality 时怎样保持无新增副作用。

---

## 1. 课程信息

| 项目 | 内容 |
| --- | --- |
| 建议时长 | 60～90 分钟 |
| 核心问题 | Quality 从哪里开启，Runner 怎样在“不启用就不产生副作用”的前提下，为一次运行和各执行池建立可关联身份？ |
| 主要认知约束 | 学习者容易把配置对象、Runner 生命周期、pytest 进程身份和最终 Quality 产物混成一层 |
| 讲解重点 | 主开关与子开关依赖、Noop / Enabled 生命周期、Runner 调用顺序、run / execution / worker 身份边界、JUnit 参数与 stage environment、精确 fail-open 范围 |
| 代码入口 | `quality/config.py`、`run_orchestration/quality_lifecycle.py`、`run_orchestration/environment.py`、`run_orchestration/runner.py`、`run_orchestration/pytest_execution.py`、`quality/identifiers.py`、`quality/runtime_context.py`；`quality/pytest_plugin_runtime.py` 仅静态确认下一课的 worker 边界 |
| 轻量验证 | 55 条离线测试；验证配置依赖、Noop 无新增副作用、Runner 身份传播、JUnit 参数、环境恢复和标识符合同 |
| 安全边界 | 禁用第三方 pytest 插件自动加载和项目默认 addopts；测试只使用 monkeypatch、临时目录、内存对象和受控子进程，不访问真实 API |
| 课后产出 | 一张 Quality 生命周期与身份链图；课堂判断和三分钟复述不要求提交 |

### 1.1 学完本课，你应该能够

1. 解释 `QUALITY_ENABLE` 与 Semantic、Metrics、Flaky 子开关的依赖关系，并区分主开关非法与子开关非法的结果。
2. 复述 `create_quality_run_lifecycle()` 怎样选择 `NoopQualityRunLifecycle` 或 `EnabledQualityRunLifecycle`，以及关闭路径为什么只加载轻量配置。
3. 沿 Runner 真实顺序说明 `prepare()`、`ensure_junit_args()`、`stage_environment()` 和 `finalize()` 分别发生在哪里。
4. 区分 `run_id`、当前 Runner 使用的 `execution_id` 与第 17 课才在 pytest 进程内确定的 `worker_id`。
5. 判断 Noop、Enabled、collect-only、无 `-n` 和启用 `-n` 场景中的身份、环境、JUnit 参数与副作用边界。

### 1.2 本课刻意不展开

- 不展开 pytest 插件何时创建 `QualityRunContext`、绑定 Adapter 和写 worker 分片；第 17 课学习。
- 不展开 Case、Request、Integrity JSONL Schema。
- 不展开 Semantic、Metrics、Flaky 的归并算法和质量结论；本课只说明配置依赖与 `finalize()` 委托边界。
- 不把 `build_execution_id()` 误画进当前 Runner 主链；当前 Runner 直接使用 `parallel-pool` 和 `serial-pool`。
- 不运行真实 API、模型、Billing、媒体下载或真实 Jenkins 构建。
- 不修改 Quality 配置、生命周期或 pytest 插件代码。

### 1.3 课堂必讲路径

| 环节 | 对应章节 | 建议时间 |
| --- | --- | ---: |
| 第 15 课承接、身份类比与 TOC 约束 | 第 2～4 节 | 8～9 分钟 |
| 主开关、子开关依赖与配置对象 | 第 5～7 节 | 11～13 分钟 |
| 生命周期类型、工厂与 Runner 调用顺序 | 第 8～10 节 | 14～16 分钟 |
| run / execution / worker、环境与 JUnit 边界 | 第 11～13 节 | 13～15 分钟 |
| Noop 副作用与精确 fail-open | 第 14 节 | 7～8 分钟 |
| 离线证据与两个核心场景 | 第 15 节、第 16.1～16.2 节 | 8～9 分钟 |
| 累积图、复述与 3 道核心小测 | 第 17～20 节 | 9～10 分钟 |
| 缓冲与提问 | 全课 | 5 分钟 |

总计约 75～85 分钟。第 16.3～16.4 节只作为教师题库；第 18 节只穿插必讲误区，其余作为课后自查；第 20.3 节教师清单不逐条占用课堂时间。

### 1.4 课堂最短路径

```text
第 2～4 节：确认可选治理不能成为Runner执行前提
-> 第 5～7 节：先判断配置是否有效、哪些子能力真正开启
-> 第 8～10 节：追踪Noop/Enabled选择与Runner四个生命周期动作
-> 第 11～13 节：分清run、execution、worker以及JUnit和环境传播
-> 第 14～16 节：限定fail-open，用离线证据判断两个核心场景
-> 第 17～20 节：更新累积图、完成复述和三道小测
```

---

## 2. 承接第十五课：旁观者接口已经中立，但谁来决定是否安装观察能力

第 15 课已经建立：

```text
common.runtime_hooks
-> provider默认NoopRuntimeHooks
-> 外部可选绑定QualityRuntimeHooks
-> Response和原异常不经过Quality节点
```

但它没有回答外层控制问题：

```text
Quality是否开启？
谁生成本轮run_id？
parallel-pool和serial-pool怎样共享同一run_id？
pytest执行阶段怎样获得自己的execution_id？
Quality关闭时为什么不能自动创建目录和JUnit参数？
```

本课关注的是 Runner 控制面，不是 HTTP 业务面。

### 2.1 两条链必须分开

```text
业务执行链：
Test -> Task -> Request -> BaseRequest -> HTTP

Quality控制链：
环境配置 -> QualityRunLifecycle -> Runner阶段边界
```

Quality 控制链可以准备观察环境和证据输入，但不能改写 Test、Task、Request 的业务职责。

### 2.2 直接 pytest 与项目 Runner 不是同一入口

- 项目 Runner 在权威收集成功且不是 collect-only 后创建 `QualityRunLifecycle`。
- 直接 pytest 不调用 `run_orchestration.runner.run()`，因此不经过本课的 Runner 生命周期。
- 直接 pytest 怎样由插件自行解析配置和补齐手动身份，属于第 17 课。

---

## 3. 认知障碍：最容易混淆的不是开关，而是身份所有权

### 3.1 把配置读取当成启用副作用

错误直觉：

```text
load_quality_config()
-> 创建reports/quality
-> 生成run_id
-> 安装pytest插件
```

真实边界：`load_quality_config()` 只构造不可变配置对象，不创建目录，也不安装插件。生成父级 `run_id` 是 `resolve_parent_quality_config()` 在 Quality 有效开启时的职责。

### 3.2 把 run、execution、worker 当成一个编号

```text
一个编号到处复用
-> 无法区分整轮运行、执行池和pytest进程
-> 并行事实不能可靠归并
```

真正需要的是层级身份：

```text
run_id
└─ execution_id
   └─ worker_id
```

### 3.3 把“fail-open”扩大成所有生命周期错误都不影响Runner

当前实现只在若干边界捕获普通 `Exception`。`ensure_junit_args()` 没有统一安全壳，`BaseException` 也不属于普通异常捕获范围。因此不能声称“Quality 任何错误都不会影响 Runner”。

### 3.4 TOC：本课真正的约束

本课的瓶颈不是记住多少环境变量，而是回答一个判断：

> 当前事实属于整轮运行、执行池、pytest worker，还是仅仅属于一个配置值？

因果链：

```text
身份层级不清
-> 环境传播边界不清
-> JUnit、worker分片和最终归并无法对账
-> Quality即使产出很多文件也不可信
```

---

## 4. 第一性原理与类比：总闸、批次号、生产线号和工位号

可以把 Quality 看成工厂质检系统：

| 框架对象 | 工厂类比 | 核心职责 |
| --- | --- | --- |
| `QUALITY_ENABLE` | 质检总闸 | 决定本轮是否接入 Quality 生命周期 |
| `QualityRuntimeConfig` | 本轮质检配置单 | 保存开关、身份输入和输出位置，不执行测试 |
| `run_id` | 整批货的批次号 | 关联同一次 Runner 运行中的所有执行池 |
| `execution_id` | 生产线编号 | 区分 `parallel-pool`、`serial-pool` 等执行阶段 |
| `worker_id` | 工位编号 | 在 pytest 进程内区分 master 或 xdist worker |
| Noop 生命周期 | 总闸关闭后的空操作面板 | 保持 Runner 调用接口稳定，但不新增 Quality 副作用 |

类比的边界：配置单不会自己启动生产线；`run_id` 也不会自己安装 pytest 插件。

---

## 5. `QUALITY_ENABLE`：主开关默认关闭，非法值不是“半开启”

### 5.1 可接受值

`parse_boolean_setting()` 会先去除首尾空白并转为小写：

| 结果 | 当前接受值 |
| --- | --- |
| `True` | `1`、`true`、`yes`、`on` |
| `False` | 未设置、空字符串、`0`、`false`、`no`、`off` |

其他值抛 `ValueError`。例如 `QUALITY_ENABLE=sometimes` 不会被猜测为 True 或 False。

### 5.2 主开关非法时的外层结果

```text
load_quality_config()
-> QUALITY_ENABLE非法
-> 抛ValueError

create_quality_run_lifecycle()
-> 捕获普通Exception
-> 输出Quality collection disabled警告
-> 返回NoopQualityRunLifecycle
```

所以“配置解析抛错”和“Runner 最终继续执行”是两个不同层级的事实。

### 5.3 默认关闭的意义

默认关闭不是功能缺失，而是可选能力的安全默认值：

- 不要求本地开发者准备 Quality 目录；
- 不自动生成父级 `run_id`；
- 不自动增加 JUnit 参数；
- 不把 Quality 后续模块变成 Runner 的默认依赖。

---

## 6. 子开关不是平级按钮：它们有依赖图

### 6.1 当前依赖关系

```text
QUALITY_ENABLE
├─ QUALITY_SEMANTIC_ENABLE
│  └─ QUALITY_METRICS_ENABLE
└─ QUALITY_FLAKY_HISTORY_ENABLE
   └─ QUALITY_FLAKY_STATE_ENABLE
```

准确关系：

| 能力 | 必要条件 | 条件不满足时 |
| --- | --- | --- |
| Semantic | Quality 开启 + Semantic 请求开启 | `semantic_enabled=False` |
| Metrics | Quality + Semantic + Metrics 请求开启 | `metrics_enabled=False`，可附依赖警告 |
| Flaky history | Quality + history 请求开启 | `flaky_history_enabled=False` |
| Flaky state | Quality + history + state 请求开启 | `flaky_state_enabled=False`，可附依赖警告 |

### 6.2 子开关非法不会关闭Quality主开关，但可能触发依赖级联

Semantic、Metrics、Flaky history 和 Flaky state 的解析函数会捕获自己的 `ValueError`，返回关闭状态和 warning；它们不会因此把主 `enabled` 自动改成 False。但依赖能力会继续按有效状态计算：Semantic 关闭会使 Metrics 关闭，Flaky history 关闭会使 Flaky state 关闭。

例如：

```text
QUALITY_ENABLE=1
QUALITY_METRICS_ENABLE=sometimes
-> Quality主生命周期仍可开启
-> metrics_enabled=False
-> metrics_warning记录非法值
```

### 6.3 Flaky 数据库路径的特殊边界

只有 Quality 与 Flaky history 都有效开启，并且 history 开关本身解析成功时，才进入数据库路径检查。此时以下问题会产生 warning：

- 路径缺失；
- 不是绝对路径；
- 网络共享未完成 SQLite 锁审查；
- 父目录不存在或不可写；
- 已存在路径不是普通文件。

当前实现中，路径 warning 不会反向把 `flaky_history_enabled` 改成 False。它表达“能力已请求开启，但输入合同存在风险”，后续 Flaky 阶段仍需 fail-open 处理。不要把 warning 误读成配置对象已经关闭 history。

---

## 7. `QualityRuntimeConfig`：配置事实，不是运行身份上下文

### 7.1 配置对象保存什么

```text
QualityRuntimeConfig
├─ enabled
├─ run_id / execution_id输入
├─ output_dir
├─ semantic / metrics开关与warning
└─ flaky history / state开关、路径与warning
```

它是 `frozen=True` 的 dataclass。不可变指的是字段不能普通赋值修改，不代表字段所引用的所有外部资源都自动不可变。

### 7.2 空白身份会归一化为缺失

```text
QUALITY_RUN_ID="   " -> None
QUALITY_EXECUTION_ID="	" -> None
```

`load_quality_config()` 不生成替代身份，只记录“当前未提供”。

### 7.3 输出目录分两步处理

```text
load_quality_config()
-> 保存Path；默认reports/quality
-> 不创建目录

resolve_parent_quality_config()
-> 相对路径基于PROJECT_ROOT解析为绝对路径
```

即使 Quality 关闭，父级解析也可以返回绝对 `output_dir` 配置事实；但 Noop 生命周期不会因此创建该目录。

### 7.4 配置对象不等于 `QualityRunContext`

`QualityRunContext(run_id, execution_id, worker_id, output_dir)` 是 pytest 进程内使用的运行上下文，由第 17 课插件建立。第 16 课只把父级身份通过 stage environment 送到 pytest 边界，不提前创建 worker 上下文。

---

## 8. `QualityRunLifecycle`：Runner 调用稳定接口，具体实现决定是否产生副作用

### 8.1 中性生命周期合同

```python
class QualityRunLifecycle(Protocol):
    enabled: bool

    def prepare(self, start_time): ...
    def ensure_junit_args(self, pytest_args): ...
    def stage_environment(self, execution_id): ...
    def finalize(self, *, start_time, expected_case_count,
                 pool_results, status): ...
```

Protocol 只定义 Runner 可调用的方法，不决定当前返回 Noop 还是 Enabled。

### 8.2 Noop 路径保持调用形状，不新增 Quality 动作

| 方法 | `NoopQualityRunLifecycle` 当前行为 |
| --- | --- |
| `prepare()` | 直接返回 |
| `ensure_junit_args()` | 返回内容相同的新列表，不增加 JUnit 参数 |
| `stage_environment()` | 返回 `nullcontext()`，不修改环境 |
| `finalize()` | 直接返回 |

Noop 的价值是让 Runner 不需要到处写：

```python
if quality_enabled:
    ...
```

但“无新增副作用”不等于“清空所有外部状态”。如果进程原本已有 `QUALITY_RUN_ID` 或 `QUALITY_EXECUTION_ID`，Noop 不会替 Runner 删除它们；它只是不新增、不覆盖。

### 8.3 Enabled 路径负责四个外层动作

```text
prepare
-> 尝试写初始run.json

ensure_junit_args
-> 已有--junitxml则保留
-> 否则增加Quality默认JUnit路径

stage_environment
-> 在执行池范围内设置父级身份和输出目录

finalize
-> 根据已执行池、预期用例数和生命周期状态委托Quality归并
```

这些动作属于 Runner 控制面，不是 Test call 阶段的下一调用节点。

---

## 9. 生命周期工厂：先做轻量预览，只有有效开启才加载父级环境实现

### 9.1 两阶段选择链

```text
create_quality_run_lifecycle()
-> 延迟import quality.config.load_quality_config
-> 读取preview
   ├─ 解析异常 -> 警告 + Noop
   ├─ preview.enabled=False -> Noop
   └─ preview.enabled=True
      -> 延迟import resolve_parent_quality_config
      -> 解析父级配置
         ├─ 异常或最终disabled -> Noop
         └─ enabled -> EnabledQualityRunLifecycle(runtime_config)
```

### 9.2 为什么要先 preview

关闭路径测试在新 Python 子进程中证明：

```text
QUALITY_ENABLE=FALSE
-> create_quality_run_lifecycle()
-> sys.modules中只出现quality和quality.config
```

这不是说 Python 绝对没有任何其他模块，而是证明 Quality 关闭时没有继续加载 Quality 运行时、Collector、Semantic 等后端实现。

### 9.3 工厂只捕获普通 `Exception`

配置加载和父级解析中的普通异常会降级为 Noop。`KeyboardInterrupt`、`SystemExit` 等 `BaseException` 不属于当前捕获范围。

### 9.4 类型关系不是调用关系

```text
NoopQualityRunLifecycle
-. 结构化满足 .-> QualityRunLifecycle Protocol

EnabledQualityRunLifecycle
-. 结构化满足 .-> QualityRunLifecycle Protocol
```

Runner 调用的是工厂返回对象，不是先调用 Protocol 再跳转到实现。

---

## 10. Runner 真实顺序：Quality 生命周期只在可执行运行中建立

### 10.1 生命周期创建之前还有两个门禁

```text
partition_pytest_args
-> 权威pytest收集
-> 检查收集原始退出码
-> collect-only判断
-> 通过后才创建Allure与Quality生命周期
```

因此：

- 收集失败或无可执行测试时，不创建本课的 Quality Runner 生命周期；
- `--collect-only` 成功后直接返回，不生成父级 `run_id`，也不进入 pytest 执行池；
- 不能把 `run()` 入口直接连到 `create_quality_run_lifecycle()`，中间存在真实门禁。

### 10.2 Runner 主顺序

```text
quality_run_lifecycle = create_quality_run_lifecycle()
quality_start_time = datetime.now(UTC)
quality_run_lifecycle.prepare(quality_start_time)

try:
    每个计划池：
    -> ensure_junit_args(base_args)
    -> 构造parallel或serial专用参数
    -> 若本池非空且未被前池终止：
       -> with stage_environment(stage_id)
       -> execute_pool(stage_id, nodeids, args)
    -> 否则构造NOT_RUN池事实
finally:
    -> allure_lifecycle.finalize()
    -> quality_run_lifecycle.finalize(...)
```

`prepare()` 在 Runner 的池执行 `try/finally` 之前调用；当前 Enabled 实现会自行捕获普通初始化异常，但这不等于 Runner 对任意自定义生命周期都提供相同保护。进入 `finally` 后，Runner 先调用 Allure finalize，再调用 Quality finalize；前一个调用若以未隔离的 `BaseException` 退出，后一个调用不会执行。

### 10.3 无 `-n` 与启用 `-n`

无 `-n`：

```text
全部case
-> 单一serial-pool
-> 一个execution_id=serial-pool
```

启用 `-n`：

```text
parallel case非空
-> parallel-pool环境
-> 并发pytest执行

serial case非空且未被终止
-> serial-pool环境
-> 串行pytest执行
```

空池或因前一池终止而跳过的池会形成 `NOT_RUN` 事实，但不会进入对应 `stage_environment()`。

### 10.4 `finalize()` 接收的不是“所有计划池都已执行”

Enabled 生命周期先过滤 `status=NOT_RUN` 的 `PoolExecutionResult`，再把实际执行池的：

- `stage_id`；
- JUnit 路径；
- 预期用例数；
- 运行生命周期状态；

交给 `finalize_quality_run()`。因此 `expected_execution_ids` 表示已执行池，不包含 `NOT_RUN` 池。

---

## 11. `run_id`：一次 Runner 运行的父级关联键

### 11.1 来源优先级

```text
resolve_parent_quality_config()
-> Quality关闭：不生成
-> Quality开启：
   ├─ 已配置QUALITY_RUN_ID -> 使用去空白后的值
   └─ 未配置 -> new_parent_run_id()
```

### 11.2 Jenkins 与本地格式

当 `JOB_NAME` 和 `BUILD_NUMBER` 同时存在：

```text
<sanitized-job>-<build>-<UTC timestamp>-<uuid前8位>
```

否则：

```text
local-<UTC timestamp>-<uuid前8位>
```

`new_parent_run_id()` 只有在两个 Jenkins 字段都存在时才把它们交给 `build_run_id()`；单独存在其中一个时，会走本地格式。

### 11.3 “稳定”不等于跨运行固定

标识符测试传入固定时间和固定 UUID，所以相同输入得到确定输出。真实未配置运行会使用当前 UTC 时间和随机 UUID：

- 同一 Runner 运行内，各执行池共享一个 `run_id`；
- 不同真实运行通常得到不同 `run_id`；
- 不能把随机 run ID 当成跨运行用例身份。

### 11.4 同一run只生成一次

父级配置在生命周期创建阶段生成一次 run ID。后续 `parallel-pool` 与 `serial-pool` 的 stage environment 都使用该值，不为每个池重新生成。

---

## 12. `execution_id`：当前Runner直接使用语义池名，不调用`build_execution_id()`

### 12.1 当前真实值

| Runner 场景 | `execution_id` |
| --- | --- |
| 未启用 `-n` | `serial-pool` |
| 启用 `-n` 的并发池 | `parallel-pool` |
| 启用 `-n` 的串行收尾池 | `serial-pool` |

Runner 直接把这些 `stage_id` 传给 `stage_environment()`。

### 12.2 `build_execution_id()` 是可用工具，但不是当前调用节点

```python
build_execution_id("parallel pool", 1)
# -> "parallel-pool-1"
```

该函数会清洗阶段名并要求 index 大于等于 1，但当前 Runner 主链没有调用它。真实测试还明确断言执行池身份中不存在 `-pool-1`。

### 12.3 execution身份必须与run身份组合理解

单独的 `serial-pool` 会在多次运行中重复。真正可关联的层级是：

```text
run_id=run-A + execution_id=serial-pool
run_id=run-B + execution_id=serial-pool
```

它们属于两个不同执行阶段事实，不能只按 `execution_id` 全局去重。

### 12.4 stage environment设置什么

Enabled 上下文进入时临时设置：

```text
QUALITY_ENABLE=1
QUALITY_RUN_ID=<父级run_id>
QUALITY_EXECUTION_ID=<当前stage_id>
QUALITY_OUTPUT_DIR=<已解析绝对路径>
```

退出时逐项恢复进入前的值；原来不存在的变量会删除，原来存在的变量会恢复。

### 12.5 它不管理所有Quality变量

`quality_stage_environment()` 只主动覆盖上述四项。Semantic、Metrics、Flaky 等开关若已存在于进程环境，会按普通环境继承规则继续存在；本上下文不会重新写入或清理它们。

---

## 13. JUnit 与 `worker_id`：一个在Runner准备，一个在pytest进程确定

### 13.1 Enabled 为什么补 JUnit 参数

后续 Quality 归并需要把 Case、失败和机器统计对齐。Enabled 生命周期会检查当前 pytest 参数：

```text
已有--junitxml
-> 原样保留调用方路径

没有--junitxml
-> 增加<quality_output>/junit/quality.xml
```

Noop 只返回内容相同的参数列表，不新增路径。

### 13.2 多池运行会继续加后缀

生命周期先确保存在 JUnit 参数，随后 Runner 的参数构造器按池改名：

```text
quality.xml
├─ parallel-pool -> quality-parallel.xml
└─ serial-pool   -> quality-serial.xml
```

这避免两个池写同一个文件。未启用 `-n` 时不经过池后缀改写，仍使用默认或用户提供的路径。

### 13.3 参数存在不等于文件已经生成

`ensure_junit_args()` 只修改参数。JUnit 文件仍要由后续 pytest 执行成功写出；配置路径、`PoolExecutionResult.junit_path` 或 finalize 输入都不能单独证明文件存在且完整。

### 13.4 `worker_id` 不由生命周期创建

本课只建立：

```text
Runner stage environment
-> run_id + execution_id进入pytest边界
```

第 17 课的 pytest 插件才会：

```text
非xdist执行 -> worker_id=master
xdist worker -> workerinput中的workerid，例如gw0
-> 构造QualityRunContext(run_id, execution_id, worker_id, output_dir)
```

`QualityRunContext` 是 pytest 进程内对象，不是 `stage_environment()` 的返回数据，也不是 Runner 的 `PoolExecutionResult`。

---

## 14. 精确边界：Noop无新增副作用，Enabled也不是绝对fail-open

### 14.1 当前普通异常边界

| 边界 | 普通 `Exception` 当前处理 |
| --- | --- |
| 生命周期工厂读取 preview / 父级配置 | 警告并返回 Noop |
| `Enabled.prepare()` | 警告并继续 |
| 初始 `run.json` 写入 | 内层再次捕获并警告 |
| `Enabled.finalize()` | 警告并继续 |
| 最终 `run.json` 写入 | 内层捕获并警告 |
| `Enabled.stage_environment()` 创建上下文管理器 | 创建失败时警告并返回 `nullcontext()` |
| `ensure_junit_args()` | 没有统一 `try/except` 安全壳 |

### 14.2 上下文恢复不等于吞掉业务异常

```text
进入quality_stage_environment
-> 保存旧环境
-> 设置当前run/execution
-> pytest执行抛异常
-> finally恢复旧环境
-> 原异常继续交给Runner处理
```

环境恢复保证的是状态收口，不是把 pytest 异常转换成成功。

### 14.3 `BaseException` 不在普通降级范围

生命周期内部主要捕获 `Exception`，不保证隔离 `KeyboardInterrupt`、`SystemExit`。Runner 会把池执行阶段的这类中断标记为 `INTERRUPTED` 后重抛；如果中断发生在池执行 `try/finally` 建立之前，不能声称 Quality finalize 一定执行。即使已经进入 Runner `finally`，Allure finalize 仍先执行；它若抛出未捕获的 `BaseException`，后续 Quality finalize 也会被跳过。

### 14.4 生命周期状态不是测试通过状态

```text
FINISHED
-> Runner执行流程完整结束

PARTIAL
-> PoolExecutionResult出现ERROR，或Runner普通异常

INTERRUPTED
-> KeyboardInterrupt / SystemExit
```

pytest 返回测试失败退出码，但执行池正常返回时，生命周期仍可能是 `FINISHED`。测试是否通过应读取 pytest / Runner 退出事实，不能用 Quality `RunLifecycleStatus` 替代。

### 14.5 Noop的准确表述

> Noop 生命周期不创建父级身份、不增加 JUnit 参数、不主动修改 stage environment，也不写 Quality 产物；它不负责清洗调用前已经存在的外部环境。

---

## 15. 轻量验证：55条配置、生命周期、身份与Runner接线离线测试

### 15.1 安全命令

该命令只运行四个精确离线文件。教师应课前原样预跑；课堂只观察第 15.3 节核心证据，不逐行讲解脚本。命令清空项目默认 addopts、禁用第三方插件自动加载、使用仓库内专用 `--basetemp`，并在结束后恢复进程环境和安全清理临时目录。

```powershell
$environmentNames = @(
  'API_CASE_DOTENV_PATH',
  'QUALITY_ENABLE',
  'PYTHONDONTWRITEBYTECODE',
  'PYTEST_DISABLE_PLUGIN_AUTOLOAD'
)
$previousEnvironment = @{}
foreach ($name in $environmentNames) {
  $previousEnvironment[$name] =
    [Environment]::GetEnvironmentVariable(
      $name,
      [EnvironmentVariableTarget]::Process
    )
}

$trimSeparators = [char[]]@('\', '/')
$repositoryRoot = (Get-Item -LiteralPath '.').FullName.TrimEnd($trimSeparators)
$tempRoot = Join-Path `
  $repositoryRoot `
  ('.api-case-lesson16-' + [guid]::NewGuid().ToString('N'))
$pytestTemp = Join-Path $tempRoot 'pytest'
New-Item -ItemType Directory -Path $tempRoot -Force | Out-Null
$pytestExitCode = 1

try {
  [Environment]::SetEnvironmentVariable(
    'API_CASE_DOTENV_PATH',
    (Resolve-Path -LiteralPath '.env.example' -ErrorAction Stop).Path,
    [EnvironmentVariableTarget]::Process
  )
  [Environment]::SetEnvironmentVariable(
    'QUALITY_ENABLE',
    '0',
    [EnvironmentVariableTarget]::Process
  )
  [Environment]::SetEnvironmentVariable(
    'PYTHONDONTWRITEBYTECODE',
    '1',
    [EnvironmentVariableTarget]::Process
  )
  [Environment]::SetEnvironmentVariable(
    'PYTEST_DISABLE_PLUGIN_AUTOLOAD',
    '1',
    [EnvironmentVariableTarget]::Process
  )

  & .\.venv\Scripts\python.exe -m pytest `
    -o addopts= `
    -p no:cacheprovider `
    --basetemp $pytestTemp `
    tests/quality/test_quality_config.py `
    tests/quality/test_quality_lifecycle.py `
    tests/quality/test_quality_identifiers.py `
    tests/quality/test_quality_run_master.py `
    -q
  $pytestExitCode = $LASTEXITCODE
}
finally {
  foreach ($name in $environmentNames) {
    [Environment]::SetEnvironmentVariable(
      $name,
      $previousEnvironment[$name],
      [EnvironmentVariableTarget]::Process
    )
  }

  if (Test-Path -LiteralPath $tempRoot) {
    $resolvedTempRoot =
      (Get-Item -LiteralPath $tempRoot).FullName.TrimEnd($trimSeparators)
    $resolvedParent =
      (Split-Path -Parent $resolvedTempRoot).TrimEnd($trimSeparators)
    $resolvedLeaf = Split-Path -Leaf $resolvedTempRoot
    if (
      $resolvedParent -eq $repositoryRoot -and
      $resolvedLeaf -like '.api-case-lesson16-*'
    ) {
      Remove-Item -LiteralPath $resolvedTempRoot -Recurse -Force
    }
    else {
      Write-Warning "Refused to clean unexpected path: $resolvedTempRoot"
    }
  }
}

if ($pytestExitCode -ne 0) {
  throw "Lesson 16 offline tests failed: $pytestExitCode"
}
```

### 15.2 当前结果

```text
55 passed
```

### 15.3 四组测试分别冻结什么

| 测试组 | 数量 | 主要证据 |
| --- | ---: | --- |
| `test_quality_config.py` | 25 | 主开关取值、子开关依赖、空白身份、输出目录和 Flaky 路径 warning |
| `test_quality_lifecycle.py` | 3 | 关闭路径轻量导入、Noop 无新增副作用、Enabled JUnit / 环境 / 状态映射 |
| `test_quality_identifiers.py` | 14 | run、execution、case、invocation、request event 和 failure 标识合同 |
| `test_quality_run_master.py` | 13 | collect-only门禁、父级run只生成一次、池身份、环境恢复、JUnit与finalize接线 |

课堂只观察六项：

1. Quality 关闭时工厂只加载轻量配置并返回 Noop；
2. Noop 不新增文件、JUnit 参数和 stage environment；
3. collect-only 不生成或注入 Quality 身份；
4. `parallel-pool` 与 `serial-pool` 共享同一个父级 `run_id`；
5. stage environment 退出后恢复原环境；
6. Enabled 在缺少 JUnit 参数时补默认路径，已有路径时保留。

### 15.4 不能证明什么

这 55 条测试不能证明：

- 真实 Jenkins、真实 xdist worker 或远程节点已经工作；
- 第 17 课插件已创建 `QualityRunContext` 或 worker 分片；
- 配置的 JUnit 路径对应文件一定存在且完整；
- Semantic、Metrics、Flaky 的最终治理结果正确；
- 真实 API、模型、Billing 或媒体用例成功；
- 所有 `BaseException` 都被 Quality 生命周期隔离。

---

## 16. 课堂活动：两个核心场景与两个教师题库场景

课堂先独立判断 A、B，再对照答案。C、D 只供教师按需追问，不进入核心时间，也不作为课后作业。

### 16.1 场景 A：Quality关闭，但外部已有旧身份变量

```text
QUALITY_ENABLE=0
QUALITY_RUN_ID=outside-run
QUALITY_EXECUTION_ID=outside-execution
Runner执行一个serial-pool
```

答案：

- 生命周期类型：`NoopQualityRunLifecycle`；
- 是否生成新 `run_id`：否；
- 是否增加 JUnit 参数：否；
- 是否清理旧身份：否，Noop 不修改外部现有环境；
- 是否进入 Quality finalize 后端：否。

### 16.2 场景 B：Quality开启，无显式run ID，未启用`-n`

```text
QUALITY_ENABLE=1
QUALITY_RUN_ID未设置
pytest参数中没有--junitxml
全部测试进入单一串行池
```

答案：

- 父级配置生成一个 `run_id`；
- 当前 `execution_id=serial-pool`；
- Runner 增加默认 Quality JUnit 参数；
- stage environment 仅在该池执行范围内覆盖四个父级变量，退出后恢复；
- `worker_id` 尚未由本课生命周期创建。

### 16.3 教师题库 C：启用`-n`，parallel与serial池都有测试

答案：

- 两个执行池共享同一 `run_id`；
- execution IDs 依次是 `parallel-pool`、`serial-pool`；
- 每个池进入自己的 stage environment；
- JUnit 路径分别加 `parallel`、`serial` 后缀；
- 当前 Runner 不调用 `build_execution_id()`。

### 16.4 教师题库 D：主开关非法

```text
QUALITY_ENABLE=sometimes
```

答案：

- `load_quality_config()` 单独调用会抛 `ValueError`；
- 生命周期工厂捕获普通异常并返回 Noop；
- Runner 输出 Quality disabled 警告后仍可执行原测试路径；
- 不能把该状态描述成“Quality 部分开启”。

### 16.5 一张判断表

| 场景 | 生命周期 | 新父级身份 | JUnit参数 | 环境处理 |
| --- | --- | --- | --- | --- |
| Quality关闭 | Noop | 不生成 | 不增加 | 不修改，也不清洗旧值 |
| Enabled单串行池 | Enabled | 一个run ID | 缺少时补默认路径 | 临时设置serial-pool后恢复 |
| Enabled双池 | Enabled | 两池共享一个run ID | 分池后缀 | 每个实际执行池独立进入并恢复 |
| 主开关非法 | Noop | 不生成 | 不增加 | 工厂警告后降级 |

---

## 17. 第十六版累积链路总图：Runner在执行池外层建立可选Quality生命周期

本图继承第 15 课的业务控制链、Runtime Hooks 旁路，以及第 14 课的 pytest、Runner、JUnit、Allure 和 execution-result 边界。本课只展开 Quality 配置、生命周期和父级身份；第 17 课的 pytest 插件、worker 身份与 Adapter 绑定仍保持折叠。

`-->` 表示函数调用、异常或生命周期控制；`==>` 表示对象输入、返回值、环境值或事实产物；`-.->` 表示类型/依赖、配置条件、可选产物或后续课程接口。

```mermaid
flowchart TD
    ENTRY["本地命令或 Jenkins"]
    MODE{"选择执行入口"}

    subgraph DIRECT["直接 pytest 路径（既有边界）"]
        DIRECT_CMD["直接 pytest"]
        DIRECT_PYTEST["pytest.main / pytest CLI"]
        DIRECT_EXIT["本次 pytest 原始退出码"]
        DIRECT_JUNIT["JUnit XML<br/>仅传入 --junitxml 时"]
        DIRECT_RAW["Allure raw<br/>仅传入 --alluredir 时"]
        DIRECT_ALLURE_LIFECYCLE["module/conftest.py<br/>直接pytest的Allure生命周期"]
        DIRECT_ALLURE_VIEW["直接pytest的Allure HTML / history<br/>满足条件才生成"]

        DIRECT_CMD -->|启动| DIRECT_PYTEST
        DIRECT_PYTEST ==>|返回| DIRECT_EXIT
        DIRECT_PYTEST -. "按参数写入" .-> DIRECT_JUNIT
        DIRECT_PYTEST -. "按参数写入" .-> DIRECT_RAW
        DIRECT_PYTEST -. "有--alluredir且非跳过场景：sessionstart prepare" .-> DIRECT_ALLURE_LIFECYCLE
        DIRECT_RAW ==>|作为最终生成输入| DIRECT_ALLURE_LIFECYCLE
        DIRECT_ALLURE_LIFECYCLE -. "sessionfinish finalize且生成条件满足" .-> DIRECT_ALLURE_VIEW
    end

    subgraph RUNNER_PATH["项目 Runner 与并列执行事实（既有边界）"]
        RUN_MASTER["run_master.py"]
        RUNNER["run_orchestration.runner.run()"]
        COLLECT["权威收集函数"]
        COLLECTION["CollectionResult<br/>测试项 + 收集原始退出码"]
        COLLECTION_OK{"收集原始退出码为0？"}
        COLLECT_ONLY_GATE{"是否 collect-only？"}
        FAILED_COLLECTION_WRITE["收集失败且非collect-only<br/>空池execution-result payload"]
        COLLECT_ONLY_RETURN["collect-only直接返回收集原始退出码<br/>不写execution-result"]
        POOL_MODE{"是否启用 -n？"}
        ONE_POOL["无 -n<br/>完整 C 进入 serial-pool"]
        TWO_POOLS["启用 -n<br/>P 进入 parallel-pool<br/>S 进入 serial-pool"]
        ACTUAL_POOLS["真实执行池<br/>仅非空且未被终止<br/>stage_id + nodeids"]
        EXECUTE["execute_pool(stage_id, nodeids, args)"]
        PYTEST_POOL["pytest.main()<br/>执行显式 nodeid 池"]
        POOL_RAW["pytest 池级原始退出码"]
        POOL_RESULT["全部 PoolExecutionResult<br/>含 COMPLETED / ERROR / NOT_RUN"]
        MERGED_EXIT["写入前项目级归并退出码<br/>_final_exit_code()"]

        JUNIT_POOL["JUnit 池级 XML"]
        ALLURE_POOL_RAW["本池隔离 Allure raw"]
        ALLURE_MERGE["merge_pool(stage_id)"]
        FINAL_RAW["最终 Allure raw"]
        ALLURE_FINALIZE["Allure finalize()<br/>Runner 主路径 finally"]
        ALLURE_VIEW["Allure HTML / history<br/>满足条件才生成"]

        EXEC_PAYLOAD["execution-result payload"]
        WRITE_BOUNDARY["execution-result 写入边界"]
        EXEC_RESULT["Runner execution result"]
        WRITE_FAIL["普通写入异常"]
        RETURN_EXIT["Runner 项目级实际返回码"]

        RUN_MASTER -->|调用| RUNNER
        RUNNER -->|调用| COLLECT
        COLLECT ==>|返回| COLLECTION
        COLLECTION ==>|收集事实输入| COLLECTION_OK
        COLLECTION_OK -->|否，且非collect-only| FAILED_COLLECTION_WRITE
        COLLECTION_OK -->|否，且collect-only| COLLECT_ONLY_RETURN
        COLLECTION_OK -->|是| COLLECT_ONLY_GATE
        COLLECT_ONLY_GATE -->|是| COLLECT_ONLY_RETURN
        COLLECT_ONLY_GATE -->|否| POOL_MODE
        POOL_MODE -->|无 -n| ONE_POOL
        POOL_MODE -->|启用 -n| TWO_POOLS
        ONE_POOL ==>|提供| ACTUAL_POOLS
        TWO_POOLS ==>|提供| ACTUAL_POOLS
        ACTUAL_POOLS ==>|stage_id 与 nodeids| EXECUTE
        EXECUTE -->|调用| PYTEST_POOL
        PYTEST_POOL ==>|正常返回 int| POOL_RAW
        EXECUTE ==>|返回池事实| POOL_RESULT
        POOL_RAW ==>|写入 raw_pytest_exit_code| POOL_RESULT
        RUNNER ==>|空池或终止后构造 NOT_RUN| POOL_RESULT
        POOL_RESULT ==>|全部池结果归并| MERGED_EXIT

        PYTEST_POOL -. "按 JUnit 参数写入" .-> JUNIT_POOL
        PYTEST_POOL -. "按本池 --alluredir 写入" .-> ALLURE_POOL_RAW
        EXECUTE -->|finally 调用| ALLURE_MERGE
        ALLURE_POOL_RAW ==>|提供本池文件| ALLURE_MERGE
        ALLURE_MERGE ==>|逐池归并| FINAL_RAW
        RUNNER -->|主路径 finally 第1个调用| ALLURE_FINALIZE
        FINAL_RAW ==>|作为生成输入| ALLURE_FINALIZE
        ALLURE_FINALIZE -. "配置、CLI 与生成均成功" .-> ALLURE_VIEW

        COLLECTION ==>|计划与收集事实| EXEC_PAYLOAD
        POOL_RESULT ==>|全部池事实| EXEC_PAYLOAD
        MERGED_EXIT ==>|payload.final_exit_code| EXEC_PAYLOAD
        EXEC_PAYLOAD ==>|作为写入输入| WRITE_BOUNDARY
        FAILED_COLLECTION_WRITE ==>|作为写入输入| WRITE_BOUNDARY
        COLLECT_ONLY_RETURN ==>|形成直接返回事实| RETURN_EXIT
        WRITE_BOUNDARY -. "写入成功才生成" .-> EXEC_RESULT
        WRITE_BOUNDARY ==>|写入成功：返回原项目码<br/>正常归并码或收集原始码| RETURN_EXIT
        WRITE_BOUNDARY -->|普通写入异常| WRITE_FAIL
        WRITE_FAIL ==>|按0/1与2/3/4/5规则形成| RETURN_EXIT
    end

    subgraph QUALITY_RUN["第 16 课新增：Quality配置、生命周期与父级身份"]
        FACTORY["create_quality_run_lifecycle()"]
        PREVIEW["load_quality_config()<br/>轻量配置预览"]
        NOOP["NoopQualityRunLifecycle"]
        RESOLVE["resolve_parent_quality_config()"]
        PARENT_CONFIG["父级 QualityRuntimeConfig<br/>绝对 output_dir + 单一 run_id"]
        ENABLED["EnabledQualityRunLifecycle"]
        LIFECYCLE["所选 QualityRunLifecycle 对象"]
        LIFECYCLE_PROTOCOL["QualityRunLifecycle Protocol"]

        PREPARE["lifecycle.prepare(start_time)"]
        ENSURE_JUNIT["lifecycle.ensure_junit_args(pytest_args)"]
        PREPARED_ARGS["Quality处理后的base args<br/>保留已有或补默认 JUnit 路径"]
        POOL_ARGS["本池最终 pytest args<br/>无-n直接使用；-n再构造并改JUnit后缀"]
        STAGE["lifecycle.stage_environment(execution_id)"]
        EXECUTION_ID["当前 execution_id<br/>parallel-pool 或 serial-pool"]
        STAGE_ENV["本池临时环境<br/>enable + run_id + execution_id + output_dir"]
        RESTORE_ENV["退出 with 后恢复进入前环境"]
        FINALIZE["lifecycle.finalize(start_time,<br/>expected_case_count, pool_results, status)"]
        FINAL_STATUS["RunLifecycleStatus<br/>FINISHED / PARTIAL / INTERRUPTED"]
        QUALITY_BACKEND["Quality归并与治理<br/>第 18～21 课展开"]

        FACTORY -->|调用轻量预览| PREVIEW
        PREVIEW ==>|返回预览配置| FACTORY
        FACTORY -->|预览关闭、父级最终关闭，或任一步普通异常被捕获：构造| NOOP
        FACTORY -->|预览enabled=True：调用| RESOLVE
        RESOLVE ==>|返回父级配置| PARENT_CONFIG
        PARENT_CONFIG ==>|返回到工厂| FACTORY
        FACTORY -->|父级config.enabled=True：构造| ENABLED
        NOOP ==>|工厂返回| LIFECYCLE
        ENABLED ==>|工厂返回| LIFECYCLE
        NOOP -. "结构化实现" .-> LIFECYCLE_PROTOCOL
        ENABLED -. "结构化实现" .-> LIFECYCLE_PROTOCOL

        RUNNER -->|收集成功且非 collect-only 后调用| FACTORY
        LIFECYCLE ==>|由Runner持有| RUNNER
        RUNNER -->|调用| PREPARE
        RUNNER -->|每个计划池调用| ENSURE_JUNIT
        ENSURE_JUNIT ==>|返回| PREPARED_ARGS
        PREPARED_ARGS ==>|Runner继续形成parallel或serial参数| POOL_ARGS
        POOL_ARGS ==>|作为 args 输入| EXECUTE
        ACTUAL_POOLS ==>|当前 stage_id| EXECUTION_ID
        EXECUTION_ID ==>|作为参数| STAGE
        RUNNER -->|仅非空且未终止的池 with 调用| STAGE
        PARENT_CONFIG ==>|提供父级配置| STAGE
        STAGE ==>|Enabled进入上下文时设置；Noop无动作| STAGE_ENV
        STAGE_ENV ==>|作为进程环境输入| PYTEST_POOL
        RUNNER -->|在stage上下文内调用| EXECUTE
        STAGE -->|Enabled上下文退出时finally恢复| RESTORE_ENV
        RUNNER -->|Allure finalize未阻断后，第2个调用| FINALIZE
        COLLECTION ==>|提供 expected_case_count| FINALIZE
        POOL_RESULT ==>|提供已产生的池结果| FINALIZE
        RUNNER ==>|按执行结果或异常设置| FINAL_STATUS
        FINAL_STATUS ==>|提供生命周期状态| FINALIZE
        FINALIZE -. "仅Enabled委托；后续课展开" .-> QUALITY_BACKEND
    end

    subgraph BUSINESS["业务执行链（保持原控制权）"]
        TEST["Test<br/>场景、输入与预期"]
        TASK["领域 Task / BaseTask 兼容入口"]
        REQUEST["领域 Request 或窄 Capability"]
        BASE["BaseRequest.request()"]
        POLLING["BaseRequest.poll_get()<br/>独立 Polling 入口"]
        REQUEST_BRANCH["原请求分支<br/>无 Retry 或 _send_with_retry()"]
        SEND["每次 attempt 的 _send(context)<br/>Middleware + Session.request"]
        SSE["Task 内 SSE 消费循环<br/>解析、检查并关闭 Response"]
        ASSERTIONS["Assertions<br/>结构与业务判断"]
        CALL_END["Test call 阶段结束"]
        TEARDOWN["pytest teardown<br/>cleanup / close / 资源附件"]

        TEST -->|调用| TASK
        TASK -->|调用| REQUEST
        REQUEST -->|普通或 stream 请求调用| BASE
        REQUEST -->|Polling 场景调用| POLLING
        BASE -->|调用无 Retry 或 Retry 分支| REQUEST_BRANCH
        REQUEST_BRANCH -->|每次 attempt 调用| SEND
        SEND ==>|attempt Response| REQUEST_BRANCH
        REQUEST_BRANCH ==>|最终 Response| BASE
        BASE ==>|Response 返回| REQUEST
        POLLING ==>|最终 Response 返回| REQUEST
        REQUEST ==>|Response 或领域结果返回| TASK
        TASK ==>|普通路径返回| TEST
        TASK -->|stream=True 时消费并关闭| SSE
        SSE ==>|chunks 或领域结果返回| TEST
        TEST -->|调用| ASSERTIONS
        ASSERTIONS -->|正常完成 call 阶段| CALL_END
        ASSERTIONS -->|抛 AssertionError| CALL_END
        REQUEST_BRANCH -->|最终未恢复异常沿调用栈抛出| CALL_END
        POLLING -->|最终异常沿调用栈抛出| CALL_END
        CALL_END -->|pytest 生命周期进入| TEARDOWN
    end

    subgraph RUNTIME["第 15 课既有：Runtime Hooks 旁路"]
        OBSERVER["RuntimeObserver<br/>normalize / start 门面"]
        OP_OBSERVATION["RuntimeOperationObservation<br/>由 BaseRequest 局部变量持有"]
        POLL_OBSERVATION["RuntimePollingObservation<br/>由 poll_get() 局部变量持有"]
        REQUEST_MIDDLEWARE["RuntimeObservationMiddleware"]
        COMMON_LIFECYCLE["common.runtime_hooks.lifecycle<br/>安全调用与 lease"]
        PROVIDER["Runtime Hooks Provider<br/>当前 ContextVar 实现"]
        STARTING_HOOKS["开始时固定的 Hooks 对象"]
        RUNTIME_NOOP["NoopRuntimeHooks<br/>默认无后端副作用"]
        RUNTIME_PROTOCOL["RuntimeHooks Protocol<br/>中性合同"]
        ADAPTER["QualityRuntimeHooks<br/>外部可选 Adapter"]

        BASE -->|调用 normalize_metadata / start_operation| OBSERVER
        POLLING -->|调用 normalize_metadata / start_polling| OBSERVER
        OBSERVER -->|调用 begin_operation / begin_polling_session| COMMON_LIFECYCLE
        OBSERVER ==>|构造并返回| OP_OBSERVATION
        OBSERVER ==>|构造并返回| POLL_OBSERVATION
        BASE -->|最终 Response 调用 finish_response| OP_OBSERVATION
        BASE -->|异常分支调用 finish_error| OP_OBSERVATION
        POLLING -->|状态、等待、成功或异常调用| POLL_OBSERVATION
        POLL_OBSERVATION -->|观察并结束 polling / operation| COMMON_LIFECYCLE
        OP_OBSERVATION -->|普通完成或异常时结束 operation| COMMON_LIFECYCLE
        OP_OBSERVATION -->|2xx + stream + owned 时绑定流| COMMON_LIFECYCLE
        SSE -->|observe_stream_line；消费结束 finish_stream| COMMON_LIFECYCLE
        SEND -->|对应Middleware阶段实际执行时调用| REQUEST_MIDDLEWARE
        REQUEST_MIDDLEWARE -->|started / succeeded / failed| COMMON_LIFECYCLE
        RUNTIME_NOOP ==>|默认实例存入| PROVIDER
        ADAPTER -. "外层可选绑定；common不静态依赖quality" .-> PROVIDER
        COMMON_LIFECYCLE -->|无active operation时读取| PROVIDER
        PROVIDER ==>|返回| STARTING_HOOKS
        COMMON_LIFECYCLE ==>|固定到lease或RequestContext| STARTING_HOOKS
        COMMON_LIFECYCLE -->|普通Exception中性降级调用| STARTING_HOOKS
        COMMON_LIFECYCLE -. "依赖方法签名" .-> RUNTIME_PROTOCOL
        RUNTIME_NOOP -. "结构化实现" .-> RUNTIME_PROTOCOL
        ADAPTER -. "quality依赖common合同" .-> RUNTIME_PROTOCOL
    end

    subgraph NEXT_PLUGIN["第 17 课接口：pytest进程内身份与采集（折叠）"]
        PYTEST_PLUGIN["pytest Quality插件"]
        RUN_CONTEXT["QualityRunContext<br/>run + execution + worker + output_dir"]
        WORKER_ID["worker_id<br/>非xdist为master<br/>xdist读取workerinput"]
        WORKER_INPUT["xdist workerinput<br/>controller传递父级runtime config"]
        WORKER_LEDGER["worker Collector与原始账本<br/>第17课展开"]

        STAGE_ENV -. "插件读取父级环境" .-> PYTEST_PLUGIN
        DIRECT_PYTEST -. "插件独立加载配置；缺省身份可生成run_id + manual-pytest" .-> PYTEST_PLUGIN
        PYTEST_PLUGIN -. "具体执行进程确定worker身份" .-> WORKER_ID
        PYTEST_PLUGIN -. "非xdist读取配置并构造" .-> RUN_CONTEXT
        PYTEST_PLUGIN -. "xdist controller写入" .-> WORKER_INPUT
        WORKER_INPUT -. "xdist worker读取并构造" .-> RUN_CONTEXT
        WORKER_ID -. "作为构造字段" .-> RUN_CONTEXT
        PYTEST_PLUGIN -. "具体执行进程configure_collector(run_context)" .-> WORKER_LEDGER
        RUN_CONTEXT -. "作为Collector输入" .-> WORKER_LEDGER
        PYTEST_PLUGIN -. "具体执行进程bind_runtime_hooks(QualityRuntimeHooks)" .-> ADAPTER
    end

    ENTRY -->|选择| MODE
    MODE -->|直接执行| DIRECT_CMD
    MODE -->|项目 Runner| RUN_MASTER
    DIRECT_PYTEST -->|执行测试项| TEST
    PYTEST_POOL -->|执行显式测试项| TEST
```

课堂只沿 `QUALITY_RUN` 新增分支和 `NEXT_PLUGIN` 接口读图；直接 pytest、Runner 证据、业务链和 Runtime Hooks 只用于确认边界，不重新逐节点讲解。

### 17.1 读图规则

1. 直接 pytest 不调用 Runner 的 `create_quality_run_lifecycle()`，但 Quality 插件是另一条独立入口：Quality 有效开启时，它可以自行补 `run_id` 与 `execution_id=manual-pytest`。这不能反推为经过了 Runner 生命周期。
2. `load_quality_config()` 返回配置事实，生命周期工厂返回 Noop 或 Enabled 对象，pytest 插件以后才创建 `QualityRunContext`；三者不是同一个对象。
3. `prepare()`、`ensure_junit_args()`、`stage_environment()` 和 `finalize()` 都由 Runner 调用。`ensure_junit_args()` 按计划池准备参数；`stage_environment()` 只包住非空且未被终止的真实执行池。二者是兄弟调用，不是前者调用后者。
4. 无 `-n` 时完整测试集合进入一个 `serial-pool`；启用 `-n` 时才拆为 `parallel-pool` 与 `serial-pool`。当前 Runner 直接把这两个语义池名作为 `execution_id`，没有调用 `build_execution_id()`。
5. stage environment 只提供 run、execution 和输出目录等父级信息。`worker_id` 由第 17 课 pytest 插件在具体 pytest 进程内确定，生命周期不能提前伪造。
6. `ensure_junit_args()` 只保证执行参数请求一个 JUnit 路径；JUnit 文件仍由 pytest 写入，文件是否生成取决于对应 pytest 执行。
7. `PoolExecutionResult`、JUnit、Allure raw、Quality产物和 Runner execution result 是并列事实分支。`RunLifecycleStatus.FINISHED` 只表示生命周期完整走到结束，不表示测试通过。
8. Quality 生命周期和 Runtime Hooks 都是业务链侧边能力；Response、领域结果、AssertionError 和最终未恢复异常不经过 Quality 节点。

### 17.2 本课新增节点的最小闭环

```text
收集成功且非collect-only
-> Runner创建Quality生命周期
-> 轻量预览选择Noop或Enabled
-> Enabled解析一个父级run_id
-> Runner对每个计划池准备JUnit参数
-> 非空且未终止的池以当前语义池名作为execution_id进入临时环境
-> pytest执行池
-> 环境恢复
-> Runner finally先调用Allure finalize
-> 前一步未阻断时再调用Quality finalize

父级临时环境
-. 第17课pytest插件读取 .->
QualityRunContext + worker_id + worker原始账本
```

---

## 18. 常见误区

### 18.1 “`QUALITY_ENABLE=1`，所有子能力就一定开启”

错误。Semantic 依赖 Quality，Metrics 继续依赖 Semantic，Flaky state 依赖 Quality 与 history；子开关非法或依赖不满足时可以局部降级。

### 18.2 “Noop会清理所有旧Quality环境变量”

错误。Noop 的合同是“不新增本次 Quality 副作用”，不是替调用方清洗外部环境。进入 Runner 前已经存在的旧变量仍可能保留。

### 18.3 “配置对象已经包含本次worker身份”

错误。父级 `QualityRuntimeConfig` 在 Runner 层只有 run 与当前 stage 所需配置；`worker_id` 要等 pytest 插件进入具体进程后再确定。

### 18.4 “`build_execution_id()`测试通过，所以Runner一定使用它”

错误。工具函数存在且有独立合同，不等于真实主链调用它。当前 Runner 直接使用 `parallel-pool` 和 `serial-pool`。

### 18.5 “补了`--junitxml`，JUnit文件已经存在”

错误。参数只是写入请求。只有 pytest 实际运行并完成相应写入后，路径才可能对应文件。

### 18.6 “Quality是可选能力，所以所有失败都必然fail-open”

错误。当前工厂、Enabled `prepare()` 和 `finalize()` 主要对普通 `Exception` 做降级；`ensure_junit_args()` 没有统一安全壳，`BaseException` 也不在普通捕获范围。

### 18.7 “`FINISHED`表示全部测试通过”

错误。它表示 Runner 没有按当前生命周期规则被中断，测试是否通过仍看 pytest 池级事实和 Runner 最终执行事实。

课堂必讲 18.1、18.3、18.5、18.7；其余可穿插讲解或作为课后自查，不增加独立时间段。

---

## 19. 三分钟复述

建议按“开关选择 → 父级身份 → 执行池身份 → pytest进程接口 → 退出边界”复述：

```text
Quality默认关闭。Runner只有在权威收集成功且不是collect-only时，才创建QualityRunLifecycle。工厂先轻量读取配置：关闭或普通初始化异常返回Noop；有效开启才解析绝对输出目录，并为整次Runner运行保留一个父级run_id，再返回Enabled生命周期。

Runner调用prepare，然后对每个计划池准备JUnit参数；只有非空且未被前池终止的池才进入stage environment并执行pytest。无-n时，全部测试进入serial-pool；启用-n时，parallel与serial测试按计划进入各自语义池。当前Runner直接把parallel-pool或serial-pool作为execution_id，不调用build_execution_id。stage environment临时设置enable、run_id、execution_id和output_dir，执行结束后恢复原环境。

worker_id不由Runner生命周期生成。第17课pytest插件进入具体进程后，非xdist使用master，xdist worker读取workerinput，再与run_id和execution_id组成QualityRunContext。补JUnit参数不等于文件已生成；FINISHED不等于测试通过。Noop不新增Quality副作用，但也不清理外部旧变量；Enabled的fail-open范围也不能扩大为所有错误都不影响Runner。
```

---

## 20. 课堂小测与教师验收

### 20.1 三道核心小测

1. `QUALITY_ENABLE=0`，但进程外部已有 `QUALITY_RUN_ID=old-run`。Noop 是否会主动删除它？A 会 / B 不会（B）
2. Quality 开启、未传 `-n`，Runner 当前使用什么 `execution_id`？A `serial-pool` / B `serial-pool-1` / C `master`（A）
3. `ensure_junit_args()` 返回含 `--junitxml` 的参数后，能否直接断言 XML 文件已经生成？A 能 / B 不能（B）

### 20.2 教师题库（不进入核心时间）

4. `QUALITY_ENABLE=sometimes` 时，单独调用 `load_quality_config()` 与通过生命周期工厂调用的外层结果是否相同？A 相同 / B 不同（B：前者抛 `ValueError`，后者捕获普通异常并降级 Noop）
5. collect-only 是否创建 Quality 生命周期？A 创建 / B 不创建（B）
6. `worker_id=master` 是 Runner 的 `stage_environment()` 写入的吗？A 是 / B 否（B）
7. `RunLifecycleStatus.FINISHED` 能否证明 pytest 全部通过？A 能 / B 不能（B）
8. `ensure_junit_args()` 抛出 `BaseException` 时，当前代码是否承诺中性降级？A 承诺 / B 不承诺（B）

### 20.3 教师验收清单（不逐条占用课堂时间）

合格复述必须包含：

- Quality 生命周期的创建门禁：收集成功且非 collect-only；
- Noop 与 Enabled 的选择依据，以及 Noop“不新增但不清洗”的边界；
- 一个 run 下真正执行的一个或两个池共享 `run_id`，并使用自己的语义池名作为 `execution_id`；空池或终止池只形成 `NOT_RUN`，不进入对应 stage environment；
- `worker_id` 在第 17 课 pytest 进程内确定，不由本课生命周期生成；
- JUnit参数、JUnit文件、pytest退出事实和Runner最终事实彼此不同；
- `FINISHED` 与测试通过不同，fail-open 也不是无条件保证。

子开关全部取值、标识符构造细节和教师题库答案不作为三分钟复述的额外背诵项。

---

## 21. 课后作业：只更新生命周期与身份链，不写代码

### 21.1 必做内容

1. 在第 15 课累积图上增加本课 Quality 分支：标出创建门禁、Noop / Enabled、单一 `run_id`、每池 `execution_id`、JUnit参数准备、stage environment与恢复，并用虚线把父级环境连接到第 17 课 `worker_id` 接口。

课堂场景 A、B、三分钟复述和三道核心小测不作为课后提交物；文字稿选做。

### 21.2 不要求完成

- 不实现新生命周期或新标识符。
- 不修改 Runner、pytest 插件或 Quality 配置。
- 不创建 worker JSONL、Schema、Metrics 或 Flaky 结果。
- 不运行真实 xdist、Jenkins、API、模型、Billing 或媒体用例。
- 不提前完成第 17 课插件采集链。

---

## 22. 下一课接口

第 16 课已经建立：

```text
Runner执行门禁
-> Noop或Enabled生命周期
-> 一个父级run_id
-> 每个实际执行池的execution_id
-> 临时stage environment
-> pytest执行池
-> 环境恢复与finalize
```

但父级身份进入 pytest 进程后，仍有四个问题没有回答：

```text
pytest插件在什么时点读取Quality配置和父级环境？
非xdist与xdist worker怎样确定worker_id？
run + execution + worker怎样组成QualityRunContext？
插件怎样安装Collector和Runtime Adapter，并在退出时收口worker原始账本？
```

第 17 课进入 pytest 插件与 worker 原始账本：

```text
stage environment
-. pytest插件读取 .->
pytest Quality插件
├─ 构造QualityRunContext(run_id, execution_id, worker_id, output_dir)
├─ configure_collector(run_context) -> worker原始账本
└─ bind_runtime_hooks(QualityRuntimeHooks) -> Runtime Adapter
```

直接 pytest 是并列插件入口：它不经过 Runner 生命周期，但 Quality 有效开启时，插件可以补齐手动 `run_id` 与 `execution_id=manual-pytest`；xdist controller 则先通过 `workerinput` 把父级配置传给 worker。两条路径都在第 17 课展开。

第 16 课解决“Runner是否启用Quality、父级身份怎样进入执行池”；第 17 课解决“pytest进程怎样把父级身份补成worker上下文，并把运行事件写成可归并的原始账本”。
