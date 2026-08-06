# 阶段5完成Runner、Quality、Metrics、Flaky与Jenkins验收详细开发方案

> 生成日期：2026-08-05
> 对齐复审：2026-08-06

> 上位方案：`dev_plan/离线框架能力分类用例与黄金路径详细执行方案_20260805.md`
> 前置阶段：阶段0～4已经完成协议、本地服务、四件套、能力分类、并发分类与黄金路径
> 稳定黄金路径：`module/offline_framework_example/test_full_framework_flow.py::TestFullFrameworkFlow::test_offline_async_media_flow`
> 阶段性质：运行级验收，不继续开发业务用例，不重构Runner、Quality、Metrics、Flaky或Jenkins
> 默认修改策略：零生产代码、零测试代码、零Jenkins改动；运行产物全部隔离到系统临时目录
> 文档边界：本阶段不更新`README.md`和`FRAMEWORK_TEST_SPEC.md`，统一留到阶段6

## 1. 需求复述与阶段目标

用户要求基于总体执行方案输出阶段5的详细开发方案。本阶段不再回答“单项框架能力是否正确”，也不扩张黄金路径，而是通过现有稳定入口回答以下运行级问题：

1. Runner能否权威收集离线模块，并保持并发池与串行池集合守恒；
2. Quality关闭时是否完全退化为普通pytest执行，不创建质量运行身份或机器产物；
3. Quality、Semantic和Metrics开启时，是否能够消费阶段2～4产生的同一组中性Runtime事实；
4. 阶段4暂缓的精确`model_id`是否在Quality开启后正确落为`offline-media-model`；
5. Retry、Polling、请求事件、逻辑调用和usage是否形成可追溯关系；
6. Flaky是否只依靠同一黄金nodeid的真实稳定通过样本，从`OBSERVING`进入`STABLE`；
7. Pipeline Summary是否消费本轮JUnit、Runner、Quality、Metrics和Flaky事实，而不是读取上轮残留；
8. 现有Jenkins参数化Pipeline能否把目标切换到离线模块，不新增Job或报告体系；
9. 可选质量阶段失败时是否保持fail-open，同时不制造假产物、假指标或假成功；
10. 阶段5是否可以在不修改黄金路径、服务、四件套、Quality内部实现和Jenkinsfile的前提下完成。

阶段5完成后应得到两类结论：

```text
阶段5运行级结论
＝ Runner / Quality / Semantic / Metrics / Flaky / Pipeline事实链是否可信

总体发布结论
＝ 阶段0～5能力全部通过 ＋ 完整框架回归无未决基线失败
```

两类结论不得混淆。阶段5自身验收通过，不代表既有完整框架基线债务自动消失。

## 2. 第一性原理与TOC判断

### 2.1 阶段5的问题本质

阶段5的本质不是“打开几个环境变量后pytest仍然通过”，而是证明同一次执行产生的事实能够沿完整链路被正确消费：

```text
pytest真实执行
→ Runner权威计划与池级退出码
→ JUnit和Allure
→ P0 Quality Case/Request事实
→ Semantic Operation/RequestGroup/Polling事实
→ Metrics聚合
→ Flaky历史和状态
→ Pipeline Summary与邮件渲染
```

只检查pytest退出码会漏掉Quality因fail-open没有产物、Semantic读取旧run_id、Flaky重复导入、Pipeline把缺失解释为0，以及JUnit、Metrics和pytest互相矛盾等问题。因此阶段5必须同时验证执行结论、运行身份、文件哈希、完整性状态和跨层关系。

### 2.2 TOC首要约束：运行身份与产物隔离

当前首要约束不是测试数量，也不是Jenkins节点性能，而是“本轮事实能否与历史残留严格区分”。

```text
复用reports/quality或固定run_id
→ 本轮与上轮JSONL、Metrics、Flaky事实混合
→ Pipeline Summary可能读取旧成功数据
→ Quality fail-open被误判为成功
→ 阶段5失去证明价值
```

控制顺序固定为：

```text
每个场景使用独立绝对临时目录
→ 每轮Quality使用唯一run_id
→ Flaky多轮只共享独占SQLite数据库
→ 所有JSON按run_id和manifest Hash核验
→ 最后才生成Pipeline Summary
```

### 2.3 第二约束：fail-open容易形成假绿

Quality、Semantic、Metrics和Flaky采用fail-open，正确语义是“可选观测失败不覆盖pytest原始结论”，而不是“扩展失败也算扩展成功”。阶段5采用双门禁：

```text
pytest门禁：原始执行结论正确
＋
观测门禁：要求启用的产物存在、版本匹配、run_id一致、Hash可信
```

任何要求存在的质量产物缺失，都判定该场景失败，不得因为pytest为0而放行。

### 2.4 第三约束：Flaky必须积累真实历史

Flaky不是单轮标签，而是同一Case身份在相同环境和执行画像下的历史投影。

```text
复用同一run_id或输出目录
→ 导入被判为重复/NOOP
→ 样本数量没有增加
→ 状态永远停留在OBSERVING
```

另一条错误链是人为制造随机失败，得到不真实的outcome switch。正确策略是每轮唯一run_id与输出目录、同一黄金nodeid、同一执行画像、同一独占数据库、全部真实通过。

### 2.5 第四约束：Jenkins包含外部系统边界

本地代码能够验证Pipeline结构、Runner命令、报告生成和归档输入，但不能替代真实Controller/Agent上的插件、归档链接和邮件通道。因此Jenkins验收拆为：

```text
本地可重复门禁
→ Jenkinsfile结构测试
→ Jenkins等价环境变量运行离线目标
→ 临时workspace生成Summary、JSON和邮件HTML

真实节点门禁
→ 参数化Pipeline实际构建
→ JUnit/Allure/归档入口
→ 可选邮件链路
→ Agent独占Flaky数据库
```

真实Jenkins未执行时必须明确记录为“待外部环境验收”，不能写成已通过。

### 2.6 网络边界澄清

阶段5冻结的“离线”与总方案统一为业务测试网络合同：离线模块发出的业务HTTP请求只能访问`127.0.0.1`随机端口，不访问任何真实业务接口。

现有Jenkins `Prepare Python Env`仍可能访问pip/npm镜像源，`Check Runtime Env`仍要求`.env`存在，因此当前Pipeline不能无条件证明“从Checkout到Post全流程物理零egress”。若用户要求整条CI严格断网，必须停止并为依赖缓存与环境准备另立方案，阶段5不得偷偷修改Jenkinsfile扩大范围。

### 2.7 决策原则

1. 业务用例继续只依赖中性Runtime Hooks，不导入Quality、Metrics、Flaky或Runner内部实现；
2. 运行级验收可在执行后读取机器产物，但不得把产物断言写回业务测试；
3. P0、Semantic、Metrics、Flaky和Pipeline各自使用自身权威文件；
4. 日志只确认fail-open警告和人工诊断，不替代JSON、JSONL或JUnit；
5. 所有报告路径使用系统临时目录下的绝对路径；
6. `reports/execution-result.json`执行前按字节备份，结束后恢复；
7. 不清理用户已有`reports/`、Allure、Quality、SQLite或其他未跟踪文件；
8. 不通过跳过、xfail、deselect、减少用例或减少Flaky轮次获得通过；
9. 核心实现缺口触发停止条件，不在验收阶段顺手修框架；
10. 阶段6之前不更新README和规范发布基线。

## 3. 当前已验证实施基线

### 3.1 阶段4交接事实

```text
3条并发ContextVar/Session/Header分类
+ 1条稳定黄金路径
+ 23条离线模块总集合
+ 23条并发池、0条串行池
+ 并发分类10/10独立进程通过
+ 黄金路径20/20独立进程通过
+ 1张70字节输出PNG及固定SHA256
+ 2个并发control audit请求
+ 503 Retry挽救
+ pending → pending → success Polling状态
+ DELETE后tasks为空
```

稳定黄金nodeid：

```text
module/offline_framework_example/test_full_framework_flow.py::TestFullFrameworkFlow::test_offline_async_media_flow
```

阶段4在Quality关闭时只断言Runtime metadata的kind/name/role。Noop Hooks的`model_id_from_kwargs()`返回`None`，精确`model_id`按用户选择留到阶段5Quality开启场景验证。

### 3.2 当前Runner合同

- `run_master.py`先权威collect，再按Marker分为parallel与serial互斥集合；
- `-n`启用parallel-first，串行池为空时明确跳过；
- 并发池JUnit自动增加`-parallel`后缀，串行池增加`-serial`后缀；
- collect-only不写`reports/execution-result.json`；
- 普通执行原子写入`reports/execution-result.json`；
- Quality、Allure、Metrics和Flaky不得改写pytest原始退出码；
- pytest 2/3/4/5或Runner异常停止后续池，pytest 1可继续收集失败证据。

### 3.3 当前Quality流水线顺序

```text
write run.json partial
→ pytest池执行并写shards/JUnit
→ P0 merge
→ write run.json final
→ Semantic merge
→ Metrics aggregate
→ Flaky history import
→ Flaky state evaluate
```

主要环境变量：

