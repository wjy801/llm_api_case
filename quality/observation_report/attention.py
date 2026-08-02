from __future__ import annotations

from quality.flaky_models import FlakyStateSummary, ProjectionStatus
from quality.observation_models import (
    AttentionLevel,
    P1AttentionItem,
    P1FlakySection,
    P1SourceSummary,
    P1UsageCoverage,
    SourceExpectation,
    SourceStatus,
)


_SOURCE_FAILURE_STATUSES = {
    SourceStatus.FAILED,
    SourceStatus.MISSING,
    SourceStatus.INCOMPATIBLE,
}


def is_required_source_failure(source: P1SourceSummary) -> bool:
    return (
        source.expectation is SourceExpectation.REQUIRED
        and source.status in _SOURCE_FAILURE_STATUSES
    )


def build_attention_items(
    sources: tuple[P1SourceSummary, ...],
    usage: P1UsageCoverage | None,
    flaky: P1FlakySection | None,
) -> tuple[P1AttentionItem, ...]:
    items: list[P1AttentionItem] = []
    for source in sources:
        if is_required_source_failure(source):
            items.append(
                P1AttentionItem(
                    attention_code="required_source_unavailable",
                    level=AttentionLevel.ACTION_REQUIRED,
                    title="必需质量数据源不可用",
                    summary=f"{source.source_name} 当前状态为 {source.status.value}。",
                    source_name=source.source_name,
                    related_ids=source.issue_codes,
                    suggested_action="修复对应质量阶段并使用当前 run Artifact 重放报告。",
                )
            )
        elif source.status is SourceStatus.DEGRADED:
            items.append(
                P1AttentionItem(
                    attention_code="required_source_degraded",
                    level=AttentionLevel.REVIEW,
                    title="质量数据源已降级",
                    summary=f"{source.source_name} 保留可信摘要，但覆盖不完整。",
                    source_name=source.source_name,
                    related_ids=source.issue_codes,
                    suggested_action="查看源 Artifact 的完整性问题并补齐缺失证据。",
                )
            )
    if usage is not None and (usage.partial_count or usage.missing_count):
        items.append(
            P1AttentionItem(
                attention_code="usage_coverage_incomplete",
                level=AttentionLevel.REVIEW,
                title="资源用量覆盖不完整",
                summary=(
                    f"partial={usage.partial_count}, missing={usage.missing_count}；"
                    "缺失值未按零计入。"
                ),
                source_name="run_metrics",
                related_ids=usage.missing_operation_refs,
                suggested_action="补齐协议 usage 采集，或明确确认该 operation 为 not_applicable。",
            )
        )
    if flaky is not None:
        if flaky.stale_count:
            items.append(
                P1AttentionItem(
                    attention_code="flaky_projection_stale",
                    level=AttentionLevel.ACTION_REQUIRED,
                    title="Flaky 投影已过期",
                    summary=f"本次有 {flaky.stale_count} 个状态投影不可作为可信当前结论。",
                    source_name="flaky_evaluation",
                    related_ids=tuple(
                        item.flaky_key
                        for item in all_flaky_states(flaky)
                        if item.projection_status is ProjectionStatus.STALE
                    ),
                    suggested_action="人工检查数据库状态并使用 Flaky CLI 执行 dry-run 重建。",
                )
            )
        for item in flaky.newly_suspected:
            items.append(
                P1AttentionItem(
                    attention_code="flaky_newly_suspected",
                    level=AttentionLevel.REVIEW,
                    title="发现新的疑似 Flaky",
                    summary="当前样本出现结果或失败签名切换，需要继续观察。",
                    source_name="flaky_evaluation",
                    related_ids=(item.flaky_key, item.latest_observation_id),
                    suggested_action="继续观察后续可比较执行，不自动隔离或重跑。",
                )
            )
        for item in flaky.newly_confirmed:
            if item.projection_status is ProjectionStatus.STALE:
                continue
            items.append(
                P1AttentionItem(
                    attention_code="flaky_newly_confirmed",
                    level=AttentionLevel.ACTION_REQUIRED,
                    title="发现新的已确认 Flaky",
                    summary="状态机已确认波动，但这不是测试通过结论。",
                    source_name="flaky_evaluation",
                    related_ids=(item.flaky_key, item.latest_observation_id),
                    suggested_action="人工复核证据并决定是否创建 quarantine 治理项。",
                )
            )
        for item in flaky.overdue:
            items.append(
                P1AttentionItem(
                    attention_code="flaky_governance_overdue",
                    level=AttentionLevel.ACTION_REQUIRED,
                    title="Flaky 治理项已超期",
                    summary="隔离治理已超过计划到期时间，需要 owner 复核。",
                    owner=item.owner,
                    expires_at=item.expires_at,
                    source_name="flaky_evaluation",
                    related_ids=(item.flaky_key, item.governance_id or item.flaky_key),
                    suggested_action="owner 复核超期原因并决定恢复、延期或取消隔离。",
                )
            )
        if flaky.recovered:
            items.append(
                P1AttentionItem(
                    attention_code="flaky_recovered",
                    level=AttentionLevel.INFO,
                    title="Flaky 恢复证据已满足",
                    summary=f"本次有 {len(flaky.recovered)} 个治理项达到恢复条件。",
                    source_name="flaky_evaluation",
                    related_ids=tuple(item.flaky_key for item in flaky.recovered),
                    suggested_action="查看 transition 证据并确认治理生命周期已经正确收口。",
                )
            )
    deduplicated: dict[tuple[str, tuple[str, ...]], P1AttentionItem] = {}
    for item in items:
        deduplicated[(item.attention_code, item.related_ids)] = item
    level_order = {
        AttentionLevel.ACTION_REQUIRED: 0,
        AttentionLevel.REVIEW: 1,
        AttentionLevel.INFO: 2,
    }
    return tuple(
        sorted(
            deduplicated.values(),
            key=lambda item: (
                level_order[item.level],
                item.attention_code,
                item.related_ids,
            ),
        )
    )


def all_flaky_states(flaky: P1FlakySection) -> tuple[FlakyStateSummary, ...]:
    return (
        *flaky.newly_suspected,
        *flaky.newly_confirmed,
        *flaky.ongoing_confirmed,
        *flaky.quarantined,
        *flaky.recovering,
        *flaky.recovered,
        *flaky.overdue,
    )
