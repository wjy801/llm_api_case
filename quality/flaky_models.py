from __future__ import annotations

from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from quality.models import CasePhase, CaseStatus, IssueSeverity


FLAKY_IMPORT_SCHEMA_VERSION = "quality.flaky-import.v1"
FLAKY_IMPORTER_VERSION = "p1-flaky-import.v1"
FLAKY_IDENTITY_RULE_VERSION = "flaky-identity.v1"
FLAKY_ENVIRONMENT_RULE_VERSION = "flaky-environment.v1"
FLAKY_EXECUTION_PROFILE_RULE_VERSION = "flaky-execution-profile.v1"
FLAKY_OBSERVATION_RULE_VERSION = "flaky-observation.v1"
FLAKY_EVALUATION_SCHEMA_VERSION = "quality.flaky-evaluation.v1"
FLAKY_STATE_RULE_VERSION = "flaky-state.v1"
FLAKY_PROJECTION_VERSION = "flaky-projection.v1"


class FlakyImportStatus(str, Enum):
    IMPORTED = "IMPORTED"
    NOOP = "NOOP"
    DEGRADED = "DEGRADED"
    FAILED = "FAILED"
    NO_DATA = "NO_DATA"


class ObservationOutcome(str, Enum):
    PASS = "pass"
    FAIL = "fail"


class FlakyState(str, Enum):
    OBSERVING = "OBSERVING"
    STABLE = "STABLE"
    SUSPECTED = "SUSPECTED"
    CONFIRMED = "CONFIRMED"
    QUARANTINED = "QUARANTINED"
    RECOVERING = "RECOVERING"


class ProjectionStatus(str, Enum):
    CURRENT = "CURRENT"
    STALE = "STALE"


class TransitionTrigger(str, Enum):
    OBSERVATION = "observation"
    MANUAL = "manual"
    BOOTSTRAP = "bootstrap"
    REPROJECTION = "reprojection"


class GovernanceStatus(str, Enum):
    ACTIVE = "ACTIVE"
    RECOVERING = "RECOVERING"
    CLOSED = "CLOSED"


class GovernanceResolution(str, Enum):
    RECOVERED = "recovered"
    REGRESSED = "regressed"
    CANCELLED = "cancelled"


class FlakyEvaluationStatus(str, Enum):
    EVALUATED = "EVALUATED"
    NOOP = "NOOP"
    DEGRADED = "DEGRADED"
    FAILED = "FAILED"
    NO_DATA = "NO_DATA"


class FrozenFlakyModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)


class FlakyRuleConfig(FrozenFlakyModel):
    rule_version: str = FLAKY_STATE_RULE_VERSION
    projection_version: str = FLAKY_PROJECTION_VERSION
    evidence_window_size: int = Field(default=20, ge=1)
    stable_min_samples: int = Field(default=3, ge=2)
    confirmed_min_samples: int = Field(default=4, ge=2)
    confirmed_min_pass_count: int = Field(default=2, ge=1)
    confirmed_min_fail_count: int = Field(default=2, ge=1)
    confirmed_min_outcome_switches: int = Field(default=2, ge=1)
    suspected_clear_signature_streak: int = Field(default=5, ge=2)
    recovery_signature_streak: int = Field(default=5, ge=2)
    max_transition_evidence_refs: int = Field(default=20, ge=1)

    @field_validator("rule_version", "projection_version")
    @classmethod
    def _validate_version(cls, value: str) -> str:
        return _require_text(value)

    @model_validator(mode="after")
    def _validate_thresholds(self) -> FlakyRuleConfig:
        minimum = self.confirmed_min_pass_count + self.confirmed_min_fail_count
        if self.confirmed_min_samples < minimum:
            raise ValueError(
                "confirmed_min_samples must cover required pass and fail counts"
            )
        if self.evidence_window_size < max(
            self.confirmed_min_samples,
            self.suspected_clear_signature_streak,
            self.recovery_signature_streak,
        ):
            raise ValueError("evidence_window_size is smaller than a rule threshold")
        return self


