# Smoke 执行摘要初版精简修改方案（已被修订）

> 本方案最初只覆盖 Real Smoke，已被
> `dev_plan/Jenkins流水线全场景执行摘要初版修改方案.md` 替代。
> 后续实施以修订方案为准，本文件仅保留需求演进记录。

## 一、方案定位

本方案只实现一份面向人工判断的 Smoke 执行摘要：

```text
reports/quality/quality-summary.md
```

使用者打开报告后，应能在 30 秒内回答：

1. 本次执行结果是否有效。
2. 用例是否通过，是否存在未覆盖场景。
3. 请求层是否出现明显异常。
4. 重试是否掩盖了瞬时问题。
5. 哪些接口最慢。
6. 本轮是否发生需要关注的 Flaky 状态变化。

现有机器证据和详细报告继续保留：

```text
gate-report.json
gate-report.md
p1-observation.json
p1-observation.md
metrics/run-metrics.json
flaky-evaluation.json
```

但 Jenkins 邮件和 README 的日常使用入口优先指向 `quality-summary.md`。初版不删除 P0/P1 产物，不改变现有命令、Schema、目录和门禁模式。

## 二、需求理解

本次确认保留的人工可见信息为：

- 本次执行结论。
- 用例总数、通过、失败、错误、跳过和总耗时。
- 失败用例与跳过用例原因。
- 请求成功率、HTTP 5xx 和超时。
- 重试挽救率。
- 接口耗时 Top。
- Flaky 状态迁移。
- 根据事实生成的简短建议动作。

初版不展示：

- Token、媒体数量和 Usage 覆盖率。
- 逻辑调用成功率。
- 多粒度耗时明细。
- P95/P99 和性能基线。
- 数据源版本、算法版本、内部 hash、invocation ID。
- Flaky 完整投影、历史窗口和数据库治理细节。
- 请求组、请求事件、operation 三层并列指标。
- 成本估算和价格对账。

## 三、第一性原理与 TOC 约束

### 3.1 第一性原理

测试报告的价值不等于统计项数量，而等于它能否降低判断成本：

```text
可信事实
→ 清晰结论
→ 可定位问题
→ 明确下一步动作
```

pytest 用例结果是“本次业务验证是否成立”的权威事实；请求、重试、耗时和 Flaky 是解释执行效果的辅助证据。初版不能用辅助指标覆盖 pytest 结论，也不能因为 P1 内部数据源显示 `degraded` 就把一次实际通过的执行描述成业务失败。

### 3.2 当前因果链

当前两份人工报告同时展示大量内部指标：

```text
P0 展示用例、请求、分类和接口耗时
P1 展示 operation、request group、request event、Usage、Flaky 和完整性
→ 同一现象存在多种统计口径
→ 使用者需要理解框架内部模型
→ 阅读成本高于判断收益
→ 真正需要处理的失败、跳过和状态变化不突出
```

### 3.3 TOC 当前约束

当前约束不是缺少采集能力，而是缺少单一、稳定的人工决策入口。因此本迭代不继续增加指标，优先完成：

```text
复用现有可信产物
→ 统一运行身份
→ 选择少量直观指标
→ 生成一页执行摘要
```

## 四、目标与非目标

### 4.1 目标

1. 新增唯一推荐人工入口 `quality-summary.md`。
2. 报告只展示本次执行最直观的信息。
3. 所有数据复用现有产物，不重复采集请求。
4. 所有来源必须属于同一个 `run_id`。
5. 请求成功率使用明确的技术口径，不受轮询中间态和预期 4xx 干扰。
6. 无重试、无 Flaky 迁移时使用自然语言，不展示 `NO_DATA`。
7. 报告失败不覆盖 pytest 原始退出码。

### 4.2 非目标

本迭代不做：