```text
QUALITY_ENABLE
QUALITY_SEMANTIC_ENABLE
QUALITY_METRICS_ENABLE
QUALITY_FLAKY_HISTORY_ENABLE
QUALITY_FLAKY_STATE_ENABLE
QUALITY_FLAKY_DB_PATH
QUALITY_RUN_ID
QUALITY_EXECUTION_ID
QUALITY_OUTPUT_DIR
```

Metrics只有在Quality和Semantic均开启时启用；Flaky State只有在Quality与Flaky History均开启时启用。

### 3.4 当前机器产物布局

| 层级 | 权威产物 |
|---|---|
| Runner | `reports/execution-result.json` |
| JUnit | 调用方指定路径，分池时增加后缀 |
| Allure | 调用方指定`--alluredir` |
| P0运行 | `run.json` |
| P0归并 | `merged/manifest.json`、`case-results.jsonl`、`request-metrics.jsonl`、`failures.jsonl`、`integrity-issues.jsonl` |
| Semantic | `semantic/merged/manifest.json`、`request-groups.jsonl`、`polling-sessions.jsonl`、`operations.jsonl`、`integrity-issues.jsonl` |
| Metrics | `metrics/manifest.json`、`metrics/run-metrics.json` |
| Flaky | `flaky-import.json`、`flaky-evaluation.json`、独占SQLite数据库 |
| Pipeline | `pipeline-summary.md`、`pipeline-summary.json`、邮件主题与HTML |

### 3.5 当前黄金路径语义预期

Quality开启后，嵌套operation采用“最外层拥有lease”语义，因此黄金路径预期产生：

```text
1个ASYNC_TASK media_generation workload operation
  ├─ model_id = offline-media-model
  ├─ 4个request group：1个create + 3个poll
  ├─ 1个polling session
  └─ usage complete，media_count = 1

2个HTTP offline_audit_query control operation
1个HTTP offline_task_cleanup control operation

合计：4个operation、7个request group、1个polling session、8个request event
```

Polling冻结事实：

```text
observed_states = [pending, pending, success]
poll request groups = 3
第1个poll group attempts = 2
attempt status = 503 → 200
最终Retry rescue = true
```

### 3.6 当前Metrics分层预期

黄金路径单独运行时，唯一workload ASYNC_TASK能够从最终媒体结果提取`media_count=1`，预期Metrics为`aggregated`。

完整23项模块包含部分职责单一的workload分类，它们不一定返回usage。当前完整模块Metrics允许为`degraded`，但降级原因必须仅为`usage_incomplete`。

不得出现`operation_incomplete`、`unassigned_request_events`、P0/Semantic来源损坏、run_id不一致或manifest Hash不一致。

请求成功率不是用例通过率。预期503、429、业务失败或Polling中间状态可能降低请求成功率，但不能把23条pytest通过误判为失败。

### 3.7 当前Flaky规则

`FlakyRuleConfig.stable_min_samples`当前默认值为`3`。执行时不永久硬编码3，而应动态读取阈值并运行`stable_min_samples + 1`轮。

当前实现下即4轮，用于证明第1～2轮保持`OBSERVING`、第3轮进入`STABLE`、第4轮继续稳定且不重复制造迁移。

### 3.8 当前Jenkins入口

现有Pipeline无需新增参数即可切换离线目标：

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

`Real Smoke`阶段自动开启Quality、Semantic和Metrics；只有配置有效的绝对`QUALITY_FLAKY_DB_PATH`时才开启Flaky历史和状态。

### 3.9 当前回归基线

阶段5执行时对冻结工作区重新复验后的权威事实：

```text
离线模块：23 passed
Smoke collect-only：40 total / 15 parallel / 25 serial
完整tests：686 collected / 686 passed
```

上述事实为2026-08-06对当前`dev3`工作区的复审快照。方案初稿引用的`691 passed / 16 failed / 6 xfailed`，以及后续出现过的`686 collected / 682 passed / 4 failed`，均属于历史工作区事实，不能继续作为当前允许失败集合。

阶段5完整回归必须再次执行原命令并记录真实结果。任何失败都会阻止阶段5整体关闭；不得通过排除文件、修改`.gitignore`、改变收集条件、增加xfail或更新快照制造“全绿”。

“增量是否新增失败”只保留为问题归因信息，不能替代总方案要求的完整回归全绿门禁。

## 4. 对总方案的收敛修订

### 4.1 将“开发阶段”收敛为“运行级验收阶段”

阶段5默认不新增业务代码、测试代码、Quality实现或Jenkins逻辑。原因链为：阶段0～4已经提供稳定输入 → 阶段5只需证明现有消费者能够读取同一事实 → 再修改生产实现会把“验证消费者”变成“边验边改” → 结果无法区分框架缺口与验收脚本问题。

因此，本阶段允许的唯一持久化交付是阶段5验收记录；运行产物必须隔离在临时目录或Jenkins独立Workspace中。

### 4.2 将完整模块与黄金路径拆成两次Quality运行

完整23项模块用于证明Runner分池、P0归并、Semantic归并和Metrics能够消费混合成功/失败/超时业务事实；黄金路径单nodeid用于证明4个operation、7个request group、1个polling session和8个request event之间的精确关系。

不得用完整模块的聚合总数代替黄金路径精确断言，也不得用黄金路径的`aggregated`掩盖完整模块允许出现的`usage_incomplete`降级。

### 4.3 将固定数量降为阶段快照

当前`23 total / 23 parallel / 0 serial`是阶段4交接快照，不是永久框架合同。执行阶段5时若收集数量变化，先判断是否存在用户新增用例或交接基线变化；未经重新评审不得直接改写计划中的预期数量。

永久合同是：nodeid唯一、收集集合守恒、并行池与串行池不重不漏、空池正确跳过。

### 4.4 将fail-open拆成两个结论

fail-open只表示Quality、Semantic、Metrics或Flaky附加能力失败时不得改写pytest原始结果，不表示报告链成功。

每次验收必须同时记录：

1. **业务结论**：pytest与Runner原始退出码是否一致；
2. **观测结论**：预期产物是否完整、可信、可追溯。

允许“业务通过、观测降级”，禁止把它写成“阶段5全部通过”。

### 4.5 将Flaky轮数绑定真实规则

执行前从`quality.flaky_models.FlakyRuleConfig`读取`stable_min_samples`，总轮数为阈值加1。当前默认阈值3只作为2026-08-05的已验证事实，后续规则变化时应自动调整轮数，不得永久硬编码四轮。

### 4.6 统一Jenkins业务离线表述

本地业务服务与测试请求必须只访问`127.0.0.1`。现有Pipeline的Python/npm准备阶段可能访问配置镜像，因此阶段5只能证明“业务接口测试零外部请求”，不能宣称整个Pipeline物理零出口。

### 4.7 保留诊断性增量结论，不降低完整回归门禁

阶段5记录两个不同层次的结论：

- **增量结论**：用于判断失败是否由本阶段引入，只承担归因职责；
- **阶段5关闭门禁**：`tests`完整回归必须全绿，同时实际Jenkins门禁必须通过。

即使失败完全来自历史债务，也只能记录“本阶段未新增失败”，不能把阶段5或整体发布标记为完成。

## 5. 阶段边界与文件范围

### 5.1 计划执行时允许产生的持久化文件

只有以下验收记录允许进入仓库：

```text
code_history/<实际执行日期>_阶段5Runner与质量链验收记录.md
```

该文件只有在实际执行阶段5后创建，记录命令、退出码、产物Hash、Jenkins Build URL或编号、阻塞项和最终门禁结论。本记录属于项目规则要求的阶段执行证据；阶段6只汇总最终发布历史，不重复伪造或覆盖阶段5原始事实。本次“编写开发方案”不提前创建该验收记录。

### 5.2 禁止修改范围

阶段5不得修改：

```text
common/
quality/
run_orchestration/
pipeline_reporting/
module/offline_framework_example/
tests/
Jenkinsfile
README.md
FRAMEWORK_TEST_SPEC.md
.gitignore
```

若验收必须修改上述任一范围才能成立，应停止并回到缺口评审，而不是在阶段5顺手修复。

### 5.3 运行产物范围

所有可配置产物写入唯一系统临时根目录：

```text
<temp>/llm_api_case-stage5-<timestamp>-<guid>/
  collect/
  quality-disabled/
  quality-full/
  quality-golden/
  fail-open/
  flaky/
  pipeline-workspace/
  regression/
  evidence/
```

JUnit、Allure、Quality、Metrics、Flaky数据库、Pipeline Summary和命令日志均不得写入仓库`reports/`。

### 5.4 唯一共享产物的保护合同

Runner当前固定原子写入`reports/execution-result.json`，无法通过CLI重定向。执行前必须记录其存在性、字节Hash和备份；每次Runner结束后立即复制本轮文件到临时证据目录；全部验收结束后在`finally`中按字节恢复原文件，若原文件不存在则删除本轮新文件。

恢复后Hash必须与执行前一致。若恢复失败，阶段5直接阻塞，不得继续运行Jenkins或写完成记录。

### 5.5 环境变量保护合同

执行脚本必须快照并最终恢复以下变量，不得把阶段5配置遗留给用户后续命令：