class FlakyEvidence(FrozenFlakyModel):
    total_observation_count: int = Field(ge=1)
    sample_size: int = Field(ge=1)
    evidence_window_size: int = Field(ge=1)
    pass_count: int = Field(ge=0)
    fail_count: int = Field(ge=0)
    outcome_switch_count: int = Field(ge=0)
    signature_switch_count: int = Field(ge=0)
    distinct_failure_fingerprint_count: int = Field(ge=0)
    trailing_same_signature_count: int = Field(ge=1)
    latest_signature: str
    observation_ids: tuple[str, ...]
    run_ids: tuple[str, ...]

    @field_validator("latest_signature")
    @classmethod
    def _validate_signature(cls, value: str) -> str:
        return _require_text(value)

    @field_validator("observation_ids", "run_ids")
    @classmethod
    def _validate_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_require_text(item) for item in value)

    @model_validator(mode="after")
    def _validate_counts(self) -> FlakyEvidence:
        if self.sample_size > self.evidence_window_size:
            raise ValueError("sample_size cannot exceed evidence_window_size")
        if self.pass_count + self.fail_count != self.sample_size:
            raise ValueError("pass_count + fail_count must equal sample_size")
        return self


class FlakyTransitionDecision(FrozenFlakyModel):
    from_state: FlakyState | None
    to_state: FlakyState
    reason_code: str
    trigger_observation_id: str
    evidence: FlakyEvidence

    @field_validator("reason_code", "trigger_observation_id")
    @classmethod
    def _validate_text(cls, value: str) -> str:
        return _require_text(value)


class FlakyProjection(FrozenFlakyModel):
    current_state: FlakyState
    detected_state: FlakyState
    stable_outcome: ObservationOutcome | None = None
    stable_failure_id: str | None = None
    evidence: FlakyEvidence
    transitions: tuple[FlakyTransitionDecision, ...] = ()

    @field_validator("stable_failure_id")
    @classmethod
    def _validate_optional_failure(cls, value: str | None) -> str | None:
        return _optional_text(value)

    @model_validator(mode="after")
    def _validate_stable_signature(self) -> FlakyProjection:
        if self.current_state is FlakyState.STABLE and self.stable_outcome is None:
            raise ValueError("STABLE projection requires stable_outcome")
        if self.stable_outcome is ObservationOutcome.PASS and self.stable_failure_id is not None:
            raise ValueError("stable pass must not include stable_failure_id")
        if self.stable_outcome is ObservationOutcome.FAIL and self.stable_failure_id is None:
            raise ValueError("stable fail requires stable_failure_id")
        if self.detected_state in {FlakyState.QUARANTINED, FlakyState.RECOVERING}:
            raise ValueError("detected_state must be an automatic state")
        return self


