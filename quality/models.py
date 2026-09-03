from __future__ import annotations

from datetime import datetime
from enum import Enum
import re
from typing import Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


SCHEMA_VERSION = "quality.v2"
LEGACY_SCHEMA_VERSION = "quality.v1"

ManualOverrideValue: TypeAlias = str | int | float | bool | None


class RunStatus(str, Enum):
    FINISHED = "finished"
    INTERRUPTED = "interrupted"
    PARTIAL = "partial"


class RunKind(str, Enum):
    NORMAL = "NORMAL"
    FLAKY_PROBE = "FLAKY_PROBE"
    LEGACY_UNKNOWN = "LEGACY_UNKNOWN"


class IntegrityStatus(str, Enum):
    COMPLETE = "complete"
    DEGRADED = "degraded"
    FAILED = "failed"


class CaseStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    ERROR = "error"
    XFAILED = "xfailed"
    XPASSED = "xpassed"


class CasePhase(str, Enum):
    COLLECTION = "collection"
    SETUP = "setup"
    CALL = "call"
    TEARDOWN = "teardown"


class Protocol(str, Enum):
    HTTP = "http"
    SSE = "sse"
    POLLING = "polling"


class BusinessStatus(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    UNKNOWN = "unknown"


class CostSource(str, Enum):
    NONE = "none"
    ESTIMATED = "estimated"
    BILLING = "billing"


class FailureCategory(str, Enum):
    PRODUCT_DEFECT = "PRODUCT_DEFECT"
    TEST_DEFECT = "TEST_DEFECT"
    FRAMEWORK_DEFECT = "FRAMEWORK_DEFECT"
    ENVIRONMENT = "ENVIRONMENT"
    CONFIGURATION = "CONFIGURATION"
    TRANSIENT = "TRANSIENT"
    UNKNOWN = "UNKNOWN"


class OwnerDomain(str, Enum):
    PRODUCT = "product"
    TEST = "test"
    FRAMEWORK = "framework"
    ENVIRONMENT = "environment"
    CONFIGURATION = "configuration"
    UNKNOWN = "unknown"


class Confidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class IssueSeverity(str, Enum):
    INFO = "info"
    WARN = "warn"
    ERROR = "error"


class FrozenQualityModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)


class VersionedQualityModel(FrozenQualityModel):
    schema_version: Literal["quality.v1", "quality.v2"] = SCHEMA_VERSION


class IntegrityIssue(VersionedQualityModel):
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
        return _require_non_empty(value)

    @field_validator("related_id")
    @classmethod
    def _validate_optional_text(cls, value: str | None) -> str | None:
        return _optional_non_empty(value)

    @field_validator("created_at")
    @classmethod
    def _validate_created_at(cls, value: datetime) -> datetime:
        return _require_timezone(value)


