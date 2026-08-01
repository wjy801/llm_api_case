from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
import math
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from quality.aggregator import MANIFEST_VERSION as P0_MANIFEST_VERSION
from quality.metrics_models import (
    RUN_METRICS_AGGREGATION_VERSION,
    RUN_METRICS_MANIFEST_VERSION,
    RUN_METRICS_SCHEMA_VERSION,
    ArtifactEvidence,
    CaseInvocationMetric,
    CountDistribution,
    EvidenceMembership,
    Exclusions,
    MetricCompleteness,
    MetricsIntegrity,
    MetricsIssue,
    NumericAggregate,
    OperationDimension,
    OperationMetricBucket,
    OperationStability,
    OperationTimingAggregate,
    OperationUsageAggregate,
    RatioAggregate,
    RequestEventDimension,
    RequestEventMetricBucket,
    RequestEventStability,
    RequestEventTimingAggregate,
    RequestEventUsageCoverage,
    RequestGroupDimension,
    RequestGroupMetricBucket,
    RequestGroupStability,
    RequestGroupTimingAggregate,
    RetryUsageAggregate,
    RunMetricSummary,
    RunMetricsResult,
    RunMetricsStatus,
    SourceEvidence,
    UsageAggregate,
)
from quality.models import (
    SCHEMA_VERSION,
    BusinessStatus,
    IntegrityStatus,
    IssueSeverity,
    RequestMetric,
    RunRecord,
    RunStatus,
)
from quality.semantic_models import (
    SEMANTIC_MANIFEST_VERSION,
    SEMANTIC_MERGE_VERSION,
    SEMANTIC_SCHEMA_VERSION,
    AttemptTransportOutcome,
    OperationKind,
    OperationOutcome,
    OperationRecord,
    PollingSessionRecord,
    RecordCompleteness,
    RequestGroupRecord,
    SemanticIntegrityIssue,
    TimingCompleteness,
    TrafficRole,
    UsageCompleteness,
)
from quality.storage import write_json_atomic


_T = TypeVar("_T", bound=BaseModel)
_SEMANTIC_OUTPUT_MODELS: dict[str, type[BaseModel]] = {
    "request-groups": RequestGroupRecord,
    "polling-sessions": PollingSessionRecord,
    "operations": OperationRecord,
    "integrity-issues": SemanticIntegrityIssue,
}


@dataclass(frozen=True)
class RunMetricsAggregationRequest:
    run_id: str
    output_dir: Path


@dataclass(frozen=True)
class RunMetricsAggregationResult:
    run_id: str
    output_dir: Path
    manifest_path: Path
    metrics_path: Path
    status: RunMetricsStatus
    operation_count: int
    request_group_count: int
    request_event_count: int
    issues: tuple[MetricsIssue, ...]
    metrics: RunMetricsResult | None = None


@dataclass(frozen=True)
class _Sources:
    run: RunRecord
    requests: tuple[RequestMetric, ...]
    groups: tuple[RequestGroupRecord, ...]
    sessions: tuple[PollingSessionRecord, ...]
    operations: tuple[OperationRecord, ...]
    semantic_issues: tuple[SemanticIntegrityIssue, ...]
    p0_integrity_status: str
    semantic_integrity_status: str
    evidence: SourceEvidence


class _MetricsSourceError(ValueError):
    def __init__(self, code: str, summary: str, related_id: str | None = None) -> None:
        super().__init__(summary)
        self.code = code
        self.summary = summary
        self.related_id = related_id


def numeric_aggregate(
    values: Iterable[int | float | None],
    *,
    decimals: int = 3,
    not_applicable: bool = False,
) -> NumericAggregate:
    observations = tuple(values)
    known = tuple(value for value in observations if value is not None)
    for value in known:
        if isinstance(value, bool) or not math.isfinite(float(value)) or value < 0:
            raise ValueError("numeric observations must be finite and nonnegative")
    eligible_count = len(observations)
    sample_size = len(known)
    missing_count = eligible_count - sample_size
    if sample_size == 0:
        completeness = (
            MetricCompleteness.NOT_APPLICABLE
            if not_applicable and eligible_count == 0
            else MetricCompleteness.NO_DATA
        )
        return NumericAggregate(
            eligible_count=eligible_count,
            sample_size=0,
            missing_count=missing_count,
            total=None,
            mean=None,
            minimum=None,
            maximum=None,
            completeness=completeness,
        )
    ordered = tuple(sorted(known, key=float))
    all_integers = all(isinstance(value, int) and not isinstance(value, bool) for value in ordered)
    total: int | float
    if all_integers:
        total = sum(int(value) for value in ordered)
        mean = round(float(total) / sample_size, decimals)
        minimum: int | float = min(ordered)
        maximum: int | float = max(ordered)
    else:
        raw_total = math.fsum(float(value) for value in ordered)
        total = round(raw_total, decimals)
        mean = round(raw_total / sample_size, decimals)
        minimum = round(float(min(ordered)), decimals)
        maximum = round(float(max(ordered)), decimals)
    return NumericAggregate(
        eligible_count=eligible_count,
        sample_size=sample_size,
        missing_count=missing_count,
        total=total,
        mean=mean,
        minimum=minimum,
        maximum=maximum,
        completeness=(
            MetricCompleteness.COMPLETE
            if missing_count == 0
            else MetricCompleteness.PARTIAL
        ),
    )


def ratio_aggregate(values: Iterable[bool | None]) -> RatioAggregate:
    observations = tuple(values)
    known = tuple(value for value in observations if value is not None)
    numerator = sum(value is True for value in known)
    sample_size = len(known)
    unknown_count = len(observations) - sample_size
    if sample_size == 0:
        value = None
        completeness = MetricCompleteness.NO_DATA
    else:
        value = round(numerator / sample_size, 6)
        completeness = (
            MetricCompleteness.PARTIAL
            if unknown_count
            else MetricCompleteness.COMPLETE
        )
    return RatioAggregate(
        numerator=numerator,
        sample_size=sample_size,
        unknown_count=unknown_count,
        value=value,
        completeness=completeness,
    )


