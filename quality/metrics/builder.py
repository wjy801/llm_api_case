from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from datetime import datetime

from quality.metrics_models import (
    Exclusions,
    MetricsIntegrity,
    MetricsIssue,
    RunMetricSummary,
    RunMetricsResult,
    RunMetricsStatus,
)
from quality.models import IntegrityStatus, IssueSeverity, RunStatus
from quality.semantic_models import (
    RecordCompleteness,
    RequestGroupRecord,
    TrafficRole,
    UsageCompleteness,
)

from .case import case_metrics
from .contracts import MetricsSources
from .operation import (
    operation_buckets,
    operation_stability,
    operation_timing,
    operation_usage,
)
from .request_event import (
    request_event_buckets,
    request_event_stability,
    request_event_timing,
)
from .request_group import (
    request_group_buckets,
    request_group_stability,
    request_group_timing,
)


def build_run_metrics(
    run_id: str,
    sources: MetricsSources,
    *,
    generated_at: datetime,
) -> RunMetricsResult:
    events = {item.request_event_id: item for item in sources.requests}
    event_owner = {
        event_id: group
        for group in sources.groups
        for event_id in group.attempt_event_ids
    }
    workload_operations = tuple(
        sorted(
            (
                item
                for item in sources.operations
                if item.traffic_role is TrafficRole.WORKLOAD
            ),
            key=lambda item: item.operation_id,
        )
    )
    workload_groups = tuple(
        sorted(
            (
                item
                for item in sources.groups
                if item.traffic_role is TrafficRole.WORKLOAD
            ),
            key=lambda item: item.request_group_id,
        )
    )
    workload_events = tuple(
        sorted(
            (
                item
                for item in sources.requests
                if (
                    (owner := event_owner.get(item.request_event_id)) is not None
                    and owner.traffic_role is TrafficRole.WORKLOAD
                )
            ),
            key=lambda item: item.request_event_id,
        )
    )
    exclusions = build_exclusions(sources, event_owner)
    issues, degraded_reasons = build_issues(sources, exclusions)
    status = metrics_status(
        sources.run.status,
        workload_operation_count=len(workload_operations),
        degraded_reasons=degraded_reasons,
    )
    issue_counts = Counter(issue.severity.value for issue in issues)
    integrity = MetricsIntegrity(
        p0_integrity_status=sources.p0_integrity_status,
        semantic_integrity_status=sources.semantic_integrity_status,
        issue_count=len(issues),
        error_count=issue_counts[IssueSeverity.ERROR.value],
        warning_count=issue_counts[IssueSeverity.WARN.value],
        degraded_reasons=tuple(degraded_reasons),
    )
    run_metrics = RunMetricSummary(
        operation=operation_stability(workload_operations),
        usage=operation_usage(workload_operations, events),
        operation_timing=operation_timing(workload_operations),
        request_groups=request_group_stability(workload_groups, events),
        request_group_timing=request_group_timing(workload_groups, events),
        request_events=request_event_stability(workload_events),
        request_event_timing=request_event_timing(workload_events),
    )
    return RunMetricsResult(
        run_id=run_id,
        status=status,
        generated_at=generated_at,
        run_status=sources.run.status,
        source_evidence=sources.evidence,
        integrity=integrity,
        exclusions=exclusions,
        run_metrics=run_metrics,
        case_invocations=case_metrics(run_id, workload_operations, events),
        operation_buckets=operation_buckets(
            run_id, workload_operations, events
        ),
        request_group_buckets=request_group_buckets(
            run_id, workload_groups, events, sources.operations
        ),
        request_event_buckets=request_event_buckets(
            run_id, workload_events, event_owner
        ),
        issues=issues,
    )