- Runtime Hooks 和 Semantic Schema 扩展。
- expected outcome 与 Usage applicability 语义改造。
- P0/P1 强制门禁。
- 趋势数据库和跨构建性能比较。
- Flaky 状态机规则修改。
- Skip owner、到期时间和工单治理。
- 估算成本、账本价格或性能阈值。
- 新增 YAML、Excel 或报告 DSL。

## 五、报告结论规则

摘要使用独立的执行结论，不改变 P0 影子门禁和 Jenkins 状态。

优先级从高到低：

```text
数据不完整、run_id 冲突或没有用例结果
→ NO_DATA：本次结果不可判断

存在 failed 或 error
→ FAIL：本次执行失败

没有失败，但存在 skipped
→ WARN：执行成功，但覆盖不完整

没有失败和跳过，但出现重试挽救
→ WARN：执行通过，但存在瞬时稳定性风险

没有失败和跳过，但出现新增疑似/确认 Flaky、隔离或超期
→ WARN：执行通过，但存在稳定性治理事项

其余情况
→ PASS：本次执行通过
```

请求成功率和接口耗时只用于解释，不单独覆盖 pytest 执行结论。

## 六、指标口径

### 6.1 用例执行结果

来源：

```text
reports/quality/summary.json
reports/quality/merged/case-results.jsonl
reports/smoke-tests*.xml
```

展示：

- 总数。
- 通过。
- 失败。
- 错误。
- 跳过。
- 运行总耗时。

并发池与串行池 JUnit 必须按 `quality_invocation_id` 去重，不能把同一 invocation 重复计数。

### 6.2 请求成功率

来源：

```text
reports/quality/merged/request-metrics.jsonl
```

初版定义为技术请求成功率：

```text
成功请求 =
    收到 HTTP 响应
    且没有 timeout
    且没有传输异常
    且状态码不是 429
    且状态码小于 500

请求成功率 = 成功请求数 / 请求总数
```

说明：

- 预期负向用例的 400/404 表示服务按契约返回，不按技术请求失败计算。
- 业务断言是否成立仍由 pytest 用例结果决定。
- 轮询 pending 的 HTTP 200 属于成功响应，不降低请求成功率。
- 429、5xx、超时和网络异常进入请求失败。
- 分母为 0 时展示“本轮未产生请求”，不展示 `0.00%`。

同时展示：

- 请求总数。
- 请求成功率。
- HTTP 5xx 数量。
- 超时数量。

初版不展示当前 `business_status` 口径的 31.96% 请求成功率，避免将轮询中间态和预期失败混为服务异常。

### 6.3 重试挽救率

来源：

```text
reports/quality/metrics/run-metrics.json
```

复用已有：

```text
request_group.http_retry_rescue_rate
```

口径：

```text
重试请求组 = attempt_count > 1 的请求组
挽救成功 = 首次 HTTP 非成功，最终 HTTP 成功
重试挽救率 = 挽救成功请求组 / 重试请求组
```

展示：

- 发生重试的请求组数量。
- 挽救成功数量。
- 重试挽救率。

没有发生重试时展示：

```text
本轮未发生重试
```

不展示 `NO_DATA`、未知数和内部 completeness。

### 6.4 接口耗时 Top

来源：

```text
reports/quality/metrics/run-metrics.json
```

使用 `request_group_bucket` 的总耗时，只保留：

```text
traffic_role = workload
protocol != polling
```

排序：

```text
平均总耗时降序
→ 最大耗时降序
→ interface_id 升序
```

只展示 Top 5：

- 接口标识。
- 请求组数量。
- 平均耗时。
- 最大耗时。

统一换算为秒并保留两位小数。初版不展示最小值、P95/P99、模型维度、operation 维度和性能结论。

Control 接口和单次 polling 请求不进入 Top，避免余额查询、usage 查询和轮询频率污染业务接口排行。

### 6.5 Flaky 状态迁移

来源：

```text
reports/quality/flaky-evaluation.json
```

先展示汇总：

- 新增疑似。
- 新增确认。
- 恢复稳定。
- 进入隔离。
- 超期治理。

