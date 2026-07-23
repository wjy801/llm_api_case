# Flaky 测试治理方案（初期四状态版）

## 1. 建设目标

在不引入复杂生命周期和大量治理字段的前提下，为当前 pytest、pytest-xdist、Allure 测试框架增加最小可落地的 Flaky 治理能力。

初期只输出以下四类最终状态：

```text
通过
重试通过
重试不通过
失败
```

治理目标：

1. 保留首次执行结果，不允许重试覆盖首次失败事实。
2. 区分首次通过和依赖重试后通过。
3. 区分执行过重试但仍失败，以及未执行重试的直接失败。
4. 形成结构化结果，为后续趋势分析、门禁和 Flaky 修复提供数据基础。
5. 保持 pytest 和 Allure 原有执行、报告能力不受影响。

## 2. 核心原则

1. **重试通过不等于普通通过**：首次失败、重试后通过必须标记为“重试通过”。
2. **保留每次执行证据**：最终只展示四类状态，但底层保留每次 Attempt 的结果、耗时和失败信息。
3. **默认不启用全局重试**：只有明确允许重试的用例才启用，禁止全局自动重跑。
4. **重试不能掩盖产品问题**：业务断言、契约、账单、安全、权限和非幂等付费请求默认禁止重试。
5. **初期最多重试一次**：降低结果解释和执行成本。

```text
第一次通过               → 通过
第一次失败，第二次通过    → 重试通过
第一次失败，第二次失败    → 重试不通过
第一次失败，未执行重试    → 失败
```

## 3. 四类状态定义

| 状态标识 | 中文展示 | 判定条件 | 默认 CI 行为 |
|---|---|---|---|
| `PASSED` | 通过 | 第一次执行通过 | 通过 |
| `RETRY_PASSED` | 重试通过 | 第一次失败，实际执行重试后最终通过 | 警告，不阻断 |
| `RETRY_FAILED` | 重试不通过 | 第一次失败，实际执行重试后最终仍失败 | 阻断 |
| `FAILED` | 失败 | 第一次失败，并且实际没有执行重试 | 阻断 |

注意事项：

- 状态根据实际 Attempt 数量判断，不能仅根据是否配置重试判断。
- pytest 原生的 `skipped`、`xfailed`、`xpassed` 暂不纳入四类统计。
- `RETRY_PASSED` 默认允许流水线通过，但必须显示警告。
- 发布前严格模式可以配置 `RETRY_PASSED` 阻断流水线。

## 4. 状态判定规则

### 4.1 Attempt 序列映射

```text
[passed]                    → PASSED
[failed]                    → FAILED
[failed, passed]            → RETRY_PASSED
[failed, failed]            → RETRY_FAILED
[failed, failed, passed]    → RETRY_PASSED
[failed, failed, failed]    → RETRY_FAILED
```

分类器应兼容多次 Attempt，但初期策略限制为最多重试一次。

### 4.2 分类器规则

分类器输入为每次 Attempt 的结果列表，输出四类最终状态。空列表必须抛出配置错误；单次通过为 `PASSED`；单次失败为 `FAILED`；多次执行最终通过为 `RETRY_PASSED`；多次执行最终失败为 `RETRY_FAILED`。

### 4.3 Attempt 成功条件

一次执行包含 `setup`、`call` 和 `teardown`。只有三个阶段均成功，本次 Attempt 才算通过。fixture 初始化、断言、请求、teardown、Session 关闭或资源清理任一失败，都视为本次 Attempt 失败。

## 5. 最小数据模型

每条 Attempt 至少保存：`index`、`outcome`、`duration`、`failure_type` 和脱敏后的 `failure_message`。

每条用例最终结果至少保存：`nodeid`、`status`、`attempt_count`、`attempts` 和 `total_duration`。

建议同时记录但暂不参与分类：`request_id`、`worker_id`、运行环境、Git Commit 和运行 ID。

### 5.1 结果示例

```json
{
  "nodeid": "module/smoke/test_chat.py::TestChat::test_create",
  "status": "retry_passed",
  "attempt_count": 2,
  "total_duration": 15.73
}
```

完整结果中的 `attempts` 数组按执行顺序保存每次结果、耗时、异常类型和失败摘要。首次失败信息不能被最终通过覆盖。

## 6. 重试策略

初期统一策略：默认不重试；允许重试的用例最多重试一次；重试间隔 2 秒；禁止在 `pytest.ini` 配置全局 `--reruns`。

