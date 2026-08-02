# Jenkins 流水线全场景执行摘要初版修改方案

## 一、方案定位

本方案替代只覆盖 Real Smoke 的执行摘要方案，将报告生命周期提升到 Jenkins Pipeline 级别。

每一轮已经进入 Jenkinsfile 执行的构建，无论参数组合和最终状态如何，都必须生成：

```text
reports/pipeline-summary.md
```

覆盖场景：

```text
只执行框架单测
只执行 Smoke collect-only
只执行真实 Smoke
框架单测 + collect-only
框架单测 + 真实 Smoke
三者全部执行
所有测试阶段均未选择
任一阶段执行失败
准备阶段完成后测试阶段提前中断
```

`pipeline-summary.md` 是 Jenkins 邮件和人工查看的唯一推荐入口。现有 P0/P1、JUnit、Allure 和机器 JSON 继续归档，作为对应章节的详细证据。

## 二、需求理解

### 2.1 核心要求

观测不能只在 Real Smoke 启用 Quality 后存在，而应覆盖整轮流水线：

```text
Pipeline Build
├─ Framework Unit Tests
├─ Smoke Collect
├─ Real Smoke
└─ Quality Observation（仅 Real Smoke 启用时存在）
```

报告必须区分：

- 阶段已执行且通过。
- 阶段已执行但失败。
- 阶段按参数未选择。
- 阶段被前序失败阻断。
- 阶段执行了但产物缺失。
- 质量观测不适用。

`NOT_RUN`、`BLOCKED`、`NO_DATA` 和 `FAILED` 不能混为同一种状态。

### 2.2 保留的人工信息

流水线通用信息：

- Jenkins 构建结果。
- Job、构建号、分支、提交、环境和参数。
- 各阶段执行状态。
- 框架单测结果。
- Smoke 收集数量和并串行划分。
- 真实 Smoke 用例结果、失败和跳过。
- 总耗时和阶段耗时（能可靠获得时）。
- 简短建议动作。

真实 Smoke 执行时额外展示：

- 请求成功率。
- HTTP 5xx 和超时。
- 重试挽救率。
- 接口耗时 Top 5。
- Flaky 状态迁移。

### 2.3 初版不展示

- Token、媒体数量和 Usage 覆盖率。
- operation/request group/request event 多层并列数据。
- P95/P99 和性能基线。
- Flaky 完整状态投影和历史窗口。
- 数据源版本、算法版本、内部 hash、request ID 和 invocation ID。
- 成本估算和价格对账。

## 三、第一性原理与 TOC 约束

### 3.1 第一性原理

流水线报告的本质是对“一次构建”的可验证说明：

```text
这轮配置要求执行什么
→ 实际执行了什么
→ 结果是什么
→ 哪些内容没有执行
→ 下一步需要处理什么
```

因此主身份必须是：

```text
job_name + build_number
```

Quality `run_id` 只在 Real Smoke 执行并生成质量产物时作为子身份，不能作为整份流水线报告的必要条件。

### 3.2 原方案无法满足要求的因果链

```text
quality-summary 在 run_master/P1 后生成
→ 只有 Real Smoke 会进入该路径
→ framework-only 和 collect-only 没有 Quality run_id
→ 前序阶段失败时后置 summary stage 无法执行
→ 无法保证每轮流水线都有报告
```

### 3.3 TOC 当前约束

当前约束从“报告内容过多”转变为“报告生命周期过窄”。必须先把生成时机移动到：

```text
Jenkins post { always }
```

然后再决定各章节是否有数据。不能继续通过扩展 Real Smoke 内部 stage 解决 Pipeline 级问题。

## 四、目标与非目标

### 4.1 目标