状态迁移按 `from_state -> to_state` 聚合计数。

展示规则：

- 没有迁移时展示“本轮无 Flaky 状态迁移”。
- `OBSERVING -> STABLE` 等批量稳定迁移只展示数量。
- 新增疑似、确认、隔离、恢复和超期项最多展示 5 条用例。
- 省略内部 transition ID、观察 ID、证据 hash 和完整历史窗口。

## 七、报告结构

```markdown
# Smoke 执行摘要

## 本次结论

WARN：执行成功，但覆盖不完整

- 数据有效：是
- 环境：中国
- Jenkins：llm-api-case #64
- 分支/提交：origin/dev3 / 3361c4d
- 测试目标：module/smoke
- 执行画像：auto，15 条并发池 / 26 条串行池

## 执行结果

| 总数 | 通过 | 失败 | 错误 | 跳过 | 总耗时 |
| 41 | 34 | 0 | 0 | 7 | 12分50秒 |

## 请求质量

| 请求总数 | 请求成功率 | HTTP 5xx | 超时 |
| 194 | 100.00% | 0 | 0 |

## 失败用例

无失败用例。

## 跳过用例

| 用例 | 原因 |
| ... | 缺少稳定 timeout 触发方式 |

## 重试效果

本轮未发生重试。

## 接口耗时 Top 5

| 接口 | 请求组 | 平均耗时 | 最大耗时 |
| POST /v1/images/generations http | 9 | 45.34 秒 | 64.01 秒 |

## Flaky 状态迁移

本轮无 Flaky 状态迁移。

## 建议动作

- 本轮没有失败用例。
- 7 条用例未实际执行，需要查看跳过原因。
```

## 八、架构与数据流

### 8.1 新增目录

```text
quality/execution_summary/
  __init__.py
  contracts.py      # 输入、展示模型和生成结果
  loader.py         # 加载并校验现有产物
  builder.py        # 结论、请求、重试、耗时和 Flaky 摘要
  renderer.py       # 中文 Markdown 渲染
  service.py        # 生成流程和原子写入
```

职责边界：

```text
loader
→ 只做文件读取、Schema 解析和 run_id 一致性校验

builder
→ 只做本次执行摘要计算，不执行 IO

renderer
→ 只把展示模型转成 Markdown

service
→ 编排加载、构建、渲染和原子写入
```

`execution_summary` 可以读取 `quality` 公开产物模型，但不得反向被 `common`、业务用例和采集器依赖。

### 8.2 执行链路

```text
P0 merge/report
→ semantic merge
→ metrics
→ flaky import/evaluation
→ P1 observation
→ execution summary
```

新增：

```text
run_orchestration/quality_execution_summary_stage.py
```

并在 `run_orchestration/quality_pipeline.py` 最后调用。摘要只消费已经完成的产物，不改变前序阶段。

### 8.3 运行身份约束

以下来源必须属于同一 `run_id`：

- `run.json`
- `summary.json`
- `gate-report.json`
- `metrics/run-metrics.json`
- `flaky-evaluation.json`
- `case-results.jsonl`
- `request-metrics.jsonl`

处理规则：

```text
P0 核心来源 run_id 不一致
→ NO_DATA，不生成混合结论

Metrics 或 Flaky 可选来源缺失/不一致
→ 保留用例执行结论，对应章节展示“数据不可用”
```

不能扫描目录后直接取“最新修改时间”的文件拼接报告。

## 九、文件级修改清单

### 9.1 新增

```text
quality/execution_summary/__init__.py
quality/execution_summary/contracts.py
quality/execution_summary/loader.py
quality/execution_summary/builder.py
quality/execution_summary/renderer.py
quality/execution_summary/service.py
run_orchestration/quality_execution_summary_stage.py
tests/quality/test_execution_summary.py
tests/quality/test_execution_summary_sources.py
tests/quality/test_execution_summary_renderer.py
```

### 9.2 修改