def build_exclusions(
    sources: MetricsSources,
    event_owner: dict[str, RequestGroupRecord],
) -> Exclusions:
    control_groups = tuple(
        item for item in sources.groups if item.traffic_role is TrafficRole.CONTROL
    )
    unknown_groups = tuple(
        item for item in sources.groups if item.traffic_role is TrafficRole.UNKNOWN
    )
    return Exclusions(
        control_operation_ids=tuple(
            item.operation_id
            for item in sources.operations
            if item.traffic_role is TrafficRole.CONTROL
        ),
        control_group_ids=tuple(item.request_group_id for item in control_groups),
        control_event_ids=tuple(
            event_id
            for item in control_groups
            for event_id in item.attempt_event_ids
        ),
        unknown_operation_ids=tuple(
            item.operation_id
            for item in sources.operations
            if item.traffic_role is TrafficRole.UNKNOWN
        ),
        unknown_group_ids=tuple(item.request_group_id for item in unknown_groups),
        unknown_event_ids=tuple(
            event_id
            for item in unknown_groups
            for event_id in item.attempt_event_ids
        ),
        not_applicable_usage_operation_ids=tuple(
            item.operation_id
            for item in sources.operations
            if item.usage.completeness is UsageCompleteness.NOT_APPLICABLE
        ),
        unassigned_event_ids=tuple(
            item.request_event_id
            for item in sources.requests
            if item.request_event_id not in event_owner
        ),
        foreign_run_count=0,
    )


def build_issues(
    sources: MetricsSources,
    exclusions: Exclusions,
) -> tuple[tuple[MetricsIssue, ...], tuple[str, ...]]:
    issues = [
        MetricsIssue(
            severity=item.severity,
            code=item.code,
            summary="semantic source reported an integrity issue",
            related_id=item.related_id,
        )
        for item in sources.semantic_issues
    ]
    reasons: set[str] = set()
    if sources.run.status is not RunStatus.FINISHED:
        reasons.add("run_not_finished")
    if sources.p0_integrity_status != IntegrityStatus.COMPLETE.value:
        reasons.add("p0_integrity_degraded")
    if sources.semantic_integrity_status != IntegrityStatus.COMPLETE.value:
        reasons.add("semantic_integrity_degraded")
    incomplete = [
        item.operation_id
        for item in sources.operations
        if item.completeness is RecordCompleteness.INCOMPLETE
    ]
    incomplete_usage = [
        item.operation_id
        for item in sources.operations
        if item.traffic_role is TrafficRole.WORKLOAD
        and item.usage.completeness
        in {UsageCompleteness.PARTIAL, UsageCompleteness.MISSING}
    ]
    categories: tuple[tuple[str, Sequence[str], str], ...] = (
        (
            "operation_incomplete",
            incomplete,
            "one or more operations are incomplete",
        ),
        (
            "usage_incomplete",
            incomplete_usage,
            "one or more workload operations have partial or missing usage",
        ),
        (
            "unassigned_request_events",
            exclusions.unassigned_event_ids,
            "one or more P0 request events have no semantic group",
        ),
        (
            "unknown_traffic_role",
            (
                *exclusions.unknown_operation_ids,
                *exclusions.unknown_group_ids,
                *exclusions.unknown_event_ids,
            ),
            "one or more semantic facts have an unknown traffic role",
        ),
    )
    for code, member_ids, summary in categories:
        if not member_ids:
            continue
        reasons.add(code)
        issues.append(
            MetricsIssue(
                severity=IssueSeverity.WARN,
                code=code,
                summary=summary,
                related_id=sorted(member_ids)[0],
            )
        )
    ordered = tuple(
        sorted(
            issues,
            key=lambda item: (
                item.severity.value,
                item.code,
                item.related_id or "",
                item.summary,
            ),
        )
    )
    return ordered, tuple(sorted(reasons))


def metrics_status(
    run_status: RunStatus,
    *,
    workload_operation_count: int,
    degraded_reasons: Sequence[str],
) -> RunMetricsStatus:
    if degraded_reasons or run_status is not RunStatus.FINISHED:
        return RunMetricsStatus.DEGRADED
    if workload_operation_count == 0:
        return RunMetricsStatus.NO_DATA
    return RunMetricsStatus.AGGREGATED
