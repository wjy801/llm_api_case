from __future__ import annotations

from pipeline_reporting.contracts import (
    CollectSummary,
    LoadedPipelineSources,
    PipelineConclusion,
    PipelineContext,
    PipelineReport,
    StageResult,
    StageStatus,
    TestSummary,
)


def build_pipeline_report(
    context: PipelineContext,
    sources: LoadedPipelineSources,
) -> PipelineReport:
    framework = _test_stage(
        "框架单测",
        "framework_tests",
        context.framework_tests_enabled,
        sources.unit_tests,
        sources,
    )
    collect = _collect_stage(context, sources)
    real_smoke = _test_stage(
        "接口测试",
        "real_smoke",
        context.real_smoke_enabled,
        sources.smoke_tests,
        sources,
    )
    quality = _quality_stage(context, sources, real_smoke.status)
    stages = (framework, collect, real_smoke, quality)
    conclusion = _conclusion(context, sources, stages)
    actions = _actions(context, sources, stages)
    return PipelineReport(
        context=context,
        conclusion=conclusion,
        conclusion_text=_conclusion_text(conclusion, context, sources, stages),
        stages=stages,
        unit_tests=sources.unit_tests,
        smoke_tests=sources.smoke_tests,
        smoke_collect=sources.smoke_collect,
        request_health=sources.request_health,
        retry_health=sources.retry_health,
        interface_timings=sources.interface_timings,
        flaky=sources.flaky,
        actions=actions,
        warnings=sources.warnings,
    )


def _test_stage(
    display_name: str,
    stage_name: str,
    enabled: bool,
    tests: TestSummary,
    sources: LoadedPipelineSources,
) -> StageResult:
    if not enabled:
        return StageResult(display_name, StageStatus.NOT_RUN, "本轮参数未启用")
    explicit = sources.stage_statuses.get(stage_name)
    if tests.available:
        status = (
            StageStatus.FAILED
            if tests.failed or tests.errors
            else StageStatus.PASSED
        )
        return StageResult(display_name, status, _test_summary_text(tests))
    if explicit is StageStatus.FAILED:
        return StageResult(display_name, StageStatus.FAILED, "阶段执行失败，未生成可用 JUnit")
    if explicit in {StageStatus.BLOCKED, StageStatus.NOT_RUN}:
        return StageResult(display_name, StageStatus.BLOCKED, "被前序失败或中断阻止")
    return StageResult(display_name, StageStatus.NO_DATA, "已选择执行，但测试产物不可用")


def _collect_stage(
    context: PipelineContext,
    sources: LoadedPipelineSources,
) -> StageResult:
    if not context.smoke_collect_enabled:
        return StageResult("用例收集", StageStatus.NOT_RUN, "本轮参数未启用")
    if sources.smoke_collect.available:
        return StageResult(
            "用例收集",
            StageStatus.PASSED,
            _collect_summary_text(sources.smoke_collect),
        )
    explicit = sources.stage_statuses.get("smoke_collect")
    if explicit is StageStatus.FAILED:
        return StageResult("用例收集", StageStatus.FAILED, "收集失败")
    if explicit in {StageStatus.BLOCKED, StageStatus.NOT_RUN}:
        return StageResult("用例收集", StageStatus.BLOCKED, "被前序失败或中断阻止")
    return StageResult("用例收集", StageStatus.NO_DATA, "已选择执行，但收集清单不可用")


def _quality_stage(
    context: PipelineContext,
    sources: LoadedPipelineSources,
    real_smoke_status: StageStatus,
) -> StageResult:
    if not context.real_smoke_enabled:
        return StageResult("质量观测", StageStatus.NOT_RUN, "接口测试未启用")
    if real_smoke_status is StageStatus.BLOCKED:
        return StageResult("质量观测", StageStatus.BLOCKED, "接口测试未执行")
    if sources.quality_available:
        return StageResult("质量观测", StageStatus.PASSED, "P0 运行身份与汇总完整")
    return StageResult("质量观测", StageStatus.NO_DATA, "本轮 Quality 核心产物不可用")