1. 每轮 Jenkins Pipeline 生成 `reports/pipeline-summary.md`。
2. 所有参数组合都能得到与本轮配置相符的报告。
3. 未选择阶段显示“未执行”，不产生错误告警。
4. 被前序失败阻断的阶段显示“已阻断”。
5. 已选择且执行但缺少必要产物时明确显示“产物缺失”。
6. Real Smoke 执行时保留请求、重试、耗时和 Flaky 四项指标。
7. 摘要生成失败不覆盖原始 Jenkins/pytest 结果。
8. Jenkins 邮件首个入口指向流水线摘要。

### 4.3 报告生成开关

新增专用环境变量：

```text
GENERATE_PIPELINE_SUMMARY=TRUE
```

允许值沿用现有布尔配置语义：

```text
TRUE / true / 1 / yes / on
FALSE / false / 0 / no / off
```

默认值为 `TRUE`。变量只控制 Pipeline 执行摘要：

```text
TRUE
→ 生成 reports/pipeline-summary.md
→ 主生成器异常时尝试生成最小兜底报告
→ Jenkins 邮件在文件存在时展示摘要链接

FALSE
→ 不生成主报告
→ 不生成最小兜底报告
→ Jenkins 邮件不展示摘要链接
→ P0/P1/JUnit/Allure 等其他产物不受影响
```

配置优先级：

```text
Jenkins 构建参数/当前进程环境
→ .env
→ 默认 TRUE
```

关闭开关属于用户明确选择，不应显示为 `NO_DATA`、`BLOCKED` 或报告生成失败。

### 4.2 非目标

- 不修改 Runtime Hooks 和 Semantic Schema。
- 不修改 P0/P1 聚合算法。
- 不修改 Flaky Store。
- 不引入趋势数据库。
- 不建立性能基线和强制门禁。
- 不要求 collect-only 创建 Quality run_id。
- 不把未执行的真实 Smoke 标记为质量数据缺失。

## 五、流水线级状态模型

### 5.1 阶段状态

初版定义：

```text
PASSED     已执行且成功
FAILED     已执行且失败
NOT_RUN    参数未选择该阶段
BLOCKED    已选择，但被前序失败或流水线中断阻止
NO_DATA    已执行，但必要结果产物缺失或不可解析
```

适用阶段：

- Checkout。
- Check Runtime Env。
- Prepare Python Env。
- Framework Unit Tests。
- Collect Smoke Cases。
- Real Smoke。
- Quality Observation。

Quality Observation 状态规则：

```text
RUN_REAL_SMOKE=false
→ NOT_RUN

RUN_REAL_SMOKE=true，但 Real Smoke 未开始
→ BLOCKED

Real Smoke 已运行，Quality 产物完整
→ PASSED

Real Smoke 已运行，但核心 Quality 产物缺失
→ NO_DATA
```

P1 内部 `degraded` 不直接等于 Pipeline 阶段失败。初版只在详细证据链接中保留该状态，不让 Usage 等复杂观测改变执行摘要。

### 5.2 Pipeline 结论

结论优先级：

```text
Jenkins FAILURE/ABORTED，或任一已执行测试阶段 FAILED
→ FAIL

选择的测试阶段出现 NO_DATA/BLOCKED
→ WARN（如果 Jenkins 已失败，则仍为 FAIL）

用例无失败但存在 skipped
→ WARN

存在重试挽救
→ WARN

存在新增疑似/确认 Flaky、隔离或超期治理项
→ WARN

所有测试阶段均未选择
→ WARN：本轮未执行测试验证

按配置执行的阶段全部成功，且没有上述风险
→ PASS
```

NOT_RUN 是本轮配置事实，本身不产生 WARN；只有所有测试阶段都 NOT_RUN 时产生“未执行验证”的 WARN。

## 六、报告结构