def count_distribution(
    values: Iterable[str], *, unknown_count: int = 0
) -> CountDistribution:
    counter = Counter(values)
    return CountDistribution(
        sample_size=sum(counter.values()),
        counts=dict(sorted(counter.items())),
        unknown_count=unknown_count,
    )


def metric_bucket_id(run_id: str, grain: str, dimension: dict[str, Any]) -> str:
    payload = {
        "aggregation_version": RUN_METRICS_AGGREGATION_VERSION,
        "dimension": dimension,
        "grain": grain,
        "run_id": run_id,
        "schema_version": RUN_METRICS_SCHEMA_VERSION,
    }
    canonical = json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def aggregate_run_metrics(
    request: RunMetricsAggregationRequest,
) -> RunMetricsAggregationResult:
    run_id = request.run_id.strip()
    if not run_id:
        raise ValueError("run_id must not be empty")
    output_dir = Path(request.output_dir)
    metrics_dir = output_dir / "metrics"
    manifest_path = metrics_dir / "manifest.json"
    metrics_path = metrics_dir / "run-metrics.json"
    created_at = datetime.now(UTC)
    _write_manifest(
        manifest_path,
        run_id=run_id,
        write_status="aggregating",
        metrics_status=None,
        created_at=created_at,
        source_evidence=None,
        output_hashes={},
        output_counts={},
        issues=(),
    )
    try:
        sources = _load_sources(run_id, output_dir)
        metrics = _aggregate_sources(run_id, sources, generated_at=datetime.now(UTC))
        write_json_atomic(metrics_path, metrics)
        output_hash = _file_sha256(metrics_path)
        counts = {
            "case_invocations": len(metrics.case_invocations),
            "operation_buckets": len(metrics.operation_buckets),
            "request_group_buckets": len(metrics.request_group_buckets),
            "request_event_buckets": len(metrics.request_event_buckets),
            "workload_operations": (
                metrics.run_metrics.operation.operation_count
                if metrics.run_metrics is not None
                else 0
            ),
            "workload_request_groups": (
                metrics.run_metrics.request_groups.group_count
                if metrics.run_metrics is not None
                else 0
            ),
            "workload_request_events": (
                metrics.run_metrics.request_events.event_count
                if metrics.run_metrics is not None
                else 0
            ),
            "issues": len(metrics.issues),
        }
        _write_manifest(
            manifest_path,
            run_id=run_id,
            write_status="complete",
            metrics_status=metrics.status,
            created_at=created_at,
            source_evidence=metrics.source_evidence,
            output_hashes={"run_metrics": output_hash},
            output_counts=counts,
            issues=metrics.issues,
        )
        return RunMetricsAggregationResult(
            run_id=run_id,
            output_dir=output_dir,
            manifest_path=manifest_path,
            metrics_path=metrics_path,
            status=metrics.status,
            operation_count=counts["workload_operations"],
            request_group_count=counts["workload_request_groups"],
            request_event_count=counts["workload_request_events"],
            issues=metrics.issues,
            metrics=metrics,
        )
    except Exception as error:
        issue = _failure_issue(error)
        try:
            _write_manifest(
                manifest_path,
                run_id=run_id,
                write_status="failed",
                metrics_status=RunMetricsStatus.FAILED,
                created_at=created_at,
                source_evidence=None,
                output_hashes={},
                output_counts={},
                issues=(issue,),
            )
        except Exception:
            pass
        return RunMetricsAggregationResult(
            run_id=run_id,
            output_dir=output_dir,
            manifest_path=manifest_path,
            metrics_path=metrics_path,
            status=RunMetricsStatus.FAILED,
            operation_count=0,
            request_group_count=0,
            request_event_count=0,
            issues=(issue,),
        )


