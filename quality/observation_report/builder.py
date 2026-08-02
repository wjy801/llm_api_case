from __future__ import annotations

from datetime import datetime
import json
from typing import Any

from quality.flaky_models import (
    FlakyEvaluationResult,
    FlakyImportResult,
    FlakyStateSummary,
)
from quality.metrics_models import (
    RUN_METRICS_AGGREGATION_VERSION,
    NumericAggregate,
    RatioAggregate,
    RunMetricsResult,
)
from quality.observation_models import (
    P1DisplayWindow,
    P1FlakySection,
    P1IntegritySummary,
    P1KnownTotal,
    P1MetricObservation,
    P1MetricsSection,
    P1ObservationReport,
    P1P0Section,
    P1ReportStatus,
    P1RunOverview,
    P1SourceSummary,
    P1UsageCoverage,
    SourceExpectation,
    SourceStatus,
)
from quality.redaction import redact_quality_value

from .attention import build_attention_items, is_required_source_failure
from .contracts import LoadedSource, P0Value


_METRICS_ARTIFACT = "metrics/run-metrics.json"


def build_report(
    run_id: str,
    *,
    created_at: datetime,
    p0: LoadedSource[P0Value],
    metrics: LoadedSource[RunMetricsResult],
    flaky_import: LoadedSource[FlakyImportResult],
    flaky_evaluation: LoadedSource[FlakyEvaluationResult],
) -> P1ObservationReport:
    loaded = (p0, metrics, flaky_import, flaky_evaluation)
    sources = tuple(item.summary for item in loaded)
    computed_report_status = report_status(sources)
    p0_section = build_p0_section(p0.value) if p0.value is not None else None
    metrics_section = (
        build_metrics_section(metrics.value) if metrics.value is not None else None
    )
    usage = build_usage_coverage(metrics.value) if metrics.value is not None else None
    flaky = build_flaky_section(flaky_import.value, flaky_evaluation.value)
    attention = build_attention_items(sources, usage, flaky)
    source_issue_codes = {
        code for source in sources for code in source.issue_codes
    }
    required_failures = sum(
        is_required_source_failure(source) for source in sources
    )
    degraded_sources = tuple(
        source.source_name
        for source in sources
        if source.status
        in {
            SourceStatus.DEGRADED,
            SourceStatus.FAILED,
            SourceStatus.MISSING,
            SourceStatus.INCOMPATIBLE,
        }
    )
    overview = build_overview(
        run_id,
        created_at=created_at,
        report_status=computed_report_status,
        p0=p0_section,
        metrics=metrics_section,
        usage=usage,
        flaky=flaky,
        required_source_failures=required_failures,
    )
    return P1ObservationReport(
        run_id=run_id,
        generated_at=created_at,
        report_status=computed_report_status,
        overview=overview,
        sources=sources,
        p0=p0_section,
        metrics=metrics_section,
        usage_coverage=usage,
        flaky=flaky,
        display_windows=build_display_windows(metrics_section, usage, flaky),
        attention_items=attention,
        integrity=P1IntegritySummary(
            issue_codes=tuple(sorted(source_issue_codes)),
            degraded_sources=degraded_sources,
            required_source_failure_count=required_failures,
            evidence_refs=tuple(
                sorted(
                    {
                        ref
                        for source in sources
                        for ref in source.evidence_refs
                    }
                )
            ),
        ),
    )


def report_status(sources: tuple[P1SourceSummary, ...]) -> P1ReportStatus:
    required = tuple(
        item for item in sources if item.expectation is SourceExpectation.REQUIRED
    )
    consumable = {
        SourceStatus.AVAILABLE,
        SourceStatus.DEGRADED,
        SourceStatus.NO_DATA,
    }
    if required and not any(item.status in consumable for item in required):
        return P1ReportStatus.NO_DATA
    if any(
        item.status
        in {
            SourceStatus.DEGRADED,
            SourceStatus.FAILED,
            SourceStatus.MISSING,
            SourceStatus.INCOMPATIBLE,
        }
        for item in required
    ):
        return P1ReportStatus.DEGRADED
    return P1ReportStatus.COMPLETE


