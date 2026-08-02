from __future__ import annotations

from typing import Any

from quality.flaky_models import FlakyStateSummary
from quality.observation_models import (
    P1KnownTotal,
    P1ObservationReport,
)
from quality.redaction import redact_quality_value


_COMMON_STATUS_LABELS = {
    "PASS": "通过",
    "WARN": "警告",
    "BLOCK": "阻断",
    "NO_DATA": "无数据",
    "complete": "完整",
    "degraded": "降级",
    "failed": "失败",
    "shadow": "影子观察",
}
_SOURCE_NAME_LABELS = {
    "p0_report": "P0 质量报告",
    "run_metrics": "单次运行指标",
    "flaky_import": "Flaky 历史导入",
    "flaky_evaluation": "Flaky 状态评估",
}
_SOURCE_EXPECTATION_LABELS = {
    "required": "必需",
    "disabled": "已禁用",
}
_SOURCE_STATUS_LABELS = {
    "available": "可用",
    "degraded": "降级",
    "no_data": "无数据",
    "failed": "失败",
    "missing": "缺失",
    "incompatible": "不兼容",
    "disabled": "已禁用",
}
_COMPLETENESS_LABELS = {
    "complete": "完整",
    "partial": "部分完整",
    "no_data": "无数据",
    "not_applicable": "不适用",
}
_METRIC_LABELS = {
    "operation.success_rate": "逻辑调用成功率",
    "operation.timeout_rate": "逻辑调用超时率",
    "request_event.business_success_rate": "请求事件业务成功率",
    "request_event.http_429_rate": "请求事件 HTTP 429 比例",
    "request_event.http_5xx_rate": "请求事件 HTTP 5xx 比例",
    "request_event.timeout_rate": "请求事件超时率",
    "request_group.business_retry_rescue_rate": "业务重试挽救率",
    "request_group.final_business_success_rate": "请求组最终业务成功率",
    "request_group.final_http_success_rate": "请求组最终 HTTP 成功率",
    "request_group.final_transport_response_rate": "请求组最终传输响应率",
    "operation.total_duration_ms": "逻辑调用总耗时（毫秒）",
    "operation.polling_total_ms": "轮询总耗时（毫秒）",
    "operation.polling_sleep_ms": "轮询休眠耗时（毫秒）",
    "operation.response_headers_ms": "响应头等待耗时（毫秒）",
    "request_group.total_duration_ms": "请求组总耗时（毫秒）",
    "request_event.all_duration_ms": "请求事件总耗时（毫秒）",
}
_RESOURCE_LABELS = {
    "input tokens": "输入 Token",
    "output tokens": "输出 Token",
    "media count": "媒体数量",
    "media duration ms": "媒体时长（毫秒）",
    "retry input tokens": "重试输入 Token",
    "retry output tokens": "重试输出 Token",
    "retry media count": "重试媒体数量",
}
_GRAIN_LABELS = {
    "run": "单次运行",
    "operation_bucket": "逻辑调用分组",
    "request_group_bucket": "请求组分组",
    "request_event_bucket": "请求事件分组",
}
_DIMENSION_KEY_LABELS = {
    "model_id": "模型 ID",
    "operation_kind": "调用类型",
    "operation_name": "调用名称",
    "traffic_role": "流量角色",
    "interface_id": "接口标识",
    "protocol": "协议",
}
_DIMENSION_VALUE_LABELS = {
    "async_task": "异步任务",
    "polling": "轮询",
    "http": "HTTP",
    "sse": "SSE",
    "workload": "业务流量",
    "control": "控制流量",
    "media_generation": "媒体生成",
    "media_generation_polling": "媒体生成轮询",
    "image_generation": "图片生成",
}
_FLAKY_STATE_LABELS = {
    "OBSERVING": "观察中",
    "STABLE": "稳定",
    "SUSPECTED": "疑似不稳定",
    "CONFIRMED": "已确认不稳定",
    "QUARANTINED": "已隔离",
    "RECOVERING": "恢复观察中",
}
_PROJECTION_STATUS_LABELS = {
    "CURRENT": "当前",
    "STALE": "已过期",
}
_TRIGGER_LABELS = {
    "observation": "自动观测",
    "manual": "人工操作",
    "bootstrap": "初始建档",
    "reprojection": "重新投影",
}
_TRANSITION_REASON_LABELS = {
    "first_observation": "首次观测",
    "outcome_changed": "执行结果发生变化",
    "failure_fingerprint_changed": "失败指纹发生变化",
    "consistent_signature_threshold_met": "达到连续一致阈值",
    "stable_signature_broken": "稳定结果被打破",
    "confirmation_threshold_met": "达到 Flaky 确认阈值",
    "suspected_cleared_by_streak": "连续一致后解除疑似状态",
}
_ATTENTION_LEVEL_LABELS = {
    "info": "提示",
    "review": "需复核",
    "action_required": "需要处理",
}
_ISSUE_CODE_LABELS = {
    "expected_outcome_excluded": "预期结果已排除",
    "usage_incomplete": "用量覆盖不完整",
    "source_disabled": "数据源已禁用",
    "required_source_unavailable": "必需数据源不可用",
    "required_source_degraded": "必需数据源已降级",
    "usage_coverage_incomplete": "用量覆盖不完整",
    "flaky_projection_stale": "Flaky 投影已过期",
    "flaky_newly_suspected": "新增疑似 Flaky",
    "flaky_newly_confirmed": "新增确认 Flaky",
    "flaky_governance_overdue": "Flaky 治理已超期",
    "flaky_recovered": "Flaky 已恢复",
}
_DISPLAY_WINDOW_LABELS = {
    "flaky_governance": "Flaky 治理项",
    "flaky_new_and_ongoing": "新增及持续 Flaky",
    "flaky_transitions": "Flaky 状态迁移",
    "timing_observations": "耗时观测",
    "usage_missing_refs": "用量缺失引用",
}



