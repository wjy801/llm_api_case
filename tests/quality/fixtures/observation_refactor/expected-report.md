# P1 单次观察与 Flaky 报告

## 报告状态与 P0 影子门禁

- 报告状态：完整（`complete`）
- 运行 ID：`run-semantic`
- P0 门禁：警告（`WARN`）（影子观察（`shadow`））
- P0 数据完整性：完整（`complete`）
- P1 报告状态只表示观察数据完整性，不是门禁结论，也不会修改 Jenkins 结果。

## 数据源健康度

| 数据源 | 要求 | 状态 | 版本 | 问题 | 产物文件 |
| --- | --- | --- | --- | --- | --- |
| P0 质量报告（`p0_report`） | 必需（`required`） | 可用（`available`） | p0-report.v1 | - | summary.json |
| 单次运行指标（`run_metrics`） | 必需（`required`） | 可用（`available`） | p1-run-metrics.v1 | - | metrics/run-metrics.json |
| Flaky 历史导入（`flaky_import`） | 必需（`required`） | 可用（`available`） | p1-flaky-import.v1 | - | flaky-import.json |
| Flaky 状态评估（`flaky_evaluation`） | 必需（`required`） | 可用（`available`） | flaky-state.v1 | - | flaky-evaluation.json |

## 本次逻辑调用稳定性

- 业务逻辑调用：共 1 次；成功=1，失败=0，超时=0。

| 指标 | 值 | 分子 | 样本量 | 未知/缺失 | 完整性 |
| --- | --- | --- | --- | --- | --- |
| 逻辑调用成功率（`operation.success_rate`） | 1.0 | 1 | 1 | 0 | 完整（`complete`） |
| 逻辑调用超时率（`operation.timeout_rate`） | 0.0 | 0 | 1 | 0 | 完整（`complete`） |
| 请求事件业务成功率（`request_event.business_success_rate`） | 1.0 | 1 | 1 | 0 | 完整（`complete`） |
| 请求事件 HTTP 429 比例（`request_event.http_429_rate`） | 0.0 | 0 | 1 | 0 | 完整（`complete`） |
| 请求事件 HTTP 5xx 比例（`request_event.http_5xx_rate`） | 0.0 | 0 | 1 | 0 | 完整（`complete`） |
| 请求事件超时率（`request_event.timeout_rate`） | 0.0 | 0 | 1 | 0 | 完整（`complete`） |
| 业务重试挽救率（`request_group.business_retry_rescue_rate`） | 无数据（NO_DATA） | 0 | 0 | 0 | 无数据（`no_data`） |
| 请求组最终业务成功率（`request_group.final_business_success_rate`） | 1.0 | 1 | 1 | 0 | 完整（`complete`） |
| 请求组最终 HTTP 成功率（`request_group.final_http_success_rate`） | 1.0 | 1 | 1 | 0 | 完整（`complete`） |
| 请求组最终传输响应率（`request_group.final_transport_response_rate`） | 1.0 | 1 | 1 | 0 | 完整（`complete`） |

## 资源用量与覆盖率

- 完整=1，部分完整=0，缺失=0，不适用=0
| 资源 | 已知总量 | 样本量 | 缺失 | 完整性 |
| --- | --- | --- | --- | --- |
| 输入 Token（`input tokens`） | 2 | 1 | 0 | 完整（`complete`） |
| 输出 Token（`output tokens`） | 3 | 1 | 0 | 完整（`complete`） |
| 媒体数量（`media count`） | 无数据（NO_DATA） | 0 | 0 | 无数据（`no_data`） |
| 媒体时长（毫秒）（`media duration ms`） | 无数据（NO_DATA） | 0 | 0 | 无数据（`no_data`） |
| 重试输入 Token（`retry input tokens`） | 无数据（NO_DATA） | 0 | 0 | 不适用（`not_applicable`） |
| 重试输出 Token（`retry output tokens`） | 无数据（NO_DATA） | 0 | 0 | 不适用（`not_applicable`） |
| 重试媒体数量（`retry media count`） | 无数据（NO_DATA） | 0 | 0 | 不适用（`not_applicable`） |

## HTTP/SSE/异步耗时

