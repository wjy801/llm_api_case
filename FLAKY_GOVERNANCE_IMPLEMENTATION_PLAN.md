# Flaky 治理分阶段实现方案

本文档基于 `FLAKY_GOVERNANCE_PLAN.md`，将四状态 Flaky 治理、CSV 复测队列和统一复测入口拆成可逐步交付的实现阶段。

## 阶段 0：准备与约束

目标：接入基础依赖和目录结构，但不改变现有测试执行行为。

实现内容：

- 在 `requirements.txt` 中增加并锁定兼容版本的 `pytest-rerunfailures`。
- 在 `pytest.ini` 注册 `retry_once` marker。
- 在 `pytest.ini` 启用 `--strict-markers`。
- 不配置全局 `--reruns`。
- 新增 `governance/` 目录。

建议目录：

```text
governance/
├─ __init__.py
├─ flaky_models.py
├─ flaky_classifier.py
├─ flaky_plugin.py
├─ flaky_reporter.py
├─ retry_queue.py
└─ nodeid_validator.py
```

验收标准：

- 默认执行行为与现有框架一致。
- 不带 marker 的用例不会自动重试。
- `pytest --collect-only` 正常执行。

## 阶段 1：四状态模型

目标：先完成纯逻辑分类，不接入 pytest 执行链路。

实现内容：

- 在 `governance/flaky_models.py` 定义四类状态：
  - `PASSED`
  - `RETRY_PASSED`
  - `RETRY_FAILED`
  - `FAILED`
- 定义 Attempt 结果模型。
- 定义用例最终结果模型。
- 在 `governance/flaky_classifier.py` 实现分类逻辑。

分类规则：

```text
[passed]                 -> PASSED
[failed]                 -> FAILED
[failed, passed]         -> RETRY_PASSED
[failed, failed]         -> RETRY_FAILED
[failed, failed, passed] -> RETRY_PASSED
```

新增测试：

```text
tests/test_flaky_classifier.py
```

验收标准：

- 空 Attempt 列表抛出明确异常。
- 单次通过、单次失败、重试通过、重试不通过均分类正确。
- 分类器不依赖 pytest、Allure 或网络环境。

## 阶段 2：Attempt 采集

目标：接入 pytest hook，采集 `setup/call/teardown`，但暂不启用自动重试。

实现内容：

- 在 `governance/flaky_plugin.py` 中使用 pytest hook 采集报告。
- 按 `nodeid` 聚合每次 Attempt。
- 一次 Attempt 只有 `setup`、`call`、`teardown` 全部通过才算通过。
- `skipped`、`xfailed`、`xpassed` 暂不进入四状态统计。
- 在 session 结束时生成：
  - `reports/<run_id>/flaky-results.json`
  - `reports/<run_id>/flaky-summary.json`
  - `reports/<run_id>/flaky-summary.txt`

新增测试：

```text
tests/test_flaky_plugin.py
tests/test_flaky_reporter.py
```

验收标准：

- setup 失败会进入 `FAILED`。
- call 失败会进入 `FAILED`。
- teardown 失败会进入 `FAILED`。
- 首次失败异常摘要被保存。
- 敏感信息在失败摘要中被脱敏。

## 阶段 3：受控重试

目标：引入一次重试能力，但只对显式标记的用例生效。

实现内容：

- 封装统一 marker：

```python
import pytest

retry_once = pytest.mark.flaky(
    reruns=1,
    reruns_delay=2,
)
```

- 只允许使用统一 marker 启用重试。
- 第一次失败、第二次通过，状态为 `RETRY_PASSED`。
- 第一次失败、第二次仍失败，状态为 `RETRY_FAILED`。
- `RETRY_PASSED` 不能计入普通通过。

禁止重试场景：

- 普通断言失败。
- Schema 或 OpenAPI 契约失败。
- 账单、余额、用量一致性失败。
- 权限、安全和数据隔离测试。
- 非幂等 POST。
- 未提供幂等键的付费生成请求。

验收标准：

- 默认没有全局重试。
- 未标记用例失败后不会重试。
- 标记用例最多重试一次。
- 首次失败证据不会被最终通过覆盖。

## 阶段 4：CSV 复测队列

目标：将 `RETRY_PASSED` 和 `RETRY_FAILED` 的 nodeid 保存为 CSV，供下次统一复测。

实现内容：

- 新增 `governance/retry_queue.py`。
- 生成当前运行快照：

```text
reports/<run_id>/retry-nodeids.csv
reports/<run_id>/stale-retry-nodeids.csv
```

- 更新 latest 队列：

```text
reports/flaky/latest-retry-nodeids.csv
```

CSV 固定表头：

```csv
schema_version,source_run_id,source_git_commit,generated_at,priority,nodeid,status,attempt_count,first_failure_type
```

生成规则：

- 只保存 `RETRY_FAILED` 和 `RETRY_PASSED`。
- `RETRY_FAILED` 优先级为 `0`。
- `RETRY_PASSED` 优先级为 `1`。
- 按 `priority`、`nodeid` 稳定排序。
- 按 `nodeid` 去重。
- 空队列也要生成只有表头的 CSV，并覆盖旧 latest。
- 使用标准 `csv` API 读写，禁止手工按逗号切分。