def render_p1_observation_markdown(report: P1ObservationReport) -> str:
    overview = report.overview
    lines = [
        "# P1 单次观察与 Flaky 报告",
        "",
        "## 报告状态与 P0 影子门禁",
        "",
        f"- 报告状态：{localized_code(report.report_status.value, _COMMON_STATUS_LABELS)}",
        f"- 运行 ID：`{markdown_cell(report.run_id)}`",
        f"- P0 门禁：{localized_code(overview.p0_gate_overall or '-', _COMMON_STATUS_LABELS)}（{localized_code(overview.p0_gate_mode or '-', _COMMON_STATUS_LABELS)}）",
        f"- P0 数据完整性：{localized_code(overview.p0_integrity_status or '-', _COMMON_STATUS_LABELS)}",
        "- P1 报告状态只表示观察数据完整性，不是门禁结论，也不会修改 Jenkins 结果。",
        "",
        "## 数据源健康度",
        "",
        markdown_table(
            ("数据源", "要求", "状态", "版本", "问题", "产物文件"),
            [
                (
                    localized_code(item.source_name, _SOURCE_NAME_LABELS),
                    localized_code(item.expectation.value, _SOURCE_EXPECTATION_LABELS),
                    localized_code(item.status.value, _SOURCE_STATUS_LABELS),
                    item.producer_version or item.schema_version or "-",
                    "，".join(localized_issue_code(code) for code in item.issue_codes)
                    or "-",
                    item.artifact_path or "-",
                )
                for item in report.sources
            ],
        ),
        "",
        "## 本次逻辑调用稳定性",
        "",
        (
            f"- 业务逻辑调用：共 {overview.workload_operation_count} 次；"
            f"成功={overview.operation_success_count}，失败={overview.operation_failed_count}，"
            f"超时={overview.operation_timeout_count}。"
        ),
    ]
    if report.metrics is None:
        lines.append("- 指标源不可消费，本节不展示伪造值。")
    else:
        ratio_rows = [
            item
            for item in report.metrics.observations
            if item.numerator is not None and item.grain == "run"
        ][:10]
        lines.extend(
            [
                "",
                markdown_table(
                    ("指标", "值", "分子", "样本量", "未知/缺失", "完整性"),
                    [
                        (
                            localized_code(item.metric_name, _METRIC_LABELS),
                            display_value(item.value, item.sample_size),
                            item.numerator,
                            item.sample_size,
                            item.missing_sample_size,
                            localized_code(
                                item.completeness.value, _COMPLETENESS_LABELS
                            ),
                        )
                        for item in ratio_rows
                    ],
                ),
            ]
        )
    lines.extend(["", "## 资源用量与覆盖率", ""])
    if report.usage_coverage is None:
        lines.append("指标源不可消费，资源用量不按零展示。")
    else:
        usage = report.usage_coverage
        lines.extend(
            [
                f"- 完整={usage.complete_count}，部分完整={usage.partial_count}，缺失={usage.missing_count}，不适用={usage.not_applicable_count}",
                markdown_table(
                    ("资源", "已知总量", "样本量", "缺失", "完整性"),
                    [
                        usage_row("input tokens", usage.input_tokens),
                        usage_row("output tokens", usage.output_tokens),
                        usage_row("media count", usage.media_count),
                        usage_row("media duration ms", usage.media_duration_ms),
                        usage_row("retry input tokens", usage.retry_input_tokens),
                        usage_row("retry output tokens", usage.retry_output_tokens),
                        usage_row("retry media count", usage.retry_media_count),
                    ],
                ),
            ]
        )
    lines.extend(["", "## HTTP/SSE/异步耗时", ""])
    if report.metrics is None:
        lines.append("指标源不可消费，无耗时观察。")
    else:
        timing = sorted(
            (
                item
                for item in report.metrics.observations
                if "duration_ms" in item.metric_name
                or item.metric_name.endswith("_ms")
            ),
            key=lambda item: (
                -(float(item.value) if item.value is not None else -1),
                item.metric_id,
            ),
        )[:10]
        lines.append(
            markdown_table(
                ("粒度", "维度", "指标", "均值", "最小", "最大", "样本量", "缺失"),
                [
                    (
                        localized_code(item.grain, _GRAIN_LABELS),
                        dimension_text(item.dimension),
                        localized_code(item.metric_name, _METRIC_LABELS),
                        display_value(item.value, item.sample_size),
                        display_value(item.minimum, item.sample_size),
                        display_value(item.maximum, item.sample_size),
                        item.sample_size,
                        item.missing_sample_size,
                    )
                    for item in timing
                ],
            )
        )
    lines.extend(["", "## Flaky 新增与持续", ""])
    if report.flaky is None:
        lines.append("Flaky 数据源已关闭或不可消费。")
    else:
        flaky = report.flaky
        lines.extend(
            [
                f"- 新增疑似={len(flaky.newly_suspected)}，新增确认={len(flaky.newly_confirmed)}，持续确认={len(flaky.ongoing_confirmed)}，过期投影={flaky.stale_count}",
                flaky_table(
                    (*flaky.newly_suspected, *flaky.newly_confirmed, *flaky.ongoing_confirmed)[:10]
                ),
                "",
                "## 隔离、恢复与超期治理",
                "",
                "“已隔离（QUARANTINED）”是治理标签，不代表测试通过，也不会自动跳过用例。",
                "",
                f"- 已隔离={len(flaky.quarantined)}，恢复观察中={len(flaky.recovering)}，已恢复={len(flaky.recovered)}，已超期={len(flaky.overdue)}",
                flaky_table(
                    (*flaky.quarantined, *flaky.recovering, *flaky.recovered, *flaky.overdue)[:10]
                ),
                "",
                "### 本次 Flaky 状态迁移",
                "",
                markdown_table(
                    ("迁移 ID", "状态", "触发方式", "原因", "样本", "操作者", "证据"),
                    [
                        (
                            item.transition_id,
                            (
                                f"{localized_code(item.from_state.value, _FLAKY_STATE_LABELS) if item.from_state else '-'}"
                                f" → {localized_code(item.to_state.value, _FLAKY_STATE_LABELS)}"
                            ),
                            localized_code(item.trigger_type.value, _TRIGGER_LABELS),
                            localized_code(
                                item.reason_code, _TRANSITION_REASON_LABELS
                            ),
                            item.sample_size,
                            item.actor or "-",
                            ", ".join(
                                (
                                    *item.evidence_run_ids[:3],
                                    *item.evidence_observation_ids[:3],
                                )
                            )
                            or "-",
                        )
                        for item in flaky.transitions[:10]
                    ],
                ),
            ]
        )
    lines.extend(["", "## 待关注事项", ""])
    lines.append(
        markdown_table(
            ("级别", "代码", "标题", "摘要", "建议动作"),
            [
                (
                    localized_code(item.level.value, _ATTENTION_LEVEL_LABELS),
                    localized_issue_code(item.attention_code),
                    item.title,
                    item.summary,
                    item.suggested_action,
                )
                for item in report.attention_items[:20]
            ],
        )
        if report.attention_items
        else "本次没有需要额外处理的关注事项。"
    )
    lines.extend(
        [
            "",
            "## 完整性与证据入口",
            "",
            f"- 必需数据源失败数：{report.integrity.required_source_failure_count}",
            (
                "- 问题代码："
                + (
                    "，".join(
                        localized_issue_code(code)
                        for code in report.integrity.issue_codes
                    )
                    or "-"
                )
            ),
            "- 完整机器数据请查看 `p1-observation.json`；指标与 Flaky 详情请回到各自源产物文件。",
            "",
            markdown_table(
                ("展示窗口", "总数", "已展示", "已省略", "完整源"),
                [
                    (
                        localized_code(item.category, _DISPLAY_WINDOW_LABELS),
                        item.total_count,
                        item.shown_count,
                        item.omitted_count,
                        item.source_artifact,
                    )
                    for item in report.display_windows
                ],
            ),
            "",
        ]
    )
    return "\n".join(lines)

