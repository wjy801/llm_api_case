# Flaky 治理使用文档

本文档说明当前测试框架中的 Flaky 治理能力、使用方式、输出产物和 CI 门禁规则。

## 1. 核心状态

框架将实际执行过的用例归为四类：

| 状态 | 含义 | 默认门禁 |
|---|---|---|
| `passed` | 首次执行通过 | 通过 |
| `retry_passed` | 首次失败，重试后通过 | 警告，不阻断 |
| `retry_failed` | 首次失败，重试后仍失败 | 阻断 |
| `failed` | 首次失败，未执行重试 | 阻断 |

`retry_passed` 不会计入普通通过。报告中会同时输出首次通过率、最终成功率和重试恢复率。

## 2. 如何启用一次受控重试

默认不启用全局重试。只有显式使用统一 marker 的用例才允许重试一次。

```python
from common.markers import retry_once


@retry_once
def test_transient_network_case():
    ...
```

`retry_once` 当前封装为：

```python
pytest.mark.flaky(reruns=1, reruns_delay=2)
```

仅建议用于低风险、可安全重试的临时网络类问题，例如连接超时、幂等 GET 的临时 502/503/504。业务断言、账单、权限、安全、数据隔离、非幂等付费 POST 等场景不要使用重试。

## 3. 常用执行命令

普通执行：

```powershell
.\.venv\Scripts\python.exe run_master.py module/smoke
```

只收集用例，不执行接口请求：

```powershell
.\.venv\Scripts\python.exe run_master.py module/smoke --collect-only -q
```

执行 Flaky latest 队列：

```powershell
.\.venv\Scripts\python.exe run_master.py --rerun-latest-flaky
```

从指定 CSV 队列复测：

```powershell
.\.venv\Scripts\python.exe run_master.py --rerun-from reports/flaky/latest-retry-nodeids.csv
```

只复测重试不通过用例：

```powershell
.\.venv\Scripts\python.exe run_master.py --rerun-latest-flaky --rerun-status retry_failed
```

只展示复测目标，不执行：

```powershell
.\.venv\Scripts\python.exe run_master.py --rerun-latest-flaky --list-rerun-targets
```

严格 nodeid 校验，存在失效 nodeid 即阻断：

```powershell
.\.venv\Scripts\python.exe run_master.py --rerun-latest-flaky --strict-nodeids
```

严格门禁，`retry_passed` 也阻断：

```powershell
.\.venv\Scripts\python.exe run_master.py module/smoke --fail-on-retry-passed
```

自定义 Flaky 报告目录：

```powershell
.\.venv\Scripts\python.exe run_master.py module/smoke --flaky-report-dir reports/flaky/current
```

## 4. 输出产物

默认输出目录：

```text
reports/flaky/current/
├─ flaky-results.json
├─ flaky-summary.json
├─ flaky-summary.txt
├─ retry-nodeids.csv
└─ stale-retry-nodeids.csv

reports/flaky/
└─ latest-retry-nodeids.csv
```

根目录还会按日期归档 CSV：

```text
flaky_retry_queues/<YYYY-MM-DD>/<run_id>/
├─ retry-nodeids.csv
└─ latest-retry-nodeids.csv
```

`flaky-results.json` 保存每条用例的 attempt 明细，包括 outcome、耗时、失败类型和脱敏后的失败摘要。

`flaky-summary.json` 保存 CI 和质量平台可读取的统计数据：

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

## 5. 复测队列规则

完整测试执行后，框架会把 `retry_failed` 和 `retry_passed` 写入下一次复测队列：

```csv
schema_version,source_run_id,source_git_commit,generated_at,priority,nodeid,status,attempt_count,first_failure_type
```

优先级：

| status | priority |
|---|---|
| `retry_failed` | `0` |
| `retry_passed` | `1` |

生成规则：

- 只保留 `retry_failed` 和 `retry_passed`。
- 按 `nodeid` 去重。
- 按 `priority,nodeid` 稳定排序。
- 空队列也会生成只有表头的 CSV，并覆盖旧 latest。
- CSV 读写使用标准 CSV API，支持包含逗号、方括号、空格和中文的 nodeid。

复测后 latest 队列自动收敛：

| 本次复测结果 | 下一次队列处理 |
|---|---|
| `passed` | 移出队列 |
| `retry_passed` | 保留 |
| `retry_failed` | 保留 |
| `failed` | 移出 Flaky 队列，但保留在本次失败结果中 |
| 未执行 | 局部复测时继续保留 |

## 6. NodeID 校验

复测前框架会先收集当前代码中的 nodeid，再与 CSV 队列求交集：

```text
valid_nodeids = saved_nodeids ∩ collected_nodeids
stale_nodeids = saved_nodeids - collected_nodeids
```

只有有效 nodeid 会传给 pytest 执行。失效 nodeid 会写入 `stale-retry-nodeids.csv`。

注意：

- 全部 nodeid 失效时返回非零退出码。
- `--strict-nodeids` 下存在任何失效 nodeid 都会阻断。
- nodeid 通过参数列表传给 `pytest.main()`，不会拼接 shell 命令。

## 7. 门禁规则

默认门禁：

| 状态 | 行为 |
|---|---|
| `passed` | 通过 |
| `retry_passed` | 打印警告，不阻断 |
| `retry_failed` | 阻断 |
| `failed` | 阻断 |

严格模式：

```powershell
.\.venv\Scripts\python.exe run_master.py module/smoke --fail-on-retry-passed
```

严格模式下，`retry_passed` 也会返回失败退出码。

`--collect-only` 不执行门禁，避免历史 summary 影响收集命令。

## 8. Allure 展示

框架会向 `allure-results/environment.properties` 写入 Flaky 汇总字段：

```text
flaky.total
flaky.passed
flaky.retry_passed
flaky.retry_failed
flaky.failed
flaky.first_pass_rate
flaky.final_success_rate
flaky.retry_recovery_rate
```

同时会在 Allure 单用例结果中追加参数：

```text
flaky_status
flaky_attempt_count
flaky_first_outcome
flaky_final_outcome
flaky_total_duration
flaky_first_failure_type
```

这些字段用于在 Allure 中查看治理状态和每条用例的重试证据。

## 9. 最小验证命令

修改 Flaky 治理相关代码后，先执行：

```powershell
.\.venv\Scripts\python.exe -m pytest tests -m flaky_governance -q
```

再执行全量框架单测：

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
```

最后确认收集不触发真实接口：

```powershell
.\.venv\Scripts\python.exe run_master.py module/smoke --collect-only -q
```