```text
quality/junit.py
```

- 暴露去重后的用例名称、状态和 skipped message。
- 优先使用 JUnit 中的 `quality_invocation_id` 建立身份。
- 跳过原因经过统一脱敏。

```text
run_orchestration/quality_pipeline.py
```

- 在现有质量阶段结束后增加 execution summary 阶段。
- 摘要失败不覆盖 pytest 原始退出码。

```text
run_orchestration/quality_run_record.py
```

- 初版不修改 `RunRecord` Schema。
- 测试目标和 worker 参数从当前编排上下文传给摘要 stage，仅用于展示。

```text
quality/identifiers.py
```

- 补充带语义前缀动态 ID 的归一化测试。
- 将 `not-exist-<动态hash>` 等路径稳定为同一路由模板，避免耗时 Top 每轮出现新接口身份。

```text
Jenkinsfile
```

- 归档规则无需修改，`reports/**` 已覆盖新文件。
- 邮件报告入口把 `quality-summary.md` 放在第一位。
- P0/P1 链接降级为“详细证据”。
- 摘要不存在时回退到 P0/P1 链接，不生成无效 URL。

```text
README.md
FRAMEWORK_TEST_SPEC.md
```

- 日常查看顺序改为先看 `quality-summary.md`。
- 说明四项指标口径和摘要结论规则。
- 保留 P0/P1 作为详细证据入口。

### 9.3 明确不修改

```text
common/runtime_hooks/
quality/semantic_models.py
quality/semantic_collector.py
quality/metrics/ 聚合算法
quality/flaky_store/
module/smoke/
run_master.py 稳定入口
```

## 十、异常与降级策略

### 10.1 核心 P0 来源不可用

生成：

```text
NO_DATA：本次执行数据不完整，无法判断执行效果
```

只展示缺失的核心文件，不展示零值统计。

### 10.2 Metrics 不可用

用例、请求基础数据仍可展示；重试和耗时章节显示：

```text
本轮指标产物不可用
```

### 10.3 Flaky 不可用

显示：

```text
本轮未启用或未生成 Flaky 评估
```

不得把“没有数据”写成“没有 Flaky”。

### 10.4 Summary 生成异常

- 记录明确阶段错误。
- 不覆盖 pytest/Jenkins 原始执行结果。
- Jenkins 邮件回退到 P0/P1 报告。
- 不写半成品文件，使用原子写入。

## 十一、安全与脱敏

- 用例名称沿用 nodeid，但参数 ID 仍执行既有脱敏。
- skipped message、failure message 和建议动作复用 `quality.redaction`。
- 不展示 API Key、完整 prompt、账号、余额、服务端 request ID 和 task ID。
- 接口标识只展示归一化 `interface_id`。
- 不读取或展示 Allure 请求/响应附件内容。

## 十二、测试方案

### 12.1 #64 等价离线场景

构造脱敏 fixture：

```text
41 total
34 passed
0 failed
0 error
7 skipped
194 requests
0 HTTP 5xx
0 timeout
无重试
无 Flaky 迁移
```

预期：

```text
WARN：执行成功，但覆盖不完整
请求成功率 100.00%
跳过用例及原因完整展示
重试章节显示“本轮未发生重试”
Flaky 章节显示“本轮无 Flaky 状态迁移”
```

### 12.2 失败场景

- 1 个 failed 时结论为 FAIL。
- 1 个 setup/teardown error 时结论为 FAIL。
- 失败用例名称和原因可定位。
- 报告不输出完整敏感响应。

### 12.3 请求场景

- HTTP 200、400、404 计入技术请求成功。
- 429、5xx、timeout、连接异常不计入成功。
- 轮询 pending 的 200 不降低成功率。
- 请求数为 0 时展示“本轮未产生请求”。

### 12.4 重试场景

- 无重试时不展示 0%。
- 首次失败、最终成功时计为挽救。
- 首次失败、最终仍失败时不计为挽救。
- 多次 attempt 只按请求组计一次。

