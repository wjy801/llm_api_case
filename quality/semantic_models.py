from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from quality.models import IssueSeverity, Protocol


SEMANTIC_SCHEMA_VERSION = "quality.semantic.v1"
SEMANTIC_MANIFEST_VERSION = "quality.semantic-manifest.v1"
SEMANTIC_MERGE_VERSION = "p1-semantic-merge.v1"


class OperationKind(str, Enum):
    HTTP = "http"
    SSE = "sse"
    POLLING = "polling"
    ASYNC_TASK = "async_task"


class TrafficRole(str, Enum):
    WORKLOAD = "workload"
    CONTROL = "control"
    UNKNOWN = "unknown"


class OperationOutcome(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    TIMEOUT = "timeout"
    INTERRUPTED = "interrupted"
    INCOMPLETE = "incomplete"
    UNKNOWN = "unknown"


class AttemptTransportOutcome(str, Enum):
    RESPONSE = "response"
    TIMEOUT = "timeout"
    ERROR = "error"


class PollingOutcome(str, Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    TIMEOUT = "timeout"
    UNKNOWN = "unknown"
    INTERRUPTED = "interrupted"


class StreamOutcome(str, Enum):
    COMPLETE = "complete"
    INTERRUPTED = "interrupted"
    ERROR = "error"
    NOT_CONSUMED = "not_consumed"


class UsageCompleteness(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    MISSING = "missing"
    NOT_APPLICABLE = "not_applicable"


class TimingCompleteness(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    MISSING = "missing"


class RecordCompleteness(str, Enum):
    COMPLETE = "complete"
    INCOMPLETE = "incomplete"


class _FrozenSemanticModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)


class _VersionedSemanticModel(_FrozenSemanticModel):
    schema_version: Literal["quality.semantic.v1"] = SEMANTIC_SCHEMA_VERSION


class _SemanticIdentity(_VersionedSemanticModel):
    run_id: str
    execution_id: str
    worker_id: str
    case_id: str
    invocation_id: str
    created_at: datetime

    @field_validator("run_id", "execution_id", "worker_id", "case_id", "invocation_id")
    @classmethod
    def _validate_identity(cls, value: str) -> str:
        return _require_text(value)

    @field_validator("created_at")
    @classmethod
    def _validate_created_at(cls, value: datetime) -> datetime:
        return _require_timezone(value)


class RequestGroupRecord(_SemanticIdentity):
    request_group_id: str
    operation_id: str
    polling_session_id: str | None = None
    interface_id: str
    method: str
    url_template: str
    protocol: Protocol
    traffic_role: TrafficRole
    attempt_event_ids: tuple[str, ...]
    attempt_count: int = Field(ge=1)
    configured_max_attempts: int = Field(ge=1)
    retry_wait_ms: float = Field(ge=0)
    started_at: datetime
    ended_at: datetime
    total_duration_ms: float = Field(ge=0)
    first_transport_outcome: AttemptTransportOutcome
    final_transport_outcome: AttemptTransportOutcome
    first_status_code: int | None = None
    final_status_code: int | None = None
    final_request_event_id: str
    completeness: RecordCompleteness

    @field_validator(
        "request_group_id",
        "operation_id",
        "interface_id",
        "url_template",
        "final_request_event_id",
    )
    @classmethod
    def _validate_required_text(cls, value: str) -> str:
        return _require_text(value)

    @field_validator("polling_session_id")
    @classmethod
    def _validate_optional_text(cls, value: str | None) -> str | None:
        return _optional_text(value)

    @field_validator("method", mode="before")
    @classmethod
    def _normalize_method(cls, value: object) -> str:
        if not isinstance(value, str):
            raise ValueError("method must be a string")
        return _require_text(value).upper()

    @field_validator("attempt_event_ids")
    @classmethod
    def _validate_attempt_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("attempt_event_ids must not be empty")
        normalized = tuple(_require_text(item) for item in value)
        if len(set(normalized)) != len(normalized):
            raise ValueError("attempt_event_ids must be unique")
        return normalized

    @field_validator("started_at", "ended_at")
    @classmethod
    def _validate_time(cls, value: datetime) -> datetime:
        return _require_timezone(value)

    @model_validator(mode="after")
    def _validate_record(self) -> RequestGroupRecord:
        _ensure_time_order(self.started_at, self.ended_at)
        if self.attempt_count != len(self.attempt_event_ids):
            raise ValueError("attempt_count must equal attempt_event_ids length")
        if self.final_request_event_id not in self.attempt_event_ids:
            raise ValueError("final_request_event_id must belong to attempt_event_ids")
        return self


class PollingSessionRecord(_SemanticIdentity):
    polling_session_id: str
    operation_id: str
    request_group_ids: tuple[str, ...]
    poll_count: int = Field(ge=0)
    started_at: datetime
    ended_at: datetime
    total_duration_ms: float = Field(ge=0)
    sleep_duration_ms: float = Field(ge=0)
    final_outcome: PollingOutcome
    terminal_status: str | None = None
    observed_state_sequence: tuple[str, ...] = ()
    first_observed_offsets_ms: dict[str, float] = Field(default_factory=dict)
    completeness: RecordCompleteness

    @field_validator("polling_session_id", "operation_id")
    @classmethod
    def _validate_required_text(cls, value: str) -> str:
        return _require_text(value)

    @field_validator("terminal_status")
    @classmethod
    def _validate_terminal_status(cls, value: str | None) -> str | None:
        return _optional_text(value)

    @field_validator("request_group_ids", "observed_state_sequence")
    @classmethod
    def _validate_text_tuple(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_require_text(item) for item in value)

    @field_validator("first_observed_offsets_ms")
    @classmethod
    def _validate_offsets(cls, value: dict[str, float]) -> dict[str, float]:
        return {_require_text(name): _nonnegative_float(offset) for name, offset in value.items()}

    @field_validator("started_at", "ended_at")
    @classmethod
    def _validate_time(cls, value: datetime) -> datetime:
        return _require_timezone(value)

    @model_validator(mode="after")
    def _validate_record(self) -> PollingSessionRecord:
        _ensure_time_order(self.started_at, self.ended_at)
        if self.poll_count != len(self.request_group_ids):
            raise ValueError("poll_count must equal request_group_ids length")
        return self


class OperationUsage(_FrozenSemanticModel):
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    media_count: int | None = Field(default=None, ge=0)
    media_duration_ms: float | None = Field(default=None, ge=0)
    source_request_event_ids: tuple[str, ...] = ()
    completeness: UsageCompleteness
    missing_request_event_ids: tuple[str, ...] = ()

    @field_validator("source_request_event_ids", "missing_request_event_ids")
    @classmethod
    def _validate_event_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(_require_text(item) for item in value)
        if len(set(normalized)) != len(normalized):
            raise ValueError("request event ids must be unique")
        return normalized

    @model_validator(mode="after")
    def _validate_completeness(self) -> OperationUsage:
        values = (self.input_tokens, self.output_tokens, self.media_count, self.media_duration_ms)
        if self.completeness in {UsageCompleteness.MISSING, UsageCompleteness.NOT_APPLICABLE}:
            if any(value is not None for value in values) or self.source_request_event_ids:
                raise ValueError("missing/not_applicable usage cannot contain known values")
        if self.completeness is UsageCompleteness.COMPLETE and self.missing_request_event_ids:
            raise ValueError("complete usage cannot contain missing request events")
        return self


class OperationTiming(_FrozenSemanticModel):
    total_duration_ms: float = Field(ge=0)
    response_headers_ms: float | None = Field(default=None, ge=0)
    first_data_ms: float | None = Field(default=None, ge=0)
    first_content_ms: float | None = Field(default=None, ge=0)
    stream_duration_ms: float | None = Field(default=None, ge=0)
    create_request_ms: float | None = Field(default=None, ge=0)
    polling_total_ms: float | None = Field(default=None, ge=0)
    polling_sleep_ms: float | None = Field(default=None, ge=0)
    timing_completeness: TimingCompleteness


class OperationRecord(_SemanticIdentity):
    operation_id: str
    operation_kind: OperationKind
    operation_name: str
    traffic_role: TrafficRole
    model_id: str | None = None
    request_group_ids: tuple[str, ...]
    polling_session_ids: tuple[str, ...] = ()
    started_at: datetime
    ended_at: datetime
    outcome: OperationOutcome
    stream_outcome: StreamOutcome | None = None
    timing: OperationTiming
    usage: OperationUsage
    evidence_refs: tuple[str, ...] = ()
    completeness: RecordCompleteness

    @field_validator("operation_id", "operation_name")
    @classmethod
    def _validate_required_text(cls, value: str) -> str:
        return _require_text(value)

    @field_validator("model_id")
    @classmethod
    def _validate_model_id(cls, value: str | None) -> str | None:
        normalized = _optional_text(value)
        if normalized is not None and len(normalized) > 128:
            raise ValueError("model_id must not exceed 128 characters")
        return normalized

    @field_validator("request_group_ids", "polling_session_ids", "evidence_refs")
    @classmethod
    def _validate_text_tuple(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_require_text(item) for item in value)

    @field_validator("started_at", "ended_at")
    @classmethod
    def _validate_time(cls, value: datetime) -> datetime:
        return _require_timezone(value)

    @model_validator(mode="after")
    def _validate_record(self) -> OperationRecord:
        _ensure_time_order(self.started_at, self.ended_at)
        if self.operation_kind is OperationKind.SSE and self.stream_outcome is None:
            raise ValueError("SSE operation requires stream_outcome")
        if self.operation_kind is not OperationKind.SSE and self.stream_outcome is not None:
            raise ValueError("stream_outcome only applies to SSE operations")
        return self


class SemanticIntegrityIssue(_VersionedSemanticModel):
    run_id: str
    severity: IssueSeverity
    source: str
    code: str
    message: str
    related_id: str | None = None
    created_at: datetime

    @field_validator("run_id", "source", "code", "message")
    @classmethod
    def _validate_required_text(cls, value: str) -> str:
        return _require_text(value)

    @field_validator("related_id")
    @classmethod
    def _validate_optional_text(cls, value: str | None) -> str | None:
        return _optional_text(value)

    @field_validator("created_at")
    @classmethod
    def _validate_created_at(cls, value: datetime) -> datetime:
        return _require_timezone(value)


def _require_text(value: str) -> str:
    text = value.strip()
    if not text:
        raise ValueError("value must not be empty")
    return text


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    return _require_text(value)


def _require_timezone(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must include timezone information")
    return value


def _ensure_time_order(started_at: datetime, ended_at: datetime) -> None:
    if ended_at < started_at:
        raise ValueError("ended_at must be greater than or equal to started_at")


def _nonnegative_float(value: float) -> float:
    parsed = float(value)
    if parsed < 0:
        raise ValueError("value must be greater than or equal to 0")
    return parsed