建议引入 `pytest-rerunfailures`，并封装团队统一的 `retry_once` marker，禁止成员自行设置不同重试次数。

### 6.1 建议允许重试

- `ConnectTimeout`、临时性 `ConnectionError`、DNS 或 TLS 临时失败。
- `ReadTimeout`，且请求具备幂等性或确认未进入服务端处理。
- 幂等 GET 请求返回 `502/503/504`。
- 明确允许重试的限流响应。
- 最终一致性查询在合理窗口内暂未获得结果。

### 6.2 默认禁止重试

- 普通断言、Schema 或 OpenAPI 契约不匹配。
- 账单、用量、余额、安全、权限和数据隔离测试失败。
- `400/401/403/404`。
- 非幂等 POST 或未提供幂等键的付费请求。
- 数据删除、破坏性测试或异步任务最终失败。

无法确认是否安全重试时，应默认不重试。

## 7. pytest 插件设计

治理逻辑应放在独立插件中，不继续堆积到 `module/conftest.py`。

```text
governance/
├─ __init__.py
├─ flaky_models.py
├─ flaky_classifier.py
├─ flaky_plugin.py
└─ flaky_reporter.py
```

| 文件 | 职责 |
|---|---|
| `flaky_models.py` | 四类状态、Attempt 和用例结果模型 |
| `flaky_classifier.py` | 根据 Attempt 序列判定最终状态 |
| `flaky_plugin.py` | 通过 pytest hooks 采集 setup/call/teardown |
| `flaky_reporter.py` | 输出 JSON、终端汇总和门禁结果 |

实现要求：按 `nodeid` 聚合；识别每次 Attempt；保留 pytest 原始报告；在 Session 结束时统一分类；兼容 xdist，由主进程生成最终文件。

## 8. 输出产物

```text
reports/<run_id>/
├─ flaky-results.json
├─ flaky-summary.json
├─ flaky-summary.txt
├─ retry-nodeids.csv
└─ stale-retry-nodeids.csv

reports/flaky/
└─ latest-retry-nodeids.csv
```

`flaky-results.json` 保存每条用例的 Attempt 明细；`flaky-summary.json` 供 CI 和质量平台读取；文本文件用于终端和人工查看。

每次运行目录是不可变历史快照；`reports/flaky/latest-retry-nodeids.csv` 是下一次统一复测的入口。CSV 同时保存 nodeid 和必要治理字段。

### 8.1 Flaky 复测队列

每次完整执行结束后，将 `RETRY_FAILED` 和 `RETRY_PASSED` 的 nodeid 保存到复测队列。默认优先级为重试不通过在前、重试通过在后。

`retry-nodeids.csv` 使用固定表头保存复测目标。每行代表一个 nodeid，至少包含版本、来源运行 ID、来源 Git Commit、生成时间、nodeid、状态、Attempt 次数和首次失败类型。

推荐表头：

```csv
schema_version,source_run_id,source_git_commit,generated_at,priority,nodeid,status,attempt_count,first_failure_type
```

示例：

```csv
schema_version,source_run_id,source_git_commit,generated_at,priority,nodeid,status,attempt_count,first_failure_type
1,20260723_143015,a1b2c3d,2026-07-23T14:40:00+08:00,0,module/video_model/test_wan2_7_t2v.py::TestWanT2V::test_generate,retry_failed,2,TimeoutError
1,20260723_143015,a1b2c3d,2026-07-23T14:40:00+08:00,1,module/smoke/test_key_api.py::TestKeyApi::test_chat_completion,retry_passed,2,ReadTimeout
```

生成时必须按 `nodeid` 去重，并按 `priority`、`nodeid` 稳定排序。若本次没有目标，应生成只有表头的空 CSV 并覆盖 `latest`，避免继续执行上一次遗留队列。

CSV 读写必须使用标准 `csv` 模块或等价结构化 CSV API，不允许按逗号手工 `split`。参数化 nodeid 可能包含逗号、空格、方括号或非 ASCII 字符，写入时必须由 CSV writer 自动加引号和转义。

历史快照只写一次；更新 `latest` 时使用临时文件加原子替换。xdist Worker 不直接写队列，只由主进程统一生成。

### 8.2 汇总示例