```markdown
# Jenkins 流水线执行摘要

## 本次结论

PASS：本轮按配置执行完成

- Job：llm-api-case #65
- Jenkins 结果：SUCCESS
- 分支/提交：origin/dev3 / 3361c4d
- 环境：中国
- 总耗时：16分15秒

## 执行参数

| 参数 | 值 |
| RUN_FRAMEWORK_TESTS | true |
| RUN_COLLECT_ONLY | true |
| RUN_REAL_SMOKE | false |
| SMOKE_TARGET | module/smoke |
| TEST_PARALLEL_WORKERS | off |

## 阶段结果

| 阶段 | 状态 | 结果摘要 |
| Framework Unit Tests | PASSED | 596 通过 |
| Smoke Collect | PASSED | 41 项：15 并发 / 26 串行 |
| Real Smoke | NOT_RUN | 本轮参数未启用 |
| Quality Observation | NOT_RUN | 真实 Smoke 未启用 |

## 框架单测

596 通过 / 0 失败 / 0 错误 / 0 跳过

## Smoke 收集

41 项：15 条并发池 / 26 条串行池

## 真实 Smoke

本轮未启用真实 Smoke。

## 建议动作

- 本轮框架单测和 Smoke 收集均通过。
- 本轮没有执行真实接口验证。
```

当真实 Smoke 执行时，追加：

```markdown
## 请求质量
## 重试效果
## 接口耗时 Top 5
## Flaky 状态迁移
```

## 七、各运行模式的展示规则

### 7.1 默认安全构建

```text
RUN_FRAMEWORK_TESTS=true
RUN_COLLECT_ONLY=true
RUN_REAL_SMOKE=false
```

必须生成报告，结论基于框架单测和收集结果。Real Smoke 与 Quality 显示 NOT_RUN，不显示请求、重试、耗时和 Flaky 章节。

### 7.2 真实 Smoke 构建

```text
RUN_REAL_SMOKE=true
```

报告同时展示测试结果和四项保留观测。Quality 产物不可用时不伪造零值，对应章节显示“产物不可用”。

### 7.3 Framework-only

只展示框架单测；Smoke Collect、Real Smoke 和 Quality 均显示 NOT_RUN。

### 7.4 Collect-only

只展示收集数量和分池；框架单测与 Real Smoke 根据参数显示 NOT_RUN。

### 7.5 前序失败

例如 Framework Unit Tests 失败导致后续 Declarative stages 不再执行：

```text
Framework Unit Tests = FAILED
后续已选择阶段 = BLOCKED
未选择阶段 = NOT_RUN
```

报告结论为 FAIL，并列出已产生的失败 JUnit；不能因为 Real Smoke 没有 Quality 产物而改成 NO_DATA。

## 八、真实 Smoke 四项指标口径

### 8.1 请求成功率

来源：

```text
reports/quality/merged/request-metrics.jsonl
```

技术请求成功定义：

```text
收到 HTTP 响应
且没有 timeout
且没有传输异常
且状态码不是 429
且状态码小于 500
```

400/404 负向契约响应计为技术请求成功，业务验证结果由 pytest 决定。轮询 pending 的 HTTP 200 不降低成功率。

分母为 0 时展示“本轮未产生真实请求”，不展示 `0.00%`。

### 8.2 重试挽救率

复用：

```text
request_group.http_retry_rescue_rate
```

展示重试请求组、挽救成功数量和比例。无重试时显示“本轮未发生重试”。

### 8.3 接口耗时 Top 5

复用 `request_group_bucket`：

```text
traffic_role=workload
protocol!=polling
```

按平均总耗时降序，只展示接口、请求组数量、平均耗时和最大耗时。Control 与单次 polling 不进入排行。

### 8.4 Flaky 状态迁移

来源：

```text
flaky-evaluation.json
```

展示新增疑似、新增确认、恢复稳定、进入隔离和超期治理数量。迁移按方向聚合；无迁移与未启用必须使用不同文案。

## 九、生成时机与兜底策略

### 9.0 开关判定

进入任何摘要生成逻辑前，必须先解析 `GENERATE_PIPELINE_SUMMARY`：

```text
FALSE
→ 记录一条简短日志：Pipeline summary generation is disabled
→ 直接结束摘要逻辑
→ 不进入 Python 主生成器
→ 不进入 Jenkins 最小兜底

TRUE
→ 执行后续主生成与兜底逻辑
```