```text
QUALITY_ENABLE
QUALITY_SEMANTIC_ENABLE
QUALITY_METRICS_ENABLE
QUALITY_FLAKY_HISTORY_ENABLE
QUALITY_FLAKY_STATE_ENABLE
QUALITY_FLAKY_DB_PATH
QUALITY_RUN_ID
QUALITY_EXECUTION_ID
QUALITY_OUTPUT_DIR
USE_CHINA_ENVIRONMENT
GENERATE_PIPELINE_SUMMARY
RUN_FRAMEWORK_TESTS
RUN_COLLECT_ONLY
RUN_REAL_SMOKE
SMOKE_TARGET
TEST_PARALLEL_WORKERS
PIPELINE_BUILD_RESULT
PIPELINE_DURATION_MS
```

每次Quality运行都使用新的`QUALITY_RUN_ID`和新的空输出目录。`QUALITY_EXECUTION_ID`由Runner按池设置，父进程不得预填固定值。

### 5.6 工作树保护合同

执行前后分别保存`git status --short`和目标文件Hash。阶段5不得清理、还原、暂存或格式化执行时已经存在的任何用户改动。保护清单必须以执行开始时的真实工作树为准，不在方案中永久绑定某个历史未提交文件集合。

范围判断采用“前后状态差集”，不能把执行前已有未跟踪文件归因给阶段5。

## 6. 证据模型与判定顺序

### 6.1 证据优先级

同一轮运行按以下顺序判定：

```text
进程退出码
→ reports/execution-result.json本轮副本
→ JUnit
→ run.json与P0 manifest
→ Semantic manifest及JSONL
→ Metrics manifest与run-metrics.json
→ Flaky import/evaluation与SQLite检查
→ Pipeline Summary机器JSON与Markdown
```

下游证据不能推翻上游原始事实。例如Pipeline Summary显示成功，但Runner`final_exit_code`非0时，结论仍为失败。

### 6.2 本轮身份一致性

Quality链所有JSON/JSONL的`run_id`必须等于本轮显式`QUALITY_RUN_ID`。P0、Semantic和Metrics manifest记录的输出Hash必须与实际文件Hash一致；Flaky import的`source_hashes`必须指向同轮P0产物。

任何外来`run_id`、历史文件、Hash不一致或输出目录非空启动都按陈旧证据处理，不能降级为warning继续验收。

### 6.3 数量关系

核心守恒关系为：

```text
Runner planned nodeids集合
= JUnit testcase集合
= P0唯一invocation集合
```

P0生命周期事件数可以大于用例数，不能直接用`case-results.jsonl`物理行数等同用例数。应以manifest的`invocations`、JUnit testcase和唯一nodeid判定。

### 6.4 失败与降级语义

| 情况 | 业务结论 | 观测结论 | 阶段结论 |
|---|---|---|---|
| pytest非0 | 失败 | 继续收集可用证据 | 失败 |
| pytest为0、Quality完整 | 通过 | 通过 | 通过 |
| pytest为0、预期`usage_incomplete` | 通过 | 受控降级 | 对应检查通过 |
| pytest为0、Quality缺失/损坏 | 通过 | 失败 | 阻塞 |
| Pipeline Summary fail-open fallback | 不改变测试结论 | 报告失败 | Jenkins验收阻塞 |

## 7. 执行前保护与准备

### 7.1 前置检查

从仓库根目录执行：

```powershell
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path '.').Path
$python = Join-Path $repo '.venv\Scripts\python.exe'
$goldenNodeId = 'module/offline_framework_example/test_full_framework_flow.py::TestFullFrameworkFlow::test_offline_async_media_flow'

if (!(Test-Path -LiteralPath $python -PathType Leaf)) {
    throw ('Python virtual environment missing: ' + $python)
}
if (!(Test-Path -LiteralPath 'module/offline_framework_example/test_full_framework_flow.py')) {
    throw 'Stage 4 golden path is missing.'
}

$stamp = Get-Date -Format 'yyyyMMddHHmmss'
$rootName = 'llm_api_case-stage5-{0}-{1}' -f $stamp, [guid]::NewGuid().ToString('N')
$stage5Root = Join-Path ([IO.Path]::GetTempPath()) $rootName
New-Item -ItemType Directory -Path $stage5Root | Out-Null
git status --short | Set-Content -LiteralPath (Join-Path $stage5Root 'git-status-before.txt') -Encoding UTF8
```

### 7.2 共享Runner产物备份

```powershell
$runnerResult = Join-Path $repo 'reports\execution-result.json'
$runnerBackup = Join-Path $stage5Root 'evidence\execution-result.before.bin'
$runnerExisted = Test-Path -LiteralPath $runnerResult -PathType Leaf
New-Item -ItemType Directory -Path (Split-Path $runnerBackup) -Force | Out-Null

if ($runnerExisted) {
    Copy-Item -LiteralPath $runnerResult -Destination $runnerBackup
    $runnerHashBefore = (Get-FileHash -LiteralPath $runnerResult -Algorithm SHA256).Hash
} else {
    $runnerHashBefore = $null
}
```

### 7.3 环境变量快照

```powershell
$stage5EnvNames = @(
    'QUALITY_ENABLE','QUALITY_SEMANTIC_ENABLE','QUALITY_METRICS_ENABLE',
    'QUALITY_FLAKY_HISTORY_ENABLE','QUALITY_FLAKY_STATE_ENABLE',
    'QUALITY_FLAKY_DB_PATH','QUALITY_RUN_ID','QUALITY_EXECUTION_ID',
    'QUALITY_OUTPUT_DIR','USE_CHINA_ENVIRONMENT','GENERATE_PIPELINE_SUMMARY',
    'RUN_FRAMEWORK_TESTS','RUN_COLLECT_ONLY','RUN_REAL_SMOKE','SMOKE_TARGET',
    'TEST_PARALLEL_WORKERS','PIPELINE_BUILD_RESULT','PIPELINE_DURATION_MS'
)
$stage5EnvBefore = @{}
foreach ($name in $stage5EnvNames) {
    $stage5EnvBefore[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
}
```

### 7.4 执行包装与恢复

所有本地命令放入同一个`try/finally`。`finally`必须恢复环境变量和Runner共享产物：

```powershell
try {
    # 按第8～16节顺序执行。
}
finally {
    foreach ($name in $stage5EnvNames) {
        [Environment]::SetEnvironmentVariable($name, $stage5EnvBefore[$name], 'Process')
    }
    if ($runnerExisted) {
        Copy-Item -LiteralPath $runnerBackup -Destination $runnerResult -Force
        $restored = (Get-FileHash -LiteralPath $runnerResult -Algorithm SHA256).Hash
        if ($restored -ne $runnerHashBefore) { throw 'Runner result restore hash mismatch.' }
    } else {
        Remove-Item -LiteralPath $runnerResult -Force -ErrorAction SilentlyContinue
    }
    git status --short | Set-Content -LiteralPath (Join-Path $stage5Root 'git-status-after.txt') -Encoding UTF8
}
```

临时根目录在人工复核与验收记录写完前保留，不能在脚本末尾自动删除。

## 8. Collect-only验收

### 8.1 执行命令

```powershell
$collectDir = Join-Path $stage5Root 'collect'
New-Item -ItemType Directory -Path $collectDir | Out-Null
$collectLog = Join-Path $collectDir 'collect.log'

$collectOutput = & $python run_master.py `
    module/offline_framework_example --collect-only -q 2>&1
$collectExit = $LASTEXITCODE
$collectOutput | Set-Content -LiteralPath $collectLog -Encoding UTF8
if ($collectExit -ne 0) { throw ('Collect-only failed: ' + $collectExit) }
```

### 8.2 验收规则

从日志提取以`- module/offline_framework_example/`开头的nodeid，并校验：

- 当前阶段快照为23个唯一nodeid；
- 黄金路径nodeid恰好出现一次；
- `Collected test cases`、`Parallel pool cases`、`Serial pool cases`和`tests collected`四个数字内部守恒；
- 当前快照为`23 total / 23 parallel / 0 serial`；
- collect-only不创建或改写`reports/execution-result.json`；
- 不启动本地服务、不创建JUnit、Allure或Quality目录。

若数量变化但集合仍守恒，不能自动通过；必须先重新确认阶段4交接是否被用户修改。

## 9. Quality关闭验收

### 9.1 环境与命令

```powershell
$disabledRoot = Join-Path $stage5Root 'quality-disabled'
$disabledQuality = Join-Path $disabledRoot 'quality'
$disabledJunit = Join-Path $disabledRoot 'offline-disabled.xml'
$disabledAllure = Join-Path $disabledRoot 'allure-results'
New-Item -ItemType Directory -Path $disabledRoot | Out-Null

$env:QUALITY_ENABLE = '0'
$env:QUALITY_SEMANTIC_ENABLE = '0'
$env:QUALITY_METRICS_ENABLE = '0'
$env:QUALITY_FLAKY_HISTORY_ENABLE = '0'
$env:QUALITY_FLAKY_STATE_ENABLE = '0'
$env:QUALITY_OUTPUT_DIR = $disabledQuality
Remove-Item Env:QUALITY_RUN_ID,Env:QUALITY_EXECUTION_ID,Env:QUALITY_FLAKY_DB_PATH -ErrorAction SilentlyContinue

