from __future__ import annotations

from datetime import datetime
from enum import Enum
import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from quality.models import IssueSeverity, RunStatus


RUN_METRICS_SCHEMA_VERSION = "quality.run-metrics.v1"
RUN_METRICS_MANIFEST_VERSION = "quality.run-metrics-manifest.v1"
RUN_METRICS_AGGREGATION_VERSION = "p1-run-metrics.v1"


class RunMetricsStatus(str, Enum):
    AGGREGATED = "aggregated"
    DEGRADED = "degraded"
    NO_DATA = "no_data"
    FAILED = "failed"


class MetricCompleteness(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    NO_DATA = "no_data"
    NOT_APPLICABLE = "not_applicable"


class _FrozenMetricsModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)


class NumericAggregate(_FrozenMetricsModel):
    eligible_count: int = Field(ge=0)
    sample_size: int = Field(ge=0)
    missing_count: int = Field(ge=0)
    total: int | float | None = None
    mean: float | None = None
    minimum: int | float | None = None
    maximum: int | float | None = None
    completeness: MetricCompleteness

    @model_validator(mode="after")
    def _validate_aggregate(self) -> NumericAggregate:
        if self.sample_size + self.missing_count != self.eligible_count:
            raise ValueError("sample_size + missing_count must equal eligible_count")
        values = (self.total, self.mean, self.minimum, self.maximum)
        if self.sample_size == 0:
            if any(value is not None for value in values):
                raise ValueError("an aggregate without samples cannot contain numeric values")
            if self.completeness not in {
                MetricCompleteness.NO_DATA,
                MetricCompleteness.NOT_APPLICABLE,
            }:
                raise ValueError("an aggregate without samples must be no_data/not_applicable")
            return self
        if any(value is None for value in values):
            raise ValueError("an aggregate with samples requires total/mean/minimum/maximum")
        for value in values:
            assert value is not None
            if not math.isfinite(float(value)) or float(value) < 0:
                raise ValueError("aggregate numeric values must be finite and nonnegative")
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("minimum must not exceed maximum")
        expected = MetricCompleteness.COMPLETE if self.missing_count == 0 else MetricCompleteness.PARTIAL
        if self.completeness is not expected:
            raise ValueError("numeric aggregate completeness does not match coverage")
        return self


class RatioAggregate(_FrozenMetricsModel):
    numerator: int = Field(ge=0)
    sample_size: int = Field(ge=0)
    unknown_count: int = Field(default=0, ge=0)
    value: float | None = Field(default=None, ge=0, le=1)
    completeness: MetricCompleteness

    @model_validator(mode="after")
    def _validate_ratio(self) -> RatioAggregate:
        if self.numerator > self.sample_size:
            raise ValueError("numerator must not exceed sample_size")
        if self.sample_size == 0:
            if self.value is not None:
                raise ValueError("a ratio without samples must have a null value")
            expected = MetricCompleteness.NO_DATA
        else:
            expected_value = round(self.numerator / self.sample_size, 6)
            if self.value is None or not math.isclose(self.value, expected_value, abs_tol=1e-9):
                raise ValueError("ratio value must equal numerator / sample_size")
            expected = (
                MetricCompleteness.PARTIAL
                if self.unknown_count
                else MetricCompleteness.COMPLETE
            )
        if self.completeness is not expected:
            raise ValueError("ratio completeness does not match known/unknown coverage")
        return self


class CountDistribution(_FrozenMetricsModel):
    sample_size: int = Field(ge=0)
    counts: dict[str, int] = Field(default_factory=dict)
    unknown_count: int = Field(default=0, ge=0)

    @field_validator("counts")
    @classmethod
    def _validate_counts(cls, value: dict[str, int]) -> dict[str, int]:
        normalized: dict[str, int] = {}
        for name, count in value.items():
            key = name.strip()
            if not key:
                raise ValueError("distribution keys must not be empty")
            if isinstance(count, bool) or count < 0:
                raise ValueError("distribution counts must be nonnegative integers")
            normalized[key] = count
        return dict(sorted(normalized.items()))

    @model_validator(mode="after")
    def _validate_sample_size(self) -> CountDistribution:
        if sum(self.counts.values()) != self.sample_size:
            raise ValueError("distribution counts must sum to sample_size")
        return self


class ArtifactEvidence(_FrozenMetricsModel):
    path: str
    sha256: str
    schema_version: str | None = None
    manifest_version: str | None = None
    merge_version: str | None = None

    @field_validator("path", "sha256")
    @classmethod
    def _validate_required_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("value must not be empty")
        return text

    @field_validator("schema_version", "manifest_version", "merge_version")
    @classmethod
    def _validate_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        if not text:
            raise ValueError("value must not be empty")
        return text


class SourceEvidence(_FrozenMetricsModel):
    p0_manifest: ArtifactEvidence
    p0_request_metrics: ArtifactEvidence
    semantic_manifest: ArtifactEvidence
    semantic_outputs: dict[str, ArtifactEvidence]

    @field_validator("semantic_outputs")
    @classmethod
    def _validate_semantic_outputs(
        cls, value: dict[str, ArtifactEvidence]
    ) -> dict[str, ArtifactEvidence]:
        if not value:
            raise ValueError("semantic_outputs must not be empty")
        return dict(sorted(value.items()))