非法值不得静默当作 TRUE 或 FALSE。处理方式：

```text
记录明确配置错误
→ 不改变原始 Jenkins 结果
→ 尝试生成最小兜底报告，说明开关配置无效
```

Jenkins boolean parameter 本身不会产生非法字符串；非法值主要用于保护手工环境变量和本地调用。

### 9.1 主生成时机

在 Jenkinsfile 中调整：

```groovy
post {
    always {
        script {
            generatePipelineSummary()
        }
        allure ...
        archiveArtifacts ...
    }
    failure { ...发送邮件... }
    unstable { ...发送邮件... }
    success { ...发送邮件... }
}
```

必须满足顺序：

```text
生成摘要
→ 发布 Allure/JUnit
→ 归档报告
→ 发送状态邮件
```

摘要必须在归档和邮件之前存在。

### 9.2 Python 主生成器

新增：

```text
pipeline_reporting/
  __init__.py
  __main__.py
  config.py
  contracts.py
  sources.py
  builder.py
  renderer.py
  service.py
```

职责：

- 接收 Jenkins 构建身份、参数和结果。
- 从进程环境和 `.env` 加载 `GENERATE_PIPELINE_SUMMARY`。
- 读取本轮 JUnit、collect 输出和可选 Quality 产物。
- 推导阶段状态。
- 生成 Markdown。
- 原子写入 `reports/pipeline-summary.md`。

该模块属于 Pipeline 报告层，不放入 `quality/`，因为 framework-only 和 collect-only 构建没有 Quality 生命周期。

依赖方向：

```text
pipeline_reporting
→ 可读取 quality 的公开产物模型

quality/common/module
-X-> pipeline_reporting
```

### 9.3 Jenkins 最小兜底报告

如果以下情况导致 Python 主生成器不可用：

- Checkout 后代码不完整。
- Python 环境创建失败。
- 依赖安装失败。
- 主生成器自身异常。

Jenkinsfile 必须捕获异常并用 Groovy/`writeFile` 写入最小报告：

```markdown
# Jenkins 流水线执行摘要

- 构建：llm-api-case #65
- Jenkins 结果：FAILURE
- 报告状态：详细摘要生成失败
- 失败阶段：Prepare Python Env
- 建议：查看 Console 日志定位环境准备问题
```

兜底摘要不解析业务指标，只保证“每轮有报告”。兜底生成失败不得覆盖原始构建结果。

### 9.4 保证边界

仓库内 Jenkinsfile 能保证的是：

```text
Jenkins 已成功加载 Jenkinsfile 并分配 Agent
→ post always 尝试生成主报告或兜底报告
```

如果 Jenkins 在加载 Jenkinsfile 前就因 SCM、Controller 或 Agent 基础设施失败，仓库代码无法执行，此类情况只能由 Jenkins 系统级通知覆盖，不伪称仓库方案可以保证。

## 十、阶段状态采集

### 10.1 参数状态

Python 生成器接收：

- `RUN_FRAMEWORK_TESTS`
- `RUN_COLLECT_ONLY`
- `RUN_REAL_SMOKE`
- `GENERATE_PIPELINE_SUMMARY`
- `USE_CHINA_ENVIRONMENT`
- `SMOKE_TARGET`
- `TEST_PARALLEL_WORKERS`
- `JOB_NAME`
- `BUILD_NUMBER`
- `BUILD_URL`
- `GIT_BRANCH`
- `GIT_COMMIT`
- `currentBuild.currentResult`

不读取或输出凭据环境变量。

### 10.2 阶段状态文件

为避免仅凭文件存在推测执行状态，新增：

```text
reports/pipeline-stage-status.json
```

Jenkins 在阶段开始、成功和失败边界更新状态：

```json
{
  "framework_tests": "PASSED",
  "smoke_collect": "PASSED",
  "real_smoke": "NOT_RUN"
}
```