& $python run_master.py module/offline_framework_example -n 2 `
    --junitxml=$disabledJunit --alluredir=$disabledAllure -q
$disabledExit = $LASTEXITCODE
$disabledEvidence = Join-Path $disabledRoot 'execution-result.json'
Copy-Item -LiteralPath $runnerResult -Destination $disabledEvidence
```

### 9.2 Runner验收

读取本轮`execution-result.json`，必须满足：

```text
schema_version = runner-execution.v1
test_target = module/offline_framework_example
planned_case_count = 23
planned_nodeids唯一且等于collect-only集合
collection_exit_code = 0
final_exit_code = disabledExit = 0
所有已执行pool status = COMPLETED
所有已执行pool raw_pytest_exit_code = 0
未执行空池status = NOT_RUN或不进入实际执行集合
```

分池后JUnit文件名可能被Runner追加`-parallel`或`-serial`。验收必须读取`pool_results[].junit_path`，不得假定调用参数就是最终文件名。

### 9.3 JUnit、Allure与Quality边界

- 所有Runner记录的JUnit路径存在，testcase总数为23，failure/error均为0；
- Allure原始结果目录存在且非空；
- `$disabledQuality`不存在或没有任何文件；
- 环境中没有本轮自动生成后泄漏的`QUALITY_RUN_ID`或`QUALITY_EXECUTION_ID`；
- pytest退出码与Runner`final_exit_code`一致。

Quality关闭验收不读取Quality内部模型，也不允许通过缺少Quality产物推断业务失败。

## 10. 完整模块Quality、Semantic与Metrics验收

### 10.1 环境与命令

```powershell
$fullRoot = Join-Path $stage5Root 'quality-full'
$fullQuality = Join-Path $fullRoot 'quality'
$fullJunit = Join-Path $fullRoot 'offline-full.xml'
$fullAllure = Join-Path $fullRoot 'allure-results'
$fullRunId = 'stage5-full-{0}' -f [guid]::NewGuid().ToString('N')
New-Item -ItemType Directory -Path $fullRoot | Out-Null

$env:QUALITY_ENABLE = '1'
$env:QUALITY_SEMANTIC_ENABLE = '1'
$env:QUALITY_METRICS_ENABLE = '1'
$env:QUALITY_FLAKY_HISTORY_ENABLE = '0'
$env:QUALITY_FLAKY_STATE_ENABLE = '0'
$env:QUALITY_RUN_ID = $fullRunId
$env:QUALITY_OUTPUT_DIR = $fullQuality
$env:USE_CHINA_ENVIRONMENT = 'FALSE'
Remove-Item Env:QUALITY_EXECUTION_ID,Env:QUALITY_FLAKY_DB_PATH -ErrorAction SilentlyContinue

& $python run_master.py module/offline_framework_example -n 2 `
    --junitxml=$fullJunit --alluredir=$fullAllure -q
$fullExit = $LASTEXITCODE
Copy-Item -LiteralPath $runnerResult -Destination (Join-Path $fullRoot 'execution-result.json')
if ($fullExit -ne 0) { throw ('Full Quality run failed: ' + $fullExit) }
```

输出目录必须在执行前不存在；禁止复用任何已有Quality目录。

### 10.2 Runner与P0验收

- Runner计划23项、最终退出码0、执行池原始退出码均为0；
- `run.json`的`run_id`等于`$fullRunId`，`status=finished`、`integrity_status=complete`、`integrity_issues=[]`；
- P0 manifest的`expected_case_count=23`、`output_counts.invocations=23`；
- `expected_execution_ids`与Runner实际执行池一致；
- foreign、invalid、conflict和integrity issue均为0；
- `case-results.jsonl`、`request-metrics.jsonl`、`failures.jsonl`和`integrity-issues.jsonl`Hash等于manifest声明值；
- JUnit testcase集合与Runner计划集合一致。

### 10.3 Semantic验收

Semantic manifest必须为`status=complete`、`integrity_status=complete`，并满足：

- `run_id`与P0一致；
- `foreign_run_records=0`、`conflict_duplicates=0`、`incomplete_operations=0`；
- `integrity_issues=0`；
- operations、request groups和polling sessions均有非零输出；
- 四个Semantic输出文件Hash与manifest一致；
- Semantic记录引用的P0 manifest与request-metrics Hash等于本轮实际文件；
- 每个operation、request group和polling session均能追溯到本轮case、invocation和execution。

完整模块的总operation/group/polling数量只记录为阶段证据，不升级为永久合同。

### 10.4 Metrics验收

`metrics/manifest.json`和`metrics/run-metrics.json`必须存在且Hash闭环。当前完整模块允许：

```text
status = degraded
integrity.degraded_reasons = [usage_incomplete]
integrity.error_count = 0
run_status = finished
```

若状态为`aggregated`也可接受，但不得出现除`usage_incomplete`外的降级原因。以下任一原因均直接阻塞：

```text
p0_integrity_degraded
semantic_integrity_degraded
operation_incomplete
unassigned_request_events
来源文件缺失、Hash不一致或run_id不一致
```

### 10.5 业务指标合理性

- request event、request group和operation样本数均非零；
- 至少存在一个`attempt_count > 1`且最终成功的请求组，证明Retry挽救事实被消费；
- 至少存在`pending → pending → success`的Polling会话；
- 接口耗时分桶包含离线端点；
- 请求成功率可以低于100%，因为503、429、超时和预期业务失败属于测试输入；
- 用例通过率必须保持100%，不得把请求成功率当成pytest通过率；
- 所有接口标识只包含冻结的离线路径，不出现外部域名或真实模型服务端点。

### 10.6 产物可追溯脚本要求

验收脚本应使用`ConvertFrom-Json`、逐行JSONL解析和`Get-FileHash`完成检查，将结果写入`quality-full/assertions.json`。脚本可以读取公开机器产物，但不得导入`quality.*`或`run_orchestration.*`内部Python实现来替报告自证。

## 11. 黄金路径精确产物验收

### 11.1 独立运行命令

黄金路径不得复用完整模块输出。为保持后续Flaky执行profile一致，本轮不传`-n`：

```powershell
$goldenRoot = Join-Path $stage5Root 'quality-golden'
$goldenQuality = Join-Path $goldenRoot 'quality'
$goldenJunit = Join-Path $goldenRoot 'golden.xml'
$goldenAllure = Join-Path $goldenRoot 'allure-results'
$goldenRunId = 'stage5-golden-{0}' -f [guid]::NewGuid().ToString('N')
New-Item -ItemType Directory -Path $goldenRoot | Out-Null

$env:QUALITY_ENABLE = '1'
$env:QUALITY_SEMANTIC_ENABLE = '1'
$env:QUALITY_METRICS_ENABLE = '1'
$env:QUALITY_FLAKY_HISTORY_ENABLE = '0'
$env:QUALITY_FLAKY_STATE_ENABLE = '0'
$env:QUALITY_RUN_ID = $goldenRunId
$env:QUALITY_OUTPUT_DIR = $goldenQuality
Remove-Item Env:QUALITY_EXECUTION_ID,Env:QUALITY_FLAKY_DB_PATH -ErrorAction SilentlyContinue

& $python run_master.py $goldenNodeId `
    --junitxml=$goldenJunit --alluredir=$goldenAllure -q
$goldenExit = $LASTEXITCODE
Copy-Item -LiteralPath $runnerResult -Destination (Join-Path $goldenRoot 'execution-result.json')
if ($goldenExit -ne 0) { throw ('Golden Quality run failed: ' + $goldenExit) }
```

### 11.2 Runner与P0精确数量

```text
planned_case_count = 1
planned_nodeids = [goldenNodeId]
final_exit_code = 0
P0 invocations = 1
P0 request_metrics = 8
P0 integrity_status = complete
```

JUnit必须只有一个通过的testcase。Allure原始结果必须包含该用例结果与阶段4冻结的输出PNG附件Hash。

### 11.3 Semantic精确数量

```text
operations = 4
request_groups = 7
polling_sessions = 1
integrity_issues = 0
incomplete_operations = 0
```

每条Semantic记录的`run_id`、`case_id`和`invocation_id`必须与黄金路径一致。

### 11.4 ASYNC_TASK归属

唯一workload异步operation必须满足：

```text
operation_kind = async_task
operation_name = media_generation
traffic_role = workload
model_id = offline-media-model
outcome = success
completeness = complete
request_group_ids数量 = 4
polling_session_ids数量 = 1
usage.completeness = complete
usage.media_count = 1
```

四个请求组必须是一个创建组与三个轮询组；不得把两个audit或cleanup控制请求吸收到ASYNC_TASK中。

### 11.5 控制operation归属

其余三个operation必须为：

```text
2 × HTTP / offline_audit_query / control
1 × HTTP / offline_task_cleanup / control
```

它们各自只拥有一个request group，且不产生polling session。四个operation拥有的request group并集必须恰好等于七个组，不重不漏。

### 11.6 Retry与Polling关系

唯一polling session必须满足：

```text
observed_state_sequence = [pending, pending, success]
poll_count = 3
terminal_status = success
final_outcome = success
completeness = complete
request_group_ids数量 = 3
```

第一个poll request group必须为两次attempt，状态码`503 → 200`；后两个poll group各一次attempt并返回200。创建组返回202。由此得到请求事件守恒：

