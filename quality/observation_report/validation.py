from __future__ import annotations

from pathlib import Path
from typing import Any

from quality.flaky_models import (
    FLAKY_PROJECTION_VERSION,
    FLAKY_STATE_RULE_VERSION,
    FlakyEvaluationResult,
)
from quality.gate import GATE_RULESET_VERSION
from quality.metrics_models import (
    RUN_METRICS_AGGREGATION_VERSION,
    RUN_METRICS_MANIFEST_VERSION,
    RUN_METRICS_SCHEMA_VERSION,
    RunMetricsResult,
    RunMetricsStatus,
)
from quality.models import (
    SCHEMA_VERSION,
    GateDecision,
    GateMode,
    QualitySummary,
    RunRecord,
)
from quality.report import REPORT_VERSION


class IncompatibleSource(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def required_text(value: str, name: str) -> str:
    text = value.strip()
    if not text:
        raise ValueError(f"{name} must not be empty")
    return text


def validated_count_map(value: object, name: str) -> dict[str, int]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    result: dict[str, int] = {}
    for key, count in value.items():
        if not isinstance(key, str) or not key.strip():
            raise ValueError(f"{name} has an invalid key")
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ValueError(f"{name} has an invalid count")
        result[key.strip()] = count
    return dict(sorted(result.items()))


def validate_p0_contract(
    run_id: str,
    run: RunRecord,
    summary_payload: dict[str, Any],
    gate_payload: dict[str, Any],
) -> tuple[QualitySummary, GateDecision, dict[str, int]]:
    if (
        run.run_id != run_id
        or summary_payload.get("run_id") != run_id
        or gate_payload.get("run_id") != run_id
    ):
        raise IncompatibleSource("p0_run_id_mismatch")
    if summary_payload.get("schema_version") != SCHEMA_VERSION:
        raise IncompatibleSource("p0_schema_version_unsupported")
    if summary_payload.get("report_version") != REPORT_VERSION:
        raise IncompatibleSource("p0_report_version_unsupported")
    if gate_payload.get("schema_version") != SCHEMA_VERSION:
        raise IncompatibleSource("p0_gate_schema_version_unsupported")
    if gate_payload.get("report_version") != REPORT_VERSION:
        raise IncompatibleSource("p0_gate_report_version_unsupported")
    if gate_payload.get("gate_ruleset_version") != GATE_RULESET_VERSION:
        raise IncompatibleSource("p0_gate_ruleset_version_unsupported")
    summary = QualitySummary.model_validate(summary_payload.get("summary"))
    gate = GateDecision.model_validate(gate_payload.get("decision"))
    if summary.run_id != run_id or gate.run_id != run_id:
        raise IncompatibleSource("p0_nested_run_id_mismatch")
    if gate.mode is not GateMode.SHADOW:
        raise IncompatibleSource("p0_gate_mode_not_shadow")
    if (
        gate_payload.get("mode") != gate.mode.value
        or gate_payload.get("overall") != gate.overall.value
    ):
        raise IncompatibleSource("p0_gate_envelope_mismatch")
    return (
        summary,
        gate,
        validated_count_map(
            summary_payload.get("failure_categories"), "failure_categories"
        ),
    )


def validate_metrics_manifest_identity(
    manifest: dict[str, Any], run_id: str
) -> None:
    if manifest.get("run_id") != run_id:
        raise IncompatibleSource("metrics_run_id_mismatch")
    if (
        manifest.get("manifest_version") != RUN_METRICS_MANIFEST_VERSION
        or manifest.get("schema_version") != RUN_METRICS_SCHEMA_VERSION
        or manifest.get("aggregation_version") != RUN_METRICS_AGGREGATION_VERSION
    ):
        raise IncompatibleSource("metrics_version_unsupported")


def validate_expected_hash(
    expected_hash: object, actual_hash: str, *, code: str
) -> None:
    if expected_hash != actual_hash:
        raise IncompatibleSource(code)


def validate_metrics_contract(
    metrics: RunMetricsResult,
    manifest: dict[str, Any],
    run_id: str,
) -> None:
    if metrics.run_id != run_id:
        raise ValueError("metrics run_id mismatch")
    if metrics.status is RunMetricsStatus.FAILED or metrics.run_metrics is None:
        raise ValueError("complete manifest cannot consume failed metrics")
    counts = manifest.get("output_counts")
    if not isinstance(counts, dict):
        raise ValueError("metrics output_counts is invalid")
    expected = {
        "workload_operations": metrics.run_metrics.operation.operation_count,
        "workload_request_groups": metrics.run_metrics.request_groups.group_count,
        "workload_request_events": metrics.run_metrics.request_events.event_count,
        "operation_buckets": len(metrics.operation_buckets),
        "request_group_buckets": len(metrics.request_group_buckets),
        "request_event_buckets": len(metrics.request_event_buckets),
    }
    if any(counts.get(name) != value for name, value in expected.items()):
        raise ValueError("metrics manifest counts do not match result")
    validate_bucket_members(
        metrics.operation_buckets,
        metrics.run_metrics.operation.operation_count,
    )
    validate_bucket_members(
        metrics.request_group_buckets,
        metrics.run_metrics.request_groups.group_count,
    )
    validate_bucket_members(
        metrics.request_event_buckets,
        metrics.run_metrics.request_events.event_count,
    )


def validate_bucket_members(buckets: tuple[Any, ...], expected_count: int) -> None:
    members = [member for bucket in buckets for member in bucket.evidence.member_ids]
    if len(members) != expected_count or len(members) != len(set(members)):
        raise ValueError("metrics bucket membership is incomplete or duplicated")


def validate_flaky_identity(value: object, *, source_name: str, run_id: str) -> None:
    if getattr(value, "run_id", None) != run_id:
        raise IncompatibleSource(f"{source_name}_run_id_mismatch")
    artifact_ref = getattr(value, "artifact_ref", None)
    if artifact_ref and Path(artifact_ref).is_absolute():
        raise IncompatibleSource(f"{source_name}_absolute_artifact_ref")


def validate_flaky_evaluation_contract(value: FlakyEvaluationResult) -> None:
    if (
        value.rule_version != FLAKY_STATE_RULE_VERSION
        or value.projection_version != FLAKY_PROJECTION_VERSION
        or any(
            len(item.evidence_observation_ids) > 20
            or len(item.evidence_run_ids) > 20
            for item in value.transitions
        )
    ):
        raise IncompatibleSource("flaky_evaluation_contract_incompatible")