def _conclusion(
    context: PipelineContext,
    sources: LoadedPipelineSources,
    stages: tuple[StageResult, ...],
) -> PipelineConclusion:
    result = context.build_result.upper()
    if result in {"FAILURE", "ABORTED", "NOT_BUILT"}:
        return PipelineConclusion.FAIL
    if any(stage.status is StageStatus.FAILED for stage in stages):
        return PipelineConclusion.FAIL
    if not any(
        (
            context.framework_tests_enabled,
            context.smoke_collect_enabled,
            context.real_smoke_enabled,
        )
    ):
        return PipelineConclusion.WARN
    if any(stage.status in {StageStatus.BLOCKED, StageStatus.NO_DATA} for stage in stages):
        return PipelineConclusion.WARN
    if result == "UNSTABLE":
        return PipelineConclusion.WARN
    if sources.unit_tests.skipped or sources.smoke_tests.skipped:
        return PipelineConclusion.WARN
    if sources.retry_health.rescued_group_count:
        return PipelineConclusion.WARN
    if sources.flaky.actionable_count:
        return PipelineConclusion.WARN
    return PipelineConclusion.PASS


def _conclusion_text(
    conclusion: PipelineConclusion,
    context: PipelineContext,
    sources: LoadedPipelineSources,
    stages: tuple[StageResult, ...],
) -> str:
    if conclusion is PipelineConclusion.FAIL:
        return "本轮流水线执行失败"
    if not any(
        (
            context.framework_tests_enabled,
            context.smoke_collect_enabled,
            context.real_smoke_enabled,
        )
    ):
        return "本轮未执行测试验证"
    if sources.unit_tests.skipped or sources.smoke_tests.skipped:
        return "执行成功，但存在未覆盖用例"
    if any(stage.status in {StageStatus.BLOCKED, StageStatus.NO_DATA} for stage in stages):
        return "流水线已结束，但部分阶段没有可用结果"
    if sources.retry_health.rescued_group_count:
        return "执行通过，但重试挽救了瞬时失败"
    if sources.flaky.actionable_count:
        return "执行通过，但存在 Flaky 稳定性事项"
    return "本轮按配置执行完成"


def _actions(
    context: PipelineContext,
    sources: LoadedPipelineSources,
    stages: tuple[StageResult, ...],
) -> tuple[str, ...]:
    actions: list[str] = []
    failed_count = (
        sources.unit_tests.failed
        + sources.unit_tests.errors
        + sources.smoke_tests.failed
        + sources.smoke_tests.errors
    )
    skipped_count = sources.unit_tests.skipped + sources.smoke_tests.skipped
    if failed_count:
        actions.append(f"检查 {failed_count} 条失败/错误用例及对应 JUnit、Allure 证据。")
    if skipped_count:
        actions.append(f"确认 {skipped_count} 条跳过用例是否仍符合预期。")
    if any(stage.status is StageStatus.NO_DATA for stage in stages):
        actions.append("检查已选择但没有可用产物的阶段日志。")
    if any(stage.status is StageStatus.BLOCKED for stage in stages):
        actions.append("先处理前序失败或中断，再重新执行被阻断阶段。")
    if sources.retry_health.rescued_group_count:
        actions.append(
            f"复核 {sources.retry_health.rescued_group_count} 个被重试挽救的请求组。"
        )
    if sources.flaky.actionable_count:
        actions.append("查看本轮新增或待治理的 Flaky 用例。")
    if not context.real_smoke_enabled:
        actions.append("本轮未执行真实接口验证。")
    if not actions:
        actions.append("本轮未发现需要立即处理的问题。")
    return tuple(actions)


def _test_summary_text(summary: TestSummary) -> str:
    return (
        f"{summary.total} 总计 / {summary.passed} 通过 / "
        f"{summary.failed + summary.errors} 失败或错误 / {summary.skipped} 跳过"
    )


def _collect_summary_text(summary: CollectSummary) -> str:
    if summary.parallel is None or summary.serial is None:
        return f"{summary.total} 项"
    return f"{summary.total} 项：{summary.parallel} 并发 / {summary.serial} 串行"