def _load_sources(run_id: str, output_dir: Path) -> _Sources:
    run_path = output_dir / "run.json"
    p0_manifest_path = output_dir / "merged" / "manifest.json"
    request_metrics_path = output_dir / "merged" / "request-metrics.jsonl"
    semantic_dir = output_dir / "semantic" / "merged"
    semantic_manifest_path = semantic_dir / "manifest.json"

    run = _read_model(run_path, RunRecord, "run_record_invalid")
    if run.run_id != run_id:
        _source_error("run_id_mismatch", "run.json belongs to a different run", run.run_id)

    p0_manifest = _read_json_object(p0_manifest_path, "p0_manifest_invalid")
    _require_manifest(
        p0_manifest,
        run_id=run_id,
        status="complete",
        versions={
            "manifest_version": P0_MANIFEST_VERSION,
            "schema_version": SCHEMA_VERSION,
        },
        code_prefix="p0",
    )
    p0_integrity_status = str(p0_manifest.get("integrity_status") or "unknown")
    if p0_integrity_status == IntegrityStatus.FAILED.value:
        _source_error("p0_integrity_failed", "P0 merged facts failed integrity validation")
    p0_request_hash = _validated_output_hash(
        request_metrics_path,
        (p0_manifest.get("output_hashes") or {}).get("request-metrics"),
        "p0_request_metrics",
    )
    requests = tuple(
        _read_jsonl_models(request_metrics_path, RequestMetric, "p0_request_metric_invalid")
    )

    semantic_manifest = _read_json_object(
        semantic_manifest_path, "semantic_manifest_invalid"
    )
    _require_manifest(
        semantic_manifest,
        run_id=run_id,
        status="complete",
        versions={
            "manifest_version": SEMANTIC_MANIFEST_VERSION,
            "schema_version": SEMANTIC_SCHEMA_VERSION,
            "merge_version": SEMANTIC_MERGE_VERSION,
        },
        code_prefix="semantic",
    )
    semantic_integrity_status = str(
        semantic_manifest.get("integrity_status") or "unknown"
    )
    if semantic_integrity_status == IntegrityStatus.FAILED.value:
        _source_error(
            "semantic_integrity_failed",
            "semantic merged facts failed integrity validation",
        )
    p0_manifest_hash = _file_sha256(p0_manifest_path)
    semantic_p0 = semantic_manifest.get("p0_evidence") or {}
    if semantic_p0.get("manifest_sha256") != p0_manifest_hash:
        _source_error(
            "semantic_p0_manifest_evidence_mismatch",
            "semantic facts reference a different P0 manifest",
        )
    if semantic_p0.get("request_metrics_sha256") != p0_request_hash:
        _source_error(
            "semantic_p0_request_evidence_mismatch",
            "semantic facts reference different P0 request metrics",
        )

    parsed: dict[str, tuple[BaseModel, ...]] = {}
    semantic_evidence: dict[str, ArtifactEvidence] = {}
    for name, model in _SEMANTIC_OUTPUT_MODELS.items():
        path = semantic_dir / f"{name}.jsonl"
        digest = _validated_output_hash(
            path,
            (semantic_manifest.get("output_hashes") or {}).get(name),
            f"semantic_{name.replace('-', '_')}",
        )
        parsed[name] = tuple(
            _read_jsonl_models(path, model, f"semantic_{name.replace('-', '_')}_invalid")
        )
        semantic_evidence[name] = ArtifactEvidence(
            path=_relative_path(path, output_dir),
            sha256=digest,
            schema_version=SEMANTIC_SCHEMA_VERSION,
        )

    sources = _Sources(
        run=run,
        requests=tuple(item for item in requests if isinstance(item, RequestMetric)),
        groups=tuple(
            item for item in parsed["request-groups"] if isinstance(item, RequestGroupRecord)
        ),
        sessions=tuple(
            item
            for item in parsed["polling-sessions"]
            if isinstance(item, PollingSessionRecord)
        ),
        operations=tuple(
            item for item in parsed["operations"] if isinstance(item, OperationRecord)
        ),
        semantic_issues=tuple(
            item
            for item in parsed["integrity-issues"]
            if isinstance(item, SemanticIntegrityIssue)
        ),
        p0_integrity_status=p0_integrity_status,
        semantic_integrity_status=semantic_integrity_status,
        evidence=SourceEvidence(
            p0_manifest=ArtifactEvidence(
                path=_relative_path(p0_manifest_path, output_dir),
                sha256=p0_manifest_hash,
                schema_version=SCHEMA_VERSION,
                manifest_version=P0_MANIFEST_VERSION,
                merge_version=str(p0_manifest.get("merge_version") or "unknown"),
            ),
            p0_request_metrics=ArtifactEvidence(
                path=_relative_path(request_metrics_path, output_dir),
                sha256=p0_request_hash,
                schema_version=SCHEMA_VERSION,
            ),
            semantic_manifest=ArtifactEvidence(
                path=_relative_path(semantic_manifest_path, output_dir),
                sha256=_file_sha256(semantic_manifest_path),
                schema_version=SEMANTIC_SCHEMA_VERSION,
                manifest_version=SEMANTIC_MANIFEST_VERSION,
                merge_version=SEMANTIC_MERGE_VERSION,
            ),
            semantic_outputs=semantic_evidence,
        ),
    )
    _validate_source_relationships(run_id, sources)
    return sources