def build_p0_section(value: P0Value) -> P1P0Section:
    summary = value.summary
    return P1P0Section(
        gate_mode=value.gate.mode.value,
        gate_overall=value.gate.overall.value,
        integrity_status=summary.integrity_status.value,
        case_total=summary.case_total,
        case_passed=summary.case_passed,
        case_failed=summary.case_failed,
        case_error=summary.case_error,
        case_skipped=summary.case_skipped,
        request_total=summary.request_total,
        http_5xx_count=summary.http_5xx_count,
        timeout_count=summary.timeout_count,
        failure_categories=value.failure_categories,
        evidence_refs=("run.json", "summary.json", "gate-report.json", "gate-report.md"),
    )


def build_metrics_section(result: RunMetricsResult) -> P1MetricsSection:
    run = result.run_metrics
    if run is None:
        raise ValueError("consumable metrics result has no run metrics")
    observations: list[P1MetricObservation] = []
    run_ratios = {
        "operation.success_rate": run.operation.success_rate,
        "operation.timeout_rate": run.operation.timeout_rate,
        "request_group.retry_rate": run.request_groups.retry_rate,
        "request_group.first_transport_response_rate": run.request_groups.first_transport_response_rate,
        "request_group.final_transport_response_rate": run.request_groups.final_transport_response_rate,
        "request_group.first_http_success_rate": run.request_groups.first_http_success_rate,
        "request_group.final_http_success_rate": run.request_groups.final_http_success_rate,
        "request_group.first_business_success_rate": run.request_groups.first_business_success_rate,
        "request_group.final_business_success_rate": run.request_groups.final_business_success_rate,
        "request_group.http_retry_rescue_rate": run.request_groups.http_retry_rescue_rate,
        "request_group.business_retry_rescue_rate": run.request_groups.business_retry_rescue_rate,
        "request_event.timeout_rate": run.request_events.timeout_rate,
        "request_event.http_5xx_rate": run.request_events.http_5xx_rate,
        "request_event.http_429_rate": run.request_events.http_429_rate,
        "request_event.business_success_rate": run.request_events.business_success_rate,
    }
    observations.extend(
        ratio_observation(
            metric_id=f"run:{name}",
            grain="run",
            dimension={},
            name=name,
            aggregate=aggregate,
            evidence_refs=("metrics/run-metrics.json",),
        )
        for name, aggregate in run_ratios.items()
    )
    run_numeric = {
        "operation.total_duration_ms": run.operation_timing.total_duration_ms,
        "operation.response_headers_ms": run.operation_timing.response_headers_ms,
        "operation.first_data_ms": run.operation_timing.first_data_ms,
        "operation.first_content_ms": run.operation_timing.first_content_ms,
        "operation.stream_duration_ms": run.operation_timing.stream_duration_ms,
        "operation.create_request_ms": run.operation_timing.create_request_ms,
        "operation.polling_total_ms": run.operation_timing.polling_total_ms,
        "operation.polling_sleep_ms": run.operation_timing.polling_sleep_ms,
        "request_group.total_duration_ms": run.request_group_timing.total_duration_ms,
        "request_group.retry_wait_ms": run.request_group_timing.retry_wait_ms,
        "request_group.first_attempt_duration_ms": run.request_group_timing.first_attempt_duration_ms,
        "request_group.retry_attempt_duration_ms": run.request_group_timing.retry_attempt_duration_ms,
        "request_event.all_duration_ms": run.request_event_timing.all_duration_ms,
        "request_event.timeout_duration_ms": run.request_event_timing.timeout_duration_ms,
        "request_event.transport_error_duration_ms": run.request_event_timing.transport_error_duration_ms,
    }
    observations.extend(
        numeric_observation(
            metric_id=f"run:{name}",
            grain="run",
            dimension={},
            name=name,
            aggregate=aggregate,
            evidence_refs=("metrics/run-metrics.json",),
        )
        for name, aggregate in run_numeric.items()
    )
    for bucket in result.operation_buckets:
        dimension = bucket.dimension.model_dump(mode="json")
        base_id = bucket.evidence.metric_bucket_id
        evidence = bucket_evidence(bucket.evidence)
        observations.append(
            ratio_observation(
                metric_id=f"{base_id}:success_rate",
                grain="operation_bucket",
                dimension=dimension,
                name="operation.success_rate",
                aggregate=bucket.stability.success_rate,
                evidence_refs=evidence,
            )
        )
        for name, aggregate in (
            ("operation.total_duration_ms", bucket.timing.total_duration_ms),
            ("operation.response_headers_ms", bucket.timing.response_headers_ms),
            ("operation.first_data_ms", bucket.timing.first_data_ms),
            ("operation.first_content_ms", bucket.timing.first_content_ms),
            ("operation.stream_duration_ms", bucket.timing.stream_duration_ms),
            ("operation.create_request_ms", bucket.timing.create_request_ms),
            ("operation.polling_total_ms", bucket.timing.polling_total_ms),
            ("operation.polling_sleep_ms", bucket.timing.polling_sleep_ms),
        ):
            observations.append(
                numeric_observation(
                    metric_id=f"{base_id}:{name}",
                    grain="operation_bucket",
                    dimension=dimension,
                    name=name,
                    aggregate=aggregate,
                    evidence_refs=evidence,
                )
            )
    for bucket in result.request_group_buckets:
        dimension = bucket.dimension.model_dump(mode="json")
        base_id = bucket.evidence.metric_bucket_id
        evidence = bucket_evidence(bucket.evidence)
        for name, aggregate in (
            ("request_group.retry_rate", bucket.stability.retry_rate),
            ("request_group.final_http_success_rate", bucket.stability.final_http_success_rate),
            ("request_group.http_retry_rescue_rate", bucket.stability.http_retry_rescue_rate),
        ):
            observations.append(
                ratio_observation(
                    metric_id=f"{base_id}:{name}",
                    grain="request_group_bucket",
                    dimension=dimension,
                    name=name,
                    aggregate=aggregate,
                    evidence_refs=evidence,
                )
            )
        observations.append(
            numeric_observation(
                metric_id=f"{base_id}:request_group.total_duration_ms",
                grain="request_group_bucket",
                dimension=dimension,
                name="request_group.total_duration_ms",
                aggregate=bucket.timing.total_duration_ms,
                evidence_refs=evidence,
            )
        )
    for bucket in result.request_event_buckets:
        dimension = bucket.dimension.model_dump(mode="json")
        base_id = bucket.evidence.metric_bucket_id
        evidence = bucket_evidence(bucket.evidence)
        for name, aggregate in (
            ("request_event.timeout_rate", bucket.stability.timeout_rate),
            ("request_event.http_5xx_rate", bucket.stability.http_5xx_rate),
        ):
            observations.append(
                ratio_observation(
                    metric_id=f"{base_id}:{name}",
                    grain="request_event_bucket",
                    dimension=dimension,
                    name=name,
                    aggregate=aggregate,
                    evidence_refs=evidence,
                )
            )
        observations.append(
            numeric_observation(
                metric_id=f"{base_id}:request_event.all_duration_ms",
                grain="request_event_bucket",
                dimension=dimension,
                name="request_event.all_duration_ms",
                aggregate=bucket.timing.all_duration_ms,
                evidence_refs=evidence,
            )
        )
    exclusions = result.exclusions
    return P1MetricsSection(
        metrics_status=result.status.value,
        aggregation_version=result.aggregation_version,
        workload_operation_count=run.operation.operation_count,
        request_group_count=run.request_groups.group_count,
        request_event_count=run.request_events.event_count,
        operation_outcomes=run.operation.outcomes.counts,
        control_operation_count=len(exclusions.control_operation_ids),
        control_group_count=len(exclusions.control_group_ids),
        control_event_count=len(exclusions.control_event_ids),
        unknown_operation_count=len(exclusions.unknown_operation_ids),
        unknown_group_count=len(exclusions.unknown_group_ids),
        unknown_event_count=len(exclusions.unknown_event_ids),
        unknown_role_count=(
            len(exclusions.unknown_operation_ids)
            + len(exclusions.unknown_group_ids)
            + len(exclusions.unknown_event_ids)
        ),
        unassigned_event_count=len(exclusions.unassigned_event_ids),
        observations=tuple(
            sorted(
                observations,
                key=lambda item: (
                    item.grain,
                    json.dumps(item.dimension, sort_keys=True),
                    item.metric_name,
                    item.metric_id,
                ),
            )
        ),
        source_artifact=_METRICS_ARTIFACT,
    )