新增测试：

```text
tests/test_retry_queue.py
```

验收标准：

- CSV 表头固定。
- nodeid 包含逗号、方括号、空格或非 ASCII 字符时读写正确。
- 空结果能覆盖旧 `latest-retry-nodeids.csv`。
- xdist 模式下只有主进程写 CSV。

## 阶段 5：NodeID 校验与统一复测

目标：支持读取 CSV 队列，并在执行前校验 nodeid 是否仍存在。

实现内容：

- 新增 `governance/nodeid_validator.py`。
- 复测前先执行当前代码的 `--collect-only`。
- 保存 nodeid 与当前收集结果取交集：

```text
valid_nodeids = saved_nodeids ∩ collected_nodeids
stale_nodeids = saved_nodeids - collected_nodeids
```

- 只执行有效 nodeid。
- 失效 nodeid 写入 `stale-retry-nodeids.csv`。
- 禁止将 nodeid 拼接成 shell 命令，必须通过参数列表传给 `pytest.main()`。

修改 `run_master.py`，增加参数：

```text
--rerun-latest-flaky
--rerun-from <retry-nodeids.csv>
--rerun-status retry_failed|retry_passed
--list-rerun-targets
--strict-nodeids
```

验收标准：

- 能执行 `reports/flaky/latest-retry-nodeids.csv` 中的有效 nodeid。
- `--rerun-status retry_failed` 只执行重试不通过用例。
- `--list-rerun-targets` 只展示有效和失效数量，不发起请求。
- 全部 nodeid 失效时返回非零退出码。
- `--strict-nodeids` 下存在失效 nodeid 即阻断。

## 阶段 6：复测后队列更新

目标：让复测队列自动收敛，避免已经稳定的用例长期残留。

更新规则：

| 本次复测结果 | 下一次队列处理 |
|---|---|
| `PASSED` | 从 Flaky 队列移除 |
| `RETRY_PASSED` | 继续保留 |
| `RETRY_FAILED` | 继续保留 |
| `FAILED` | 从 Flaky 队列移出，但保留在本次失败结果中 |
| `SKIPPED` | 暂不移除，继续保留 |
| 未收集到 | 写入失效 nodeid 清单 |

实现要求：

- 完整测试集执行后，用完整结果覆盖 latest 队列。
- Flaky 局部复测后，只更新本次复测目标。
- 局部复测不能误删本次未执行的其他队列项。

验收标准：

- 复测通过的 nodeid 会从下一版队列移除。
- 两类重试状态会继续保留。
- 局部复测不会误删未执行目标。
- 失败用例不会静默消失。

## 阶段 7：门禁与 Allure 展示

目标：将四状态结果接入 CI 决策和 Allure 报告。

实现内容：

- 修改 `run_master.py`，增加：

```text
--fail-on-retry-passed
--flaky-report-dir <path>
```

- 默认门禁：
  - `PASSED`：通过。
  - `RETRY_PASSED`：黄色警告，不阻断。
  - `RETRY_FAILED`：阻断。
  - `FAILED`：阻断。
- 严格模式：
  - `RETRY_PASSED` 也阻断。

Allure 展示：

- 治理状态。
- Attempt 次数。
- 首次结果。
- 最终结果。
- 总耗时。
- 首次失败摘要。

验收标准：

- 终端、JSON、Allure 三处数量一致。
- 首次通过率和最终成功率同时展示。
- 严格模式能阻断重试通过。
- `flaky-summary.json` 可被 CI 直接读取。

## 推荐落地顺序

1. 先完成阶段 0 到阶段 2，只观测、不重试。
2. 再完成阶段 3，对少量低风险用例试点 `retry_once`。
3. 阶段 4 和阶段 5 单独交付，重点验证 CSV 和 nodeid 校验。
4. 阶段 6 完成后再接入持续复测。
5. 最后启用阶段 7 的 CI 门禁和 Allure 展示。

## 初期验收口径

第一版上线建议只要求：

- 默认无全局重试。
- 四状态分类准确。
- `RETRY_PASSED` 不计入普通通过。
- `RETRY_FAILED` 和 `FAILED` 阻断。
- CSV 复测队列可生成、可读取、可校验。
- nodeid 只通过参数列表传给 pytest。
- xdist 下只有主进程写汇总和 CSV 队列。

## 每阶段最小验证集

每次 Flaky 治理开发完成后，先执行最小验证集：

```powershell
.\.venv\Scripts\python.exe -m pytest tests -m flaky_governance -q
```

纳入规则：

- 所有 `governance/` 相关单元测试必须标记 `flaky_governance`。
- 新增阶段测试优先加入该 marker。
- 最小验证集通过后，再按阶段风险运行更大范围测试。

当前推荐验证顺序：

```powershell
.\.venv\Scripts\python.exe -m pytest tests -m flaky_governance -q
.\.venv\Scripts\python.exe -m pytest tests -q
.\.venv\Scripts\python.exe run_master.py module/smoke --collect-only -q
```