def safe_markdown_text(value: object, maximum: int = 500) -> str:
    redacted = redact_quality_value(str(value), remove_url_query=True)
    text = str(redacted).replace("\x00", "").strip()
    return text[:maximum] or "-"


def markdown_cell(value: object) -> str:
    return (
        safe_markdown_text(value)
        .replace("|", "\\|")
        .replace("\r", " ")
        .replace("\n", "<br>")
    )


def localized_code(value: object, labels: dict[str, str]) -> str:
    raw = str(value)
    label = labels.get(raw)
    return f"{label}（`{raw}`）" if label else f"`{raw}`"


def localized_issue_code(value: str) -> str:
    return localized_code(value, _ISSUE_CODE_LABELS)


def markdown_table(headers: tuple[str, ...], rows: list[tuple[Any, ...]]) -> str:
    rendered = [
        "| " + " | ".join(markdown_cell(item) for item in headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    rendered.extend(
        "| " + " | ".join(markdown_cell(item) for item in row) + " |" for row in rows
    )
    if not rows:
        rendered.append("| " + " | ".join("-" for _ in headers) + " |")
    return "\n".join(rendered)


def display_value(value: int | float | None, sample_size: int) -> str:
    if sample_size == 0 or value is None:
        return "无数据（NO_DATA）"
    return str(value)


def dimension_text(value: dict[str, str | None]) -> str:
    parts = []
    for key, item in value.items():
        display_key = _DIMENSION_KEY_LABELS.get(key, key)
        raw_value = item if item is not None else "-"
        display_value = _DIMENSION_VALUE_LABELS.get(raw_value, raw_value)
        if display_key != key:
            display_key = f"{display_key}（{key}）"
        if display_value != raw_value:
            display_value = f"{display_value}（{raw_value}）"
        parts.append(f"{display_key}={display_value}")
    return "，".join(parts) or "单次运行（run）"


def usage_row(name: str, value: P1KnownTotal) -> tuple[Any, ...]:
    return (
        localized_code(name, _RESOURCE_LABELS),
        display_value(value.total, value.sample_size),
        value.sample_size,
        value.missing_sample_size,
        localized_code(value.completeness.value, _COMPLETENESS_LABELS),
    )


def flaky_table(values: tuple[FlakyStateSummary, ...]) -> str:
    return markdown_table(
        ("用例", "环境/执行画像", "当前/检测状态", "样本", "投影", "责任人", "到期"),
        [
            (
                item.case_id,
                f"{item.environment}/{item.execution_profile}",
                (
                    f"{localized_code(item.current_state.value, _FLAKY_STATE_LABELS)}"
                    f" / {localized_code(item.detected_state.value, _FLAKY_STATE_LABELS)}"
                ),
                item.sample_size,
                localized_code(
                    item.projection_status.value, _PROJECTION_STATUS_LABELS
                ),
                item.owner or "-",
                item.expires_at.isoformat() if item.expires_at is not None else "-",
            )
            for item in values
        ],
    )