def build_usage_coverage(result: RunMetricsResult) -> P1UsageCoverage:
    run = result.run_metrics
    if run is None:
        raise ValueError("consumable metrics result has no usage metrics")
    usage = run.usage
    counts = usage.completeness.counts
    missing_buckets = tuple(
        bucket.evidence.metric_bucket_id
        for bucket in result.operation_buckets
        if (
            bucket.usage.completeness.counts.get("partial", 0)
            + bucket.usage.completeness.counts.get("missing", 0)
        )
        > 0
    )
    missing_event_buckets = tuple(
        bucket.evidence.metric_bucket_id
        for bucket in result.operation_buckets
        if bucket.usage.missing_source_event_count > 0
    )
    retry = usage.retry_extra_usage
    return P1UsageCoverage(
        eligible_operation_count=run.operation.operation_count,
        complete_count=counts.get("complete", 0),
        partial_count=counts.get("partial", 0),
        missing_count=counts.get("missing", 0),
        not_applicable_count=counts.get("not_applicable", 0),
        input_tokens=known_total(usage.input_tokens),
        output_tokens=known_total(usage.output_tokens),
        media_count=known_total(usage.media_count),
        media_duration_ms=known_total(usage.media_duration_ms),
        retry_input_tokens=known_total(retry.retry_input_tokens),
        retry_output_tokens=known_total(retry.retry_output_tokens),
        retry_media_count=known_total(retry.retry_media_count),
        retry_missing_attempt_count=retry.retry_missing_attempt_count,
        missing_operation_refs=missing_buckets,
        missing_event_refs=missing_event_buckets,
        source_artifact=_METRICS_ARTIFACT,
    )