状态更新使用仓库提供的小型 CLI，或 Jenkinsfile 中的受控 JSON 写入函数。初版只记录阶段枚举和时间，不记录 Console 内容。

如果状态文件缺失，生成器使用“参数 + 产物 + Jenkins 结果”降级推断，并在报告中标记来源不完整。

## 十一、数据源映射

| 报告区域 | 主要来源 | 不存在时的语义 |
| --- | --- | --- |
| 构建身份/参数 | Jenkins 环境和参数 | 兜底报告 |
| 阶段状态 | pipeline-stage-status.json | 降级推断 |
| 框架单测 | reports/unit-tests.xml | NOT_RUN/BLOCKED/NO_DATA 按阶段判断 |
| Smoke 收集 | reports/smoke-collect.txt | NOT_RUN/BLOCKED/NO_DATA 按阶段判断 |
| 真实 Smoke | reports/smoke-tests*.xml | NOT_RUN/BLOCKED/NO_DATA 按阶段判断 |
| 请求质量 | merged/request-metrics.jsonl | 真实 Smoke 未运行时不适用 |
| 重试/耗时 | metrics/run-metrics.json | 章节数据不可用 |
| Flaky | flaky-evaluation.json | 未启用或数据不可用 |

JUnit 文件按 testcase 身份去重，Quality 文件必须校验与本轮 Real Smoke `run_id` 一致。Pipeline 报告不能扫描历史目录后按修改时间拼接数据。

## 十二、文件级修改清单

### 12.1 新增

```text
pipeline_reporting/__init__.py
pipeline_reporting/__main__.py
pipeline_reporting/config.py
pipeline_reporting/contracts.py
pipeline_reporting/sources.py
pipeline_reporting/builder.py
pipeline_reporting/renderer.py
pipeline_reporting/service.py
tests/test_pipeline_reporting.py
tests/test_pipeline_reporting_sources.py
tests/test_pipeline_reporting_renderer.py
tests/quality/fixtures/pipeline_reporting/
```

### 12.2 修改

```text
.env.example
```

- 新增 `GENERATE_PIPELINE_SUMMARY=TRUE`。
- 说明它只控制 Pipeline 摘要，不控制 Allure、历史报告或 P0/P1 采集。

```text
Jenkinsfile
```

- 新增 boolean 参数 `GENERATE_PIPELINE_SUMMARY`，默认 `true`。
- 将参数值作为同名环境变量传给 Python 主生成器。
- 每日参数化真实 Smoke 显式携带 `GENERATE_PIPELINE_SUMMARY=true`。
- `post always` 首先判断开关，再决定是否生成主报告或兜底报告。
- 初始化阶段状态。
- 在测试阶段开始/结束边界记录状态。
- `post always` 调用 Python 主生成器。
- 主生成器异常时生成最小兜底报告。
- 在归档和邮件前完成摘要。
- 邮件首链指向 `pipeline-summary.md`。
- P0/P1/Allure/JUnit 作为详细入口保留。

```text
quality/junit.py
```

- 复用身份和 skipped message 解析能力。
- 暴露给 Pipeline 报告层使用的稳定只读接口。

```text
quality/identifiers.py
```

- 补充带动态后缀任务 ID 的归一化，保证接口耗时排行身份稳定。

```text
README.md
FRAMEWORK_TEST_SPEC.md
JENKINS_MIGRATION_TEMPLATE.md
```

- 说明本地 `.env` 和 Jenkins 中的开关用法、默认值与优先级。
- 默认查看入口改为 Pipeline 摘要。
- 说明不同参数组合下的章节差异。
- 说明 NOT_RUN、BLOCKED、NO_DATA 和 FAILED。
- 补充 Jenkins 迁移时摘要生成依赖与兜底行为。

```text
tests/quality/test_quality_jenkinsfile.py
```

- 验证 post always 先生成摘要再归档。
- 验证邮件链接和 fallback。
- 验证所有参数组合具有明确阶段状态。

### 12.3 不修改