def _validate_source_relationships(run_id: str, sources: _Sources) -> None:
    events = _unique_index(
        sources.requests,
        lambda item: item.request_event_id,
        "request_event_duplicate",
    )
    groups = _unique_index(
        sources.groups,
        lambda item: item.request_group_id,
        "request_group_duplicate",
    )
    sessions = _unique_index(
        sources.sessions,
        lambda item: item.polling_session_id,
        "polling_session_duplicate",
    )
    operations = _unique_index(
        sources.operations,
        lambda item: item.operation_id,
        "operation_duplicate",
    )
    for record in (*sources.requests, *sources.groups, *sources.sessions, *sources.operations):
        if record.run_id != run_id:
            _source_error("foreign_run_record", "a source record belongs to another run")

    event_owner: dict[str, str] = {}
    for group in sources.groups:
        operation = operations.get(group.operation_id)
        if operation is None:
            _source_error(
                "group_operation_missing",
                "request group references a missing operation",
                group.request_group_id,
            )
        if group.request_group_id not in operation.request_group_ids:
            _source_error(
                "group_operation_reference_mismatch",
                "request group is absent from its operation references",
                group.request_group_id,
            )
        _require_identity_match(group, operation, group.request_group_id)
        if group.final_request_event_id != group.attempt_event_ids[-1]:
            _source_error(
                "final_request_event_invalid",
                "request group final event is not its last attempt",
                group.request_group_id,
            )
        for expected_index, event_id in enumerate(group.attempt_event_ids, start=1):
            previous = event_owner.setdefault(event_id, group.request_group_id)
            if previous != group.request_group_id:
                _source_error(
                    "request_event_multiple_groups",
                    "one request event belongs to multiple request groups",
                    event_id,
                )
            event = events.get(event_id)
            if event is None:
                _source_error(
                    "request_event_missing",
                    "request group references a missing request event",
                    event_id,
                )
            _require_identity_match(group, event, event_id)
            if event.attempt_index != expected_index:
                _source_error(
                    "attempt_index_sequence_invalid",
                    "request attempt indexes are not continuous",
                    group.request_group_id,
                )
            if (
                event.interface_id != group.interface_id
                or event.protocol is not group.protocol
                or event.method != group.method
                or event.url_template != group.url_template
            ):
                _source_error(
                    "group_event_interface_mismatch",
                    "request group and event interface identity differ",
                    event_id,
                )
        first_event = events[group.attempt_event_ids[0]]
        final_event = events[group.final_request_event_id]
        if (
            group.first_transport_outcome is not _event_transport_outcome(first_event)
            or group.final_transport_outcome is not _event_transport_outcome(final_event)
            or group.first_status_code != first_event.status_code
            or group.final_status_code != final_event.status_code
        ):
            _source_error(
                "group_outcome_evidence_mismatch",
                "request group first/final outcome differs from its event evidence",
                group.request_group_id,
            )

    group_owner: dict[str, str] = {}
    session_owner: dict[str, str] = {}
    usage_owner: dict[str, str] = {}
    for operation in sources.operations:
        operation_event_ids: set[str] = set()
        for group_id in operation.request_group_ids:
            previous = group_owner.setdefault(group_id, operation.operation_id)
            if previous != operation.operation_id:
                _source_error(
                    "request_group_multiple_operations",
                    "one request group belongs to multiple operations",
                    group_id,
                )
            group = groups.get(group_id)
            if group is None:
                _source_error(
                    "operation_group_missing",
                    "operation references a missing request group",
                    group_id,
                )
            if group.operation_id != operation.operation_id:
                _source_error(
                    "operation_group_identity_mismatch",
                    "operation and request group ids disagree",
                    group_id,
                )
            _require_identity_match(operation, group, group_id)
            operation_event_ids.update(group.attempt_event_ids)
        for session_id in operation.polling_session_ids:
            previous = session_owner.setdefault(session_id, operation.operation_id)
            if previous != operation.operation_id:
                _source_error(
                    "polling_session_multiple_operations",
                    "one polling session belongs to multiple operations",
                    session_id,
                )
            session = sessions.get(session_id)
            if session is None:
                _source_error(
                    "operation_polling_session_missing",
                    "operation references a missing polling session",
                    session_id,
                )
            if session.operation_id != operation.operation_id:
                _source_error(
                    "operation_polling_identity_mismatch",
                    "operation and polling session ids disagree",
                    session_id,
                )
            _require_identity_match(operation, session, session_id)
        usage_ids = (
            *operation.usage.source_request_event_ids,
            *operation.usage.missing_request_event_ids,
        )
        if len(set(usage_ids)) != len(usage_ids):
            _source_error(
                "usage_evidence_overlap",
                "operation usage known/missing evidence overlaps",
                operation.operation_id,
            )
        for event_id in usage_ids:
            if event_id not in operation_event_ids or event_id not in events:
                _source_error(
                    "usage_event_outside_operation",
                    "usage evidence is outside its operation",
                    event_id,
                )
            previous = usage_owner.setdefault(event_id, operation.operation_id)
            if previous != operation.operation_id:
                _source_error(
                    "usage_event_multiple_operations",
                    "one usage event belongs to multiple operations",
                    event_id,
                )

    if set(groups) != set(group_owner):
        orphan = sorted(set(groups) - set(group_owner))[0]
        _source_error(
            "request_group_unassigned",
            "a semantic request group has no owning operation",
            orphan,
        )

    for session in sources.sessions:
        operation = operations.get(session.operation_id)
        if operation is None or session.polling_session_id not in operation.polling_session_ids:
            _source_error(
                "polling_session_unassigned",
                "a polling session has no owning operation",
                session.polling_session_id,
            )
        for group_id in session.request_group_ids:
            group = groups.get(group_id)
            if group is None:
                _source_error(
                    "polling_group_missing",
                    "polling session references a missing request group",
                    group_id,
                )
            if (
                group.polling_session_id != session.polling_session_id
                or group.operation_id != session.operation_id
            ):
                _source_error(
                    "polling_group_identity_mismatch",
                    "polling session and request group identity differ",
                    group_id,
                )
            _require_identity_match(session, group, group_id)