def known_total(aggregate: NumericAggregate) -> P1KnownTotal:
    return P1KnownTotal(
        sample_size=aggregate.sample_size,
        missing_sample_size=aggregate.missing_count,
        total=aggregate.total,
        completeness=aggregate.completeness,
    )


def build_flaky_section(
    imported: FlakyImportResult | None,
    evaluated: FlakyEvaluationResult | None,
) -> P1FlakySection | None:
    if imported is None and evaluated is None:
        return None
    if evaluated is None:
        return P1FlakySection(
            import_status=imported.status.value if imported is not None else None,
            import_database_schema_version=(
                imported.database_schema_version if imported is not None else None
            ),
            quick_check=(
                sanitize_quick_check(imported.quick_check, 128)
                if imported is not None and imported.quick_check is not None
                else None
            ),
            issue_codes=tuple(item.code for item in imported.issues) if imported else (),
            source_artifacts=("flaky-import.json",) if imported is not None else (),
        )
    issue_codes = {
        *(item.code for item in evaluated.issues),
        *(item.code for item in (imported.issues if imported is not None else ())),
    }
    return P1FlakySection(
        import_status=imported.status.value if imported is not None else None,
        evaluation_status=evaluated.status.value,
        rule_version=evaluated.rule_version,
        projection_version=evaluated.projection_version,
        import_database_schema_version=(
            imported.database_schema_version if imported is not None else None
        ),
        evaluation_database_schema_version=evaluated.database_schema_version,
        quick_check=(
            sanitize_quick_check(evaluated.quick_check, 128)
            if evaluated.quick_check is not None
            else (
                sanitize_quick_check(imported.quick_check, 128)
                if imported is not None and imported.quick_check is not None
                else None
            )
        ),
        affected_count=evaluated.affected_count,
        evaluated_count=evaluated.evaluated_count,
        transitioned_count=evaluated.transitioned_count,
        stale_count=evaluated.stale_count,
        newly_suspected=sorted_states(evaluated.newly_suspected),
        newly_confirmed=sorted_states(evaluated.newly_confirmed),
        ongoing_confirmed=sorted_states(evaluated.ongoing_confirmed),
        quarantined=sorted_states(evaluated.quarantined),
        recovering=sorted_states(evaluated.recovering),
        recovered=sorted_states(evaluated.recovered),
        overdue=sorted_states(evaluated.overdue),
        transitions=tuple(
            sorted(evaluated.transitions, key=lambda item: item.transition_id)
        ),
        issue_codes=tuple(sorted(issue_codes)),
        source_artifacts=tuple(
            item
            for item, exists in (
                ("flaky-import.json", imported is not None),
                ("flaky-evaluation.json", True),
            )
            if exists
        ),
    )


