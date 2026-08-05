from __future__ import annotations

from pipeline_reporting.contracts import PipelineReport, StageStatus, TestSummary


_CONCLUSION_LABELS = {
    "PASS": "通过",
    "WARN": "需关注",
    "FAIL": "失败",
    "NO_DATA": "无数据",
}
_STAGE_LABELS = {
    StageStatus.PASSED: "通过",
    StageStatus.FAILED: "失败",
    StageStatus.NOT_RUN: "未执行",
    StageStatus.BLOCKED: "已阻断",
    StageStatus.NO_DATA: "无可用产物",
}


def render_pipeline_summary(report: PipelineReport) -> str:
    context = report.context
    parameter_rows = [
        ("框架测试", _enabled_state(context.framework_tests_enabled)),
        ("用例收集", _enabled_state(context.smoke_collect_enabled)),
        ("接口测试", _enabled_state(context.real_smoke_enabled)),
        ("质量观测", _enabled_state(context.quality_enabled)),
    ]
    if context.real_smoke_enabled:
        parameter_rows.append(("测试目标", context.smoke_target))
    parameter_rows.append(("并发配置", context.parallel_workers))
    lines = [
        "# Jenkins 流水线执行摘要",
        "",
        "## 本次结论",
        "",
        (
            f"- 结论：{_CONCLUSION_LABELS[report.conclusion.value]}"
            f"（`{report.conclusion.value}`）——{_md(report.conclusion_text)}"
        ),
        f"- Jenkins 结果：`{_md(context.build_result)}`",
        f"- 构建：`{_md(context.job_name)} #{_md(context.build_number)}`",
        f"- 分支/提交：`{_md(context.branch)}` / `{_md(_short_commit(context.commit_sha))}`",
        f"- 环境：{_environment_label(context.environment_name)}",
        f"- 总耗时：{_duration(context.duration_ms / 1000)}",
        "",
        "## 执行参数",
        "",
        _table(
            ("配置项", "本轮值"),
            tuple(parameter_rows),
        ),
        "",
        "## 阶段结果",
        "",
        _table(
            ("阶段", "状态", "结果摘要"),
            tuple(
                (stage.name, _STAGE_LABELS[stage.status], stage.summary)
                for stage in report.stages
            ),
        ),
        "",
    ]

    lines.extend(_test_section("框架单测", report.unit_tests, context.framework_tests_enabled))
    lines.extend(_collect_section(report))
    lines.extend(_test_section("接口测试", report.smoke_tests, context.real_smoke_enabled))
    lines.extend(_case_details(report))
    if context.real_smoke_enabled and context.quality_enabled:
        lines.extend(_request_section(report))
        lines.extend(_retry_section(report))
        lines.extend(_timing_section(report))
        lines.extend(_flaky_section(report))
    lines.extend(
        [
            "## 建议动作",
            "",
            *(f"- {_md(item)}" for item in report.actions),
            "",
        ]
    )
    if report.warnings:
        lines.extend(
            [
                "## 报告生成提示",
                "",
                *(f"- {_md(item)}" for item in report.warnings[:10]),
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def _test_section(title: str, summary: TestSummary, enabled: bool) -> list[str]:
    lines = [f"## {title}", ""]
    if not enabled:
        return [*lines, "本轮参数未启用。", ""]
    if not summary.available:
        return [*lines, "本轮没有可用的 JUnit 结果。", ""]
    return [
        *lines,
        _table(
            ("总数", "通过", "失败", "错误", "跳过", "用例耗时合计"),
            (
                (
                    str(summary.total),
                    str(summary.passed),
                    str(summary.failed),
                    str(summary.errors),
                    str(summary.skipped),
                    _duration(summary.duration_seconds),
                ),
            ),
        ),
        "",
    ]


def _collect_section(report: PipelineReport) -> list[str]:
    summary = report.smoke_collect
    lines = ["## 用例收集", ""]
    if not report.context.smoke_collect_enabled:
        return [*lines, "本轮参数未启用。", ""]
    if not summary.available:
        return [*lines, "本轮没有可用的用例收集清单。", ""]
    parts = [f"共收集 {summary.total} 项"]
    if summary.parallel is not None and summary.serial is not None:
        parts.append(f"并发池 {summary.parallel} 项，串行池 {summary.serial} 项")
    return [*lines, "；".join(parts) + "。", ""]


def _case_details(report: PipelineReport) -> list[str]:
    failed = (
        *(('框架单测', item) for item in report.unit_tests.failed_cases),
        *(('接口测试', item) for item in report.smoke_tests.failed_cases),
    )
    skipped = (
        *(('框架单测', item) for item in report.unit_tests.skipped_cases),
        *(('接口测试', item) for item in report.smoke_tests.skipped_cases),
    )
    lines = ["## 失败用例", ""]
    if failed:
        lines.append(
            _table(
                ("阶段", "用例", "原因"),
                tuple(
                    (stage, item.name, _first_line(item.message) or item.status)
                    for stage, item in failed[:10]
                ),
            )
        )
        if len(failed) > 10:
            lines.append(f"\n另有 {len(failed) - 10} 条失败/错误用例，请查看 JUnit/Allure。")
    else:
        lines.append("无失败用例。")
    lines.extend(["", "## 跳过用例", ""])
    if skipped:
        lines.append(
            _table(
                ("阶段", "用例", "原因"),
                tuple(
                    (stage, item.name, _first_line(item.message) or "未提供原因")
                    for stage, item in skipped[:10]
                ),
            )
        )
        if len(skipped) > 10:
            lines.append(f"\n另有 {len(skipped) - 10} 条跳过用例，请查看 JUnit。")
    else:
        lines.append("无跳过用例。")
    return [*lines, ""]


def _request_section(report: PipelineReport) -> list[str]:
    value = report.request_health
    lines = ["## 请求质量", ""]
    if not value.available:
        return [*lines, "本轮请求指标不可用。", ""]
    rate = "未产生请求" if value.success_rate is None else f"{value.success_rate:.2%}"
    return [
        *lines,
        _table(
            ("请求总数", "请求成功率", "HTTP 5xx", "超时"),
            ((str(value.total), rate, str(value.http_5xx_count), str(value.timeout_count)),),
        ),
        "",
    ]


def _retry_section(report: PipelineReport) -> list[str]:
    value = report.retry_health
    lines = ["## 重试效果", ""]
    if not value.available:
        return [*lines, "本轮重试指标不可用。", ""]
    if value.retried_group_count == 0:
        return [*lines, "本轮未发生重试。", ""]
    return [
        *lines,
        _table(
            ("重试请求组", "挽救成功", "重试挽救率"),
            (
                (
                    str(value.retried_group_count),
                    str(value.rescued_group_count),
                    f"{value.rescue_rate:.2%}",
                ),
            ),
        ),
        "",
    ]


def _timing_section(report: PipelineReport) -> list[str]:
    lines = ["## 接口耗时 Top 5", ""]
    if not report.interface_timings:
        return [*lines, "本轮没有可展示的 workload 非轮询接口耗时。", ""]
    return [
        *lines,
        _table(
            ("接口", "请求组", "平均耗时", "最大耗时"),
            tuple(
                (
                    item.interface_id,
                    str(item.request_group_count),
                    f"{item.mean_ms / 1000:.2f} 秒",
                    f"{item.maximum_ms / 1000:.2f} 秒",
                )
                for item in report.interface_timings
            ),
        ),
        "",
    ]


def _flaky_section(report: PipelineReport) -> list[str]:
    value = report.flaky
    lines = ["## Flaky 状态迁移", ""]
    if not value.available:
        return [*lines, "本轮未启用或未生成 Flaky 评估。", ""]
    lines.extend(
        [
            _table(
                ("新增疑似", "新增确认", "恢复稳定", "进入隔离", "超期治理"),
                (
                    (
                        str(value.newly_suspected_count),
                        str(value.newly_confirmed_count),
                        str(value.recovered_count),
                        str(value.newly_quarantined_count),
                        str(value.overdue_count),
                    ),
                ),
            ),
            "",
        ]
    )
    if value.transition_count == 0:
        lines.extend(["本轮无 Flaky 状态迁移。", ""])
    elif value.transition_directions:
        lines.extend(
            [
                _table(
                    ("状态变化", "数量"),
                    tuple((direction, str(count)) for direction, count in value.transition_directions),
                ),
                "",
            ]
        )
    if value.actionable_changes:
        lines.extend(
            [
                _table(
                    ("用例", "变化"),
                    tuple((item.case_id, item.change) for item in value.actionable_changes),
                ),
                "",
            ]
        )
    return lines


def _table(headers: tuple[str, ...], rows: tuple[tuple[str, ...], ...]) -> str:
    lines = [
        "| " + " | ".join(_md(item) for item in headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    lines.extend(
        "| " + " | ".join(_md(str(item)) for item in row) + " |" for row in rows
    )
    return "\n".join(lines)


def _md(value: str) -> str:
    return str(value).replace("|", "\\|").replace("\r", " ").replace("\n", " ").strip()


def _first_line(value: str | None) -> str | None:
    if value is None:
        return None
    return value.splitlines()[0].strip()[:240] or None


def _enabled_state(value: bool) -> str:
    return "启用" if value else "未启用"


def _short_commit(value: str) -> str:
    return value[:12] if value and value != "-" else "-"


def _environment_label(value: str) -> str:
    normalized = value.casefold()
    if normalized in {"china", "true"}:
        return "中国环境"
    if normalized in {"overseas", "false"}:
        return "海外环境"
    return "未知环境"


def _duration(seconds: float) -> str:
    total = max(int(round(seconds)), 0)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    parts: list[str] = []
    if hours:
        parts.append(f"{hours}小时")
    if minutes:
        parts.append(f"{minutes}分")
    if secs or not parts:
        parts.append(f"{secs}秒")
    return "".join(parts)