```text
1 create + 2 first poll attempts + 1 second poll + 1 third poll
+ 2 audit + 1 cleanup = 8 request events
```

### 11.7 黄金路径Metrics

黄金路径`run-metrics.json`必须为：

```text
status = aggregated
run_status = finished
integrity.degraded_reasons = []
integrity.error_count = 0
run_metrics.operation.operation_count = 1
```

Semantic仍必须保持4个总operation；Metrics只聚合workload，因此排除两个audit和一个cleanup控制operation。Metrics中的唯一`media_generation/workload`桶必须提取`media_count=1`；Retry指标必须记录一个被挽救请求组；Polling和耗时指标必须能追溯至本轮Semantic Hash。

若完整模块验收为`degraded`而黄金路径为`aggregated`，这是预期分层，不构成矛盾。

## 12. Fail-open验收

### 12.1 验收目标

本节不制造业务随机失败。需要证明的是：附加观测阶段失败或配置无效时，pytest原始退出码保持不变，同时机器产物明确记录观测失败，不能生成假绿报告。

### 12.2 既有失败注入合同测试

运行仓库已有的确定性测试：

```powershell
$failOpenRoot = Join-Path $stage5Root 'fail-open'
$failOpenAllure = Join-Path $failOpenRoot 'contract-allure'
New-Item -ItemType Directory -Path $failOpenRoot | Out-Null

& $python -m pytest `
    tests/quality/test_quality_run_master.py::test_semantic_merge_failure_is_fail_open `
    tests/quality/test_quality_run_master.py::test_metrics_exception_is_fail_open `
    tests/quality/test_quality_run_master.py::test_quality_finalize_runs_and_original_exception_is_preserved `
    --alluredir=$failOpenAllure -q 2>&1 `
    | Set-Content -LiteralPath (Join-Path $failOpenRoot 'contract-tests.log') -Encoding UTF8
$failOpenContractExit = $LASTEXITCODE
if ($failOpenContractExit -ne 0) { throw 'Fail-open contract tests failed.' }
```

该命令只验证已有实现合同，不新增业务测试，也不把单元测试结果替代运行级验收。

### 12.3 运行级无效Flaky配置

使用相对数据库路径触发既有显式配置警告：

```powershell
$runtimeFailRoot = Join-Path $failOpenRoot 'invalid-flaky-config'
$runtimeFailQuality = Join-Path $runtimeFailRoot 'quality'
$runtimeFailRunId = 'stage5-fail-open-{0}' -f [guid]::NewGuid().ToString('N')
New-Item -ItemType Directory -Path $runtimeFailRoot | Out-Null

$env:QUALITY_ENABLE = '1'
$env:QUALITY_SEMANTIC_ENABLE = '1'
$env:QUALITY_METRICS_ENABLE = '1'
$env:QUALITY_FLAKY_HISTORY_ENABLE = '1'
$env:QUALITY_FLAKY_STATE_ENABLE = '1'
$env:QUALITY_FLAKY_DB_PATH = 'relative-stage5-flaky.db'
$env:QUALITY_RUN_ID = $runtimeFailRunId
$env:QUALITY_OUTPUT_DIR = $runtimeFailQuality

& $python run_master.py $goldenNodeId `
    --junitxml=$(Join-Path $runtimeFailRoot 'result.xml') -q
$runtimeFailExit = $LASTEXITCODE
Copy-Item -LiteralPath $runnerResult -Destination (Join-Path $runtimeFailRoot 'execution-result.json')
```

### 12.4 双结论验收

本轮必须同时满足：

```text
业务结论：runtimeFailExit = Runner final_exit_code = 0
P0/Semantic/Metrics：仍按黄金路径成功生成
flaky-import.json：status = NO_DATA
flaky-import issue code = invalid_flaky_history_configuration
flaky-evaluation.json：status = NO_DATA
flaky-evaluation issue code = flaky_history_import_not_ready
相对SQLite文件未在仓库或临时当前目录意外创建
```

阶段记录写成“业务通过、Flaky观测按合同降级”。若Flaky文件缺失、Runner非0或报告错误地宣称`IMPORTED/EVALUATED`，均判失败。

## 13. Flaky多轮稳定样本验收

### 13.1 动态轮数与独占数据库

```powershell
$flakyRoot = Join-Path $stage5Root 'flaky'
New-Item -ItemType Directory -Path $flakyRoot | Out-Null
$flakyDb = Join-Path $flakyRoot 'stage5-flaky.db'

$stableMinSamples = [int](& $python -c `
    'from quality.flaky_models import FlakyRuleConfig; print(FlakyRuleConfig().stable_min_samples)')