def sorted_states(
    values: tuple[FlakyStateSummary, ...],
) -> tuple[FlakyStateSummary, ...]:
    return tuple(sorted(values, key=lambda item: item.flaky_key))



def build_display_windows(
    metrics: P1MetricsSection | None,
    usage: P1UsageCoverage | None,
    flaky: P1FlakySection | None,
) -> tuple[P1DisplayWindow, ...]:
    windows: list[P1DisplayWindow] = []
    if metrics is not None:
        timing_count = sum(
            "duration_ms" in item.metric_name or item.metric_name.endswith("_ms")
            for item in metrics.observations
        )
        windows.append(display_window("timing_observations", timing_count, _METRICS_ARTIFACT))
    if usage is not None:
        windows.append(
            display_window(
                "usage_missing_refs",
                len(usage.missing_operation_refs),
                usage.source_artifact,
            )
        )
    if flaky is not None:
        windows.append(
            display_window(
                "flaky_new_and_ongoing",
                len(flaky.newly_suspected)
                + len(flaky.newly_confirmed)
                + len(flaky.ongoing_confirmed),
                "flaky-evaluation.json",
            )
        )
        windows.append(
            display_window(
                "flaky_governance",
                len(flaky.quarantined)
                + len(flaky.recovering)
                + len(flaky.recovered)
                + len(flaky.overdue),
                "flaky-evaluation.json",
            )
        )
        windows.append(
            display_window(
                "flaky_transitions",
                len(flaky.transitions),
                "flaky-evaluation.json",
            )
        )
    return tuple(sorted(windows, key=lambda item: item.category))


def display_window(category: str, total: int, source: str) -> P1DisplayWindow:
    shown = min(total, 10)
    return P1DisplayWindow(
        category=category,
        total_count=total,
        shown_count=shown,
        omitted_count=total - shown,
        source_artifact=source,
    )