class RunRecord(VersionedQualityModel):
    run_id: str
    job_name: str | None = None
    build_number: str | None = None
    branch: str | None = None
    commit_sha: str | None = None
    trigger: str
    environment: str
    start_time: datetime
    end_time: datetime | None = None
    status: RunStatus
    integrity_status: IntegrityStatus
    integrity_issues: tuple[IntegrityIssue, ...] = ()
    run_kind: RunKind | None = None
    policy_revision: str | None = None
    controller_commit_sha: str | None = None
    attempt_id: str | None = None
    trigger_id: str | None = None
    plan_digest: str | None = None
    round_no: int | None = Field(default=None, ge=1)
    target_commit_sha: str | None = None
    jenkins_job_name: str | None = None
    jenkins_build_number: str | None = None
    fact_schema_version: str | None = None
    plugin_version: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _read_legacy_v1(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        payload = dict(value)
        if payload.get("schema_version") == LEGACY_SCHEMA_VERSION:
            payload.update(
                {
                    "run_kind": RunKind.LEGACY_UNKNOWN,
                    "policy_revision": None,
                    "controller_commit_sha": None,
                    "attempt_id": None,
                    "trigger_id": None,
                    "plan_digest": None,
                    "round_no": None,
                    "target_commit_sha": None,
                    "jenkins_job_name": None,
                    "jenkins_build_number": None,
                    "fact_schema_version": None,
                    "plugin_version": None,
                }
            )
        return payload

    @field_validator("run_id", "trigger", "environment")
    @classmethod
    def _validate_required_text(cls, value: str) -> str:
        return _require_non_empty(value)

    @field_validator(
        "job_name",
        "build_number",
        "branch",
        "commit_sha",
        "policy_revision",
        "controller_commit_sha",
        "attempt_id",
        "trigger_id",
        "plan_digest",
        "target_commit_sha",
        "jenkins_job_name",
        "jenkins_build_number",
        "fact_schema_version",
        "plugin_version",
    )
    @classmethod
    def _validate_optional_text(cls, value: str | None) -> str | None:
        return _optional_non_empty(value)

    @field_validator("start_time")
    @classmethod
    def _validate_start_time(cls, value: datetime) -> datetime:
        return _require_timezone(value)

    @field_validator("end_time")
    @classmethod
    def _validate_end_time(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return _require_timezone(value)

    @model_validator(mode="after")
    def _validate_time_order(self) -> RunRecord:
        _ensure_time_order(self.start_time, self.end_time)
        if self.schema_version == LEGACY_SCHEMA_VERSION:
            if self.run_kind is not RunKind.LEGACY_UNKNOWN:
                raise ValueError("quality.v1 runs must map to LEGACY_UNKNOWN")
            return self
        if self.run_kind is None:
            raise ValueError("quality.v2 run_kind is required")
        if self.run_kind is RunKind.LEGACY_UNKNOWN:
            raise ValueError("quality.v2 producers cannot write LEGACY_UNKNOWN")
        common_required = {
            "job_name": self.job_name,
            "build_number": self.build_number,
            "branch": self.branch,
            "commit_sha": self.commit_sha,
            "policy_revision": self.policy_revision,
            "controller_commit_sha": self.controller_commit_sha,
            "fact_schema_version": self.fact_schema_version,
            "plugin_version": self.plugin_version,
        }
        probe_fields = {
            "attempt_id": self.attempt_id,
            "trigger_id": self.trigger_id,
            "plan_digest": self.plan_digest,
            "round_no": self.round_no,
            "target_commit_sha": self.target_commit_sha,
            "jenkins_job_name": self.jenkins_job_name,
            "jenkins_build_number": self.jenkins_build_number,
        }
        if self.run_kind is RunKind.NORMAL:
            present = sorted(name for name, value in probe_fields.items() if value is not None)
            if present:
                raise ValueError(
                    f"NORMAL run must not contain Probe fields: {', '.join(present)}"
                )
            if self.commit_sha is not None:
                _require_sha(self.commit_sha, "commit_sha")
            if self.controller_commit_sha is not None:
                _require_sha(self.controller_commit_sha, "controller_commit_sha")
        else:
            missing = sorted(
                name for name, value in common_required.items() if value is None
            )
            if missing:
                raise ValueError(
                    f"quality.v2 run fields are required: {', '.join(missing)}"
                )
            missing_probe = sorted(
                name for name, value in probe_fields.items() if value is None
            )
            if missing_probe:
                raise ValueError(
                    f"FLAKY_PROBE fields are required: {', '.join(missing_probe)}"
                )
            _require_sha(self.commit_sha, "commit_sha")
            _require_sha(self.controller_commit_sha, "controller_commit_sha")
            _require_sha(self.target_commit_sha, "target_commit_sha")
        return self


class CaseResult(VersionedQualityModel):
    run_id: str
    execution_id: str
    worker_id: str
    case_id: str
    invocation_id: str
    nodeid: str
    param_hash: str
    phase: CasePhase
    raw_status: CaseStatus
    final_status: CaseStatus
    duration_ms: float = Field(ge=0)
    start_time: datetime
    end_time: datetime
    failure_id: str | None = None
    evidence_refs: dict[str, str] = Field(default_factory=dict)

    @field_validator(
        "run_id",
        "execution_id",
        "worker_id",
        "case_id",
        "invocation_id",
        "nodeid",
        "param_hash",
    )
    @classmethod
    def _validate_required_text(cls, value: str) -> str:
        return _require_non_empty(value)

    @field_validator("failure_id")
    @classmethod
    def _validate_failure_id(cls, value: str | None) -> str | None:
        return _optional_non_empty(value)

    @field_validator("evidence_refs")
    @classmethod
    def _validate_evidence_refs(cls, value: dict[str, str]) -> dict[str, str]:
        return {
            _require_non_empty(name): _require_non_empty(reference)
            for name, reference in value.items()
        }

    @field_validator("start_time", "end_time")
    @classmethod
    def _validate_time(cls, value: datetime) -> datetime:
        return _require_timezone(value)

    @model_validator(mode="after")
    def _validate_time_order(self) -> CaseResult:
        _ensure_time_order(self.start_time, self.end_time)
        return self


class RequestUsage(FrozenQualityModel):
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    media_count: int | None = Field(default=None, ge=0)


class RequestCost(FrozenQualityModel):
    amount: float | None = Field(default=None, ge=0)
    source: CostSource = CostSource.NONE
    price_version: str | None = None

    @field_validator("price_version")
    @classmethod
    def _validate_price_version(cls, value: str | None) -> str | None:
        return _optional_non_empty(value)


class RequestMetric(VersionedQualityModel):
    run_id: str
    execution_id: str
    worker_id: str
    case_id: str
    invocation_id: str
    request_event_id: str
    server_request_id: str | None = None
    interface_id: str
    method: str
    url_template: str
    protocol: Protocol
    attempt_index: int = Field(ge=1)
    status_code: int | None = None
    business_status: BusinessStatus
    duration_ms: float = Field(ge=0)
    timeout: bool = False
    retryable: bool = False
    error_type: str | None = None
    usage: RequestUsage = Field(default_factory=RequestUsage)
    cost: RequestCost = Field(default_factory=RequestCost)

    @field_validator(
        "run_id",
        "execution_id",
        "worker_id",
        "case_id",
        "invocation_id",
        "request_event_id",
        "interface_id",
        "url_template",
    )
    @classmethod
    def _validate_required_text(cls, value: str) -> str:
        return _require_non_empty(value)

    @field_validator("method", mode="before")
    @classmethod
    def _normalize_method(cls, value: object) -> str:
        if not isinstance(value, str):
            raise ValueError("method must be a string")
        return _require_non_empty(value).upper()

    @field_validator("server_request_id", "error_type")
    @classmethod
    def _validate_optional_text(cls, value: str | None) -> str | None:
        return _optional_non_empty(value)


class FailureFingerprintSource(FrozenQualityModel):
    phase: CasePhase
    error_type: str
    message_hash: str
    interface_id: str | None = None
    assert_location: str | None = None

    @field_validator("error_type", "message_hash")
    @classmethod
    def _validate_required_text(cls, value: str) -> str:
        return _require_non_empty(value)

    @field_validator("interface_id", "assert_location")
    @classmethod
    def _validate_optional_text(cls, value: str | None) -> str | None:
        return _optional_non_empty(value)


class FailureRecord(VersionedQualityModel):
    run_id: str
    failure_id: str
    case_id: str
    invocation_id: str
    phase: CasePhase
    category: FailureCategory
    owner_domain: OwnerDomain
    confidence: Confidence
    error_type: str
    normalized_message: str
    fingerprint_source: FailureFingerprintSource
    manual_override: dict[str, ManualOverrideValue] | None = None

    @field_validator(
        "run_id",
        "failure_id",
        "case_id",
        "invocation_id",
        "error_type",
        "normalized_message",
    )
    @classmethod
    def _validate_required_text(cls, value: str) -> str:
        return _require_non_empty(value)

    @field_validator("manual_override")
    @classmethod
    def _validate_manual_override(
        cls,
        value: dict[str, ManualOverrideValue] | None,
    ) -> dict[str, ManualOverrideValue] | None:
        if value is None:
            return None
        return {_require_non_empty(name): item for name, item in value.items()}


def _require_non_empty(value: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise ValueError("value must not be empty")
    return stripped


def _optional_non_empty(value: str | None) -> str | None:
    if value is None:
        return None
    return _require_non_empty(value)


def _require_timezone(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must include timezone information")
    return value


def _ensure_time_order(start_time: datetime, end_time: datetime | None) -> None:
    if end_time is not None and end_time < start_time:
        raise ValueError("end_time must be greater than or equal to start_time")


_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")


def _require_sha(value: str | None, name: str) -> str:
    if value is None or _SHA_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{name} must be a 40-character lowercase hexadecimal SHA")
    return value