```text
common/runtime_hooks/
quality/semantic_models.py
quality/metrics/ 聚合算法
quality/flaky_store/
module/smoke/
run_master.py 稳定入口
```

## 十三、异常与安全边界

### 13.1 未运行不等于无数据

- 参数关闭：NOT_RUN。
- 参数开启但前序失败：BLOCKED。
- 确认执行但产物缺失：NO_DATA。
- 已执行且命令失败：FAILED。

### 13.2 观测失败不覆盖原始结果

- Pipeline 报告生成异常采用 fail-open。
- 原始 pytest/Jenkins 结果保持不变。
- 兜底报告明确“详细摘要生成失败”，不伪造用例数量。

### 13.3 开关关闭边界

- 开关关闭时不创建空文件或“已禁用”占位报告。
- 开关关闭时不执行 Python 报告加载和统计。
- 开关关闭时 Jenkins 邮件不产生摘要链接。
- 开关关闭不影响 JUnit、Allure、P0、P1 和 Flaky 产物。
- 下一轮重新开启时只能读取本轮 workspace 产物，不能复用旧摘要。

### 13.4 脱敏

- 不输出 `.env`、API Key、账号和 SMTP 凭据。
- 构建参数只允许白名单字段。
- 失败与跳过原因复用统一脱敏。
- 接口只展示归一化路径。
- 不嵌入完整 Console、请求或响应。

## 十四、测试矩阵

### 14.1 参数组合

至少覆盖：

| Framework | Collect | Real Smoke | 预期 |
| --- | --- | --- | --- |
| true | true | false | 单测和收集有结果，Real/Quality NOT_RUN |
| true | false | false | 仅单测有结果 |
| false | true | false | 仅收集有结果 |
| false | false | true | Real/Quality 有结果 |
| true | false | true | 单测、Real/Quality 有结果 |
| true | true | true | 全部有结果 |
| false | false | false | WARN：未执行测试验证 |

上述每种参数组合都要分别覆盖：

```text
GENERATE_PIPELINE_SUMMARY=true
→ 生成与执行路径一致的摘要

GENERATE_PIPELINE_SUMMARY=false
→ 不生成摘要，其他产物行为不变
```

### 14.2 开关配置

- 环境变量未配置时默认生成。
- `.env` 配置 TRUE 时生成。
- `.env` 配置 FALSE 时不生成。
- Jenkins 参数优先于 `.env`。
- Jenkins 参数 false 时不进入主生成器和兜底逻辑。
- 非法环境值产生明确配置错误，但不覆盖 Jenkins 原始结果。
- 开关从 false 改回 true 后不读取上一构建残留摘要。

### 14.3 失败路径

- Framework 失败，后续选择阶段为 BLOCKED。
- Collect 失败，Real 未选择时结论 FAIL。
- Real Smoke 失败但 JUnit 存在，展示失败用例。
- Real Smoke 已选择但 JUnit 缺失，显示 NO_DATA/FAILED，不显示 0 条用例。
- Python 环境失败时生成最小兜底报告。
- 主生成器异常不改变原始 Jenkins 状态。

### 14.4 Quality 可选来源

- Real Smoke 未运行时不读取 Quality 产物。
- Real Smoke 运行、Metrics 缺失时请求和用例仍可展示。
- Flaky 未启用与无迁移使用不同文案。
- Quality run_id 冲突时拒绝对应章节，不污染 Pipeline 主结论。

### 14.5 四项保留指标

- 200/400/404 计入技术请求成功。
- 429/5xx/timeout/连接异常不计入成功。
- 无重试显示自然语言。
- 重试挽救按请求组统计。
- 耗时 Top 过滤 control/polling，最多五条。
- Flaky 迁移按方向聚合，可行动项限制展示窗口。

### 14.6 collect-only 副作用

`run_master.py --collect-only` 仍不创建 Quality run_id 和质量产物；Pipeline 级报告由 Jenkins post 生成，不能改变 collect-only 的无副作用契约。