def build_overview(
    run_id: str,
    *,
    created_at: datetime,
    report_status: P1ReportStatus,
    p0: P1P0Section | None,
    metrics: P1MetricsSection | None,
    usage: P1UsageCoverage | None,
    flaky: P1FlakySection | None,
    required_source_failures: int,
) -> P1RunOverview:
    outcomes = metrics.operation_outcomes if metrics is not None else {}
    workload = metrics.workload_operation_count if metrics is not None else 0
    control = metrics.control_operation_count if metrics is not None else 0
    unknown_operations = metrics.unknown_operation_count if metrics is not None else 0
    return P1RunOverview(
        run_id=run_id,
        report_status=report_status,
        p0_gate_mode=p0.gate_mode if p0 is not None else None,
        p0_gate_overall=p0.gate_overall if p0 is not None else None,
        p0_integrity_status=p0.integrity_status if p0 is not None else None,
        case_total=p0.case_total if p0 is not None else 0,
        case_failed=p0.case_failed if p0 is not None else 0,
        case_error=p0.case_error if p0 is not None else 0,
        operation_count=workload + control + unknown_operations,
        workload_operation_count=workload,
        operation_success_count=outcomes.get("success", 0),
        operation_failed_count=outcomes.get("failed", 0),
        operation_timeout_count=outcomes.get("timeout", 0),
        usage_complete_count=usage.complete_count if usage is not None else 0,
        usage_partial_count=usage.partial_count if usage is not None else 0,
        usage_missing_count=usage.missing_count if usage is not None else 0,
        flaky_affected_count=flaky.affected_count if flaky is not None else 0,
        flaky_transitioned_count=flaky.transitioned_count if flaky is not None else 0,
        flaky_stale_count=flaky.stale_count if flaky is not None else 0,
        newly_suspected_count=len(flaky.newly_suspected) if flaky is not None else 0,
        newly_confirmed_count=len(flaky.newly_confirmed) if flaky is not None else 0,
        quarantined_count=len(flaky.quarantined) if flaky is not None else 0,
        recovering_count=len(flaky.recovering) if flaky is not None else 0,
        recovered_count=len(flaky.recovered) if flaky is not None else 0,
        overdue_count=len(flaky.overdue) if flaky is not None else 0,
        required_source_failure_count=required_source_failures,
        generated_at=created_at,
    )


def ratio_observation(
    *,
    metric_id: str,
    grain: str,
    dimension: dict[str, str | None],
    name: str,
    aggregate: RatioAggregate,
    evidence_refs: tuple[str, ...],
) -> P1MetricObservation:
    return P1MetricObservation(
        metric_id=metric_id,
        grain=grain,
        dimension=dimension,
        metric_name=name,
        value=aggregate.value,
        numerator=aggregate.numerator,
        sample_size=aggregate.sample_size,
        missing_sample_size=aggregate.unknown_count,
        completeness=aggregate.completeness,
        algorithm_version=RUN_METRICS_AGGREGATION_VERSION,
        source_artifact=_METRICS_ARTIFACT,
        evidence_refs=evidence_refs,
    )


def numeric_observation(
    *,
    metric_id: str,
    grain: str,
    dimension: dict[str, str | None],
    name: str,
    aggregate: NumericAggregate,
    evidence_refs: tuple[str, ...],
) -> P1MetricObservation:
    return P1MetricObservation(
        metric_id=metric_id,
        grain=grain,
        dimension=dimension,
        metric_name=name,
        value=aggregate.mean,
        total=aggregate.total,
        minimum=aggregate.minimum,
        maximum=aggregate.maximum,
        sample_size=aggregate.sample_size,
        missing_sample_size=aggregate.missing_count,
        completeness=aggregate.completeness,
        algorithm_version=RUN_METRICS_AGGREGATION_VERSION,
        source_artifact=_METRICS_ARTIFACT,
        evidence_refs=evidence_refs,
    )


def bucket_evidence(evidence: Any) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                evidence.metric_bucket_id,
                *evidence.source_artifact_refs,
                *evidence.member_ids[:10],
            }
        )
    )


def sanitize_quick_check(value: object, maximum: int = 500) -> str:
    redacted = redact_quality_value(str(value), remove_url_query=True)
    text = str(redacted).replace("\x00", "").strip()
    return text[:maximum] or "-"