$flakyRounds = $stableMinSamples + 1
if ($stableMinSamples -lt 2) { throw 'Unexpected stable_min_samples.' }
```

数据库路径必须是本Job、本次验收独占的绝对本地路径，父目录预先存在。不得复用仓库`reports/`、用户数据库、网络共享或前一次验收数据库。

### 13.2 多轮执行

```powershell
for ($round = 1; $round -le $flakyRounds; $round++) {
    $roundRoot = Join-Path $flakyRoot ('round-{0}' -f $round)
    $roundQuality = Join-Path $roundRoot 'quality'
    $roundRunId = 'stage5-flaky-{0}-{1}' -f $round, [guid]::NewGuid().ToString('N')
    New-Item -ItemType Directory -Path $roundRoot | Out-Null

    $env:QUALITY_ENABLE = '1'
    $env:QUALITY_SEMANTIC_ENABLE = '1'
    $env:QUALITY_METRICS_ENABLE = '1'
    $env:QUALITY_FLAKY_HISTORY_ENABLE = '1'
    $env:QUALITY_FLAKY_STATE_ENABLE = '1'
    $env:QUALITY_FLAKY_DB_PATH = $flakyDb
    $env:QUALITY_RUN_ID = $roundRunId
    $env:QUALITY_OUTPUT_DIR = $roundQuality
    $env:USE_CHINA_ENVIRONMENT = 'FALSE'
    Remove-Item Env:QUALITY_EXECUTION_ID -ErrorAction SilentlyContinue

    & $python run_master.py $goldenNodeId `
        --junitxml=$(Join-Path $roundRoot 'result.xml') `
        --alluredir=$(Join-Path $roundRoot 'allure-results') -q
    if ($LASTEXITCODE -ne 0) { throw ('Flaky round failed: ' + $round) }
    Copy-Item -LiteralPath $runnerResult `
        -Destination (Join-Path $roundRoot 'execution-result.json')

    & $python -m quality.cli flaky-state --db $flakyDb `
        --case-id $goldenNodeId --environment overseas --execution-profile serial `
        | Set-Content -LiteralPath (Join-Path $roundRoot 'flaky-state.json') -Encoding UTF8
}
```

### 13.3 每轮导入验收

每轮必须满足：

```text
Runner final_exit_code = 0
run.json status = finished
P0 integrity_status = complete
flaky-import status = IMPORTED
eligible_count = inserted_count = 1
excluded_count = 0
issues = []
quick_check = ok
flaky-evaluation status = EVALUATED
affected_count = evaluated_count = 1
stale_count = 0
```

所有轮次必须具有不同`run_id`、不同output目录和不同observation ID；`case_id`、`param_hash`、environment=`overseas`、execution_profile=`serial`、state epoch和flaky key必须保持一致。

### 13.4 状态机验收

通用规则：

- 第1轮建立`OBSERVING`；
- 在样本数小于`stable_min_samples`时保持`OBSERVING`；
- 样本数等于阈值时进入`STABLE`且`stable_outcome=pass`；
- 阈值加1轮继续`STABLE`，不重复产生迁移；
- sample size和pass count每轮恰好加1，fail count与signature switch count保持0。

当前阈值3时，冻结预期为：

| 轮次 | 状态 | transitioned_count | 迁移 |
|---|---:|---:|---|
| 1 | OBSERVING | 1 | 初始→OBSERVING |
| 2 | OBSERVING | 0 | 无 |
| 3 | STABLE | 1 | OBSERVING→STABLE |
| 4 | STABLE | 0 | 无 |

### 13.5 数据库最终检查

```powershell
& $python -m quality.cli flaky-db-check --db $flakyDb `
    | Set-Content -LiteralPath (Join-Path $flakyRoot 'db-check.json') -Encoding UTF8
if ($LASTEXITCODE -ne 0) { throw 'Flaky database check failed.' }

& $python -m quality.cli flaky-history --db $flakyDb `
    --case-id $goldenNodeId --environment overseas --execution-profile serial `
    | Set-Content -LiteralPath (Join-Path $flakyRoot 'history.json') -Encoding UTF8
```

最终history数量必须等于`$flakyRounds`，且所有观察均为真实`pass`。不得直接查询SQLite表、手工插入记录、复制旧数据库或通过随机失败演示Flaky。

## 14. Pipeline Summary本地模拟验收

### 14.1 构造隔离Workspace

本地模拟不在仓库`reports/`中生成摘要，而是复制最后一轮Flaky运行的同轮JUnit、Runner、Quality、Metrics和Flaky产物到合成Workspace：

```powershell
$pipelineRoot = Join-Path $stage5Root 'pipeline-workspace'
$pipelineReports = Join-Path $pipelineRoot 'reports'
$pipelineQuality = Join-Path $pipelineReports 'quality'
$finalRoundRoot = Join-Path $flakyRoot ('round-{0}' -f $flakyRounds)
$finalRoundQuality = Join-Path $finalRoundRoot 'quality'
New-Item -ItemType Directory -Path $pipelineQuality -Force | Out-Null

Copy-Item -Path (Join-Path $finalRoundQuality '*') `
    -Destination $pipelineQuality -Recurse
Copy-Item -LiteralPath (Join-Path $finalRoundRoot 'result.xml') `
    -Destination (Join-Path $pipelineReports 'smoke-tests-stage5.xml')
Copy-Item -LiteralPath (Join-Path $finalRoundRoot 'execution-result.json') `
    -Destination (Join-Path $pipelineReports 'execution-result.json')
```

只允许复制同一最终轮次的产物。禁止把完整模块Metrics与另一轮Flaky evaluation拼接成伪造的“完整报告”。

### 14.2 初始化Pipeline阶段状态

```powershell
$env:RUN_FRAMEWORK_TESTS = 'false'
$env:RUN_COLLECT_ONLY = 'false'
$env:RUN_REAL_SMOKE = 'true'
$env:GENERATE_PIPELINE_SUMMARY = 'true'
$env:QUALITY_ENABLE = 'true'
$env:USE_CHINA_ENVIRONMENT = 'FALSE'
$env:SMOKE_TARGET = $goldenNodeId
$env:TEST_PARALLEL_WORKERS = '2'
$env:PIPELINE_BUILD_RESULT = 'SUCCESS'
$env:PIPELINE_DURATION_MS = '0'

$stageStatus = Join-Path $pipelineReports 'pipeline-stage-status.json'
& $python -m pipeline_reporting initialize-stages --path $stageStatus `
    --framework-tests false --smoke-collect false --real-smoke true
if ($LASTEXITCODE -ne 0) { throw 'Pipeline stage initialization failed.' }

& $python -m pipeline_reporting set-stage --path $stageStatus `
    --name real_smoke --status PASSED
if ($LASTEXITCODE -ne 0) { throw 'Pipeline stage update failed.' }
```

### 14.3 生成摘要

```powershell
$summaryMd = Join-Path $pipelineReports 'pipeline-summary.md'
$summaryJson = Join-Path $pipelineReports 'pipeline-summary.json'
$emailSubject = Join-Path $pipelineReports 'pipeline-email-subject.txt'
$emailHtml = Join-Path $pipelineReports 'pipeline-email.html'

& $python -m pipeline_reporting generate --workspace $pipelineRoot `
    --output $summaryMd --machine-output $summaryJson `
    --email-subject-output $emailSubject --email-html-output $emailHtml `
    --dotenv $(Join-Path $pipelineRoot '.env')
if ($LASTEXITCODE -ne 0) { throw 'Pipeline summary generation failed.' }
```

四个输出均必须存在、非空、UTF-8可读，且不得落入仓库工作树。

### 14.4 机器摘要验收

由于黄金路径包含一次503后重试挽救，当前摘要预期为关注态而非失败：

```text
conclusion = WARN
context.real_smoke_enabled = true
context.smoke_target = goldenNodeId
stages[框架单测] = NOT_RUN
stages[用例收集] = NOT_RUN
stages[接口测试] = PASSED
stages[质量观测] = PASSED
smoke_tests total/passed = 1/1
execution planned_case_count = 1
request_health total = 8
request_health success_count = 7
由success_count / total派生请求成功率 = 0.875
retry_health retried_group_count = 1
retry_health rescued_group_count = 1
由rescued_group_count / retried_group_count派生挽救率 = 1.0
interface_timings非空
flaky available = true
flaky transition_count = 0
```

`WARN`的原因必须是“执行通过，但重试挽救了瞬时失败”，不能是测试失败、Quality缺失、阶段BLOCKED或来源Hash错误。

### 14.5 Markdown与邮件产物验收

Markdown必须包含：执行参数、阶段结果、接口测试、请求质量、重试效果、接口耗时Top 5和Flaky状态迁移。邮件主题与HTML必须来自同一机器摘要结论。

摘要不得读取旧`summary.json`、Smoke计费专属产物或历史Quality目录，也不得新增报告类型。若生成器进入fallback文本，即使Jenkins测试本身通过，本节仍判失败。

## 15. 实际Jenkins验收

### 15.1 外部前置条件

实际Jenkins属于外部系统门禁，只能在以下条件满足后执行：

- Jenkins Checkout得到的提交或变更集包含阶段0～4全部文件；
- 不能依赖本机未跟踪文件，因为Pipeline开头会`deleteDir()`并重新checkout；
- Windows Agent具备Python、npm和Allure插件；
- Job使用独占Workspace，且`disableConcurrentBuilds()`生效；
- Job配置一个绝对、本地、持久、父目录已存在的`QUALITY_FLAKY_DB_PATH`；
- Flaky数据库路径按Job隔离，不放在会被`deleteDir()`清理的Workspace内；
- 验收构建使用空数据库或记录清晰的初始样本数。

截至2026-08-06，阶段4两个测试文件已经进入`c0954ba`并可被Jenkins Checkout取得，原SCM可见性阻塞已经解除。现有Jenkins构建#73虽然Checkout了该提交，但执行目标仍为`module/smoke`，不能作为离线模块验收证据；仍需按第15.2节参数实际运行`module/offline_framework_example`。

### 15.2 构建参数

| 参数 | 值 |
|---|---|
| `RUN_FRAMEWORK_TESTS` | `false` |
| `RUN_COLLECT_ONLY` | `false` |
| `RUN_REAL_SMOKE` | `true` |
| `GENERATE_PIPELINE_SUMMARY` | `true` |
| `ALWAYS_SEND_REPORT_EMAIL` | `false` |
| `USE_CHINA_ENVIRONMENT` | `FALSE` |
| `SMOKE_TARGET` | `module/offline_framework_example` |
| `TEST_PARALLEL_WORKERS` | `2` |

不新增Jenkins参数、不新增Job、不修改`Jenkinsfile`。

### 15.3 网络边界

业务测试HTTP必须全部指向本轮fixture提供的`127.0.0.1`地址，日志和质量产物不得出现真实模型服务域名、API Key或计费请求。

`Prepare Python Env`中的pip/npm仍可能访问华为云或npm镜像，因此验收记录必须写成“业务接口测试零外部请求”。若要求整个构建物理断网，应另行提供预缓存依赖或离线Agent，不属于阶段5修改范围。

### 15.4 Jenkins执行结果

构建必须满足：

- Checkout、Runtime Env、Prepare Python Env和Real Smoke按配置执行；
- Framework Unit Tests与Collect Smoke Cases显示未执行；
- Real Smoke的Runner计划23项并全部通过；
- `reports/smoke-tests*.xml`被JUnit发布；
- `allure-results/**`可被Allure入口消费；
- `reports/quality`含同轮P0、Semantic、Metrics和Flaky产物；
- `reports/pipeline-summary.md/json`、邮件主题和HTML生成成功；
- `reports/**`与`allure-results/**`完成归档；
- Jenkins Build结果为`SUCCESS`，Pipeline Summary允许因Retry挽救显示`WARN`；
- 不出现fallback summary。

### 15.5 Jenkins质量事实

完整模块在Jenkins中的质量结论应与本地完整模块一致：P0与Semantic完整，Metrics最多只因`usage_incomplete`降级。Pipeline Summary中的请求成功率、Retry挽救率、耗时Top和Flaky变化必须来自本构建`reports/quality`的相同`run_id`。

首次使用空Flaky数据库时，大量case可能从无状态进入`OBSERVING`，这是本轮真实迁移；不得与本地只跑黄金路径得到的`STABLE`状态拼接比较。

### 15.6 邮件边界

`ALWAYS_SEND_REPORT_EMAIL=false`只关闭普通成功邮件。现有Pipeline在失败、UNSTABLE或从失败恢复为FIXED时仍可能发信。为避免验收产生非预期外部副作用，优先使用无失败历史的隔离Job。

本方案中“邮件继续消费现有报告”指Pipeline使用同一机器摘要成功生成邮件主题与HTML，并且Jenkins邮件步骤引用这两项同轮产物；默认不要求真实SMTP投递。若用户明确要求验证真实投递，必须单独确认收件人、邮件服务和副作用后再执行，不能把未授权投递作为阶段5默认动作。

### 15.7 外部证据记录

阶段5验收记录至少保存：Job名称、Build编号、Build URL、Git commit、参数快照、Agent标签、构建结果、Pipeline Summary结论、归档入口、Flaky数据库标识与业务网络边界结论。无法访问Jenkins时，本地验收可以完成，但阶段5整体状态必须写为“Jenkins门禁待外部执行”，不能宣称完成。

## 16. 完整回归与失败归因

### 16.1 框架完整回归命令

```powershell
$regressionRoot = Join-Path $stage5Root 'regression'
New-Item -ItemType Directory -Path $regressionRoot | Out-Null
$testsLog = Join-Path $regressionRoot 'tests.log'
$testsJunit = Join-Path $regressionRoot 'tests.xml'
$testsAllure = Join-Path $regressionRoot 'allure-results'

$testsOutput = & $python -m pytest tests -q --tb=no `
    --junitxml=$testsJunit --alluredir=$testsAllure 2>&1
$testsExit = $LASTEXITCODE
$testsOutput | Set-Content -LiteralPath $testsLog -Encoding UTF8
```

不得因为出现失败就追加`--ignore`、`-k not`、修改xfail或更新快照。

### 16.2 当前权威全绿基线

```text
2026-08-06复审：686 collected / 686 passed / 0 failed
```

该数字是执行快照，不是永久数量合同。永久合同是权威收集成功、测试集合可解释且完整回归零失败。历史4项仓库边界失败已经不再出现，不得继续将其作为允许失败集合。

### 16.3 判定规则

从日志中提取`FAILED `后的nodeid集合：

- 实际失败为空且收集成功：完整回归门禁通过；
- 出现任何失败：记录完整nodeid集合用于归因，但阶段5关闭门禁和整体发布门禁均阻塞；
- 出现测试收集异常或退出码2/3/4/5：阶段5失败；
- 收集数量变化时必须解释集合差异，不能只比较统计数字；
- “没有新增失败”只能作为诊断结论，不能替代零失败门禁。

### 16.4 Smoke收集回归

```powershell
$smokeCollectOutput = & $python run_master.py module/smoke --collect-only -q 2>&1
$smokeCollectExit = $LASTEXITCODE
$smokeCollectOutput | Set-Content `
    -LiteralPath (Join-Path $regressionRoot 'smoke-collect.log') -Encoding UTF8
if ($smokeCollectExit -ne 0) { throw 'Smoke collect-only regression failed.' }
```

当前快照为`40 total / 15 parallel / 25 serial`。永久合同仍是集合守恒、nodeid唯一和分池准确。数量变化时必须先识别用户改动，不能直接更新基线。

### 16.5 离线模块回归复用

第8、9、10节已经分别覆盖离线collect-only、Quality关闭整体运行和Quality开启整体运行，不再额外重复执行同一23项模块。三次结果必须引用不同证据目录并保持nodeid集合一致。

### 16.6 范围与清洁度

恢复共享Runner产物后执行：

```powershell
git diff --check
git status --short
```

并比较`git-status-before.txt`与`git-status-after.txt`。除计划允许的阶段5验收记录外，不得新增仓库文件；临时产物不得出现在工作树；`.gitignore`及用户已有文件Hash不得变化。

## 17. 实施任务分解

### 17.1 任务5.0：冻结工作树与共享状态

记录Git状态、关键文件Hash、环境变量和`execution-result.json`字节备份，创建唯一临时根目录。任何保护步骤失败均不进入后续运行。

### 17.2 任务5.1：完成Runner收集验收

执行离线模块collect-only，核对nodeid唯一性、阶段快照、分池守恒与空池语义。

### 17.3 任务5.2：完成Quality关闭验收

在Quality全关闭下运行完整模块，核对Runner、JUnit、Allure和零Quality产物边界。

### 17.4 任务5.3：完成完整模块质量链验收

开启Quality、Semantic和Metrics，验证23项P0归并、Semantic完整性、Metrics受控降级和Hash追溯。

### 17.5 任务5.4：完成黄金路径精确验收

独立运行黄金nodeid，验证4/7/1/8数量关系、operation所有权、503重试挽救、Polling迁移和`media_count=1`。

### 17.6 任务5.5：完成fail-open双结论验收

运行既有失败注入合同测试，再以无效Flaky路径完成运行级降级验证，区分业务通过与观测失败。

### 17.7 任务5.6：完成Flaky多轮验收

动态读取阈值，以同nodeid、同profile、独占数据库和逐轮新run/output积累真实稳定样本。

### 17.8 任务5.7：完成Pipeline Summary本地模拟

仅用最后一轮同源产物构造隔离Workspace，生成机器、Markdown和邮件摘要并验证WARN原因。

### 17.9 任务5.8：完成实际Jenkins门禁

在SCM可见、Job数据库和Agent条件满足时运行参数化Pipeline；否则记录明确的外部阻塞条件。

### 17.10 任务5.9：完成回归与范围审查

执行完整`tests`和Smoke collect-only，比较已知失败nodeid集合，恢复共享状态并检查工作树差集。

### 17.11 任务5.10：写入阶段5验收记录

只在实际执行后创建`code_history/<实际执行日期>_阶段5Runner与质量链验收记录.md`。记录事实，不复制大体量机器产物，不把未执行的Jenkins写成通过。

## 18. 权威命令索引

| 目的 | 权威入口 |
|---|---|
| 离线收集 | `python run_master.py module/offline_framework_example --collect-only -q` |
| Quality关闭完整模块 | `python run_master.py module/offline_framework_example -n 2 ...` |
| Quality开启完整模块 | 同上，开启Quality/Semantic/Metrics并使用独立输出目录 |
| 黄金路径 | `python run_master.py <golden-nodeid> ...` |
| Flaky状态 | `python -m quality.cli flaky-state ...` |
| Flaky历史 | `python -m quality.cli flaky-history ...` |
| Flaky数据库检查 | `python -m quality.cli flaky-db-check ...` |
| Pipeline Summary | `python -m pipeline_reporting generate ...` |
| 框架完整回归 | `python -m pytest tests -q` |
| Smoke收集 | `python run_master.py module/smoke --collect-only -q` |

所有命令从仓库根目录使用`.venv/Scripts/python.exe`执行；表中省略的路径参数以第7～16节详细命令为准。

## 19. 验收矩阵

| 能力 | 主要证据 | 通过标准 |
|---|---|---|
| Collect-only | collect日志 | nodeid唯一，23/23/0阶段快照，集合守恒 |
| Quality关闭 | Runner、JUnit、Allure | 23项通过，无Quality文件 |
| Runner原始语义 | execution-result副本 | collection/pool/final退出码一致 |
| P0完整模块 | run与merged manifest | run_id一致，23 invocations，完整无污染 |
| Semantic完整模块 | semantic manifest与JSONL | 完整、无外来记录、无不完整operation |
| Metrics完整模块 | metrics产物 | 最多仅`usage_incomplete`降级 |
| 黄金P0 | request-metrics | 1 invocation、8 events |
| 黄金Semantic | operations/groups/polling | 精确4/7/1，所有权闭环 |
| 黄金Retry | request groups | 首次poll为503→200且被挽救 |
| 黄金Polling | polling session | pending→pending→success |
| 黄金Usage | ASYNC_TASK与Metrics | `offline-media-model`、`media_count=1` |
| Fail-open | 合同测试与NO_DATA文件 | 业务退出码0，观测降级明确 |
| Flaky历史 | import、history、DB check | 每轮插入1，最终样本数等于轮数 |
| Flaky状态 | evaluation与公开CLI | OBSERVING→STABLE符合动态阈值 |
| Pipeline本地模拟 | summary JSON/Markdown | 同轮来源，WARN仅因Retry挽救 |
| Jenkins | Build与归档入口 | SUCCESS、报告可访问、业务仅loopback |
| 完整回归 | pytest日志/JUnit | 权威收集成功且零失败 |
| 阶段5关闭门禁 | 完整回归与实际Jenkins | 两者均通过才允许关闭阶段5 |
| 工作树保护 | 前后状态与Hash | 只增加允许的验收记录 |

任何一行缺少原始证据时不得用口头确认或截图摘要替代机器产物。

## 20. 风险与控制

### 20.1 历史产物制造假通过

**因果链**：复用输出目录 → 新旧run_id混合 → 下游读取可用旧文件 → 本轮失败被遮蔽。控制：每轮唯一空目录、显式run ID、Hash和时间范围校验。

### 20.2 Runner共享文件污染用户现场

**因果链**：固定写`reports/execution-result.json` → 多轮覆盖 → 用户原证据丢失。控制：字节备份、逐轮立即复制、finally恢复和Hash复核。

### 20.3 环境变量泄漏

**因果链**：Quality/Flaky开关残留 → 后续命令误启用报告或数据库 → 难以归因。控制：进程级快照与finally恢复。

### 20.4 JUnit文件名误判

Runner会按池追加后缀，直接检查调用参数路径可能得到假缺失。控制：只信任`pool_results[].junit_path`。

### 20.5 把受控降级当成失败或假绿

完整模块的`usage_incomplete`来自职责单一用例，黄金路径应为`aggregated`。控制：分两轮验收并冻结允许降级码集合。

### 20.6 Flaky数据库已有样本

旧样本会让首轮直接STABLE或产生错误迁移。控制：使用本次临时目录中的新数据库，检查初始文件不存在。

### 20.7 Flaky execution profile漂移

同nodeid在`serial`与`parallel`profile下属于不同状态键。控制：所有Flaky轮次均不传`-n`，查询时显式`--execution-profile serial`。

### 20.8 多轮run ID或输出目录复用

重复run ID可能触发NOOP，复用目录会污染source digest。控制：每轮GUID run ID与独立目录，共享项只有独占SQLite数据库。

### 20.9 Pipeline Summary拼接不同轮次

把完整模块Metrics与黄金Flaky混合会得到看似丰富但不可追溯的摘要。控制：本地模拟只复制最终Flaky轮的同源产物。

### 20.10 WARN被误判为构建失败

Retry挽救会主动产生Pipeline Summary `WARN`，但Jenkins测试可为SUCCESS。控制：分别记录Build结果和摘要结论，并核对WARN原因。

### 20.11 Jenkins看不到本机未跟踪文件

`deleteDir()`与checkout会丢弃本机工作树状态。控制：Jenkins前确认目标文件在SCM变更集中；阶段5不自行提交。

### 20.12 夸大断网能力

业务请求虽为loopback，但pip/npm可能访问镜像。控制：网络结论限定为业务接口测试；全Pipeline离线化另立任务。

### 20.13 `.env`引入真实配置副作用

Jenkins会复制`D:/API_CASE/.env`。控制：离线模块不得读取真实密钥完成业务请求，日志和产物执行脱敏检查，Flaky路径按Job隔离。

### 20.14 已知失败数量相同但集合变化

一个旧失败消失、一个新失败出现时数量仍为16。控制：比较完整nodeid集合，不只比较统计数字。

### 20.15 fail-open掩盖报告损坏

pytest返回0不代表Quality验收通过。控制：业务与观测双结论，缺产物或fallback摘要均阻塞对应门禁。

### 20.16 成功构建意外发邮件

`ALWAYS_SEND_REPORT_EMAIL=false`仍可能触发FIXED邮件。控制：使用无失败历史的隔离Job，验收邮件文件而非投递。

## 21. 停止条件

出现以下任一条件立即停止对应后续步骤并记录阻塞：

1. 阶段4黄金nodeid不存在或收集不唯一；
2. 离线模块收集集合无法解释地偏离23项阶段快照；
3. 收集集合在并行池和串行池中重复或丢失；
4. 必须修改业务测试、Quality、Runner、Pipeline Reporting或Jenkinsfile才能继续；
5. 必须修改`.gitignore`、快照、xfail或排除测试才能制造绿色结果；
6. 临时输出目录在运行前非空；
7. 本轮产物出现外来run ID或Hash不一致；
8. pytest退出码与Runner final exit code不一致；
9. Quality关闭时仍生成质量机器产物；
10. P0或Semantic integrity不是complete；
11. 完整模块Metrics出现`usage_incomplete`以外的降级原因；
12. 黄金路径数量不是4 operations、7 groups、1 polling、8 events；
13. 黄金ASYNC_TASK缺少`offline-media-model`或`media_count=1`；
14. 503→200 Retry与pending→pending→success Polling关系不成立；
15. fail-open改变pytest原始退出码或生成假成功报告；
16. Flaky数据库不是绝对、独占、本地路径；
17. Flaky轮次run ID、output目录或execution profile发生漂移；
18. Flaky状态不按当前动态阈值变化；
19. Pipeline Summary混用不同轮次产物或进入fallback；
20. Pipeline Summary WARN来源不是预期Retry/Flaky事实；
21. Jenkins Checkout不包含阶段0～4文件；
22. Jenkins业务请求出现外部域名、真实凭证或费用风险；
23. 完整回归出现任何失败或收集异常；
24. Smoke收集分池发生无法解释的回退；
25. `execution-result.json`无法按原Hash恢复；
26. 用户已有工作树文件Hash变化；
27. 必须清理、还原或提交用户改动才能继续；
28. 实际Jenkins不可访问或缺少必要Agent/插件/Job权限。

不得通过复用旧产物、放宽Hash检查、减少Flaky轮数、改用随机失败、忽略Jenkins Checkout差异、删除失败用例或把观测失败改写成warning来绕过停止条件。

## 22. 阶段完成门禁

阶段5只有在以下条件全部有证据时才能关闭：

- [ ] 执行前工作树、环境变量和Runner共享产物已冻结；
- [ ] 所有可配置运行产物位于唯一系统临时目录；
- [ ] collect-only nodeid唯一且分池守恒；
- [ ] 当前离线收集快照23/23/0已确认；
- [ ] Quality关闭完整模块23项通过；
- [ ] Quality关闭未生成质量机器产物；
- [ ] 自定义JUnit和Allure路径生效；
- [ ] Runner、JUnit和P0用例集合一致；
- [ ] 完整模块P0 integrity complete；
- [ ] 完整模块Semantic integrity complete；
- [ ] 完整模块Metrics仅允许`usage_incomplete`降级；
- [ ] 完整模块Retry、Polling与耗时事实非零且可追溯；
- [ ] 黄金路径独立运行通过；
- [ ] 黄金P0为1 invocation和8 request events；
- [ ] 黄金Semantic为4 operations、7 groups、1 polling；
- [ ] 黄金ASYNC_TASK拥有4个业务请求组；
- [ ] 两个audit和一个cleanup保持control operation；
- [ ] 首个poll请求组为503→200；
- [ ] Polling状态为pending→pending→success；
- [ ] 黄金usage完整且`media_count=1`；
- [ ] 黄金Metrics为aggregated；
- [ ] fail-open合同测试通过；
- [ ] 运行级无效Flaky配置保持pytest退出码0；
- [ ] fail-open NO_DATA原因准确；
- [ ] Flaky阈值动态读取；
- [ ] Flaky执行轮数为阈值加1；
- [ ] 每轮导入一个真实稳定样本；
- [ ] case_id、param_hash、profile和epoch稳定；
- [ ] 状态按OBSERVING→STABLE变化；
- [ ] 阈值加1轮不重复迁移；
- [ ] SQLite quick check和公开CLI查询通过；
- [ ] Pipeline本地模拟只使用同轮事实；
- [ ] Pipeline机器、Markdown和邮件产物一致；
- [ ] Pipeline WARN原因准确且不是测试失败；
- [ ] 实际Jenkins Build成功；
- [ ] Jenkins JUnit、Allure、Quality和Summary入口可访问；
- [ ] Jenkins业务接口请求只访问loopback；
- [ ] 完整回归权威收集成功且零失败；
- [ ] 任何完整回归失败均阻止阶段5关闭和整体发布；
- [ ] Smoke收集集合与分池不退化；
- [ ] Runner共享产物恢复原Hash；
- [ ] 环境变量恢复；
- [ ] `git diff --check`通过；
- [ ] 工作树只增加允许的阶段5验收记录；
- [ ] 用户已有改动未被触碰；
- [ ] 验收记录区分本地通过、Jenkins通过与发布阻塞状态。

若实际Jenkins条件尚未满足，所有实际Jenkins相关项不得勾选，阶段5状态为“本地门禁完成、外部门禁阻塞”，不能关闭为完成。

## 23. 阶段6交接合同

### 23.1 交接内容

阶段5向阶段6提供：

```text
Runner collect-only与Quality关闭证据
+ 完整模块P0/Semantic/Metrics证据
+ 黄金路径4/7/1/8精确关系证据
+ fail-open业务/观测双结论
+ Flaky动态阈值和多轮状态证据
+ Pipeline Summary同轮来源证据
+ 实际Jenkins Build与归档证据
+ 完整回归新增失败集合判断
+ 发布门禁状态
```

机器产物保留在临时证据目录或Jenkins归档，不提交到仓库。仓库内只记录必要Hash、路径、Build链接和结论。

### 23.2 三层状态语义

阶段6必须分别消费三个状态：

1. **本地验收状态**：Runner、Quality、Metrics、Flaky和本地Pipeline是否通过；
2. **外部门禁状态**：实际Jenkins是否执行并通过；
3. **发布状态**：完整`tests`是否零失败。

禁止因本地门禁通过就把Jenkins或发布状态写成通过；若出现历史债务，可以记录增量归因结论，但不得据此降低阶段5关闭或发布门禁。

### 23.3 阶段6可执行内容

阶段6根据实际验收结果更新`README.md`、`FRAMEWORK_TEST_SPEC.md`和最终发布历史，说明学习顺序、Quality开关、黄金nodeid、产物位置、Flaky多轮规则、Jenkins参数和已知发布阻塞。

文档命令必须以本阶段真实成功命令为准，不得抄写未执行示例或仓库`reports/`中的历史路径。

### 23.4 未决债务交接

若阶段5执行时重新出现任何完整回归失败，阶段6必须明确记录失败nodeid、增量归因和阻塞状态，并把债务交给独立任务处理；不得在文档阶段顺手修改测试、忽略规则或更新基线制造通过。

## 24. 本阶段最终交付

实际执行阶段5后的最终交付应为：

```text
1套Runner collect-only与Quality关闭验收证据
+ 1套完整23项Quality/Semantic/Metrics消费证据
+ 1套黄金路径4 operations / 7 groups / 1 polling / 8 events精确证据
+ 1套fail-open双结论证据
+ stable_min_samples + 1轮真实Flaky历史与状态证据
+ 1套同源Pipeline Summary本地模拟证据
+ 1次实际Jenkins参数化构建与归档证据
+ 1份完整回归失败归因与阶段5关闭结论
+ 1份阶段5验收记录
+ 0个生产、测试、Quality、Runner和Jenkins代码改动
```

阶段5的完成定义不是“所有命令返回0”，而是每个消费者都能用本轮、同源、可追溯的事实得出正确结论，并且任何降级、外部阻塞和既有债务都没有被隐藏。