### 12.5 耗时 Top 场景

- 只保留 workload。
- 排除 control 和 polling。
- 按平均耗时稳定排序。
- 相同平均值按最大值和接口名确定顺序。
- 最多展示 5 条。

### 12.6 Flaky 场景

- 无评估与无迁移使用不同文案。
- 状态迁移按方向聚合。
- 新增疑似、确认、隔离、恢复和超期最多展示 5 条。
- 批量稳定迁移不展开完整列表。

### 12.7 来源与边界场景

- P0 run_id 冲突时结论为 NO_DATA。
- Metrics run_id 冲突时只降级对应章节。
- Flaky run_id 冲突时不展示错误构建的迁移。
- 缺失可选来源不影响用例执行结论。
- `common` 仍可独立导入，不新增 `common -> quality` 依赖。
- collect-only 不创建执行摘要。

## 十三、验收命令

离线验收：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/quality -q
.\.venv\Scripts\python.exe -m pytest tests -q
.\.venv\Scripts\python.exe run_master.py module/smoke --collect-only -q
```

必须确认：

- 全量离线测试通过。
- Smoke collect-only 仍为 41 项。
- collect-only 不生成 Quality run_id 和 `quality-summary.md`。
- 未调用真实付费接口。
- 新增架构边界测试已进入 Git 跟踪范围。

真实验收使用下一次已授权 Smoke 或 Jenkins 定时 Smoke，重点核对：

1. 摘要与 JUnit 用例数一致。
2. 摘要与 P0 5xx/超时一致。
3. 重试数据与 run-metrics 一致。
4. 接口耗时 Top 过滤掉 control/polling。
5. Flaky 迁移与 flaky-evaluation 一致。
6. Jenkins 邮件首个报告入口为 `quality-summary.md`。

## 十四、实施顺序

### 步骤一：定义摘要契约和来源校验

- 新建 `quality/execution_summary/`。
- 实现同一 run_id 校验。
- 实现 JUnit 用例与 skip 原因加载。
- 先完成纯 builder 单测。

### 步骤二：实现四项保留指标

- 请求技术成功率。
- HTTP 重试挽救率。
- workload 非 polling 接口耗时 Top 5。
- Flaky 状态迁移摘要。

### 步骤三：渲染与运行编排

- 生成 `quality-summary.md`。
- 接入 quality pipeline 最后阶段。
- 验证异常不覆盖 pytest 退出码。

### 步骤四：Jenkins 与文档

- 邮件首链改为摘要。
- README 和用例规范同步。
- 输出独立 `code_history`。

### 步骤五：离线回归与真实产物核对

- 完成离线回归。
- 使用真实 Jenkins Smoke 产物核对统计一致性。
- 不在本迭代引入趋势、成本和强制门禁。

## 十五、完成标准

本迭代完成必须同时满足：

```text
[ ] 生成 quality-summary.md
[ ] 30 秒内可判断本次执行效果
[ ] 用例结果与 JUnit 完全一致
[ ] 请求成功率使用技术口径
[ ] 重试挽救率无重试时使用自然语言
[ ] 接口耗时只展示 workload 非 polling Top 5
[ ] Flaky 无数据与无迁移明确区分
[ ] 所有来源执行 run_id 一致性校验
[ ] 摘要失败不覆盖 pytest 退出码
[ ] Jenkins 邮件首链指向摘要
[ ] P0/P1 详细证据继续保留
[ ] 全量离线测试和 collect-only 通过
[ ] 独立代码变更记录写入 code_history
```

## 十六、后续迭代边界

只有初版稳定并获得真实使用反馈后，再评估：

- expected outcome 与 Usage applicability。
- Skip owner、到期时间和工单治理。
- 跨构建耗时趋势。
- Flaky 长期治理视图。
- 性能基线和强制门禁。

后续能力不得回填到初版摘要首页；首页继续保持一页、少指标和可直接判断。