```json
{
  "total": 100,
  "passed": 91,
  "retry_passed": 5,
  "retry_failed": 2,
  "failed": 2,
  "first_pass_rate": 0.91,
  "final_success_rate": 0.96,
  "retry_recovery_rate": 0.7143
}
```

## 9. 统计指标

- 首次通过率 = 通过数 / 实际执行用例数。
- 最终成功率 =（通过数 + 重试通过数）/ 实际执行用例数。
- 重试恢复率 = 重试通过数 /（重试通过数 + 重试不通过数）。

首次通过率是测试稳定性的首要指标。报告必须同时展示首次通过率和最终成功率，禁止仅展示最终成功率。

## 9.1 NodeID 执行前校验

保存的 nodeid 可能因文件、类、方法或参数化 ID 变化而失效。统一复测前必须先执行当前代码的 `--collect-only`，再与保存清单取交集。

```text
valid_nodeids = saved_nodeids ∩ collected_nodeids
stale_nodeids = saved_nodeids - collected_nodeids
```

有效 nodeid 才能进入 pytest 参数列表；失效 nodeid 写入 `stale-retry-nodeids.csv`。普通模式警告，严格模式可阻断；全部失效时必须返回非零退出码，不能显示执行成功。

不得把 nodeid 拼接成 Shell 命令。应使用列表参数调用 `pytest.main()`，并拒绝清单中不属于当前收集结果的内容。

参数化用例必须使用稳定、显式的 `id`，禁止时间戳、随机值、对象地址等动态参数 ID，否则下次无法准确复测。

## 9.2 复测后的队列更新

| 本次复测结果 | 下一次队列处理 |
|---|---|
| `PASSED` | 从 Flaky 复测队列移除 |
| `RETRY_PASSED` | 继续保留 |
| `RETRY_FAILED` | 继续保留 |
| `FAILED` | 从 Flaky 队列移出，但保留在本次失败结果中 |
| `SKIPPED` | 暂不移除，继续保留 |
| 未收集到 | 进入失效 nodeid 清单 |

默认采用“本次复测结果生成下一版队列”，而不是无限累加历史 nodeid。这样已恢复稳定的用例可以自动退出，持续不稳定用例继续跟踪。

如果执行的是完整测试集，则以完整测试结果覆盖 `latest` 队列；如果执行的是 Flaky 复测队列，则只更新本次复测目标，不能误删本次未执行的其他队列项。

## 10. CI 门禁策略

| 状态 | 初期宽松模式 | 严格模式 |
|---|---|---|
| 通过 | 通过 | 通过 |
| 重试通过 | 黄色警告，不阻断 | 阻断 |
| 重试不通过 | 阻断 | 阻断 |
| 失败 | 阻断 | 阻断 |

建议在 `run_master.py` 增加 `--fail-on-retry-passed`。发布前回归、P0 核心链路、鉴权、安全、账单和框架单测使用严格模式。

初期建议阈值：首次通过率不低于 95%，重试不通过数为 0，失败数为 0。运行两到四周后再调整。

## 11. Allure 展示方案

Allure 继续承担明细和证据展示，但不作为四类状态的唯一统计数据源。

每条用例建议展示：治理状态、执行次数、首次结果、最终结果和总耗时。重试通过必须保留第一次和第二次的请求、响应、异常与耗时。

Allure 首页或环境信息中展示四类数量、首次通过率、最终成功率和重试恢复率。结构化 `flaky-summary.json` 作为 CI 的正式数据源。

## 12. xdist 并发要求

1. 每个 Worker 只采集自己执行的 Attempt。
2. Worker 不直接写共享汇总文件。
3. 所有 Worker 结果由主进程统一聚合。
4. 同一 `nodeid` 的 Attempt 按实际顺序排列。
5. 主进程结束后只生成一份结果、汇总和 CSV 复测队列。
6. CSV 队列只由主进程写入，Worker 不写 `retry-nodeids.csv` 或 `latest-retry-nodeids.csv`。

必须验证 `-n 0`、`-n 2`、`-n auto`，以及 setup、call、teardown 失败和多个用例同时重试。

## 13. 当前框架修改点

- `requirements.txt`：增加并锁定兼容版本的 `pytest-rerunfailures`。
- `pytest.ini`：注册 `retry_once` marker，启用 `--strict-markers`，不配置全局重试。
- `module/conftest.py`：只负责注册独立 Flaky 插件，避免继续堆积完整治理实现。
- `run_master.py`：增加严格门禁、复测队列读取、nodeid 校验和清单查看能力。
- `tests/`：新增分类器、报告器和插件单元测试。