def _aggregate_sources(
    run_id: str,
    sources: _Sources,
    *,
    generated_at: datetime,
) -> RunMetricsResult:
    events = {item.request_event_id: item for item in sources.requests}
    groups = {item.request_group_id: item for item in sources.groups}
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
    exclusions = _build_exclusions(sources, event_owner)
    issues, degraded_reasons = _build_issues(sources, exclusions)
    status = _metrics_status(
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
    operation_stability = _operation_stability(workload_operations)
    operation_usage = _operation_usage(workload_operations, events)
    operation_timing = _operation_timing(workload_operations)
    group_stability = _request_group_stability(workload_groups, events)
    group_timing = _request_group_timing(workload_groups, events)
    event_stability = _request_event_stability(workload_events)
    event_timing = _request_event_timing(workload_events)
    run_metrics = RunMetricSummary(
        operation=operation_stability,
        usage=operation_usage,
        operation_timing=operation_timing,
        request_groups=group_stability,
        request_group_timing=group_timing,
        request_events=event_stability,
        request_event_timing=event_timing,
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
        case_invocations=_case_metrics(run_id, workload_operations, events),
        operation_buckets=_operation_buckets(run_id, workload_operations, events),
        request_group_buckets=_request_group_buckets(
            run_id, workload_groups, events, sources.operations
        ),
        request_event_buckets=_request_event_buckets(run_id, workload_events, event_owner),
        issues=issues,
    )


def _build_exclusions(
    sources: _Sources,
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
            event_id for item in control_groups for event_id in item.attempt_event_ids
        ),
        unknown_operation_ids=tuple(
            item.operation_id
            for item in sources.operations
            if item.traffic_role is TrafficRole.UNKNOWN
        ),
        unknown_group_ids=tuple(item.request_group_id for item in unknown_groups),
        unknown_event_ids=tuple(
            event_id for item in unknown_groups for event_id in item.attempt_event_ids
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


def _build_issues(
    sources: _Sources,
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
        and item.usage.completeness in {UsageCompleteness.PARTIAL, UsageCompleteness.MISSING}
    ]
    categories: tuple[tuple[str, Sequence[str], str], ...] = (
        ("operation_incomplete", incomplete, "one or more operations are incomplete"),
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


def _metrics_status(
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


def _operation_stability(
    operations: Sequence[OperationRecord],
) -> OperationStability:
    outcomes = tuple(item.outcome.value for item in operations)
    completeness = tuple(item.completeness.value for item in operations)
    return OperationStability(
        operation_count=len(operations),
        outcomes=count_distribution(outcomes),
        success_rate=ratio_aggregate(
            item.outcome is OperationOutcome.SUCCESS for item in operations
        ),
        timeout_rate=ratio_aggregate(
            item.outcome is OperationOutcome.TIMEOUT for item in operations
        ),
        incomplete_or_unknown_count=sum(
            item.outcome in {OperationOutcome.INCOMPLETE, OperationOutcome.UNKNOWN}
            for item in operations
        ),
        record_completeness=count_distribution(completeness),
    )


def _operation_usage(
    operations: Sequence[OperationRecord],
    events: dict[str, RequestMetric],
) -> OperationUsageAggregate:
    applicable = tuple(
        item for item in operations if item.usage.completeness is not UsageCompleteness.NOT_APPLICABLE
    )
    return OperationUsageAggregate(
        completeness=count_distribution(
            item.usage.completeness.value for item in operations
        ),
        input_tokens=_known_field_aggregate(applicable, lambda item: item.usage.input_tokens),
        output_tokens=_known_field_aggregate(applicable, lambda item: item.usage.output_tokens),
        media_count=_known_field_aggregate(applicable, lambda item: item.usage.media_count),
        media_duration_ms=_known_field_aggregate(
            applicable, lambda item: item.usage.media_duration_ms
        ),
        known_source_event_count=sum(
            len(item.usage.source_request_event_ids) for item in applicable
        ),
        missing_source_event_count=sum(
            len(item.usage.missing_request_event_ids) for item in applicable
        ),
        retry_extra_usage=_retry_usage(operations, events),
    )


def _known_field_aggregate(
    items: Sequence[_T],
    getter: Callable[[_T], int | float | None],
) -> NumericAggregate:
    known = tuple(value for item in items if (value := getter(item)) is not None)
    return numeric_aggregate(known, not_applicable=not items)


def _retry_usage(
    operations: Sequence[OperationRecord],
    events: dict[str, RequestMetric],
    *,
    allowed_event_ids: set[str] | None = None,
) -> RetryUsageAggregate:
    first_events: list[RequestMetric] = []
    retry_events: list[RequestMetric] = []
    retry_missing = 0
    for operation in operations:
        for event_id in operation.usage.source_request_event_ids:
            if allowed_event_ids is not None and event_id not in allowed_event_ids:
                continue
            event = events[event_id]
            (first_events if event.attempt_index == 1 else retry_events).append(event)
        retry_missing += sum(
            events[event_id].attempt_index > 1
            for event_id in operation.usage.missing_request_event_ids
            if allowed_event_ids is None or event_id in allowed_event_ids
        )
    return RetryUsageAggregate(
        first_attempt_input_tokens=_event_known_usage(first_events, "input_tokens"),
        first_attempt_output_tokens=_event_known_usage(first_events, "output_tokens"),
        first_attempt_media_count=_event_known_usage(first_events, "media_count"),
        retry_input_tokens=_event_known_usage(retry_events, "input_tokens"),
        retry_output_tokens=_event_known_usage(retry_events, "output_tokens"),
        retry_media_count=_event_known_usage(retry_events, "media_count"),
        retry_missing_attempt_count=retry_missing,
    )


def _event_known_usage(
    events: Sequence[RequestMetric], field: str
) -> NumericAggregate:
    values = tuple(
        value
        for item in events
        if (value := getattr(item.usage, field)) is not None
    )
    return numeric_aggregate(values, not_applicable=not events)


def _operation_timing(
    operations: Sequence[OperationRecord],
) -> OperationTimingAggregate:
    total = tuple(item.timing.total_duration_ms for item in operations)
    successful = tuple(
        item.timing.total_duration_ms
        for item in operations
        if item.outcome is OperationOutcome.SUCCESS
    )
    unsuccessful = tuple(
        item.timing.total_duration_ms
        for item in operations
        if item.outcome is not OperationOutcome.SUCCESS
    )
    header_eligible = tuple(
        item
        for item in operations
        if item.operation_kind in {OperationKind.HTTP, OperationKind.SSE}
    )
    sse = tuple(item for item in operations if item.operation_kind is OperationKind.SSE)
    async_items = tuple(
        item for item in operations if item.operation_kind is OperationKind.ASYNC_TASK
    )
    return OperationTimingAggregate(
        total_duration_ms=numeric_aggregate(total, not_applicable=not total),
        success_total_duration_ms=numeric_aggregate(
            successful, not_applicable=not successful
        ),
        unsuccessful_total_duration_ms=numeric_aggregate(
            unsuccessful, not_applicable=not unsuccessful
        ),
        response_headers_ms=numeric_aggregate(
            (item.timing.response_headers_ms for item in header_eligible),
            not_applicable=not header_eligible,
        ),
        first_data_ms=numeric_aggregate(
            (item.timing.first_data_ms for item in sse),
            not_applicable=not sse,
        ),
        first_content_ms=numeric_aggregate(
            (item.timing.first_content_ms for item in sse),
            not_applicable=not sse,
        ),
        stream_duration_ms=numeric_aggregate(
            (item.timing.stream_duration_ms for item in sse),
            not_applicable=not sse,
        ),
        create_request_ms=numeric_aggregate(
            (item.timing.create_request_ms for item in async_items),
            not_applicable=not async_items,
        ),
        polling_total_ms=numeric_aggregate(
            (item.timing.polling_total_ms for item in async_items),
            not_applicable=not async_items,
        ),
        polling_sleep_ms=numeric_aggregate(
            (item.timing.polling_sleep_ms for item in async_items),
            not_applicable=not async_items,
        ),
        timing_completeness=count_distribution(
            item.timing.timing_completeness.value for item in operations
        ),
    )


def _request_group_stability(
    groups: Sequence[RequestGroupRecord],
    events: dict[str, RequestMetric],
) -> RequestGroupStability:
    first_events = tuple(events[item.attempt_event_ids[0]] for item in groups)
    final_events = tuple(events[item.final_request_event_id] for item in groups)
    retried = tuple(item for item in groups if item.attempt_count > 1)
    first_business = tuple(_business_success(item) for item in first_events)
    final_business = tuple(_business_success(item) for item in final_events)
    business_pairs = tuple(
        (_business_success(events[item.attempt_event_ids[0]]),
         _business_success(events[item.final_request_event_id]))
        for item in retried
    )
    return RequestGroupStability(
        group_count=len(groups),
        attempt_count=numeric_aggregate(tuple(item.attempt_count for item in groups)),
        retried_group_count=len(retried),
        retry_rate=ratio_aggregate(item.attempt_count > 1 for item in groups),
        first_transport=count_distribution(
            item.first_transport_outcome.value for item in groups
        ),
        final_transport=count_distribution(
            item.final_transport_outcome.value for item in groups
        ),
        first_transport_response_rate=ratio_aggregate(
            item.first_transport_outcome is AttemptTransportOutcome.RESPONSE
            for item in groups
        ),
        final_transport_response_rate=ratio_aggregate(
            item.final_transport_outcome is AttemptTransportOutcome.RESPONSE
            for item in groups
        ),
        first_http_success_rate=ratio_aggregate(_http_success(item) for item in first_events),
        final_http_success_rate=ratio_aggregate(_http_success(item) for item in final_events),
        first_business_success_rate=ratio_aggregate(first_business),
        final_business_success_rate=ratio_aggregate(final_business),
        http_retry_rescue_rate=ratio_aggregate(
            (not _http_success(events[item.attempt_event_ids[0]]))
            and _http_success(events[item.final_request_event_id])
            for item in retried
        ),
        business_retry_rescue_rate=ratio_aggregate(
            None if first is None or final is None else (not first and final)
            for first, final in business_pairs
        ),
    )


def _request_group_timing(
    groups: Sequence[RequestGroupRecord],
    events: dict[str, RequestMetric],
) -> RequestGroupTimingAggregate:
    retry_attempt_durations = tuple(
        events[event_id].duration_ms
        for group in groups
        for event_id in group.attempt_event_ids[1:]
    )
    return RequestGroupTimingAggregate(
        total_duration_ms=numeric_aggregate(
            tuple(item.total_duration_ms for item in groups),
            not_applicable=not groups,
        ),
        retry_wait_ms=numeric_aggregate(
            tuple(item.retry_wait_ms for item in groups),
            not_applicable=not groups,
        ),
        first_attempt_duration_ms=numeric_aggregate(
            tuple(events[item.attempt_event_ids[0]].duration_ms for item in groups),
            not_applicable=not groups,
        ),
        retry_attempt_duration_ms=numeric_aggregate(
            retry_attempt_durations,
            not_applicable=not retry_attempt_durations,
        ),
    )


def _request_event_stability(
    events: Sequence[RequestMetric],
) -> RequestEventStability:
    transports = tuple(_transport_category(item) for item in events)
    return RequestEventStability(
        event_count=len(events),
        transport=count_distribution(transports),
        business_status=count_distribution(item.business_status.value for item in events),
        timeout_rate=ratio_aggregate(item.timeout for item in events),
        http_5xx_rate=ratio_aggregate(
            item.status_code is not None and 500 <= item.status_code < 600
            for item in events
        ),
        http_429_rate=ratio_aggregate(item.status_code == 429 for item in events),
        business_success_rate=ratio_aggregate(_business_success(item) for item in events),
        http_429_count=sum(item.status_code == 429 for item in events),
    )


def _request_event_timing(
    events: Sequence[RequestMetric],
) -> RequestEventTimingAggregate:
    by_transport: dict[str, list[float]] = defaultdict(list)
    for event in events:
        by_transport[_transport_category(event)].append(event.duration_ms)
    aggregate = lambda category: numeric_aggregate(  # noqa: E731
        tuple(by_transport[category]), not_applicable=not by_transport[category]
    )
    return RequestEventTimingAggregate(
        all_duration_ms=numeric_aggregate(
            tuple(item.duration_ms for item in events), not_applicable=not events
        ),
        timeout_duration_ms=aggregate("timeout"),
        transport_error_duration_ms=aggregate("transport_error"),
        http_2xx_duration_ms=aggregate("http_2xx"),
        http_3xx_duration_ms=aggregate("http_3xx"),
        http_4xx_duration_ms=aggregate("http_4xx"),
        http_5xx_duration_ms=aggregate("http_5xx"),
    )


def _request_event_usage_coverage(
    events: Sequence[RequestMetric],
) -> RequestEventUsageCoverage:
    known = tuple(item for item in events if _event_has_usage(item))
    return RequestEventUsageCoverage(
        known_event_count=len(known),
        missing_event_count=len(events) - len(known),
        input_tokens=_event_known_usage(events, "input_tokens"),
        output_tokens=_event_known_usage(events, "output_tokens"),
        media_count=_event_known_usage(events, "media_count"),
    )


def _http_success(event: RequestMetric) -> bool:
    return event.status_code is not None and 200 <= event.status_code < 300


def _business_success(event: RequestMetric) -> bool | None:
    if event.business_status is BusinessStatus.UNKNOWN:
        return None
    return event.business_status is BusinessStatus.SUCCESS


def _transport_category(event: RequestMetric) -> str:
    if event.timeout:
        return "timeout"
    if event.status_code is None:
        return "transport_error"
    if 200 <= event.status_code < 300:
        return "http_2xx"
    if 300 <= event.status_code < 400:
        return "http_3xx"
    if 400 <= event.status_code < 500:
        return "http_4xx"
    if 500 <= event.status_code < 600:
        return "http_5xx"
    return "http_other"


def _event_transport_outcome(event: RequestMetric) -> AttemptTransportOutcome:
    if event.timeout:
        return AttemptTransportOutcome.TIMEOUT
    if event.status_code is None:
        return AttemptTransportOutcome.ERROR
    return AttemptTransportOutcome.RESPONSE


def _event_has_usage(event: RequestMetric) -> bool:
    return any(
        value is not None
        for value in (
            event.usage.input_tokens,
            event.usage.output_tokens,
            event.usage.media_count,
        )
    )


def _case_metrics(
    run_id: str,
    operations: Sequence[OperationRecord],
    events: dict[str, RequestMetric],
) -> tuple[CaseInvocationMetric, ...]:
    partitions: dict[tuple[str, str], list[OperationRecord]] = defaultdict(list)
    for operation in operations:
        partitions[(operation.case_id, operation.invocation_id)].append(operation)
    metrics: list[CaseInvocationMetric] = []
    for (case_id, invocation_id), members in sorted(partitions.items()):
        ordered = tuple(sorted(members, key=lambda item: item.operation_id))
        dimension = {"case_id": case_id, "invocation_id": invocation_id}
        bucket_id = metric_bucket_id(run_id, "case_invocation", dimension)
        metrics.append(
            CaseInvocationMetric(
                case_id=case_id,
                invocation_id=invocation_id,
                operation_count=len(ordered),
                outcomes=count_distribution(item.outcome.value for item in ordered),
                operation_success_rate=ratio_aggregate(
                    item.outcome is OperationOutcome.SUCCESS for item in ordered
                ),
                usage=_operation_usage(ordered, events),
                operation_duration_ms=numeric_aggregate(
                    tuple(item.timing.total_duration_ms for item in ordered)
                ),
                model_ids=count_distribution(
                    item.model_id if item.model_id is not None else "(none)"
                    for item in ordered
                ),
                operation_kinds=count_distribution(
                    item.operation_kind.value for item in ordered
                ),
                evidence=_membership(
                    bucket_id,
                    tuple(item.operation_id for item in ordered),
                    ("semantic/merged/operations.jsonl",),
                ),
            )
        )
    return tuple(metrics)


def _operation_buckets(
    run_id: str,
    operations: Sequence[OperationRecord],
    events: dict[str, RequestMetric],
) -> tuple[OperationMetricBucket, ...]:
    partitions: dict[tuple[str, str, str, str | None], list[OperationRecord]] = defaultdict(list)
    for item in operations:
        partitions[
            (
                item.operation_kind.value,
                item.operation_name,
                item.traffic_role.value,
                item.model_id,
            )
        ].append(item)
    buckets: list[OperationMetricBucket] = []
    for key, members in sorted(partitions.items(), key=lambda pair: _canonical_key(pair[0])):
        kind, name, role, model_id = key
        dimension = OperationDimension(
            operation_kind=kind,
            operation_name=name,
            traffic_role=role,
            model_id=model_id,
        )
        bucket_id = metric_bucket_id(
            run_id, "operation", dimension.model_dump(mode="json")
        )
        ordered = tuple(sorted(members, key=lambda item: item.operation_id))
        buckets.append(
            OperationMetricBucket(
                dimension=dimension,
                stability=_operation_stability(ordered),
                usage=_operation_usage(ordered, events),
                timing=_operation_timing(ordered),
                evidence=_membership(
                    bucket_id,
                    tuple(item.operation_id for item in ordered),
                    ("semantic/merged/operations.jsonl",),
                ),
            )
        )
    return tuple(buckets)


def _request_group_buckets(
    run_id: str,
    groups: Sequence[RequestGroupRecord],
    events: dict[str, RequestMetric],
    operations: Sequence[OperationRecord],
) -> tuple[RequestGroupMetricBucket, ...]:
    partitions: dict[tuple[str, str, str], list[RequestGroupRecord]] = defaultdict(list)
    for item in groups:
        partitions[(item.interface_id, item.protocol.value, item.traffic_role.value)].append(item)
    operation_by_group = {
        group_id: operation
        for operation in operations
        for group_id in operation.request_group_ids
    }
    buckets: list[RequestGroupMetricBucket] = []
    for key, members in sorted(partitions.items(), key=lambda pair: _canonical_key(pair[0])):
        interface_id, protocol, role = key
        dimension = RequestGroupDimension(
            interface_id=interface_id,
            protocol=protocol,
            traffic_role=role,
        )
        bucket_id = metric_bucket_id(
            run_id, "request_group", dimension.model_dump(mode="json")
        )
        ordered = tuple(sorted(members, key=lambda item: item.request_group_id))
        member_operations = tuple(
            {
                operation.operation_id: operation
                for group in ordered
                if (operation := operation_by_group.get(group.request_group_id)) is not None
            }.values()
        )
        buckets.append(
            RequestGroupMetricBucket(
                dimension=dimension,
                stability=_request_group_stability(ordered, events),
                timing=_request_group_timing(ordered, events),
                retry_usage=_retry_usage(
                    member_operations,
                    events,
                    allowed_event_ids={
                        event_id for group in ordered for event_id in group.attempt_event_ids
                    },
                ),
                evidence=_membership(
                    bucket_id,
                    tuple(item.request_group_id for item in ordered),
                    (
                        "semantic/merged/request-groups.jsonl",
                        "merged/request-metrics.jsonl",
                    ),
                ),
            )
        )
    return tuple(buckets)


def _request_event_buckets(
    run_id: str,
    events: Sequence[RequestMetric],
    owners: dict[str, RequestGroupRecord],
) -> tuple[RequestEventMetricBucket, ...]:
    partitions: dict[tuple[str, str, str], list[RequestMetric]] = defaultdict(list)
    for item in events:
        owner = owners[item.request_event_id]
        partitions[(item.interface_id, item.protocol.value, owner.traffic_role.value)].append(item)
    buckets: list[RequestEventMetricBucket] = []
    for key, members in sorted(partitions.items(), key=lambda pair: _canonical_key(pair[0])):
        interface_id, protocol, role = key
        dimension = RequestEventDimension(
            interface_id=interface_id,
            protocol=protocol,
            traffic_role=role,
        )
        bucket_id = metric_bucket_id(
            run_id, "request_event", dimension.model_dump(mode="json")
        )
        ordered = tuple(sorted(members, key=lambda item: item.request_event_id))
        buckets.append(
            RequestEventMetricBucket(
                dimension=dimension,
                stability=_request_event_stability(ordered),
                timing=_request_event_timing(ordered),
                usage_coverage=_request_event_usage_coverage(ordered),
                evidence=_membership(
                    bucket_id,
                    tuple(item.request_event_id for item in ordered),
                    ("merged/request-metrics.jsonl",),
                ),
            )
        )
    return tuple(buckets)


def _membership(
    bucket_id: str,
    member_ids: tuple[str, ...],
    source_refs: tuple[str, ...],
) -> EvidenceMembership:
    members = tuple(sorted(member_ids))
    return EvidenceMembership(
        metric_bucket_id=bucket_id,
        member_count=len(members),
        member_ids=members,
        source_artifact_refs=source_refs,
    )


def _read_model(path: Path, model: type[_T], code: str) -> _T:
    try:
        return model.model_validate_json(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        _source_error(code, f"required source {path.name} is missing", path.name)
    except (OSError, ValidationError, ValueError):
        _source_error(code, f"required source {path.name} is invalid", path.name)


def _read_json_object(path: Path, code: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        _source_error(code, f"required manifest {path.name} is missing", path.name)
    except (OSError, json.JSONDecodeError):
        _source_error(code, f"required manifest {path.name} is invalid", path.name)
    if not isinstance(value, dict):
        _source_error(code, f"required manifest {path.name} is not an object", path.name)
    return value


def _read_jsonl_models(path: Path, model: type[_T], code: str) -> list[_T]:
    records: list[_T] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    records.append(model.model_validate_json(line))
                except (ValidationError, ValueError):
                    _source_error(
                        code,
                        f"{path.name} contains an invalid record",
                        f"{path.name}:{line_number}",
                    )
    except FileNotFoundError:
        _source_error(code, f"required source {path.name} is missing", path.name)
    except OSError:
        _source_error(code, f"required source {path.name} cannot be read", path.name)
    return records


def _require_manifest(
    manifest: dict[str, Any],
    *,
    run_id: str,
    status: str,
    versions: dict[str, str],
    code_prefix: str,
) -> None:
    if manifest.get("run_id") != run_id:
        _source_error(
            f"{code_prefix}_manifest_run_id_mismatch",
            f"{code_prefix} manifest belongs to a different run",
        )
    if manifest.get("status") != status:
        _source_error(
            f"{code_prefix}_manifest_not_complete",
            f"{code_prefix} manifest is not committed",
        )
    for field, expected in versions.items():
        if manifest.get(field) != expected:
            _source_error(
                f"{code_prefix}_{field}_unsupported",
                f"{code_prefix} manifest uses an unsupported {field}",
            )


def _validated_output_hash(path: Path, expected: object, code_prefix: str) -> str:
    if not path.exists() or not path.is_file():
        _source_error(
            f"{code_prefix}_missing",
            f"required source {path.name} is missing",
            path.name,
        )
    actual = _file_sha256(path)
    if not isinstance(expected, str) or expected != actual:
        _source_error(
            f"{code_prefix}_hash_mismatch",
            f"source hash does not match its manifest for {path.name}",
            path.name,
        )
    return actual


def _unique_index(
    values: Sequence[_T],
    key: Callable[[_T], str],
    code: str,
) -> dict[str, _T]:
    result: dict[str, _T] = {}
    for value in values:
        identity = key(value)
        if identity in result:
            _source_error(code, "source contains a duplicate identity", identity)
        result[identity] = value
    return result


def _require_identity_match(left: Any, right: Any, related_id: str) -> None:
    if (
        left.run_id != right.run_id
        or left.case_id != right.case_id
        or left.invocation_id != right.invocation_id
    ):
        _source_error(
            "semantic_identity_mismatch",
            "related semantic facts have different run/case/invocation identity",
            related_id,
        )


def _source_error(code: str, summary: str, related_id: str | None = None) -> None:
    raise _MetricsSourceError(code, summary, related_id)


def _failure_issue(error: Exception) -> MetricsIssue:
    if isinstance(error, _MetricsSourceError):
        return MetricsIssue(
            severity=IssueSeverity.ERROR,
            code=error.code,
            summary=error.summary,
            related_id=error.related_id,
        )
    return MetricsIssue(
        severity=IssueSeverity.ERROR,
        code="metrics_aggregation_failed",
        summary=f"run metrics aggregation failed: {type(error).__name__}",
    )


def _write_manifest(
    path: Path,
    *,
    run_id: str,
    write_status: str,
    metrics_status: RunMetricsStatus | None,
    created_at: datetime,
    source_evidence: SourceEvidence | None,
    output_hashes: dict[str, str],
    output_counts: dict[str, int],
    issues: Sequence[MetricsIssue],
) -> None:
    write_json_atomic(
        path,
        {
            "manifest_version": RUN_METRICS_MANIFEST_VERSION,
            "schema_version": RUN_METRICS_SCHEMA_VERSION,
            "aggregation_version": RUN_METRICS_AGGREGATION_VERSION,
            "run_id": run_id,
            "write_status": write_status,
            "metrics_status": metrics_status.value if metrics_status is not None else None,
            "created_at": created_at,
            "source_evidence": source_evidence,
            "output_hashes": output_hashes,
            "output_counts": output_counts,
            "issues": tuple(issues),
        },
    )


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative_path(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        _source_error("source_path_outside_output", "source path is outside output_dir")


def _canonical_key(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