class FlakyImportRequest(FrozenFlakyModel):
    run_id: str
    quality_output_dir: Path
    database_path: Path
    importer_version: str = FLAKY_IMPORTER_VERSION

    @field_validator("run_id", "importer_version")
    @classmethod
    def _validate_text(cls, value: str) -> str:
        return _require_text(value)

    @field_validator("database_path")
    @classmethod
    def _validate_database_path(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError("database_path must be an absolute path")
        return value


class FlakyImportIssue(FrozenFlakyModel):
    severity: IssueSeverity
    code: str
    summary: str
    related_id: str | None = None

    @field_validator("code", "summary")
    @classmethod
    def _validate_text(cls, value: str) -> str:
        return _require_text(value)

    @field_validator("related_id")
    @classmethod
    def _validate_optional_text(cls, value: str | None) -> str | None:
        return _optional_text(value)


class CaseObservationCandidate(FrozenFlakyModel):
    run_id: str
    invocation_id: str
    case_id: str
    param_hash: str
    environment: str
    execution_profile: str
    decisive_phase: CasePhase
    raw_status: CaseStatus
    final_status: CaseStatus
    observation_outcome: ObservationOutcome
    failure_id: str | None = None
    failure_category: str | None = None
    observed_at: datetime
    identity_rule_version: str = FLAKY_IDENTITY_RULE_VERSION
    environment_rule_version: str = FLAKY_ENVIRONMENT_RULE_VERSION
    execution_profile_rule_version: str = FLAKY_EXECUTION_PROFILE_RULE_VERSION
    observation_rule_version: str = FLAKY_OBSERVATION_RULE_VERSION
    fingerprint_version: str

    @field_validator(
        "run_id",
        "invocation_id",
        "case_id",
        "param_hash",
        "environment",
        "execution_profile",
        "identity_rule_version",
        "environment_rule_version",
        "execution_profile_rule_version",
        "observation_rule_version",
        "fingerprint_version",
    )
    @classmethod
    def _validate_text(cls, value: str) -> str:
        return _require_text(value)

    @field_validator("failure_id", "failure_category")
    @classmethod
    def _validate_optional_text(cls, value: str | None) -> str | None:
        return _optional_text(value)

    @field_validator("observed_at")
    @classmethod
    def _validate_observed_at(cls, value: datetime) -> datetime:
        return _require_timezone(value)

    @model_validator(mode="after")
    def _validate_failure_reference(self) -> CaseObservationCandidate:
        if self.observation_outcome is ObservationOutcome.PASS and self.failure_id is not None:
            raise ValueError("pass observation must not include failure_id")
        if self.observation_outcome is ObservationOutcome.FAIL and self.failure_id is None:
            raise ValueError("fail observation must include failure_id")
        return self


class CaseObservation(CaseObservationCandidate):
    observation_id: str
    flaky_key: str
    epoch_scope_key: str
    state_epoch: int = Field(ge=1)

    @field_validator("observation_id", "flaky_key", "epoch_scope_key")
    @classmethod
    def _validate_identity_text(cls, value: str) -> str:
        return _require_text(value)


class FlakyRunMetadata(FrozenFlakyModel):
    run_id: str
    source_digest: str
    source_kind: str
    artifact_ref: str
    job_name: str | None = None
    build_number: str | None = None
    branch: str | None = None
    commit_sha: str | None = None
    environment: str
    run_status: str
    p0_integrity_status: str
    run_start_time: datetime
    run_end_time: datetime
    p0_schema_version: str
    p0_merge_version: str
    fingerprint_version: str
    run_record_sha256: str
    manifest_sha256: str
    case_results_sha256: str
    failures_sha256: str
    integrity_issues_sha256: str
    importer_version: str
    identity_rule_version: str = FLAKY_IDENTITY_RULE_VERSION
    environment_rule_version: str = FLAKY_ENVIRONMENT_RULE_VERSION
    execution_profile_rule_version: str = FLAKY_EXECUTION_PROFILE_RULE_VERSION
    observation_rule_version: str = FLAKY_OBSERVATION_RULE_VERSION
    eligible_count: int = Field(ge=0)
    excluded_count: int = Field(ge=0)

    @field_validator(
        "run_id",
        "source_digest",
        "source_kind",
        "artifact_ref",
        "environment",
        "run_status",
        "p0_integrity_status",
        "p0_schema_version",
        "p0_merge_version",
        "fingerprint_version",
        "run_record_sha256",
        "manifest_sha256",
        "case_results_sha256",
        "failures_sha256",
        "integrity_issues_sha256",
        "importer_version",
        "identity_rule_version",
        "environment_rule_version",
        "execution_profile_rule_version",
        "observation_rule_version",
    )
    @classmethod
    def _validate_text(cls, value: str) -> str:
        return _require_text(value)

    @field_validator("job_name", "build_number", "branch", "commit_sha")
    @classmethod
    def _validate_optional_text(cls, value: str | None) -> str | None:
        return _optional_text(value)

    @field_validator("run_start_time", "run_end_time")
    @classmethod
    def _validate_time(cls, value: datetime) -> datetime:
        return _require_timezone(value)

    @model_validator(mode="after")
    def _validate_time_order(self) -> FlakyRunMetadata:
        if self.run_end_time < self.run_start_time:
            raise ValueError("run_end_time must be greater than or equal to run_start_time")
        return self


class FlakyImportResult(FrozenFlakyModel):
    schema_version: Literal["quality.flaky-import.v1"] = FLAKY_IMPORT_SCHEMA_VERSION
    run_id: str
    status: FlakyImportStatus
    source_digest: str | None = None
    artifact_ref: str | None = None
    environment: str | None = None
    profile_distribution: dict[str, int] = Field(default_factory=dict)
    eligible_count: int = Field(default=0, ge=0)
    excluded_count: int = Field(default=0, ge=0)
    inserted_count: int = Field(default=0, ge=0)
    excluded_reasons: dict[str, int] = Field(default_factory=dict)
    database_schema_version: int | None = Field(default=None, ge=0)
    quick_check: str | None = None
    source_hashes: dict[str, str] = Field(default_factory=dict)
    p0_integrity_status: str | None = None
    migration_applied: bool = False
    backup_created: bool = False
    issues: tuple[FlakyImportIssue, ...] = ()

    @field_validator("run_id")
    @classmethod
    def _validate_run_id(cls, value: str) -> str:
        return _require_text(value)

    @field_validator(
        "source_digest",
        "artifact_ref",
        "environment",
        "quick_check",
        "p0_integrity_status",
    )
    @classmethod
    def _validate_optional_text(cls, value: str | None) -> str | None:
        return _optional_text(value)

    @field_validator("profile_distribution", "excluded_reasons")
    @classmethod
    def _validate_counts(cls, value: dict[str, int]) -> dict[str, int]:
        result: dict[str, int] = {}
        for name, count in value.items():
            key = _require_text(name)
            if count < 0:
                raise ValueError("count values must be greater than or equal to 0")
            result[key] = count
        return result


class EpochResetRequest(FrozenFlakyModel):
    case_id: str
    environment: str
    execution_profile: str
    actor: str
    reason: str

    @field_validator("case_id", "environment", "execution_profile", "actor", "reason")
    @classmethod
    def _validate_text(cls, value: str) -> str:
        return _require_text(value)

    @field_validator("actor")
    @classmethod
    def _validate_actor_length(cls, value: str) -> str:
        if len(value) > 128:
            raise ValueError("actor must not exceed 128 characters")
        return value

    @field_validator("reason")
    @classmethod
    def _validate_reason_length(cls, value: str) -> str:
        if len(value) > 500:
            raise ValueError("reason must not exceed 500 characters")
        return value


class EpochResetResult(FrozenFlakyModel):
    override_id: str
    epoch_scope_key: str
    case_id: str
    environment: str
    execution_profile: str
    previous_epoch: int = Field(ge=1)
    new_epoch: int = Field(ge=2)
    actor: str
    reason: str
    created_at: datetime

    @field_validator(
        "override_id",
        "epoch_scope_key",
        "case_id",
        "environment",
        "execution_profile",
        "actor",
        "reason",
    )
    @classmethod
    def _validate_text(cls, value: str) -> str:
        return _require_text(value)

    @field_validator("created_at")
    @classmethod
    def _validate_created_at(cls, value: datetime) -> datetime:
        return _require_timezone(value)

    @model_validator(mode="after")
    def _validate_epoch_increment(self) -> EpochResetResult:
        if self.new_epoch != self.previous_epoch + 1:
            raise ValueError("new_epoch must equal previous_epoch + 1")
        return self


class FlakyDatabaseCheck(FrozenFlakyModel):
    database_name: str
    schema_version: int = Field(ge=0)
    migrations: dict[int, str] = Field(default_factory=dict)
    quick_check: str
    run_count: int = Field(ge=0)
    observation_count: int = Field(ge=0)
    state_count: int = Field(default=0, ge=0)
    transition_count: int = Field(default=0, ge=0)
    open_governance_count: int = Field(default=0, ge=0)
    missing_projection_count: int = Field(default=0, ge=0)
    stale_projection_count: int = Field(default=0, ge=0)
    incompatible_rule_version_count: int = Field(default=0, ge=0)
    orphan_transition_count: int = Field(default=0, ge=0)
    orphan_governance_count: int = Field(default=0, ge=0)

    @field_validator("database_name", "quick_check")
    @classmethod
    def _validate_text(cls, value: str) -> str:
        return _require_text(value)


class FlakyHistoryEntry(CaseObservation):
    artifact_ref: str
    source_digest: str
    run_end_time: datetime
    imported_at: datetime

    @field_validator("artifact_ref", "source_digest")
    @classmethod
    def _validate_source_text(cls, value: str) -> str:
        return _require_text(value)

    @field_validator("run_end_time", "imported_at")
    @classmethod
    def _validate_source_time(cls, value: datetime) -> datetime:
        return _require_timezone(value)


class FlakyStateRecord(FrozenFlakyModel):
    flaky_key: str
    epoch_scope_key: str
    case_id: str
    param_hash: str
    environment: str
    execution_profile: str
    state_epoch: int = Field(ge=1)
    current_state: FlakyState
    detected_state: FlakyState
    stable_outcome: ObservationOutcome | None = None
    stable_failure_id: str | None = None
    total_observation_count: int = Field(ge=1)
    sample_size: int = Field(ge=1)
    evidence_window_size: int = Field(ge=1)
    pass_count: int = Field(ge=0)
    fail_count: int = Field(ge=0)
    outcome_switch_count: int = Field(ge=0)
    signature_switch_count: int = Field(ge=0)
    distinct_failure_fingerprint_count: int = Field(ge=0)
    trailing_same_signature_count: int = Field(ge=1)
    evaluation_anchor_observation_id: str | None = None
    latest_observation_id: str
    latest_run_id: str
    latest_observed_at: datetime
    last_transition_id: str | None = None
    rule_version: str
    projection_version: str
    projection_status: ProjectionStatus
    created_at: datetime
    updated_at: datetime

    @field_validator(
        "flaky_key",
        "epoch_scope_key",
        "case_id",
        "param_hash",
        "environment",
        "execution_profile",
        "latest_observation_id",
        "latest_run_id",
        "rule_version",
        "projection_version",
    )
    @classmethod
    def _validate_text(cls, value: str) -> str:
        return _require_text(value)

    @field_validator(
        "stable_failure_id",
        "evaluation_anchor_observation_id",
        "last_transition_id",
    )
    @classmethod
    def _validate_optional_text(cls, value: str | None) -> str | None:
        return _optional_text(value)

    @field_validator("latest_observed_at", "created_at", "updated_at")
    @classmethod
    def _validate_time(cls, value: datetime) -> datetime:
        return _require_timezone(value)

    @model_validator(mode="after")
    def _validate_state(self) -> FlakyStateRecord:
        if self.detected_state in {FlakyState.QUARANTINED, FlakyState.RECOVERING}:
            raise ValueError("detected_state must be an automatic state")
        if self.pass_count + self.fail_count != self.sample_size:
            raise ValueError("pass_count + fail_count must equal sample_size")
        if self.sample_size > self.evidence_window_size:
            raise ValueError("sample_size cannot exceed evidence_window_size")
        if self.current_state is FlakyState.STABLE and self.stable_outcome is None:
            raise ValueError("STABLE state requires stable_outcome")
        if self.stable_outcome is ObservationOutcome.PASS and self.stable_failure_id is not None:
            raise ValueError("stable pass must not include stable_failure_id")
        if self.stable_outcome is ObservationOutcome.FAIL and self.stable_failure_id is None:
            raise ValueError("stable fail requires stable_failure_id")
        return self


class FlakyTransitionRecord(FrozenFlakyModel):
    transition_id: str
    flaky_key: str
    from_state: FlakyState | None = None
    to_state: FlakyState
    trigger_type: TransitionTrigger
    reason_code: str
    rule_version: str
    projection_version: str
    sample_size: int = Field(ge=1)
    trigger_observation_id: str | None = None
    evidence_observation_ids: tuple[str, ...] = ()
    evidence_run_ids: tuple[str, ...] = ()
    actor: str | None = None
    created_at: datetime

    @field_validator(
        "transition_id",
        "flaky_key",
        "reason_code",
        "rule_version",
        "projection_version",
    )
    @classmethod
    def _validate_text(cls, value: str) -> str:
        return _require_text(value)

    @field_validator("trigger_observation_id", "actor")
    @classmethod
    def _validate_optional_text(cls, value: str | None) -> str | None:
        return _optional_text(value)

    @field_validator("created_at")
    @classmethod
    def _validate_time(cls, value: datetime) -> datetime:
        return _require_timezone(value)

    @model_validator(mode="after")
    def _validate_actor(self) -> FlakyTransitionRecord:
        if self.trigger_type is TransitionTrigger.MANUAL and self.actor is None:
            raise ValueError("manual transition requires actor")
        return self


class FlakyGovernanceRecord(FrozenFlakyModel):
    governance_id: str
    flaky_key: str
    status: GovernanceStatus
    owner: str
    reason: str
    created_by: str
    created_at: datetime
    expires_at: datetime
    recovery_started_by: str | None = None
    recovery_started_at: datetime | None = None
    recovery_reason: str | None = None
    recovery_anchor_observation_id: str | None = None
    closed_at: datetime | None = None
    resolution: GovernanceResolution | None = None

    @field_validator(
        "governance_id", "flaky_key", "owner", "reason", "created_by"
    )
    @classmethod
    def _validate_text(cls, value: str) -> str:
        return _require_text(value)

    @field_validator(
        "recovery_started_by", "recovery_reason", "recovery_anchor_observation_id"
    )
    @classmethod
    def _validate_optional_text(cls, value: str | None) -> str | None:
        return _optional_text(value)

    @field_validator("created_at", "expires_at", "recovery_started_at", "closed_at")
    @classmethod
    def _validate_time(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return _require_timezone(value)

    @model_validator(mode="after")
    def _validate_lifecycle(self) -> FlakyGovernanceRecord:
        if self.expires_at <= self.created_at:
            raise ValueError("expires_at must be later than created_at")
        if self.status is GovernanceStatus.RECOVERING and (
            self.recovery_started_by is None
            or self.recovery_started_at is None
            or self.recovery_reason is None
        ):
            raise ValueError("RECOVERING governance requires recovery audit fields")
        if self.status is GovernanceStatus.CLOSED and (
            self.closed_at is None or self.resolution is None
        ):
            raise ValueError("CLOSED governance requires resolution and closed_at")
        return self


class FlakyManualActionRequest(FrozenFlakyModel):
    flaky_key: str
    actor: str
    reason: str

    @field_validator("flaky_key", "actor", "reason")
    @classmethod
    def _validate_text(cls, value: str) -> str:
        return _require_text(value)

    @field_validator("actor")
    @classmethod
    def _validate_actor_length(cls, value: str) -> str:
        if len(value) > 128:
            raise ValueError("actor must not exceed 128 characters")
        return value

    @field_validator("reason")
    @classmethod
    def _validate_reason_length(cls, value: str) -> str:
        if len(value) > 500:
            raise ValueError("reason must not exceed 500 characters")
        return value


class FlakyQuarantineRequest(FlakyManualActionRequest):
    owner: str
    expires_at: datetime

    @field_validator("owner")
    @classmethod
    def _validate_owner(cls, value: str) -> str:
        value = _require_text(value)
        if len(value) > 128:
            raise ValueError("owner must not exceed 128 characters")
        return value

    @field_validator("expires_at")
    @classmethod
    def _validate_expiry(cls, value: datetime) -> datetime:
        return _require_timezone(value)


class FlakyStateSummary(FrozenFlakyModel):
    flaky_key: str
    case_id: str
    param_hash: str
    environment: str
    execution_profile: str
    state_epoch: int = Field(ge=1)
    current_state: FlakyState
    detected_state: FlakyState
    sample_size: int = Field(ge=1)
    projection_status: ProjectionStatus
    latest_run_id: str
    latest_observation_id: str
    transition_reason: str | None = None
    governance_id: str | None = None
    owner: str | None = None
    expires_at: datetime | None = None

    @field_validator(
        "flaky_key",
        "case_id",
        "param_hash",
        "environment",
        "execution_profile",
        "latest_run_id",
        "latest_observation_id",
    )
    @classmethod
    def _validate_text(cls, value: str) -> str:
        return _require_text(value)

    @field_validator("transition_reason", "governance_id", "owner")
    @classmethod
    def _validate_optional_text(cls, value: str | None) -> str | None:
        return _optional_text(value)

    @field_validator("expires_at")
    @classmethod
    def _validate_optional_time(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return _require_timezone(value)


class FlakyEvaluationResult(FrozenFlakyModel):
    schema_version: Literal["quality.flaky-evaluation.v1"] = (
        FLAKY_EVALUATION_SCHEMA_VERSION
    )
    run_id: str
    status: FlakyEvaluationStatus
    rule_version: str = FLAKY_STATE_RULE_VERSION
    projection_version: str = FLAKY_PROJECTION_VERSION
    evaluated_at: datetime
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
    database_schema_version: int | None = Field(default=None, ge=0)
    quick_check: str | None = None
    issues: tuple[FlakyImportIssue, ...] = ()

    @field_validator("run_id", "rule_version", "projection_version")
    @classmethod
    def _validate_text(cls, value: str) -> str:
        return _require_text(value)

    @field_validator("quick_check")
    @classmethod
    def _validate_optional_text(cls, value: str | None) -> str | None:
        return _optional_text(value)

    @field_validator("evaluated_at")
    @classmethod
    def _validate_time(cls, value: datetime) -> datetime:
        return _require_timezone(value)


def _require_text(value: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise ValueError("value must not be empty")
    return stripped


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    return _require_text(value)


def _require_timezone(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must include timezone information")
    return value