class MetricsIssue(_FrozenMetricsModel):
    severity: IssueSeverity
    code: str
    summary: str
    related_id: str | None = None

    @field_validator("code", "summary")
    @classmethod
    def _validate_required_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("value must not be empty")
        return text[:512]

    @field_validator("related_id")
    @classmethod
    def _validate_related_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        return text[:256] if text else None


class MetricsIntegrity(_FrozenMetricsModel):
    p0_integrity_status: str
    semantic_integrity_status: str
    issue_count: int = Field(ge=0)
    error_count: int = Field(ge=0)
    warning_count: int = Field(ge=0)
    degraded_reasons: tuple[str, ...] = ()

    @field_validator("p0_integrity_status", "semantic_integrity_status")
    @classmethod
    def _validate_status(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("integrity status must not be empty")
        return text

    @field_validator("degraded_reasons")
    @classmethod
    def _validate_reasons(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(sorted({item.strip() for item in value if item.strip()}))


class Exclusions(_FrozenMetricsModel):
    control_operation_ids: tuple[str, ...] = ()
    control_group_ids: tuple[str, ...] = ()
    control_event_ids: tuple[str, ...] = ()
    unknown_operation_ids: tuple[str, ...] = ()
    unknown_group_ids: tuple[str, ...] = ()
    unknown_event_ids: tuple[str, ...] = ()
    not_applicable_usage_operation_ids: tuple[str, ...] = ()
    unassigned_event_ids: tuple[str, ...] = ()
    foreign_run_count: int = Field(default=0, ge=0)

    @field_validator(
        "control_operation_ids",
        "control_group_ids",
        "control_event_ids",
        "unknown_operation_ids",
        "unknown_group_ids",
        "unknown_event_ids",
        "not_applicable_usage_operation_ids",
        "unassigned_event_ids",
    )
    @classmethod
    def _validate_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(sorted({item.strip() for item in value if item.strip()}))
        return normalized


class EvidenceMembership(_FrozenMetricsModel):
    metric_bucket_id: str
    member_count: int = Field(ge=0)
    member_ids: tuple[str, ...]
    source_artifact_refs: tuple[str, ...]

    @field_validator("metric_bucket_id")
    @classmethod
    def _validate_bucket_id(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("metric_bucket_id must not be empty")
        return text

    @field_validator("member_ids", "source_artifact_refs")
    @classmethod
    def _validate_members(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(sorted({item.strip() for item in value if item.strip()}))
        return normalized

    @model_validator(mode="after")
    def _validate_member_count(self) -> EvidenceMembership:
        if self.member_count != len(self.member_ids):
            raise ValueError("member_count must equal member_ids length")
        return self


class UsageAggregate(_FrozenMetricsModel):
    completeness: CountDistribution
    input_tokens: NumericAggregate
    output_tokens: NumericAggregate
    media_count: NumericAggregate
    media_duration_ms: NumericAggregate
    known_source_event_count: int = Field(default=0, ge=0)
    missing_source_event_count: int = Field(default=0, ge=0)


class RetryUsageAggregate(_FrozenMetricsModel):
    first_attempt_input_tokens: NumericAggregate
    first_attempt_output_tokens: NumericAggregate
    first_attempt_media_count: NumericAggregate
    retry_input_tokens: NumericAggregate
    retry_output_tokens: NumericAggregate
    retry_media_count: NumericAggregate
    retry_missing_attempt_count: int = Field(default=0, ge=0)


class OperationStability(_FrozenMetricsModel):
    operation_count: int = Field(ge=0)
    outcomes: CountDistribution
    success_rate: RatioAggregate
    timeout_rate: RatioAggregate
    incomplete_or_unknown_count: int = Field(ge=0)
    record_completeness: CountDistribution


class OperationTimingAggregate(_FrozenMetricsModel):
    total_duration_ms: NumericAggregate
    success_total_duration_ms: NumericAggregate
    unsuccessful_total_duration_ms: NumericAggregate
    response_headers_ms: NumericAggregate
    first_data_ms: NumericAggregate
    first_content_ms: NumericAggregate
    stream_duration_ms: NumericAggregate
    create_request_ms: NumericAggregate
    polling_total_ms: NumericAggregate
    polling_sleep_ms: NumericAggregate
    timing_completeness: CountDistribution


class OperationUsageAggregate(UsageAggregate):
    retry_extra_usage: RetryUsageAggregate


class OperationDimension(_FrozenMetricsModel):
    operation_kind: str
    operation_name: str
    traffic_role: str
    model_id: str | None = None


class OperationMetricBucket(_FrozenMetricsModel):
    dimension: OperationDimension
    stability: OperationStability
    usage: OperationUsageAggregate
    timing: OperationTimingAggregate
    evidence: EvidenceMembership


class CaseInvocationMetric(_FrozenMetricsModel):
    case_id: str
    invocation_id: str
    operation_count: int = Field(ge=0)
    outcomes: CountDistribution
    operation_success_rate: RatioAggregate
    usage: OperationUsageAggregate
    operation_duration_ms: NumericAggregate
    model_ids: CountDistribution
    operation_kinds: CountDistribution
    evidence: EvidenceMembership


class RequestGroupStability(_FrozenMetricsModel):
    group_count: int = Field(ge=0)
    attempt_count: NumericAggregate
    retried_group_count: int = Field(ge=0)
    retry_rate: RatioAggregate
    first_transport: CountDistribution
    final_transport: CountDistribution
    first_transport_response_rate: RatioAggregate
    final_transport_response_rate: RatioAggregate
    first_http_success_rate: RatioAggregate
    final_http_success_rate: RatioAggregate
    first_business_success_rate: RatioAggregate
    final_business_success_rate: RatioAggregate
    http_retry_rescue_rate: RatioAggregate
    business_retry_rescue_rate: RatioAggregate


class RequestGroupTimingAggregate(_FrozenMetricsModel):
    total_duration_ms: NumericAggregate
    retry_wait_ms: NumericAggregate
    first_attempt_duration_ms: NumericAggregate
    retry_attempt_duration_ms: NumericAggregate


class RequestGroupDimension(_FrozenMetricsModel):
    interface_id: str
    protocol: str
    traffic_role: str


class RequestGroupMetricBucket(_FrozenMetricsModel):
    dimension: RequestGroupDimension
    stability: RequestGroupStability
    timing: RequestGroupTimingAggregate
    retry_usage: RetryUsageAggregate
    evidence: EvidenceMembership


class RequestEventStability(_FrozenMetricsModel):
    event_count: int = Field(ge=0)
    transport: CountDistribution
    business_status: CountDistribution
    timeout_rate: RatioAggregate
    http_5xx_rate: RatioAggregate
    http_429_rate: RatioAggregate
    business_success_rate: RatioAggregate
    http_429_count: int = Field(ge=0)


class RequestEventTimingAggregate(_FrozenMetricsModel):
    all_duration_ms: NumericAggregate
    timeout_duration_ms: NumericAggregate
    transport_error_duration_ms: NumericAggregate
    http_2xx_duration_ms: NumericAggregate
    http_3xx_duration_ms: NumericAggregate
    http_4xx_duration_ms: NumericAggregate
    http_5xx_duration_ms: NumericAggregate


class RequestEventUsageCoverage(_FrozenMetricsModel):
    known_event_count: int = Field(ge=0)
    missing_event_count: int = Field(ge=0)
    input_tokens: NumericAggregate
    output_tokens: NumericAggregate
    media_count: NumericAggregate


class RequestEventDimension(_FrozenMetricsModel):
    interface_id: str
    protocol: str
    traffic_role: str


class RequestEventMetricBucket(_FrozenMetricsModel):
    dimension: RequestEventDimension
    stability: RequestEventStability
    timing: RequestEventTimingAggregate
    usage_coverage: RequestEventUsageCoverage
    evidence: EvidenceMembership


class RunMetricSummary(_FrozenMetricsModel):
    operation: OperationStability
    usage: OperationUsageAggregate
    operation_timing: OperationTimingAggregate
    request_groups: RequestGroupStability
    request_group_timing: RequestGroupTimingAggregate
    request_events: RequestEventStability
    request_event_timing: RequestEventTimingAggregate


class RunMetricsResult(_FrozenMetricsModel):
    schema_version: Literal["quality.run-metrics.v1"] = RUN_METRICS_SCHEMA_VERSION
    aggregation_version: Literal["p1-run-metrics.v1"] = RUN_METRICS_AGGREGATION_VERSION
    run_id: str
    status: RunMetricsStatus
    generated_at: datetime
    run_status: RunStatus
    source_evidence: SourceEvidence
    integrity: MetricsIntegrity
    exclusions: Exclusions
    run_metrics: RunMetricSummary | None
    case_invocations: tuple[CaseInvocationMetric, ...] = ()
    operation_buckets: tuple[OperationMetricBucket, ...] = ()
    request_group_buckets: tuple[RequestGroupMetricBucket, ...] = ()
    request_event_buckets: tuple[RequestEventMetricBucket, ...] = ()
    issues: tuple[MetricsIssue, ...] = ()

    @field_validator("run_id")
    @classmethod
    def _validate_run_id(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("run_id must not be empty")
        return text

    @field_validator("generated_at")
    @classmethod
    def _validate_generated_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("generated_at must include timezone information")
        return value

    @model_validator(mode="after")
    def _validate_status_payload(self) -> RunMetricsResult:
        if self.status is RunMetricsStatus.FAILED:
            if self.run_metrics is not None or any(
                (
                    self.case_invocations,
                    self.operation_buckets,
                    self.request_group_buckets,
                    self.request_event_buckets,
                )
            ):
                raise ValueError("failed metrics must not contain aggregate payloads")
        elif self.run_metrics is None:
            raise ValueError("non-failed metrics require a run_metrics payload")
        return self