建议提供以下参数：

```text
--fail-on-retry-passed
--flaky-report-dir <path>
--rerun-latest-flaky
--rerun-from <retry-nodeids.csv>
--rerun-status retry_failed|retry_passed
--list-rerun-targets
--strict-nodeids
```

`--rerun-latest-flaky` 默认读取 `reports/flaky/latest-retry-nodeids.csv` 并执行两类目标；`--rerun-status` 可只执行指定状态；`--list-rerun-targets` 只展示校验后的有效与失效数量，不发起请求。

## 14. 必须覆盖的单元测试

分类器至少覆盖：`[passed]`、`[failed]`、`[failed, passed]`、`[failed, failed]`、多次重试最终通过和空列表。

插件至少覆盖：setup/call/teardown 失败；首次失败证据保留；skipped 和 xfailed 不进入四类统计；xdist 聚合；失败摘要脱敏。

报告器至少覆盖：数量和比率计算；除数为零；JSON 输出；默认和严格模式退出码。

复测队列测试至少覆盖：只保存 `RETRY_PASSED/RETRY_FAILED`；固定 CSV 表头；去重和稳定排序；空结果覆盖旧 `latest`；重试不通过优先；原子更新；包含逗号、方括号或非 ASCII 的 nodeid 能被正确读写。

NodeID 校验测试至少覆盖：有效与失效清单拆分；全部失效退出码；参数化 nodeid；清单中 pytest 参数注入被拒绝；串行和 xdist 主进程聚合。

队列更新测试至少覆盖：通过移除、两类重试状态保留、失败移出 Flaky 队列但保留结果、跳过继续保留，以及局部复测不误删未执行目标。

## 15. 分阶段实施

### 第一阶段：状态采集

新增模型和分类器，先处理单次通过与失败，不启用自动重试。

### 第二阶段：受控重试

引入 `pytest-rerunfailures` 和统一 marker，采集每次 Attempt，区分重试通过与重试不通过。

### 第三阶段：报告与门禁

输出 JSON 和终端汇总，阻断失败与重试不通过，增加严格模式；同时生成 `retry-nodeids.csv` 和 `stale-retry-nodeids.csv`。

### 第四阶段：统一复测

增加 `--rerun-latest-flaky`、指定清单复测、状态筛选、nodeid 收集校验、失效清单和复测后的队列更新。

### 第五阶段：观察

持续收集两到四周，统计各模块首次通过率和高频重试通过用例，再决定是否引入 Owner、隔离和趋势治理。

## 16. 初期暂不引入

- Flaky 生命周期状态、自动隔离和自动创建缺陷。
- Owner、修复期限和长期趋势数据库。
- 失败自动分类状态机。
- 根据历史结果动态调整重试次数。

## 17. 最终验收标准

1. 除 pytest 原生跳过类结果外，执行用例均进入四类状态。
2. 重试通过不计入普通通过。
3. 重试不通过和失败正确阻断 CI。
4. 首次失败请求、响应和异常证据不丢失。
5. 默认无全局重试，非幂等付费接口禁止自动重试。
6. 串行和 xdist 并发汇总一致。
7. Allure、终端和 JSON 数量一致。
8. 报告同时展示首次通过率和最终成功率。
9. 严格模式能够阻断重试通过。
10. 每次完整执行都能生成两类重试状态的 nodeid 清单。
11. 清单去重、稳定排序，且空清单能覆盖旧 `latest`。
12. 统一复测前能够识别有效和失效 nodeid。
13. nodeid 只能以参数列表传入 pytest，不能拼接 Shell 命令。
14. 复测通过后自动退出下一版队列，两类重试状态继续保留。
15. 局部复测不会误删本次未执行的其他队列项。

## 18. 推荐初始配置

```text
允许重试次数：1
重试间隔：2 秒
全局重试：关闭
重试通过：警告
重试不通过：阻断
失败：阻断
首次通过率目标：不低于 95%
复测队列状态：RETRY_FAILED、RETRY_PASSED
复测执行优先级：RETRY_FAILED 优先
失效 nodeid：普通模式警告、严格模式阻断
```

该方案优先解决“报告看起来全绿，但实际依赖重试”的信任问题，并为后续治理能力保留扩展空间。
