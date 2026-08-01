from __future__ import annotations

from datetime import datetime
from enum import Enum
import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from quality.flaky_models import FlakyStateSummary, FlakyTransitionRecord
from quality.metrics_models import MetricCompleteness


P1_OBSERVATION_SCHEMA_VERSION = "quality.p1-observation.v1"
P1_OBSERVATION_REPORT_VERSION = "p1-observation-report.v1"
P1_OBSERVATION_MANIFEST_VERSION = "quality.p1-observation-manifest.v1"


class SourceExpectation(str, Enum):
    REQUIRED = "required"
    DISABLED = "disabled"


class SourceStatus(str, Enum):
    AVAILABLE = "available"
    DEGRADED = "degraded"
    NO_DATA = "no_data"
    FAILED = "failed"
    MISSING = "missing"
    INCOMPATIBLE = "incompatible"
    DISABLED = "disabled"


class P1ReportStatus(str, Enum):
    COMPLETE = "complete"
    DEGRADED = "degraded"
    NO_DATA = "no_data"


class AttentionLevel(str, Enum):
    INFO = "info"
    REVIEW = "review"
    ACTION_REQUIRED = "action_required"


class _FrozenObservationModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)


class P1SourceSummary(_FrozenObservationModel):
    source_name: str
    expectation: SourceExpectation
    status: SourceStatus
    artifact_path: str | None = None
    schema_version: str | None = None
    producer_version: str | None = None
    sha256: str | None = None
    issue_codes: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()

    @field_validator("source_name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        return _required_text(value, 64)

    @field_validator(
        "artifact_path", "schema_version", "producer_version", "sha256"
    )
    @classmethod
    def _validate_optional_text(cls, value: str | None) -> str | None:
        return _optional_text(value, 256)

    @field_validator("issue_codes", "evidence_refs")
    @classmethod
    def _validate_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(sorted({_required_text(item, 512) for item in value}))
        return normalized

    @model_validator(mode="after")
    def _validate_expectation(self) -> P1SourceSummary:
        if self.expectation is SourceExpectation.DISABLED:
            if self.status is not SourceStatus.DISABLED:
                raise ValueError("disabled source expectation requires disabled status")
        elif self.status is SourceStatus.DISABLED:
            raise ValueError("required source cannot use disabled status")
        if self.status in {
            SourceStatus.AVAILABLE,
            SourceStatus.DEGRADED,
            SourceStatus.NO_DATA,
        }:
            if self.artifact_path is None or self.sha256 is None:
                raise ValueError("consumable source requires artifact_path and sha256")
        return self


class P1P0Section(_FrozenObservationModel):
    gate_mode: str
    gate_overall: str
    integrity_status: str
    case_total: int = Field(ge=0)
    case_passed: int = Field(ge=0)
    case_failed: int = Field(ge=0)
    case_error: int = Field(ge=0)
    case_skipped: int = Field(ge=0)
    request_total: int = Field(ge=0)
    http_5xx_count: int = Field(ge=0)
    timeout_count: int = Field(ge=0)
    failure_categories: dict[str, int] = Field(default_factory=dict)
    evidence_refs: tuple[str, ...]

    @field_validator("gate_mode", "gate_overall", "integrity_status")
    @classmethod
    def _validate_text(cls, value: str) -> str:
        return _required_text(value, 64)

    @field_validator("failure_categories")
    @classmethod
    def _validate_counts(cls, value: dict[str, int]) -> dict[str, int]:
        return _count_map(value)

    @field_validator("evidence_refs")
    @classmethod
    def _validate_evidence(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(sorted({_required_text(item, 512) for item in value}))


class P1MetricObservation(_FrozenObservationModel):
    metric_id: str
    grain: str
    dimension: dict[str, str | None] = Field(default_factory=dict)
    metric_name: str
    value: int | float | None = None
    total: int | float | None = None
    minimum: int | float | None = None
    maximum: int | float | None = None
    numerator: int | None = Field(default=None, ge=0)
    sample_size: int = Field(ge=0)
    missing_sample_size: int = Field(default=0, ge=0)
    completeness: MetricCompleteness
    algorithm_version: str
    source_artifact: str
    evidence_refs: tuple[str, ...] = ()

    @field_validator(
        "metric_id", "grain", "metric_name", "algorithm_version", "source_artifact"
    )
    @classmethod
    def _validate_required_text(cls, value: str) -> str:
        return _required_text(value, 512)

    @field_validator("dimension")
    @classmethod
    def _validate_dimension(
        cls, value: dict[str, str | None]
    ) -> dict[str, str | None]:
        return {
            _required_text(key, 128): _optional_text(item, 256)
            for key, item in sorted(value.items())
        }

    @field_validator("evidence_refs")
    @classmethod
    def _validate_evidence(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(sorted({_required_text(item, 512) for item in value}))

    @model_validator(mode="after")
    def _validate_metric(self) -> P1MetricObservation:
        numeric_values = (self.value, self.total, self.minimum, self.maximum)
        for item in numeric_values:
            if item is not None and (
                isinstance(item, bool)
                or not math.isfinite(float(item))
                or float(item) < 0
            ):
                raise ValueError("metric values must be finite and nonnegative")
        if self.sample_size == 0:
            if any(item is not None for item in numeric_values) or self.numerator not in {
                None,
                0,
            }:
                raise ValueError("metric without samples cannot contain a known value")
        if self.numerator is not None:
            if self.numerator > self.sample_size:
                raise ValueError("numerator cannot exceed sample_size")
            expected = (
                None
                if self.sample_size == 0
                else round(self.numerator / self.sample_size, 6)
            )
            if self.value != expected:
                raise ValueError("ratio value must equal numerator / sample_size")
        return self


class P1KnownTotal(_FrozenObservationModel):
    sample_size: int = Field(ge=0)
    missing_sample_size: int = Field(default=0, ge=0)
    total: int | float | None = None
    completeness: MetricCompleteness

    @model_validator(mode="after")
    def _validate_total(self) -> P1KnownTotal:
        if self.sample_size == 0 and self.total is not None:
            raise ValueError("known total must be null when sample_size is zero")
        if self.sample_size > 0 and self.total is None:
            raise ValueError("known total is required when samples exist")
        if self.total is not None and (
            isinstance(self.total, bool)
            or not math.isfinite(float(self.total))
            or float(self.total) < 0
        ):
            raise ValueError("known total must be finite and nonnegative")
        return self


class P1UsageCoverage(_FrozenObservationModel):
    eligible_operation_count: int = Field(ge=0)
    complete_count: int = Field(ge=0)
    partial_count: int = Field(ge=0)
    missing_count: int = Field(ge=0)
    not_applicable_count: int = Field(ge=0)
    input_tokens: P1KnownTotal
    output_tokens: P1KnownTotal
    media_count: P1KnownTotal
    media_duration_ms: P1KnownTotal
    retry_input_tokens: P1KnownTotal
    retry_output_tokens: P1KnownTotal
    retry_media_count: P1KnownTotal
    retry_missing_attempt_count: int = Field(ge=0)
    missing_operation_refs: tuple[str, ...] = ()
    missing_event_refs: tuple[str, ...] = ()
    source_artifact: str

    @field_validator("missing_operation_refs", "missing_event_refs")
    @classmethod
    def _validate_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(sorted({_required_text(item, 512) for item in value}))

    @field_validator("source_artifact")
    @classmethod
    def _validate_source(cls, value: str) -> str:
        return _required_text(value, 512)

    @model_validator(mode="after")
    def _validate_operation_coverage(self) -> P1UsageCoverage:
        if (
            self.complete_count
            + self.partial_count
            + self.missing_count
            + self.not_applicable_count
            != self.eligible_operation_count
        ):
            raise ValueError("usage completeness counts must equal operation count")
        return self


class P1MetricsSection(_FrozenObservationModel):
    metrics_status: str
    aggregation_version: str
    workload_operation_count: int = Field(ge=0)
    request_group_count: int = Field(ge=0)
    request_event_count: int = Field(ge=0)
    operation_outcomes: dict[str, int] = Field(default_factory=dict)
    control_operation_count: int = Field(ge=0)
    control_group_count: int = Field(ge=0)
    control_event_count: int = Field(ge=0)
    unknown_operation_count: int = Field(ge=0)
    unknown_group_count: int = Field(ge=0)
    unknown_event_count: int = Field(ge=0)
    unknown_role_count: int = Field(ge=0)
    unassigned_event_count: int = Field(ge=0)
    observations: tuple[P1MetricObservation, ...] = ()
    source_artifact: str

    @field_validator("metrics_status", "aggregation_version", "source_artifact")
    @classmethod
    def _validate_text(cls, value: str) -> str:
        return _required_text(value, 512)

    @field_validator("operation_outcomes")
    @classmethod
    def _validate_outcomes(cls, value: dict[str, int]) -> dict[str, int]:
        return _count_map(value)

    @model_validator(mode="after")
    def _validate_unknown_count(self) -> P1MetricsSection:
        if self.unknown_role_count != (
            self.unknown_operation_count
            + self.unknown_group_count
            + self.unknown_event_count
        ):
            raise ValueError("unknown role count must equal per-grain unknown counts")
        return self


class P1FlakySection(_FrozenObservationModel):
    import_status: str | None = None
    evaluation_status: str | None = None
    rule_version: str | None = None
    projection_version: str | None = None
    import_database_schema_version: int | None = Field(default=None, ge=0)
    evaluation_database_schema_version: int | None = Field(default=None, ge=0)
    quick_check: str | None = None
    affected_count: int = Field(default=0, ge=0)
    evaluated_count: int = Field(default=0, ge=0)
    transitioned_count: int = Field(default=0, ge=0)
    stale_count: int = Field(default=0, ge=0)
    newly_suspected: tuple[FlakyStateSummary, ...] = ()
    newly_confirmed: tuple[FlakyStateSummary, ...] = ()
    ongoing_confirmed: tuple[FlakyStateSummary, ...] = ()
    quarantined: tuple[FlakyStateSummary, ...] = ()
    recovering: tuple[FlakyStateSummary, ...] = ()
    recovered: tuple[FlakyStateSummary, ...] = ()
    overdue: tuple[FlakyStateSummary, ...] = ()
    transitions: tuple[FlakyTransitionRecord, ...] = ()
    issue_codes: tuple[str, ...] = ()
    source_artifacts: tuple[str, ...] = ()

    @field_validator(
        "import_status",
        "evaluation_status",
        "rule_version",
        "projection_version",
        "quick_check",
    )
    @classmethod
    def _validate_optional_text(cls, value: str | None) -> str | None:
        return _optional_text(value, 128)

    @field_validator("issue_codes", "source_artifacts")
    @classmethod
    def _validate_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(sorted({_required_text(item, 512) for item in value}))


class P1AttentionItem(_FrozenObservationModel):
    attention_code: str
    level: AttentionLevel
    title: str
    summary: str
    owner: str | None = None
    expires_at: datetime | None = None
    source_name: str
    related_ids: tuple[str, ...] = ()
    suggested_action: str

    @field_validator("attention_code", "source_name")
    @classmethod
    def _validate_code(cls, value: str) -> str:
        return _required_text(value, 128)

    @field_validator("title", "summary", "suggested_action")
    @classmethod
    def _validate_content(cls, value: str) -> str:
        return _required_text(value, 500)

    @field_validator("owner")
    @classmethod
    def _validate_owner(cls, value: str | None) -> str | None:
        return _optional_text(value, 128)

    @field_validator("expires_at")
    @classmethod
    def _validate_expiry(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("expires_at must include timezone information")
        return value

    @field_validator("related_ids")
    @classmethod
    def _validate_related_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(sorted({_required_text(item, 512) for item in value}))


class P1RunOverview(_FrozenObservationModel):
    run_id: str
    report_status: P1ReportStatus
    p0_gate_mode: str | None = None
    p0_gate_overall: str | None = None
    p0_integrity_status: str | None = None
    case_total: int = Field(default=0, ge=0)
    case_failed: int = Field(default=0, ge=0)
    case_error: int = Field(default=0, ge=0)
    operation_count: int = Field(default=0, ge=0)
    workload_operation_count: int = Field(default=0, ge=0)
    operation_success_count: int = Field(default=0, ge=0)
    operation_failed_count: int = Field(default=0, ge=0)
    operation_timeout_count: int = Field(default=0, ge=0)
    usage_complete_count: int = Field(default=0, ge=0)
    usage_partial_count: int = Field(default=0, ge=0)
    usage_missing_count: int = Field(default=0, ge=0)
    flaky_affected_count: int = Field(default=0, ge=0)
    flaky_transitioned_count: int = Field(default=0, ge=0)
    flaky_stale_count: int = Field(default=0, ge=0)
    newly_suspected_count: int = Field(default=0, ge=0)
    newly_confirmed_count: int = Field(default=0, ge=0)
    quarantined_count: int = Field(default=0, ge=0)
    recovering_count: int = Field(default=0, ge=0)
    recovered_count: int = Field(default=0, ge=0)
    overdue_count: int = Field(default=0, ge=0)
    required_source_failure_count: int = Field(default=0, ge=0)
    generated_at: datetime

    @field_validator("run_id")
    @classmethod
    def _validate_run_id(cls, value: str) -> str:
        return _required_text(value, 256)

    @field_validator("p0_gate_mode", "p0_gate_overall", "p0_integrity_status")
    @classmethod
    def _validate_optional_text(cls, value: str | None) -> str | None:
        return _optional_text(value, 64)

    @field_validator("generated_at")
    @classmethod
    def _validate_generated_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("generated_at must include timezone information")
        return value


class P1IntegritySummary(_FrozenObservationModel):
    issue_codes: tuple[str, ...] = ()
    degraded_sources: tuple[str, ...] = ()
    required_source_failure_count: int = Field(default=0, ge=0)
    evidence_refs: tuple[str, ...] = ()

    @field_validator("issue_codes", "degraded_sources", "evidence_refs")
    @classmethod
    def _validate_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(sorted({_required_text(item, 512) for item in value}))


class P1DisplayWindow(_FrozenObservationModel):
    category: str
    total_count: int = Field(ge=0)
    shown_count: int = Field(ge=0)
    omitted_count: int = Field(ge=0)
    source_artifact: str

    @field_validator("category", "source_artifact")
    @classmethod
    def _validate_text(cls, value: str) -> str:
        return _required_text(value, 512)

    @model_validator(mode="after")
    def _validate_counts(self) -> P1DisplayWindow:
        if self.shown_count + self.omitted_count != self.total_count:
            raise ValueError("shown_count + omitted_count must equal total_count")
        return self


class P1ObservationReport(_FrozenObservationModel):
    schema_version: Literal["quality.p1-observation.v1"] = P1_OBSERVATION_SCHEMA_VERSION
    report_version: Literal["p1-observation-report.v1"] = P1_OBSERVATION_REPORT_VERSION
    run_id: str
    generated_at: datetime
    report_status: P1ReportStatus
    overview: P1RunOverview
    sources: tuple[P1SourceSummary, ...]
    p0: P1P0Section | None = None
    metrics: P1MetricsSection | None = None
    usage_coverage: P1UsageCoverage | None = None
    flaky: P1FlakySection | None = None
    display_windows: tuple[P1DisplayWindow, ...] = ()
    attention_items: tuple[P1AttentionItem, ...] = ()
    integrity: P1IntegritySummary

    @field_validator("run_id")
    @classmethod
    def _validate_run_id(cls, value: str) -> str:
        return _required_text(value, 256)

    @field_validator("generated_at")
    @classmethod
    def _validate_generated_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("generated_at must include timezone information")
        return value

    @model_validator(mode="after")
    def _validate_identity(self) -> P1ObservationReport:
        if self.overview.run_id != self.run_id:
            raise ValueError("overview run_id must match report run_id")
        if self.overview.report_status is not self.report_status:
            raise ValueError("overview report_status must match report status")
        names = [item.source_name for item in self.sources]
        if len(names) != len(set(names)):
            raise ValueError("source names must be unique")
        return self


def _required_text(value: str, maximum: int) -> str:
    text = value.strip()
    if not text:
        raise ValueError("value must not be empty")
    return text[:maximum]


def _optional_text(value: str | None, maximum: int) -> str | None:
    if value is None:
        return None
    return _required_text(value, maximum)


def _count_map(value: dict[str, int]) -> dict[str, int]:
    normalized: dict[str, int] = {}
    for name, count in value.items():
        if isinstance(count, bool) or count < 0:
            raise ValueError("count values must be nonnegative integers")
        normalized[_required_text(name, 128)] = count
    return dict(sorted(normalized.items()))