## 十五、验收命令

离线：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_pipeline_reporting.py -q
.\.venv\Scripts\python.exe -m pytest tests/quality -q
.\.venv\Scripts\python.exe -m pytest tests -q
.\.venv\Scripts\python.exe run_master.py module/smoke --collect-only -q
```

Jenkins 验收至少执行：

1. 默认安全构建。
2. Framework-only。
3. Collect-only。
4. 一次已授权 Real Smoke。
5. 一个受控失败分支或离线模拟失败流水线。

每轮均确认：

- 开关开启时 `reports/pipeline-summary.md` 存在。
- 开关关闭时 `reports/pipeline-summary.md` 不存在，邮件无摘要链接。
- 报告参数与构建参数一致。
- 阶段状态与实际执行路径一致。
- 未执行阶段没有被误报为失败或无数据。
- 邮件首链可打开本轮摘要。
- 详细产物链接只在对应产物存在时出现。

## 十六、实施顺序

### 步骤一：Pipeline 报告契约

- 增加 `GENERATE_PIPELINE_SUMMARY` 配置解析与优先级。
- 定义构建身份、参数、阶段状态和章节可用性。
- 完成 NOT_RUN/BLOCKED/NO_DATA/FAILED 判定单测。

### 步骤二：通用来源加载

- 加载 Unit JUnit、Smoke collect 和 Real Smoke JUnit。
- 支持没有 Quality run_id 的构建。
- 完成测试用例身份去重。

### 步骤三：Real Smoke 可选观测

- 接入请求成功率。
- 接入重试挽救率。
- 接入接口耗时 Top 5。
- 接入 Flaky 状态迁移。

### 步骤四：Jenkins 生命周期

- 记录阶段状态。
- 在 post always 生成摘要。
- 增加 Python 失败时的最小兜底。
- 确保摘要先于归档和邮件。

### 步骤五：文档与回归

- 更新 README、规范和 Jenkins 迁移模板。
- 完成全参数矩阵离线测试。
- 完成多种 Jenkins 构建模式验收。
- 输出独立代码变更记录。

## 十七、完成标准

```text
[ ] GENERATE_PIPELINE_SUMMARY=true 时，每轮已进入 Jenkinsfile 的构建生成 pipeline-summary.md
[ ] .env.example 包含 GENERATE_PIPELINE_SUMMARY=TRUE
[ ] Jenkins 提供同名 boolean 参数且默认开启
[ ] Jenkins 参数优先于 .env
[ ] 开关关闭时主报告、兜底报告和邮件链接均不产生
[ ] 非法配置不覆盖 Jenkins 原始结果
[ ] Framework-only 有对应报告
[ ] Collect-only 有对应报告
[ ] Real Smoke 有对应报告及四项保留指标
[ ] 未选择阶段显示 NOT_RUN
[ ] 前序失败阻断的已选阶段显示 BLOCKED
[ ] 已执行但产物缺失显示 NO_DATA
[ ] 失败阶段显示 FAILED
[ ] 所有测试阶段未选择时明确 WARN
[ ] 摘要生成失败时产生最小兜底报告
[ ] 摘要失败不覆盖原始 Jenkins 结果
[ ] collect-only 仍不创建 Quality run_id
[ ] Jenkins 邮件首链指向 pipeline-summary.md
[ ] P0/P1/JUnit/Allure 继续作为详细证据
[ ] 所有新增测试进入 Git 跟踪范围
[ ] 代码变更记录写入 code_history
```

## 十八、后续边界

初版稳定后再评估：

- Pipeline 阶段耗时趋势。
- expected outcome 与 Usage applicability。
- Skip owner、过期和工单治理。
- 跨构建性能趋势与基线。
- Controller/SCM 在 Jenkinsfile 加载前失败的系统级通知。

后续能力不得让 `pipeline-summary.md` 重新变成内部指标大全；首页继续保持少指标、按阶段组织和可直接判断。