| 粒度 | 维度 | 指标 | 均值 | 最小 | 最大 | 样本量 | 缺失 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 逻辑调用分组（`operation_bucket`） | 模型 ID（model_id）=-，调用类型（operation_kind）=HTTP（http），调用名称（operation_name）=http_request，流量角色（traffic_role）=业务流量（workload） | 逻辑调用总耗时（毫秒）（`operation.total_duration_ms`） | 5.89 | 5.89 | 5.89 | 1 | 0 |
| 单次运行（`run`） | 单次运行（run） | 逻辑调用总耗时（毫秒）（`operation.total_duration_ms`） | 5.89 | 5.89 | 5.89 | 1 | 0 |
| 逻辑调用分组（`operation_bucket`） | 模型 ID（model_id）=-，调用类型（operation_kind）=HTTP（http），调用名称（operation_name）=http_request，流量角色（traffic_role）=业务流量（workload） | 响应头等待耗时（毫秒）（`operation.response_headers_ms`） | 5.808 | 5.808 | 5.808 | 1 | 0 |
| 单次运行（`run`） | 单次运行（run） | 响应头等待耗时（毫秒）（`operation.response_headers_ms`） | 5.808 | 5.808 | 5.808 | 1 | 0 |
| 请求组分组（`request_group_bucket`） | 接口标识（interface_id）=GET /v1/items http，协议（protocol）=HTTP（http），流量角色（traffic_role）=业务流量（workload） | 请求组总耗时（毫秒）（`request_group.total_duration_ms`） | 4.742 | 4.742 | 4.742 | 1 | 0 |
| 单次运行（`run`） | 单次运行（run） | 请求组总耗时（毫秒）（`request_group.total_duration_ms`） | 4.742 | 4.742 | 4.742 | 1 | 0 |
| 请求事件分组（`request_event_bucket`） | 接口标识（interface_id）=GET /v1/items http，协议（protocol）=HTTP（http），流量角色（traffic_role）=业务流量（workload） | 请求事件总耗时（毫秒）（`request_event.all_duration_ms`） | 0.146 | 0.146 | 0.146 | 1 | 0 |
| 单次运行（`run`） | 单次运行（run） | 请求事件总耗时（毫秒）（`request_event.all_duration_ms`） | 0.146 | 0.146 | 0.146 | 1 | 0 |
| 单次运行（`run`） | 单次运行（run） | `request_group.first_attempt_duration_ms` | 0.146 | 0.146 | 0.146 | 1 | 0 |
| 单次运行（`run`） | 单次运行（run） | `request_group.retry_wait_ms` | 0.0 | 0.0 | 0.0 | 1 | 0 |

## Flaky 新增与持续

- 新增疑似=0，新增确认=0，持续确认=0，过期投影=0
| 用例 | 环境/执行画像 | 当前/检测状态 | 样本 | 投影 | 责任人 | 到期 |
| --- | --- | --- | --- | --- | --- | --- |
| - | - | - | - | - | - | - |

## 隔离、恢复与超期治理

“已隔离（QUARANTINED）”是治理标签，不代表测试通过，也不会自动跳过用例。

- 已隔离=0，恢复观察中=0，已恢复=0，已超期=0
| 用例 | 环境/执行画像 | 当前/检测状态 | 样本 | 投影 | 责任人 | 到期 |
| --- | --- | --- | --- | --- | --- | --- |
| - | - | - | - | - | - | - |

### 本次 Flaky 状态迁移

| 迁移 ID | 状态 | 触发方式 | 原因 | 样本 | 操作者 | 证据 |
| --- | --- | --- | --- | --- | --- | --- |
| - | - | - | - | - | - | - |

## 待关注事项

本次没有需要额外处理的关注事项。

## 完整性与证据入口

- 必需数据源失败数：0
- 问题代码：-
- 完整机器数据请查看 `p1-observation.json`；指标与 Flaky 详情请回到各自源产物文件。

| 展示窗口 | 总数 | 已展示 | 已省略 | 完整源 |
| --- | --- | --- | --- | --- |
| Flaky 治理项（`flaky_governance`） | 0 | 0 | 0 | flaky-evaluation.json |
| 新增及持续 Flaky（`flaky_new_and_ongoing`） | 0 | 0 | 0 | flaky-evaluation.json |
| Flaky 状态迁移（`flaky_transitions`） | 0 | 0 | 0 | flaky-evaluation.json |
| 耗时观测（`timing_observations`） | 25 | 10 | 15 | metrics/run-metrics.json |
| 用量缺失引用（`usage_missing_refs`） | 0 | 0 | 0 | metrics/run-metrics.json |
