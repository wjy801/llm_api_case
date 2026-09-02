from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
from typing import Callable, Iterator, Mapping, Protocol, Sequence
import unicodedata
from urllib.parse import quote, urljoin, urlparse
import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from quality.flaky_store.contracts import DEFAULT_BUSY_TIMEOUT_MS, FlakyStoreError
from quality.flaky_store.migration import MIGRATIONS_DIRECTORY, validate_store_schema
from quality.flaky_store.repository import FlakyRepository, utc_text
from quality.flaky_store.writer_lock import database_writer_lock
from quality.flaky_v3 import DEFAULT_GOVERNANCE_POLICY, PROBE_EVIDENCE_RULE_VERSION
from quality.models import RunRecord


PROBE_PLAN_VERSION = "flaky-probe-plan.v1"
PROBE_ENVELOPE_VERSION = "flaky-probe-envelope.v2"
PROBE_CREATE_PAYLOAD_VERSION = "flaky-probe-create.v1"
PROBE_DB_SCHEMA_VERSION = 4
DEFAULT_MAX_DISPATCH_ATTEMPTS = 3
DEFAULT_MAX_ORCHESTRATION_ROUNDS = 10
DEFAULT_JENKINS_CONNECT_TIMEOUT_SECONDS = 3.0
DEFAULT_JENKINS_TOTAL_TIMEOUT_SECONDS = 10.0
ACTIVE_TRIGGER_STATES = frozenset(
    {
        "PENDING",
        "DISPATCHING",
        "QUEUED",
        "DISPATCH_UNKNOWN",
        "RUNNING",
        "CANCEL_REQUESTED",
    }
)


class TriggerStatus(str, Enum):
    PENDING = "PENDING"
    DISPATCHING = "DISPATCHING"
    QUEUED = "QUEUED"
    DISPATCH_UNKNOWN = "DISPATCH_UNKNOWN"
    RUNNING = "RUNNING"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class FailureDisposition(str, Enum):
    RETRYABLE = "RETRYABLE"
    TERMINAL = "TERMINAL"


class ProbeRoundStatus(str, Enum):
    AUTHORIZED = "AUTHORIZED"
    STARTED = "STARTED"
    IMPORTED = "IMPORTED"
    ABANDONED = "ABANDONED"


class DispatchResultKind(str, Enum):
    QUEUED = "QUEUED"
    RETRYABLE_FAILURE = "RETRYABLE_FAILURE"
    UNKNOWN = "UNKNOWN"


class JenkinsObservationKind(str, Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    NOT_RECEIVED = "NOT_RECEIVED"
    UNKNOWN = "UNKNOWN"


class FrozenProbeModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)


class ProbePlan(FrozenProbeModel):
    schema_version: str = PROBE_PLAN_VERSION
    attempt_id: str
    governance_id: str
    flaky_key: str
    case_id: str
    param_hash: str
    environment: str
    execution_profile: str
    state_epoch: int = Field(ge=1)
    target_branch: str = "dev3"
    target_commit_sha: str
    controller_commit_sha: str
    policy_revision: str
    probe_evidence_rule_version: str = PROBE_EVIDENCE_RULE_VERSION
    fact_schema_version: str = "quality.fact.v1"
    required_consecutive_passes: int = Field(default=5, ge=1)
    min_interval_minutes: int = Field(default=30, ge=0)
    max_attempt_age_hours: int = Field(default=72, ge=1)
    max_non_counting_runs: int = Field(default=3, ge=1)
    max_dispatch_attempts: int = Field(default=3, ge=1)
    max_orchestration_rounds: int = Field(default=10, ge=1)
    allowed_job_full_name: str

    @field_validator("target_commit_sha", "controller_commit_sha")
    @classmethod
    def _sha(cls, value: str) -> str:
        return _require_sha(value, "commit sha")

    @field_validator("attempt_id", "governance_id", "flaky_key", "case_id")
    @classmethod
    def _text(cls, value: str) -> str:
        return _required(value, "plan field")

    @field_validator("allowed_job_full_name")
    @classmethod
    def _job(cls, value: str) -> str:
        return validate_job_full_name(value)

    @model_validator(mode="after")
    def _fixed_contract(self) -> "ProbePlan":
        if self.schema_version != PROBE_PLAN_VERSION:
            raise ValueError("unsupported Probe plan version")
        if self.target_branch != "dev3":
            raise ValueError("Probe target branch must be dev3")
        if self.required_consecutive_passes != 5:
            raise ValueError("Probe plan requires exactly five consecutive passes")
        if self.min_interval_minutes != 30:
            raise ValueError("Probe plan interval must be 30 minutes")
        if self.max_attempt_age_hours != 72:
            raise ValueError("Probe plan age budget must be 72 hours")
        if self.max_non_counting_runs != 3:
            raise ValueError("Probe non-counting budget must be three")
        if self.max_dispatch_attempts != DEFAULT_MAX_DISPATCH_ATTEMPTS:
            raise ValueError("Probe dispatch budget must be three")
        if self.max_orchestration_rounds != DEFAULT_MAX_ORCHESTRATION_ROUNDS:
            raise ValueError("Probe orchestration budget must be ten")
        return self

    @property
    def canonical_json(self) -> str:
        return canonical_json(self.model_dump(mode="json"))

    @property
    def plan_digest(self) -> str:
        return sha256_text(self.canonical_json)


class ProbeCreateRequest(FrozenProbeModel):
    governance_id: str
    reason: str
    row_version: int = Field(ge=0)
    request_id: str

    @field_validator("governance_id")
    @classmethod
    def _governance(cls, value: str) -> str:
        return _required(value, "governance_id")

    @field_validator("reason")
    @classmethod
    def _reason(cls, value: str) -> str:
        return normalize_reason(value)

    @field_validator("request_id")
    @classmethod
    def _request_id(cls, value: str) -> str:
        return require_uuid4(value)

    @property
    def payload_hash(self) -> str:
        return sha256_json(
            {
                "schema_version": PROBE_CREATE_PAYLOAD_VERSION,
                "governance_id": self.governance_id,
                "row_version": self.row_version,
                "reason": self.reason,
            }
        )


class ProbeEvidenceEnvelope(FrozenProbeModel):
    schema_version: str = PROBE_ENVELOPE_VERSION
    key_id: str
    attempt_id: str
    trigger_id: str
    plan_digest: str
    round_no: int = Field(ge=1)
    run_id: str
    target_commit_sha: str
    controller_commit_sha: str
    environment: str
    execution_profile: str
    jenkins_origin_id: str
    job_full_name: str
    build_number: int = Field(ge=1)
    trusted_started_at: datetime
    trusted_finished_at: datetime
    p0_bundle_status: str
    p0_manifest_sha256: str | None = None
    p0_file_hashes: dict[str, str] = Field(default_factory=dict)
    outcome: str
    trusted_failure: bool = False
    rerun_supported: bool = True
    diagnostic_codes: tuple[str, ...] = ()
    signature: str

    @field_validator("target_commit_sha", "controller_commit_sha")
    @classmethod
    def _envelope_sha(cls, value: str) -> str:
        return _require_sha(value, "commit sha")

    @field_validator("environment", "execution_profile")
    @classmethod
    def _execution_identity(cls, value: str) -> str:
        return _required(value, "execution identity")

    @field_validator("plan_digest", "p0_manifest_sha256")
    @classmethod
    def _digest(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _require_digest(value, "digest")

    @field_validator("outcome")
    @classmethod
    def _outcome(cls, value: str) -> str:
        normalized = _required(value, "outcome").upper()
        if normalized not in {"PASS", "FAIL", "SKIP", "XFAIL", "XPASS", "NO_DATA"}:
            raise ValueError("unsupported Probe outcome")
        return normalized

    @field_validator("p0_bundle_status")
    @classmethod
    def _bundle(cls, value: str) -> str:
        normalized = _required(value, "p0_bundle_status").upper()
        if normalized not in {"VALID", "MISSING", "INVALID", "OVERSIZE"}:
            raise ValueError("unsupported P0 bundle status")
        return normalized

    @field_validator("signature")
    @classmethod
    def _signature(cls, value: str) -> str:
        if re.fullmatch(r"hmac-sha256:[0-9a-f]{64}", str(value)) is None:
            raise ValueError("invalid envelope signature")
        return str(value)

    @model_validator(mode="after")
    def _envelope_contract(self) -> "ProbeEvidenceEnvelope":
        if self.schema_version != PROBE_ENVELOPE_VERSION:
            raise ValueError("unsupported Probe envelope version")
        _aware(self.trusted_started_at, "trusted_started_at")
        _aware(self.trusted_finished_at, "trusted_finished_at")
        if self.trusted_finished_at < self.trusted_started_at:
            raise ValueError("trusted_finished_at precedes trusted_started_at")
        if self.p0_bundle_status == "VALID" and self.p0_manifest_sha256 is None:
            raise ValueError("valid P0 bundle requires manifest hash")
        if self.p0_bundle_status != "VALID" and self.p0_manifest_sha256 is not None:
            raise ValueError("invalid P0 bundle cannot carry manifest hash")
        for path, digest in self.p0_file_hashes.items():
            validate_relative_artifact_path(path)
            _require_digest(digest, "P0 file digest")
        return self

    @property
    def signing_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"signature"})


@dataclass(frozen=True)
class ProbeRuntimeConfig:
    requested_enabled: bool = False
    enabled: bool = False
    warning: str | None = None
    jenkins_origin: str | None = None
    job_full_name: str | None = None
    credential_file: Path | None = None
    controller_commit_sha: str | None = None
    controller_jenkinsfile_sha256: str | None = None
    csrf_secret_file: Path | None = None
    evidence_hmac_key_file: Path | None = None
    evidence_key_id: str = "probe-evidence-key-v1"
    git_remote: str = "origin"


@dataclass(frozen=True)
class DispatchResult:
    kind: DispatchResultKind
    queue_id: int | None = None
    error_code: str | None = None


@dataclass(frozen=True)
class JenkinsObservation:
    kind: JenkinsObservationKind
    queue_id: int | None = None
    build_number: int | None = None
    error_code: str | None = None


class JenkinsGateway(Protocol):
    def dispatch(self, *, trigger_id: str, dispatch_token: str, plan_digest: str) -> DispatchResult: ...

    def observe(self, trigger: Mapping[str, object]) -> JenkinsObservation: ...

    def cancel(self, trigger: Mapping[str, object]) -> JenkinsObservation: ...


class GitTargetResolver:
    def __init__(self, repository_root: str | Path, *, remote: str = "origin") -> None:
        self.repository_root = Path(repository_root).resolve()
        self.remote = _required(remote, "remote")

    def resolve_dev3(self) -> str:
        try:
            subprocess.run(
                ["git", "fetch", "--quiet", self.remote, "dev3"],
                cwd=self.repository_root,
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
            result = subprocess.run(
                ["git", "rev-parse", f"refs/remotes/{self.remote}/dev3"],
                cwd=self.repository_root,
                check=True,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise FlakyStoreError(
                "target_head_unavailable", "origin/dev3 could not be verified"
            ) from error
        return _require_sha(result.stdout.strip(), "origin/dev3 HEAD")


def load_probe_runtime_config(
    environ: Mapping[str, str] | None = None,
    *,
    repository_root: str | Path | None = None,
) -> ProbeRuntimeConfig:
    values = os.environ if environ is None else environ
    raw_enabled = str(values.get("QUALITY_FLAKY_TRIGGER_ENABLE", "0")).strip().casefold()
    requested = raw_enabled in {"1", "true", "yes", "on"}
    if raw_enabled not in {"", "0", "false", "no", "off", "1", "true", "yes", "on"}:
        return ProbeRuntimeConfig(
            requested_enabled=True,
            warning="invalid QUALITY_FLAKY_TRIGGER_ENABLE value",
        )
    required_runtime_names = (
        "QUALITY_FLAKY_JENKINS_ORIGIN",
        "QUALITY_FLAKY_JENKINS_JOB",
        "QUALITY_FLAKY_JENKINS_CREDENTIAL_FILE",
        "QUALITY_FLAKY_CONTROLLER_COMMIT",
        "QUALITY_FLAKY_CONTROLLER_JENKINSFILE_SHA256",
        "QUALITY_FLAKY_CSRF_SECRET_FILE",
        "QUALITY_FLAKY_EVIDENCE_HMAC_KEY_FILE",
    )
    if not requested and not any(
        str(values.get(name, "")).strip() for name in required_runtime_names
    ):
        return ProbeRuntimeConfig(requested_enabled=False, enabled=False)
    try:
        origin = validate_jenkins_origin(values.get("QUALITY_FLAKY_JENKINS_ORIGIN"))
        job = validate_job_full_name(values.get("QUALITY_FLAKY_JENKINS_JOB"))
        controller_sha = _require_sha(
            str(values.get("QUALITY_FLAKY_CONTROLLER_COMMIT", "")),
            "controller commit",
        )
        jenkinsfile_digest = _require_hex_digest(
            str(values.get("QUALITY_FLAKY_CONTROLLER_JENKINSFILE_SHA256", "")),
            "controller Jenkinsfile digest",
        )
        root = Path(repository_root).resolve() if repository_root is not None else None
        credential = _secret_path(values, "QUALITY_FLAKY_JENKINS_CREDENTIAL_FILE", root)
        csrf = _secret_path(values, "QUALITY_FLAKY_CSRF_SECRET_FILE", root)
        evidence = _secret_path(values, "QUALITY_FLAKY_EVIDENCE_HMAC_KEY_FILE", root)
        if len({credential, csrf, evidence}) != 3:
            raise ValueError("Jenkins, CSRF, and evidence secrets must use separate files")
        key_id = _required(
            str(values.get("QUALITY_FLAKY_EVIDENCE_KEY_ID", "probe-evidence-key-v1")),
            "evidence key id",
        )
        git_remote = validate_git_remote(
            values.get("QUALITY_FLAKY_GIT_REMOTE", "origin")
        )
    except (ValueError, FlakyStoreError) as error:
        return ProbeRuntimeConfig(
            requested_enabled=True,
            enabled=False,
            warning=str(error),
        )
    return ProbeRuntimeConfig(
        requested_enabled=True,
        enabled=requested,
        jenkins_origin=origin,
        job_full_name=job,
        credential_file=credential,
        controller_commit_sha=controller_sha,
        controller_jenkinsfile_sha256=jenkinsfile_digest,
        csrf_secret_file=csrf,
        evidence_hmac_key_file=evidence,
        evidence_key_id=key_id,
        git_remote=git_remote,
    )


def load_probe_evidence_key(
    environ: Mapping[str, str] | None = None,
    *,
    repository_root: str | Path | None = None,
) -> tuple[str, bytes]:
    """Load the evidence key independently of the trigger kill switch."""
    values = os.environ if environ is None else environ
    root = Path(repository_root).resolve() if repository_root is not None else None
    key_path = _secret_path(values, "QUALITY_FLAKY_EVIDENCE_HMAC_KEY_FILE", root)
    key_id = _required(
        str(values.get("QUALITY_FLAKY_EVIDENCE_KEY_ID", "probe-evidence-key-v1")),
        "evidence key id",
    )
    try:
        secret = key_path.read_bytes().strip()
    except OSError as error:
        raise ValueError("evidence HMAC key file cannot be read") from error
    if len(secret) < 32:
        raise ValueError("evidence HMAC key must contain at least 32 bytes")
    return key_id, secret


def load_probe_controller_runtime_config(
    environ: Mapping[str, str] | None = None,
    *,
    repository_root: str | Path | None = None,
) -> ProbeRuntimeConfig:
    """Load only the fixed settings needed inside the trusted controller checkout."""
    values = os.environ if environ is None else environ
    raw_enabled = str(values.get("QUALITY_FLAKY_TRIGGER_ENABLE", "0")).strip().casefold()
    allowed_switches = {"", "0", "false", "no", "off", "1", "true", "yes", "on"}
    if raw_enabled not in allowed_switches:
        return ProbeRuntimeConfig(
            requested_enabled=True,
            enabled=False,
            warning="invalid QUALITY_FLAKY_TRIGGER_ENABLE value",
        )
    requested = raw_enabled in {"1", "true", "yes", "on"}
    try:
        origin = validate_jenkins_origin(values.get("QUALITY_FLAKY_JENKINS_ORIGIN"))
        job = validate_job_full_name(values.get("QUALITY_FLAKY_JENKINS_JOB"))
        controller_sha = _require_sha(
            str(values.get("QUALITY_FLAKY_CONTROLLER_COMMIT", "")),
            "controller commit",
        )
        jenkinsfile_digest = _require_hex_digest(
            str(values.get("QUALITY_FLAKY_CONTROLLER_JENKINSFILE_SHA256", "")),
            "controller Jenkinsfile digest",
        )
        root = Path(repository_root).resolve() if repository_root is not None else None
        evidence = None
        if str(values.get("QUALITY_FLAKY_EVIDENCE_HMAC_KEY_FILE", "")).strip():
            evidence = _secret_path(
                values, "QUALITY_FLAKY_EVIDENCE_HMAC_KEY_FILE", root
            )
        key_id = _required(
            str(values.get("QUALITY_FLAKY_EVIDENCE_KEY_ID", "probe-evidence-key-v1")),
            "evidence key id",
        )
    except (ValueError, FlakyStoreError) as error:
        return ProbeRuntimeConfig(
            requested_enabled=requested,
            enabled=False,
            warning=str(error),
        )
    return ProbeRuntimeConfig(
        requested_enabled=requested,
        enabled=requested,
        jenkins_origin=origin,
        job_full_name=job,
        controller_commit_sha=controller_sha,
        controller_jenkinsfile_sha256=jenkinsfile_digest,
        evidence_hmac_key_file=evidence,
        evidence_key_id=key_id,
    )


class ProbeControlService:
    def __init__(
        self,
        database_path: str | Path,
        runtime: ProbeRuntimeConfig,
        *,
        target_resolver: Callable[[], str] | None = None,
        busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
        migrations_directory: str | Path = MIGRATIONS_DIRECTORY,
    ) -> None:
        self.database_path = Path(database_path)
        if not self.database_path.is_absolute():
            raise FlakyStoreError("invalid_database_path", "Flaky database path must be absolute")
        self.runtime = runtime
        self.target_resolver = target_resolver
        self.busy_timeout_ms = busy_timeout_ms
        self.migrations_directory = Path(migrations_directory)
        self.repository = FlakyRepository(self.database_path, busy_timeout_ms=busy_timeout_ms)

    def create_attempt(
        self,
        request: ProbeCreateRequest,
        *,
        now: datetime,
    ) -> dict[str, object]:
        _aware(now, "now")
        self._require_enabled()
        with self._read() as connection:
            duplicate = self._request_row(connection, request.request_id)
            if duplicate is not None:
                return self._idempotent_result(duplicate, request.payload_hash)
        if self.target_resolver is None:
            raise FlakyStoreError("target_head_unavailable", "origin/dev3 resolver is unavailable")
        target_sha = _require_sha(self.target_resolver(), "origin/dev3 HEAD")
        with self._write() as connection:
            duplicate = self._request_row(connection, request.request_id)
            if duplicate is not None:
                return self._idempotent_result(duplicate, request.payload_hash)
            governance = connection.execute(
                """SELECT governance.*, identity.case_id, identity.param_hash,
                          identity.environment, identity.execution_profile,
                          identity.state_epoch
                   FROM flaky_governance AS governance
                   JOIN flaky_identity AS identity USING (flaky_key)
                   WHERE governance.governance_id = ?""",
                (request.governance_id,),
            ).fetchone()
            if governance is None:
                raise FlakyStoreError("governance_not_found", "governance does not exist")
            if governance["status"] != "ACTIVE":
                raise FlakyStoreError("attempt_already_active", "governance is not ACTIVE")
            if int(governance["row_version"]) != request.row_version:
                raise FlakyStoreError("row_version_conflict", "governance row changed")
            case_path = str(governance["case_id"]).split("::", 1)[0].replace("\\", "/")
            if not (case_path == "module/smoke" or case_path.startswith("module/smoke/")):
                raise FlakyStoreError("governance_out_of_scope", "governance is outside module/smoke")
            occupied = connection.execute("SELECT trigger_id FROM flaky_probe_capacity_slot WHERE slot_id = 1").fetchone()
            if occupied is not None:
                raise FlakyStoreError("probe_capacity_exhausted", "the global Probe slot is occupied")
            attempt_id = stable_id(
                "attempt-v1",
                {"governance_id": request.governance_id, "request_id": request.request_id},
            )
            trigger_id = stable_id(
                "trigger-v1", {"attempt_id": attempt_id, "request_id": request.request_id}
            )
            policy = DEFAULT_GOVERNANCE_POLICY
            plan = ProbePlan(
                attempt_id=attempt_id,
                governance_id=request.governance_id,
                flaky_key=str(governance["flaky_key"]),
                case_id=str(governance["case_id"]),
                param_hash=str(governance["param_hash"]),
                environment=str(governance["environment"]),
                execution_profile=str(governance["execution_profile"]),
                state_epoch=int(governance["state_epoch"]),
                target_commit_sha=target_sha,
                controller_commit_sha=str(self.runtime.controller_commit_sha),
                policy_revision=policy.revision,
                required_consecutive_passes=policy.required_consecutive_passes,
                min_interval_minutes=policy.min_interval_minutes,
                max_attempt_age_hours=policy.max_attempt_age_hours,
                max_non_counting_runs=policy.max_non_counting_runs,
                allowed_job_full_name=str(self.runtime.job_full_name),
            )
            attempt_no = int(
                connection.execute(
                    "SELECT COALESCE(MAX(attempt_no), 0) + 1 FROM flaky_verification_attempt WHERE governance_id = ?",
                    (request.governance_id,),
                ).fetchone()[0]
            )
            expires_at = now + timedelta(hours=plan.max_attempt_age_hours)
            updated = connection.execute(
                """UPDATE flaky_governance
                   SET status='RECOVERING', row_version=row_version+1,
                       recovery_started_by='dashboard-anonymous', recovery_started_at=?,
                       recovery_reason=?
                   WHERE governance_id=? AND status='ACTIVE' AND row_version=?""",
                (utc_text(now), request.reason, request.governance_id, request.row_version),
            )
            if updated.rowcount != 1:
                raise FlakyStoreError("row_version_conflict", "governance row changed")
            connection.execute(
                """INSERT INTO flaky_verification_attempt (
                       attempt_id, governance_id, attempt_no, status,
                       target_commit_sha, policy_revision,
                       required_consecutive_passes, min_interval_minutes,
                       max_non_counting_runs, counted_passes, non_counting_runs,
                       started_by, start_reason, started_at, expires_at,
                       created_at, updated_at
                   ) VALUES (?, ?, ?, 'ACTIVE', ?, ?, ?, ?, ?, 0, 0,
                             'dashboard-anonymous', ?, ?, ?, ?, ?)""",
                (
                    attempt_id, request.governance_id, attempt_no,
                    target_sha, plan.policy_revision,
                    plan.required_consecutive_passes, plan.min_interval_minutes,
                    plan.max_non_counting_runs, request.reason,
                    utc_text(now), utc_text(expires_at), utc_text(now), utc_text(now),
                ),
            )
            self._insert_plan(connection, plan, now)
            connection.execute(
                """INSERT INTO flaky_probe_trigger (
                       trigger_id, attempt_id, request_id, payload_hash,
                       plan_digest, target_commit_sha, status,
                       allowed_job_full_name, created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, 'PENDING', ?, ?, ?)""",
                (
                    trigger_id, attempt_id, request.request_id, request.payload_hash,
                    plan.plan_digest, target_sha, plan.allowed_job_full_name,
                    utc_text(now), utc_text(now),
                ),
            )
            connection.execute(
                "INSERT INTO flaky_probe_capacity_slot(slot_id, trigger_id, acquired_at) VALUES (1, ?, ?)",
                (trigger_id, utc_text(now)),
            )
            self._insert_event(
                connection,
                governance_id=request.governance_id,
                attempt_id=attempt_id,
                event_type="probe_attempt_created",
                causal_id=request.request_id,
                from_status="ACTIVE",
                to_status="RECOVERING",
                actor="dashboard-anonymous",
                reason=request.reason,
                now=now,
            )
            row = self._trigger_row(connection, trigger_id)
            return self._safe_trigger_result(row, created=True)

    def dispatch_once(
        self,
        gateway: JenkinsGateway,
        *,
        now: datetime,
    ) -> dict[str, object]:
        _aware(now, "now")
        self._require_enabled()
        claimed = self._claim_dispatch(now)
        if claimed is None:
            return {"status": "IDLE"}
        trigger, raw_token = claimed
        try:
            result = gateway.dispatch(
                trigger_id=str(trigger["trigger_id"]),
                dispatch_token=raw_token,
                plan_digest=str(trigger["plan_digest"]),
            )
        except Exception:
            result = DispatchResult(
                DispatchResultKind.UNKNOWN,
                error_code="jenkins_dispatch_exception",
            )
        return self._record_dispatch_result(trigger, result, now=now)

    def claim(
        self,
        *,
        trigger_id: str,
        dispatch_token: str,
        plan_digest: str,
        job_full_name: str,
        build_number: int,
        now: datetime,
    ) -> dict[str, object]:
        _aware(now, "now")
        self._require_enabled()
        if build_number < 1:
            raise FlakyStoreError("invalid_build_number", "build number must be positive")
        token_hash = sha256_text(_required(dispatch_token, "dispatch token"))
        with self._write() as connection:
            row = connection.execute(
                """SELECT trigger.*, attempt.status AS attempt_status,
                          attempt.expires_at, governance.status AS governance_status
                   FROM flaky_probe_trigger AS trigger
                   JOIN flaky_verification_attempt AS attempt USING (attempt_id)
                   JOIN flaky_governance AS governance USING (governance_id)
                   WHERE trigger.trigger_id=?""",
                (_required(trigger_id, "trigger_id"),),
            ).fetchone()
            if row is None:
                raise FlakyStoreError("probe_trigger_not_found", "Probe trigger does not exist")
            if row["status"] == TriggerStatus.RUNNING.value:
                same = (
                    row["claimed_job_full_name"] == job_full_name
                    and int(row["claimed_build_number"]) == build_number
                    and row["claimed_token_hash"] is not None
                    and hmac.compare_digest(str(row["claimed_token_hash"]), token_hash)
                    and hmac.compare_digest(str(row["plan_digest"]), plan_digest)
                )
                if same:
                    return self._safe_trigger_result(row, created=False)
                raise FlakyStoreError("probe_claim_conflict", "another build already claimed the trigger")
            if row["status"] not in {
                TriggerStatus.DISPATCHING.value,
                TriggerStatus.DISPATCH_UNKNOWN.value,
                TriggerStatus.QUEUED.value,
            }:
                raise FlakyStoreError("probe_claim_not_allowed", "trigger cannot be claimed")
            if row["attempt_status"] != "ACTIVE" or row["governance_status"] != "RECOVERING":
                raise FlakyStoreError("probe_attempt_inactive", "Probe attempt is not active")
            if now >= _parse_time(str(row["expires_at"])):
                raise FlakyStoreError("probe_attempt_expired", "Probe attempt has expired")
            if not hmac.compare_digest(str(row["plan_digest"]), _require_digest(plan_digest, "plan digest")):
                raise FlakyStoreError("probe_plan_mismatch", "Probe plan digest differs")
            if not hmac.compare_digest(str(row["allowed_job_full_name"]), validate_job_full_name(job_full_name)):
                raise FlakyStoreError("probe_job_mismatch", "Jenkins Job identity differs")
            if row["token_hash"] is None or not hmac.compare_digest(str(row["token_hash"]), token_hash):
                raise FlakyStoreError("probe_dispatch_token_invalid", "dispatch token is invalid")
            updated = connection.execute(
                """UPDATE flaky_probe_trigger
                   SET status='RUNNING', token_hash=NULL, claimed_token_hash=?,
                       claimed_job_full_name=?, claimed_build_number=?, claimed_at=?,
                       row_version=row_version+1, updated_at=?, last_error_code=NULL
                   WHERE trigger_id=? AND status IN ('DISPATCHING','DISPATCH_UNKNOWN','QUEUED')
                     AND claimed_build_number IS NULL""",
                (
                    token_hash, job_full_name, build_number, utc_text(now),
                    utc_text(now), trigger_id,
                ),
            )
            if updated.rowcount != 1:
                raise FlakyStoreError("probe_claim_conflict", "Probe claim lost its CAS race")
            return self._safe_trigger_result(self._trigger_row(connection, trigger_id), created=False)

    def authorize_round(
        self,
        attempt_id: str,
        *,
        now: datetime,
    ) -> dict[str, object]:
        _aware(now, "now")
        self._require_enabled()
        with self._write() as connection:
            existing = connection.execute(
                """SELECT * FROM flaky_probe_round
                   WHERE attempt_id=? AND status IN ('AUTHORIZED','STARTED')""",
                (_required(attempt_id, "attempt_id"),),
            ).fetchone()
            if existing is not None:
                return dict(existing)
            row = connection.execute(
                """SELECT attempt.*, trigger.status AS trigger_status,
                          trigger.claimed_build_number, plan.max_orchestration_rounds,
                          plan.min_interval_minutes
                   FROM flaky_verification_attempt AS attempt
                   JOIN flaky_probe_trigger AS trigger USING (attempt_id)
                   JOIN flaky_probe_plan AS plan USING (attempt_id)
                   WHERE attempt.attempt_id=?""",
                (attempt_id,),
            ).fetchone()
            if row is None:
                raise FlakyStoreError("attempt_not_found", "verification attempt not found")
            if row["status"] != "ACTIVE" or row["trigger_status"] != TriggerStatus.RUNNING.value:
                raise FlakyStoreError("probe_round_not_allowed", "attempt is not running")
            if now >= _parse_time(str(row["expires_at"])):
                self._finish_attempt(
                    connection, attempt_id, "EXPIRED", "attempt_expired", now
                )
                raise FlakyStoreError("probe_attempt_expired", "Probe attempt has expired")
            previous_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM flaky_probe_round WHERE attempt_id=?",
                    (attempt_id,),
                ).fetchone()[0]
            )
            if previous_count >= int(row["max_orchestration_rounds"]):
                self._finish_attempt(
                    connection,
                    attempt_id,
                    "INCONCLUSIVE",
                    "probe_orchestration_budget_exhausted",
                    now,
                )
                raise FlakyStoreError("probe_round_budget_exhausted", "Probe round budget is exhausted")
            latest_pass = connection.execute(
                """SELECT MAX(trusted_started_at) FROM flaky_probe_evidence
                   WHERE attempt_id=? AND effect_status='APPLIED'
                     AND classification='COUNT_PASS'""",
                (attempt_id,),
            ).fetchone()[0]
            if latest_pass is not None and now < _parse_time(str(latest_pass)) + timedelta(
                minutes=int(row["min_interval_minutes"])
            ):
                raise FlakyStoreError("probe_interval_not_elapsed", "Probe pass interval has not elapsed")
            round_no = previous_count + 1
            run_id = f"probe-{attempt_id[-12:]}-{round_no:02d}-{uuid.uuid4().hex[:8]}"
            connection.execute(
                """INSERT INTO flaky_probe_round(
                       attempt_id, round_no, status, run_id, authorized_at
                   ) VALUES (?, ?, 'AUTHORIZED', ?, ?)""",
                (attempt_id, round_no, run_id, utc_text(now)),
            )
            return dict(
                connection.execute(
                    "SELECT * FROM flaky_probe_round WHERE attempt_id=? AND round_no=?",
                    (attempt_id, round_no),
                ).fetchone()
            )

    def start_round(
        self,
        attempt_id: str,
        round_no: int,
        *,
        actual_target_commit_sha: str,
        now: datetime,
    ) -> dict[str, object]:
        _aware(now, "now")
        self._require_enabled()
        actual_sha = _require_sha(actual_target_commit_sha, "actual target commit")
        with self._write() as connection:
            plan = connection.execute(
                "SELECT * FROM flaky_probe_plan WHERE attempt_id=?", (attempt_id,)
            ).fetchone()
            if plan is None:
                raise FlakyStoreError("probe_plan_not_found", "Probe plan does not exist")
            if actual_sha != plan["target_commit_sha"]:
                raise FlakyStoreError("probe_target_sha_mismatch", "target checkout SHA differs")
            row = connection.execute(
                """SELECT round.*, trigger.status AS trigger_status,
                          attempt.status AS attempt_status,
                          governance.status AS governance_status
                   FROM flaky_probe_round AS round
                   JOIN flaky_probe_trigger AS trigger USING (attempt_id)
                   JOIN flaky_verification_attempt AS attempt USING (attempt_id)
                   JOIN flaky_governance AS governance USING (governance_id)
                   WHERE round.attempt_id=? AND round.round_no=?""",
                (attempt_id, round_no),
            ).fetchone()
            if row is None:
                raise FlakyStoreError("probe_round_not_found", "Probe round does not exist")
            if row["status"] == ProbeRoundStatus.STARTED.value:
                if row["actual_target_commit_sha"] == actual_sha:
                    return dict(row)
                raise FlakyStoreError("probe_target_sha_mismatch", "round already used another SHA")
            if row["status"] != ProbeRoundStatus.AUTHORIZED.value:
                raise FlakyStoreError("probe_round_not_authorized", "Probe round is not authorized")
            if (
                row["trigger_status"] != TriggerStatus.RUNNING.value
                or row["attempt_status"] != "ACTIVE"
                or row["governance_status"] != "RECOVERING"
            ):
                raise FlakyStoreError(
                    "probe_round_not_allowed", "Probe round is no longer allowed to start"
                )
            connection.execute(
                """UPDATE flaky_probe_round
                   SET status='STARTED', actual_target_commit_sha=?, started_at=?
                   WHERE attempt_id=? AND round_no=? AND status='AUTHORIZED'""",
                (actual_sha, utc_text(now), attempt_id, round_no),
            )
            return dict(
                connection.execute(
                    "SELECT * FROM flaky_probe_round WHERE attempt_id=? AND round_no=?",
                    (attempt_id, round_no),
                ).fetchone()
            )

    def request_cancel(
        self,
        attempt_id: str,
        *,
        actor: str,
        reason: str,
        expected_row_version: int,
        now: datetime,
        gateway: JenkinsGateway | None = None,
    ) -> dict[str, object]:
        _aware(now, "now")
        actor = _required(actor, "actor")
        reason = normalize_reason(reason)
        with self._write() as connection:
            row = connection.execute(
                """SELECT trigger.*, attempt.status AS attempt_status,
                          attempt.governance_id, governance.row_version AS governance_row_version
                   FROM flaky_probe_trigger AS trigger
                   JOIN flaky_verification_attempt AS attempt USING (attempt_id)
                   JOIN flaky_governance AS governance USING (governance_id)
                   WHERE trigger.attempt_id=?""",
                (_required(attempt_id, "attempt_id"),),
            ).fetchone()
            if row is None:
                raise FlakyStoreError("attempt_not_found", "verification attempt not found")
            if int(row["governance_row_version"]) != expected_row_version:
                raise FlakyStoreError("row_version_conflict", "governance row changed")
            if row["attempt_status"] not in {"ACTIVE", "READY_TO_CLOSE"}:
                raise FlakyStoreError("attempt_not_active", "attempt is not live")
            if row["status"] == TriggerStatus.PENDING.value or (
                row["status"] == TriggerStatus.FAILED.value
                and row["failure_disposition"] == FailureDisposition.RETRYABLE.value
            ):
                return self._complete_cancel(
                    connection, row, actor=actor, reason=reason, now=now
                )
            if row["status"] not in {
                TriggerStatus.DISPATCHING.value,
                TriggerStatus.QUEUED.value,
                TriggerStatus.DISPATCH_UNKNOWN.value,
                TriggerStatus.RUNNING.value,
                TriggerStatus.CANCEL_REQUESTED.value,
            }:
                raise FlakyStoreError("probe_cancel_not_allowed", "trigger is already terminal")
            connection.execute(
                """UPDATE flaky_probe_trigger
                   SET status='CANCEL_REQUESTED', token_hash=NULL,
                       failure_disposition=NULL, cancel_requested_at=COALESCE(cancel_requested_at, ?),
                       row_version=row_version+1, updated_at=?
                   WHERE trigger_id=?""",
                (utc_text(now), utc_text(now), row["trigger_id"]),
            )
            trigger = dict(self._trigger_row(connection, str(row["trigger_id"])))
        if gateway is None:
            return self.trigger_status(str(trigger["trigger_id"]))
        try:
            observed = gateway.cancel(trigger)
        except Exception:
            observed = JenkinsObservation(JenkinsObservationKind.UNKNOWN, error_code="jenkins_cancel_exception")
        return self._apply_observation(trigger, observed, now=now)

    def reconcile_once(
        self,
        gateway: JenkinsGateway,
        *,
        now: datetime,
    ) -> dict[str, object]:
        _aware(now, "now")
        with self._read() as connection:
            row = connection.execute(
                """SELECT * FROM flaky_probe_trigger
                   WHERE status IN ('DISPATCHING','QUEUED','DISPATCH_UNKNOWN','RUNNING','CANCEL_REQUESTED')
                   ORDER BY updated_at, trigger_id LIMIT 1"""
            ).fetchone()
            trigger = dict(row) if row is not None else None
        if trigger is None:
            return {"status": "IDLE"}
        if not self.runtime.enabled and trigger["status"] != TriggerStatus.CANCEL_REQUESTED.value:
            with self._write() as connection:
                connection.execute(
                    """UPDATE flaky_probe_trigger SET status='CANCEL_REQUESTED', token_hash=NULL,
                              failure_disposition=NULL, cancel_requested_at=?,
                              row_version=row_version+1, updated_at=?
                       WHERE trigger_id=? AND status IN ('DISPATCHING','QUEUED','DISPATCH_UNKNOWN','RUNNING')""",
                    (utc_text(now), utc_text(now), trigger["trigger_id"]),
                )
                trigger = dict(self._trigger_row(connection, str(trigger["trigger_id"])))
        try:
            observed = (
                gateway.observe(trigger)
                if (
                    trigger["status"] == TriggerStatus.CANCEL_REQUESTED.value
                    and trigger.get("last_error_code") == "jenkins_cancel_requested"
                )
                else gateway.cancel(trigger)
                if trigger["status"] == TriggerStatus.CANCEL_REQUESTED.value
                else gateway.observe(trigger)
            )
        except Exception:
            observed = JenkinsObservation(JenkinsObservationKind.UNKNOWN, error_code="jenkins_reconcile_exception")
        return self._apply_observation(trigger, observed, now=now)

    def finalize_build(
        self,
        trigger_id: str,
        *,
        now: datetime,
        callback_missing: bool = False,
    ) -> dict[str, object]:
        _aware(now, "now")
        with self._write() as connection:
            row = connection.execute(
                """SELECT trigger.*, attempt.status AS attempt_status,
                          attempt.governance_id
                   FROM flaky_probe_trigger AS trigger
                   JOIN flaky_verification_attempt AS attempt USING (attempt_id)
                   WHERE trigger.trigger_id=?""",
                (_required(trigger_id, "trigger_id"),),
            ).fetchone()
            if row is None:
                raise FlakyStoreError("probe_trigger_not_found", "Probe trigger does not exist")
            if row["status"] in {TriggerStatus.COMPLETED.value, TriggerStatus.CANCELLED.value}:
                return self._safe_trigger_result(row, created=False)
            if row["status"] == TriggerStatus.CANCEL_REQUESTED.value:
                return self._complete_cancel(
                    connection,
                    row,
                    actor="probe-reconciler",
                    reason="jenkins_terminal_after_cancel",
                    now=now,
                )
            if row["status"] != TriggerStatus.RUNNING.value:
                raise FlakyStoreError("probe_build_not_running", "trigger has no claimed build")
            inflight = connection.execute(
                """SELECT * FROM flaky_probe_round
                   WHERE attempt_id=? AND status IN ('AUTHORIZED','STARTED')""",
                (row["attempt_id"],),
            ).fetchone()
            evidence_incomplete = False
            if inflight is not None:
                evidence_incomplete = True
                connection.execute(
                    """UPDATE flaky_probe_round
                       SET status='ABANDONED', abandoned_at=?, diagnostic_code='probe_build_ended_before_import'
                       WHERE attempt_id=? AND round_no=?""",
                    (utc_text(now), row["attempt_id"], inflight["round_no"]),
                )
                self._finish_attempt(
                    connection,
                    str(row["attempt_id"]),
                    "INCONCLUSIVE",
                    "probe_round_abandoned",
                    now,
                )
            elif row["attempt_status"] == "ACTIVE":
                evidence_incomplete = True
                self._finish_attempt(
                    connection,
                    str(row["attempt_id"]),
                    "INCONCLUSIVE",
                    "probe_build_finished_without_conclusion",
                    now,
                )
            connection.execute(
                """UPDATE flaky_probe_trigger
                   SET status=?, failure_disposition=?, token_hash=NULL,
                       last_error_code=?,
                       terminal_at=?, row_version=row_version+1, updated_at=?
                   WHERE trigger_id=?""",
                (
                    "FAILED" if callback_missing and evidence_incomplete else "COMPLETED",
                    "TERMINAL" if callback_missing and evidence_incomplete else None,
                    "probe_build_finished_without_evidence"
                    if callback_missing and evidence_incomplete else None,
                    utc_text(now), utc_text(now), trigger_id,
                ),
            )
            self._release_slot(connection, trigger_id)
            return self._safe_trigger_result(self._trigger_row(connection, trigger_id), created=False)

    def trigger_status(self, trigger_id: str) -> dict[str, object]:
        with self._read() as connection:
            row = self._trigger_row(connection, _required(trigger_id, "trigger_id"))
            if row is None:
                raise FlakyStoreError("probe_trigger_not_found", "Probe trigger does not exist")
            return self._safe_trigger_result(row, created=False)

    def _claim_dispatch(self, now: datetime):
        with self._write() as connection:
            row = connection.execute(
                """SELECT trigger.*, plan.max_dispatch_attempts,
                          attempt.status AS attempt_status, attempt.governance_id
                   FROM flaky_probe_trigger AS trigger
                   JOIN flaky_probe_plan AS plan USING (attempt_id)
                   JOIN flaky_verification_attempt AS attempt USING (attempt_id)
                    WHERE plan.plan_version='flaky-probe-plan.v1'
                      AND (trigger.status='PENDING'
                       OR (trigger.status='FAILED' AND trigger.failure_disposition='RETRYABLE'))
                   ORDER BY trigger.created_at, trigger.trigger_id LIMIT 1"""
            ).fetchone()
            if row is None:
                return None
            attempt_no = int(row["dispatch_attempt_no"]) + 1
            if attempt_no > int(row["max_dispatch_attempts"]):
                self._terminal_dispatch_failure(connection, row, "probe_dispatch_budget_exhausted", now)
                return None
            slot = connection.execute(
                "SELECT trigger_id FROM flaky_probe_capacity_slot WHERE slot_id=1"
            ).fetchone()
            if slot is None:
                connection.execute(
                    "INSERT INTO flaky_probe_capacity_slot(slot_id, trigger_id, acquired_at) VALUES(1, ?, ?)",
                    (row["trigger_id"], utc_text(now)),
                )
            elif slot["trigger_id"] != row["trigger_id"]:
                return None
            raw_token = secrets.token_urlsafe(32)
            token_hash = sha256_text(raw_token)
            updated = connection.execute(
                """UPDATE flaky_probe_trigger
                   SET status='DISPATCHING', failure_disposition=NULL,
                       dispatch_attempt_no=?, token_hash=?, claimed_token_hash=NULL,
                       dispatch_started_at=?, last_error_code=NULL,
                       row_version=row_version+1, updated_at=?
                   WHERE trigger_id=? AND row_version=?
                     AND (status='PENDING' OR (status='FAILED' AND failure_disposition='RETRYABLE'))""",
                (
                    attempt_no, token_hash, utc_text(now), utc_text(now),
                    row["trigger_id"], row["row_version"],
                ),
            )
            if updated.rowcount != 1:
                return None
            return dict(self._trigger_row(connection, str(row["trigger_id"]))), raw_token

    def _record_dispatch_result(
        self,
        claimed: Mapping[str, object],
        result: DispatchResult,
        *,
        now: datetime,
    ) -> dict[str, object]:
        with self._write() as connection:
            current = self._trigger_row(connection, str(claimed["trigger_id"]))
            if current is None:
                raise FlakyStoreError("probe_trigger_not_found", "Probe trigger disappeared")
            same_attempt = int(current["dispatch_attempt_no"]) == int(claimed["dispatch_attempt_no"])
            if current["status"] == TriggerStatus.RUNNING.value:
                if result.kind is DispatchResultKind.QUEUED and current["jenkins_queue_id"] is None:
                    connection.execute(
                        "UPDATE flaky_probe_trigger SET jenkins_queue_id=?, queued_at=?, updated_at=? WHERE trigger_id=? AND status='RUNNING'",
                        (result.queue_id, utc_text(now), utc_text(now), current["trigger_id"]),
                    )
                return self._safe_trigger_result(self._trigger_row(connection, str(current["trigger_id"])), created=False)
            if current["status"] != TriggerStatus.DISPATCHING.value or not same_attempt:
                return self._safe_trigger_result(current, created=False)
            if result.kind is DispatchResultKind.QUEUED:
                if result.queue_id is None or result.queue_id < 1:
                    result = DispatchResult(DispatchResultKind.UNKNOWN, error_code="jenkins_queue_id_invalid")
                else:
                    connection.execute(
                        """UPDATE flaky_probe_trigger
                           SET status='QUEUED', jenkins_queue_id=?, queued_at=?,
                               row_version=row_version+1, updated_at=?
                           WHERE trigger_id=? AND status='DISPATCHING' AND dispatch_attempt_no=?""",
                        (
                            result.queue_id, utc_text(now), utc_text(now),
                            current["trigger_id"], current["dispatch_attempt_no"],
                        ),
                    )
            if result.kind is DispatchResultKind.UNKNOWN:
                connection.execute(
                    """UPDATE flaky_probe_trigger
                       SET status='DISPATCH_UNKNOWN', last_error_code=?,
                           next_reconcile_at=?, row_version=row_version+1, updated_at=?
                       WHERE trigger_id=? AND status='DISPATCHING' AND dispatch_attempt_no=?""",
                    (
                        result.error_code or "jenkins_dispatch_unknown",
                        utc_text(now), utc_text(now), current["trigger_id"],
                        current["dispatch_attempt_no"],
                    ),
                )
            elif result.kind is DispatchResultKind.RETRYABLE_FAILURE:
                plan = connection.execute(
                    "SELECT max_dispatch_attempts FROM flaky_probe_plan WHERE attempt_id=?",
                    (current["attempt_id"],),
                ).fetchone()
                if int(current["dispatch_attempt_no"]) >= int(plan["max_dispatch_attempts"]):
                    self._terminal_dispatch_failure(
                        connection, current, result.error_code or "jenkins_dispatch_rejected", now
                    )
                else:
                    connection.execute(
                        """UPDATE flaky_probe_trigger
                           SET status='FAILED', failure_disposition='RETRYABLE',
                               token_hash=NULL, last_error_code=?, terminal_at=?,
                               row_version=row_version+1, updated_at=?
                           WHERE trigger_id=? AND status='DISPATCHING'""",
                        (
                            result.error_code or "jenkins_dispatch_rejected",
                            utc_text(now), utc_text(now), current["trigger_id"],
                        ),
                    )
                    self._release_slot(connection, str(current["trigger_id"]))
            return self._safe_trigger_result(
                self._trigger_row(connection, str(current["trigger_id"])), created=False
            )

    def _apply_observation(
        self,
        trigger: Mapping[str, object],
        observed: JenkinsObservation,
        *,
        now: datetime,
    ) -> dict[str, object]:
        with self._write() as connection:
            current = self._trigger_row(connection, str(trigger["trigger_id"]))
            if current is None:
                raise FlakyStoreError("probe_trigger_not_found", "Probe trigger disappeared")
            state = str(current["status"])
            if state == TriggerStatus.CANCEL_REQUESTED.value:
                if observed.kind in {
                    JenkinsObservationKind.CANCELLED,
                    JenkinsObservationKind.COMPLETED,
                    JenkinsObservationKind.NOT_RECEIVED,
                }:
                    return self._complete_cancel(
                        connection,
                        current,
                        actor="probe-reconciler",
                        reason="jenkins_cancel_confirmed",
                        now=now,
                    )
                connection.execute(
                    """UPDATE flaky_probe_trigger
                       SET last_error_code=?, next_reconcile_at=?, updated_at=?
                       WHERE trigger_id=?""",
                    (
                        observed.error_code or "jenkins_cancel_state_unknown",
                        utc_text(now),
                        utc_text(now),
                        current["trigger_id"],
                    ),
                )
            elif state == TriggerStatus.RUNNING.value and observed.kind is JenkinsObservationKind.COMPLETED:
                inflight = connection.execute(
                    """SELECT 1 FROM flaky_probe_round
                       WHERE attempt_id=? AND status IN ('AUTHORIZED','STARTED') LIMIT 1""",
                    (current["attempt_id"],),
                ).fetchone()
                grace_until = (
                    _parse_time(str(current["next_reconcile_at"]))
                    if current["next_reconcile_at"] is not None else None
                )
                if inflight is not None and (
                    current["last_error_code"] != "probe_build_terminal_waiting_import"
                    or grace_until is None
                ):
                    grace_until = now + timedelta(minutes=5)
                    connection.execute(
                        """UPDATE flaky_probe_trigger
                           SET last_error_code='probe_build_terminal_waiting_import',
                               next_reconcile_at=?, updated_at=? WHERE trigger_id=?""",
                        (utc_text(grace_until), utc_text(now), current["trigger_id"]),
                    )
                if inflight is not None and grace_until is not None and now < grace_until:
                    return self._safe_trigger_result(
                        self._trigger_row(connection, str(current["trigger_id"])), created=False
                    )
            elif state == TriggerStatus.QUEUED.value and observed.kind is JenkinsObservationKind.CANCELLED:
                self._terminal_dispatch_failure(
                    connection, current, "jenkins_queue_cancelled", now
                )
            elif observed.kind is JenkinsObservationKind.QUEUED and state in {
                TriggerStatus.DISPATCHING.value,
                TriggerStatus.DISPATCH_UNKNOWN.value,
                TriggerStatus.QUEUED.value,
            }:
                if observed.queue_id is not None and observed.queue_id > 0:
                    connection.execute(
                        """UPDATE flaky_probe_trigger
                           SET status='QUEUED', jenkins_queue_id=?, queued_at=COALESCE(queued_at, ?),
                               last_error_code=NULL, row_version=row_version+1, updated_at=?
                           WHERE trigger_id=? AND status IN ('DISPATCHING','DISPATCH_UNKNOWN','QUEUED')""",
                        (
                            observed.queue_id, utc_text(now), utc_text(now), current["trigger_id"],
                        ),
                    )
            elif observed.kind is JenkinsObservationKind.RUNNING and state != TriggerStatus.RUNNING.value:
                connection.execute(
                    """UPDATE flaky_probe_trigger
                       SET status='DISPATCH_UNKNOWN', last_error_code='unclaimed_build_observed',
                           next_reconcile_at=?, row_version=row_version+1, updated_at=?
                       WHERE trigger_id=? AND status IN ('DISPATCHING','DISPATCH_UNKNOWN','QUEUED')""",
                    (utc_text(now), utc_text(now), current["trigger_id"]),
                )
            elif observed.kind is JenkinsObservationKind.NOT_RECEIVED and state in {
                TriggerStatus.DISPATCHING.value,
                TriggerStatus.DISPATCH_UNKNOWN.value,
            }:
                connection.execute(
                    """UPDATE flaky_probe_trigger
                       SET status='FAILED', failure_disposition='RETRYABLE', token_hash=NULL,
                           last_error_code='jenkins_not_received', terminal_at=?,
                           row_version=row_version+1, updated_at=?
                       WHERE trigger_id=?""",
                    (utc_text(now), utc_text(now), current["trigger_id"]),
                )
                self._release_slot(connection, str(current["trigger_id"]))
            else:
                error_code = observed.error_code or "jenkins_state_unknown"
                if state == TriggerStatus.DISPATCHING.value:
                    connection.execute(
                        """UPDATE flaky_probe_trigger
                           SET status='DISPATCH_UNKNOWN', last_error_code=?,
                               next_reconcile_at=?, row_version=row_version+1, updated_at=?
                           WHERE trigger_id=? AND status='DISPATCHING'""",
                        (
                            error_code, utc_text(now), utc_text(now),
                            current["trigger_id"],
                        ),
                    )
                else:
                    connection.execute(
                        """UPDATE flaky_probe_trigger
                           SET last_error_code=?, next_reconcile_at=?, updated_at=?
                           WHERE trigger_id=?""",
                        (
                            error_code, utc_text(now), utc_text(now),
                            current["trigger_id"],
                        ),
                    )
            result = self._safe_trigger_result(
                self._trigger_row(connection, str(current["trigger_id"])), created=False
            )
        if state == TriggerStatus.RUNNING.value and observed.kind is JenkinsObservationKind.COMPLETED:
            return self.finalize_build(
                str(trigger["trigger_id"]), now=now, callback_missing=True
            )
        return result

    def _terminal_dispatch_failure(self, connection, row, reason: str, now: datetime) -> None:
        connection.execute(
            """UPDATE flaky_probe_trigger
               SET status='FAILED', failure_disposition='TERMINAL', token_hash=NULL,
                   last_error_code=?, terminal_at=?, row_version=row_version+1, updated_at=?
               WHERE trigger_id=?""",
            (reason, utc_text(now), utc_text(now), row["trigger_id"]),
        )
        self._finish_attempt(connection, str(row["attempt_id"]), "INCONCLUSIVE", reason, now)
        self._release_slot(connection, str(row["trigger_id"]))

    def _finish_attempt(
        self,
        connection,
        attempt_id: str,
        status: str,
        reason: str,
        now: datetime,
    ) -> None:
        attempt = connection.execute(
            "SELECT * FROM flaky_verification_attempt WHERE attempt_id=?", (attempt_id,)
        ).fetchone()
        if attempt is None or attempt["status"] not in {"ACTIVE", "READY_TO_CLOSE"}:
            return
        connection.execute(
            """UPDATE flaky_verification_attempt
               SET status=?, ended_at=?, end_reason=?, updated_at=? WHERE attempt_id=?""",
            (status, utc_text(now), reason, utc_text(now), attempt_id),
        )
        governance = connection.execute(
            "SELECT * FROM flaky_governance WHERE governance_id=?",
            (attempt["governance_id"],),
        ).fetchone()
        if governance is not None and governance["status"] == "RECOVERING":
            connection.execute(
                """UPDATE flaky_governance
                   SET status='ACTIVE', row_version=row_version+1,
                       recovery_started_by=NULL, recovery_started_at=NULL, recovery_reason=NULL
                   WHERE governance_id=? AND status='RECOVERING'""",
                (attempt["governance_id"],),
            )
            self._insert_event(
                connection,
                governance_id=str(attempt["governance_id"]),
                attempt_id=attempt_id,
                event_type=f"attempt_{status.casefold()}",
                causal_id=reason,
                from_status="RECOVERING",
                to_status="ACTIVE",
                actor="probe-controller",
                reason=reason,
                now=now,
            )

    def _complete_cancel(
        self,
        connection,
        row,
        *,
        actor: str,
        reason: str,
        now: datetime,
    ) -> dict[str, object]:
        connection.execute(
            """UPDATE flaky_probe_trigger
               SET status='CANCELLED', failure_disposition=NULL, token_hash=NULL,
                   terminal_at=?, row_version=row_version+1, updated_at=?
               WHERE trigger_id=?""",
            (utc_text(now), utc_text(now), row["trigger_id"]),
        )
        connection.execute(
            """UPDATE flaky_verification_attempt
               SET status='CANCELLED', ended_at=?, end_reason=?, updated_at=?
               WHERE attempt_id=? AND status IN ('ACTIVE','READY_TO_CLOSE')""",
            (utc_text(now), reason, utc_text(now), row["attempt_id"]),
        )
        attempt = connection.execute(
            "SELECT governance_id FROM flaky_verification_attempt WHERE attempt_id=?",
            (row["attempt_id"],),
        ).fetchone()
        if attempt is not None:
            governance = connection.execute(
                "SELECT * FROM flaky_governance WHERE governance_id=?",
                (attempt["governance_id"],),
            ).fetchone()
            if governance is not None and governance["status"] == "RECOVERING":
                connection.execute(
                    """UPDATE flaky_governance
                       SET status='ACTIVE', row_version=row_version+1,
                           recovery_started_by=NULL, recovery_started_at=NULL,
                           recovery_reason=NULL
                       WHERE governance_id=?""",
                    (attempt["governance_id"],),
                )
                self._insert_event(
                    connection,
                    governance_id=str(attempt["governance_id"]),
                    attempt_id=str(row["attempt_id"]),
                    event_type="probe_cancelled",
                    causal_id=str(row["trigger_id"]),
                    from_status="RECOVERING",
                    to_status="ACTIVE",
                    actor=actor,
                    reason=reason,
                    now=now,
                )
        self._release_slot(connection, str(row["trigger_id"]))
        return self._safe_trigger_result(
            self._trigger_row(connection, str(row["trigger_id"])), created=False
        )

    @staticmethod
    def _release_slot(connection, trigger_id: str) -> None:
        connection.execute(
            "DELETE FROM flaky_probe_capacity_slot WHERE slot_id=1 AND trigger_id=?",
            (trigger_id,),
        )

    def get_plan(self, attempt_id: str) -> ProbePlan:
        with self._read() as connection:
            row = connection.execute(
                "SELECT * FROM flaky_probe_plan WHERE attempt_id = ?",
                (_required(attempt_id, "attempt_id"),),
            ).fetchone()
            if row is None:
                raise FlakyStoreError("probe_plan_not_found", "Probe plan does not exist")
            return parse_plan_row(row)

    def get_round(self, attempt_id: str, round_no: int) -> dict[str, object]:
        with self._read() as connection:
            row = connection.execute(
                "SELECT * FROM flaky_probe_round WHERE attempt_id=? AND round_no=?",
                (_required(attempt_id, "attempt_id"), int(round_no)),
            ).fetchone()
            if row is None:
                raise FlakyStoreError("probe_round_not_found", "Probe round does not exist")
            return dict(row)

    def _insert_plan(self, connection, plan: ProbePlan, now: datetime) -> None:
        connection.execute(
            """INSERT INTO flaky_probe_plan (
                   attempt_id, governance_id, flaky_key, plan_version,
                   canonical_json, plan_digest, case_id, param_hash,
                   environment, execution_profile, state_epoch, target_branch,
                   target_commit_sha, controller_commit_sha, policy_revision,
                   probe_evidence_rule_version, fact_schema_version,
                   required_consecutive_passes, min_interval_minutes,
                   max_attempt_age_hours, max_non_counting_runs,
                   max_dispatch_attempts, max_orchestration_rounds,
                   allowed_job_full_name, created_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                         ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                plan.attempt_id, plan.governance_id, plan.flaky_key,
                plan.schema_version, plan.canonical_json, plan.plan_digest,
                plan.case_id, plan.param_hash, plan.environment,
                plan.execution_profile, plan.state_epoch, plan.target_branch,
                plan.target_commit_sha, plan.controller_commit_sha,
                plan.policy_revision, plan.probe_evidence_rule_version,
                plan.fact_schema_version, plan.required_consecutive_passes,
                plan.min_interval_minutes, plan.max_attempt_age_hours,
                plan.max_non_counting_runs, plan.max_dispatch_attempts,
                plan.max_orchestration_rounds, plan.allowed_job_full_name,
                utc_text(now),
            ),
        )

    def _request_row(self, connection, request_id: str):
        return connection.execute(
            """SELECT trigger.*, plan.controller_commit_sha
               FROM flaky_probe_trigger AS trigger
               JOIN flaky_probe_plan AS plan ON plan.plan_digest=trigger.plan_digest
               WHERE trigger.request_id=?""",
            (request_id,),
        ).fetchone()

    def _idempotent_result(self, row, payload_hash: str) -> dict[str, object]:
        if not hmac.compare_digest(str(row["payload_hash"]), payload_hash):
            raise FlakyStoreError("idempotency_conflict", "request_id payload differs")
        return self._safe_trigger_result(row, created=False)

    @staticmethod
    def _safe_trigger_result(row, *, created: bool) -> dict[str, object]:
        return {
            "schema_version": "flaky-probe-attempt-result.v1",
            "created": created,
            "attempt_id": str(row["attempt_id"]),
            "trigger_id": str(row["trigger_id"]),
            "target_commit_sha": str(row["target_commit_sha"]),
            "plan_digest": str(row["plan_digest"]),
            "status": str(row["status"]),
        }

    @staticmethod
    def _trigger_row(connection, trigger_id: str):
        return connection.execute(
            "SELECT * FROM flaky_probe_trigger WHERE trigger_id=?", (trigger_id,)
        ).fetchone()

    def _require_enabled(self) -> None:
        if not self.runtime.enabled:
            raise FlakyStoreError(
                "probe_trigger_disabled",
                self.runtime.warning or "Probe trigger is disabled",
            )

    @contextmanager
    def _read(self) -> Iterator[object]:
        with self.repository.connection(require_existing=True, read_only=True) as connection:
            initialization = validate_store_schema(connection, self.repository, self.migrations_directory)
            if initialization.schema_version != PROBE_DB_SCHEMA_VERSION:
                raise FlakyStoreError("schema_migration_required", "Probe dispatch requires schema 4")
            yield connection

    @contextmanager
    def _write(self) -> Iterator[object]:
        with database_writer_lock(self.database_path, timeout_ms=self.busy_timeout_ms):
            with self.repository.connection(require_existing=True) as connection:
                initialization = validate_store_schema(connection, self.repository, self.migrations_directory)
                if initialization.schema_version != PROBE_DB_SCHEMA_VERSION:
                    raise FlakyStoreError("schema_migration_required", "Probe dispatch requires schema 4")
                with self.repository.transaction(connection):
                    yield connection

    @staticmethod
    def _insert_event(
        connection,
        *,
        governance_id: str,
        attempt_id: str,
        event_type: str,
        causal_id: str,
        from_status: str | None,
        to_status: str,
        actor: str | None,
        reason: str | None,
        now: datetime,
    ) -> None:
        event_id = stable_id(
            "governance-event-v1",
            {"governance_id": governance_id, "event_type": event_type, "causal_id": causal_id},
        )
        connection.execute(
            """INSERT OR IGNORE INTO flaky_governance_event (
                   event_id, governance_id, attempt_id, event_type, causal_id,
                   from_status, to_status, actor, reason, created_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event_id, governance_id, attempt_id, event_type, causal_id,
                from_status, to_status, actor, reason, utc_text(now),
            ),
        )


class FixedJenkinsClient:
    def __init__(
        self,
        runtime: ProbeRuntimeConfig,
        *,
        session=None,
        connect_timeout_seconds: float = DEFAULT_JENKINS_CONNECT_TIMEOUT_SECONDS,
        total_timeout_seconds: float = DEFAULT_JENKINS_TOTAL_TIMEOUT_SECONDS,
    ) -> None:
        if (
            runtime.jenkins_origin is None
            or runtime.job_full_name is None
            or runtime.credential_file is None
        ):
            raise ValueError("complete Probe Jenkins configuration is required")
        self.runtime = runtime
        self.connect_timeout_seconds = float(connect_timeout_seconds)
        self.total_timeout_seconds = float(total_timeout_seconds)
        if self.connect_timeout_seconds <= 0 or self.total_timeout_seconds <= 0:
            raise ValueError("Jenkins timeouts must be positive")
        if session is None:
            try:
                import requests
            except ImportError as error:
                raise RuntimeError("requests is required for Jenkins dispatch") from error
            session = requests.Session()
        self.session = session
        self._auth = _read_jenkins_credentials(runtime.credential_file)
        self._job_url = _jenkins_job_url(
            str(runtime.jenkins_origin), str(runtime.job_full_name)
        )

    def dispatch(
        self,
        *,
        trigger_id: str,
        dispatch_token: str,
        plan_digest: str,
    ) -> DispatchResult:
        try:
            response = self.session.post(
                f"{self._job_url}/buildWithParameters",
                data={
                    "TRIGGER_ID": _required(trigger_id, "trigger_id"),
                    "DISPATCH_TOKEN": _required(dispatch_token, "dispatch token"),
                    "PLAN_DIGEST": _require_digest(plan_digest, "plan digest"),
                },
                auth=self._auth,
                allow_redirects=False,
                timeout=(self.connect_timeout_seconds, self.total_timeout_seconds),
            )
        except Exception as error:
            name = type(error).__name__
            if name == "ConnectTimeout":
                return DispatchResult(
                    DispatchResultKind.RETRYABLE_FAILURE,
                    error_code="jenkins_connection_not_established",
                )
            return DispatchResult(
                DispatchResultKind.UNKNOWN,
                error_code="jenkins_dispatch_response_unknown",
            )
        if response.status_code == 201:
            queue_id = _validated_queue_location(
                str(self.runtime.jenkins_origin), response.headers.get("Location")
            )
            if queue_id is not None:
                return DispatchResult(DispatchResultKind.QUEUED, queue_id=queue_id)
            return DispatchResult(
                DispatchResultKind.UNKNOWN,
                error_code="jenkins_queue_location_invalid",
            )
        if 400 <= response.status_code < 500:
            return DispatchResult(
                DispatchResultKind.RETRYABLE_FAILURE,
                error_code="jenkins_dispatch_rejected",
            )
        return DispatchResult(
            DispatchResultKind.UNKNOWN,
            error_code=(
                "jenkins_redirect_rejected"
                if 300 <= response.status_code < 400
                else "jenkins_dispatch_response_unknown"
            ),
        )

    def observe(self, trigger: Mapping[str, object]) -> JenkinsObservation:
        build_number = trigger.get("claimed_build_number")
        if build_number is not None:
            url = f"{self._job_url}/{int(build_number)}/api/json"
            try:
                response = self.session.get(
                    url,
                    auth=self._auth,
                    allow_redirects=False,
                    timeout=(self.connect_timeout_seconds, self.total_timeout_seconds),
                )
            except Exception:
                return JenkinsObservation(
                    JenkinsObservationKind.UNKNOWN,
                    build_number=int(build_number),
                    error_code="jenkins_unreachable",
                )
            if (
                response.status_code == 404
                and trigger.get("status") == TriggerStatus.CANCEL_REQUESTED.value
            ):
                return JenkinsObservation(
                    JenkinsObservationKind.CANCELLED,
                    build_number=int(build_number),
                )
            if response.status_code != 200:
                return JenkinsObservation(
                    JenkinsObservationKind.UNKNOWN,
                    build_number=int(build_number),
                    error_code="jenkins_build_state_unknown",
                )
            try:
                building = bool(response.json()["building"])
            except (KeyError, TypeError, ValueError):
                return JenkinsObservation(
                    JenkinsObservationKind.UNKNOWN,
                    build_number=int(build_number),
                    error_code="jenkins_build_response_invalid",
                )
            return JenkinsObservation(
                JenkinsObservationKind.RUNNING if building else JenkinsObservationKind.COMPLETED,
                build_number=int(build_number),
            )
        queue_id = trigger.get("jenkins_queue_id")
        if queue_id is None:
            return self._observe_unidentified(trigger)
        try:
            response = self.session.get(
                f"{self.runtime.jenkins_origin}/queue/item/{int(queue_id)}/api/json",
                auth=self._auth,
                allow_redirects=False,
                timeout=(self.connect_timeout_seconds, self.total_timeout_seconds),
            )
        except Exception:
            return JenkinsObservation(
                JenkinsObservationKind.UNKNOWN,
                queue_id=int(queue_id),
                error_code="jenkins_unreachable",
            )
        if (
            response.status_code == 404
            and trigger.get("status") == TriggerStatus.CANCEL_REQUESTED.value
        ):
            return JenkinsObservation(
                JenkinsObservationKind.CANCELLED, queue_id=int(queue_id)
            )
        if response.status_code != 200:
            return JenkinsObservation(
                JenkinsObservationKind.UNKNOWN,
                queue_id=int(queue_id),
                error_code="jenkins_queue_state_unknown",
            )
        try:
            payload = response.json()
        except ValueError:
            return JenkinsObservation(
                JenkinsObservationKind.UNKNOWN,
                queue_id=int(queue_id),
                error_code="jenkins_queue_response_invalid",
            )
        if bool(payload.get("cancelled")):
            return JenkinsObservation(JenkinsObservationKind.CANCELLED, queue_id=int(queue_id))
        executable = payload.get("executable")
        if isinstance(executable, dict) and isinstance(executable.get("number"), int):
            return JenkinsObservation(
                JenkinsObservationKind.RUNNING,
                queue_id=int(queue_id),
                build_number=int(executable["number"]),
            )
        return JenkinsObservation(JenkinsObservationKind.QUEUED, queue_id=int(queue_id))

    def _observe_unidentified(self, trigger: Mapping[str, object]) -> JenkinsObservation:
        try:
            response = self.session.get(
                f"{self.runtime.jenkins_origin}/queue/api/json",
                params={
                    "tree": "items[id,task[fullName],actions[parameters[name,value]],executable[number]]"
                },
                auth=self._auth,
                allow_redirects=False,
                timeout=(self.connect_timeout_seconds, self.total_timeout_seconds),
            )
        except Exception:
            return JenkinsObservation(
                JenkinsObservationKind.UNKNOWN, error_code="jenkins_unreachable"
            )
        if response.status_code != 200:
            return JenkinsObservation(
                JenkinsObservationKind.UNKNOWN, error_code="jenkins_queue_scan_unknown"
            )
        try:
            items = response.json().get("items", [])
        except (AttributeError, ValueError):
            return JenkinsObservation(
                JenkinsObservationKind.UNKNOWN, error_code="jenkins_queue_response_invalid"
            )
        matches: list[tuple[int, int | None]] = []
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict) or not isinstance(item.get("id"), int):
                continue
            task = item.get("task")
            if not isinstance(task, dict) or task.get("fullName") != self.runtime.job_full_name:
                continue
            parameters: dict[str, object] = {}
            for action in item.get("actions", []) if isinstance(item.get("actions"), list) else []:
                if not isinstance(action, dict):
                    continue
                for parameter in action.get("parameters", []) if isinstance(action.get("parameters"), list) else []:
                    if isinstance(parameter, dict) and isinstance(parameter.get("name"), str):
                        parameters[parameter["name"]] = parameter.get("value")
            if (
                parameters.get("TRIGGER_ID") != trigger.get("trigger_id")
                or parameters.get("PLAN_DIGEST") != trigger.get("plan_digest")
            ):
                continue
            executable = item.get("executable")
            build = executable.get("number") if isinstance(executable, dict) else None
            matches.append((int(item["id"]), int(build) if isinstance(build, int) else None))
        if len(matches) != 1:
            return JenkinsObservation(
                JenkinsObservationKind.UNKNOWN,
                error_code="jenkins_identity_missing" if not matches else "jenkins_identity_ambiguous",
            )
        queue, build = matches[0]
        return JenkinsObservation(
            JenkinsObservationKind.RUNNING if build is not None else JenkinsObservationKind.QUEUED,
            queue_id=queue,
            build_number=build,
        )

    def cancel(self, trigger: Mapping[str, object]) -> JenkinsObservation:
        build_number = trigger.get("claimed_build_number")
        queue_id = trigger.get("jenkins_queue_id")
        if build_number is not None:
            url = f"{self._job_url}/{int(build_number)}/stop"
        elif queue_id is not None:
            url = f"{self.runtime.jenkins_origin}/queue/cancelItem?id={int(queue_id)}"
        else:
            return JenkinsObservation(
                JenkinsObservationKind.UNKNOWN,
                error_code="jenkins_cancel_identity_unknown",
            )
        try:
            response = self.session.post(
                url,
                auth=self._auth,
                allow_redirects=False,
                timeout=(self.connect_timeout_seconds, self.total_timeout_seconds),
            )
        except Exception:
            return JenkinsObservation(
                JenkinsObservationKind.UNKNOWN, error_code="jenkins_cancel_unknown"
            )
        if response.status_code in {200, 201, 302}:
            return JenkinsObservation(
                JenkinsObservationKind.UNKNOWN,
                queue_id=int(queue_id) if queue_id is not None else None,
                build_number=int(build_number) if build_number is not None else None,
                error_code="jenkins_cancel_requested",
            )
        return JenkinsObservation(
            JenkinsObservationKind.UNKNOWN, error_code="jenkins_cancel_rejected"
        )


class CsrfProtector:
    def __init__(
        self,
        secret: bytes,
        *,
        ttl_seconds: int = 600,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if len(secret) < 32:
            raise ValueError("CSRF secret must contain at least 32 bytes")
        if ttl_seconds < 1:
            raise ValueError("CSRF TTL must be positive")
        self._secret = bytes(secret)
        self._ttl_seconds = int(ttl_seconds)
        self._clock = clock or (lambda: datetime.now(UTC))

    def issue(self) -> str:
        issued = int(_aware(self._clock(), "CSRF clock").timestamp())
        payload = f"{issued}.{secrets.token_hex(16)}"
        signature = hmac.new(self._secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()
        return f"{payload}.{signature}"

    def validate(self, *, cookie_token: str | None, header_token: str | None) -> bool:
        if not cookie_token or not header_token or not hmac.compare_digest(cookie_token, header_token):
            return False
        parts = header_token.split(".")
        if len(parts) != 3 or re.fullmatch(r"[0-9a-f]{32}", parts[1]) is None:
            return False
        try:
            issued = int(parts[0])
        except ValueError:
            return False
        now = int(_aware(self._clock(), "CSRF clock").timestamp())
        if issued > now + 30 or now - issued > self._ttl_seconds:
            return False
        payload = f"{parts[0]}.{parts[1]}"
        expected = hmac.new(self._secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, parts[2])


def sign_probe_envelope(payload: Mapping[str, object], secret: bytes) -> str:
    if len(secret) < 32:
        raise ValueError("evidence HMAC key must contain at least 32 bytes")
    signature = hmac.new(secret, canonical_json(dict(payload)).encode("utf-8"), hashlib.sha256).hexdigest()
    return f"hmac-sha256:{signature}"


def build_probe_envelope(*, secret: bytes, **fields: object) -> ProbeEvidenceEnvelope:
    unsigned = dict(fields)
    unsigned.setdefault("schema_version", PROBE_ENVELOPE_VERSION)
    provisional = ProbeEvidenceEnvelope.model_validate(
        {**unsigned, "signature": f"hmac-sha256:{'0' * 64}"}
    )
    signature = sign_probe_envelope(provisional.signing_payload, secret)
    return provisional.model_copy(update={"signature": signature})


def verify_probe_envelope(envelope: ProbeEvidenceEnvelope, secret: bytes) -> None:
    expected = sign_probe_envelope(envelope.signing_payload, secret)
    if not hmac.compare_digest(expected, envelope.signature):
        raise FlakyStoreError("probe_envelope_signature_invalid", "Probe envelope signature is invalid")


def select_probe_nodeid(
    plan: ProbePlan,
    collected_cases: Sequence[object],
    *,
    execution_profile: str,
) -> str:
    if execution_profile != plan.execution_profile:
        raise FlakyStoreError("probe_profile_mismatch", "execution profile cannot be reproduced")
    matches = []
    for case in collected_cases:
        if (
            getattr(case, "case_id", None) == plan.case_id
            and getattr(case, "param_hash", None) == plan.param_hash
            and getattr(case, "normalized_case_path", None)
            == plan.case_id.split("::", 1)[0].replace("\\", "/")
        ):
            matches.append(str(getattr(case, "nodeid")))
    if len(matches) != 1:
        raise FlakyStoreError(
            "probe_identity_not_unique",
            "Probe identity must resolve to exactly one collected nodeid",
        )
    return matches[0]


def restricted_target_environment(
    source: Mapping[str, str],
    *,
    allowed_names: Sequence[str],
    required_values: Mapping[str, str] | None = None,
) -> dict[str, str]:
    forbidden_fragments = ("TOKEN", "SECRET", "PASSWORD", "HMAC", "CSRF", "JENKINS")
    forbidden_exact = {
        "QUALITY_FLAKY_DB_PATH",
        "QUALITY_FLAKY_TRIGGER_ENABLE",
        "QUALITY_FLAKY_JENKINS_CREDENTIAL_FILE",
        "DISPATCH_TOKEN",
    }
    result = {
        name: str(source[name])
        for name in allowed_names
        if name in source
        and name not in forbidden_exact
        and not any(fragment in name.upper() for fragment in forbidden_fragments)
    }
    for name, value in (required_values or {}).items():
        if name in forbidden_exact or any(fragment in name.upper() for fragment in forbidden_fragments):
            raise ValueError(f"target environment cannot receive {name}")
        result[name] = str(value)
    return result


def validate_p0_bundle(
    directory: str | Path,
    *,
    max_total_bytes: int = 10 * 1024 * 1024,
) -> tuple[str, str | None, dict[str, str], tuple[str, ...]]:
    root = Path(directory).resolve()
    required = (
        "run.json",
        "merged/manifest.json",
        "merged/case-results.jsonl",
        "merged/failures.jsonl",
        "merged/integrity-issues.jsonl",
    )
    hashes: dict[str, str] = {}
    total = 0
    diagnostics: list[str] = []
    for relative in required:
        path = root.joinpath(*relative.split("/"))
        try:
            resolved = path.resolve(strict=True)
        except OSError:
            diagnostics.append("probe_p0_file_missing")
            continue
        if root not in resolved.parents or resolved.is_symlink() or not resolved.is_file():
            diagnostics.append("probe_p0_path_invalid")
            continue
        size = resolved.stat().st_size
        total += size
        if total > max_total_bytes:
            return "OVERSIZE", None, {}, ("probe_p0_bundle_oversize",)
        digest = hashlib.sha256(resolved.read_bytes()).hexdigest()
        hashes[relative] = f"sha256:{digest}"
    if diagnostics:
        status = "MISSING" if set(diagnostics) == {"probe_p0_file_missing"} else "INVALID"
        return status, None, {}, tuple(sorted(set(diagnostics)))
    try:
        manifest = json.loads((root / "merged" / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return "INVALID", None, {}, ("probe_p0_manifest_invalid",)
    if not isinstance(manifest, dict):
        return "INVALID", None, {}, ("probe_p0_manifest_invalid",)
    return "VALID", hashes["merged/manifest.json"], dict(sorted(hashes.items())), ()


def probe_invariant_issues(connection) -> tuple[str, ...]:
    issues: list[str] = []
    sql_checks = {
        "probe_attempt_plan_trigger_mismatch": """
            SELECT 1 FROM flaky_verification_attempt AS attempt
            LEFT JOIN flaky_probe_plan AS plan USING (attempt_id)
            LEFT JOIN flaky_probe_trigger AS trigger USING (attempt_id)
            WHERE plan.attempt_id IS NULL OR trigger.trigger_id IS NULL LIMIT 1""",
        "probe_active_slot_missing": """
            SELECT 1 FROM flaky_probe_trigger AS trigger
            LEFT JOIN flaky_probe_capacity_slot AS slot USING (trigger_id)
            WHERE trigger.status IN ('PENDING','DISPATCHING','QUEUED','DISPATCH_UNKNOWN','RUNNING','CANCEL_REQUESTED')
              AND slot.trigger_id IS NULL LIMIT 1""",
        "probe_slot_not_active": """
            SELECT 1 FROM flaky_probe_capacity_slot AS slot
            JOIN flaky_probe_trigger AS trigger USING (trigger_id)
            WHERE trigger.status NOT IN ('PENDING','DISPATCHING','QUEUED','DISPATCH_UNKNOWN','RUNNING','CANCEL_REQUESTED')
            LIMIT 1""",
        "probe_running_claim_invalid": """
            SELECT 1 FROM flaky_probe_trigger
            WHERE status='RUNNING' AND (
                claimed_build_number IS NULL OR claimed_job_full_name IS NULL
                OR claimed_at IS NULL OR token_hash IS NOT NULL
            ) LIMIT 1""",
        "probe_queued_claim_invalid": """
            SELECT 1 FROM flaky_probe_trigger
            WHERE status='QUEUED' AND (
                jenkins_queue_id IS NULL OR claimed_build_number IS NOT NULL
            ) LIMIT 1""",
        "probe_multiple_inflight_rounds": """
            SELECT 1 FROM flaky_probe_round
            WHERE status IN ('AUTHORIZED','STARTED')
            GROUP BY attempt_id HAVING COUNT(*) > 1 LIMIT 1""",
        "probe_round_evidence_mismatch": """
            SELECT 1 FROM flaky_probe_round AS round
            LEFT JOIN flaky_probe_evidence AS evidence ON evidence.evidence_id=round.evidence_id
            WHERE (round.status='IMPORTED' AND (
                       evidence.evidence_id IS NULL OR evidence.attempt_id!=round.attempt_id
                       OR evidence.round_no!=round.round_no OR evidence.run_id!=round.run_id
                   ))
               OR (round.status!='IMPORTED' AND round.evidence_id IS NOT NULL)
            LIMIT 1""",
    }
    for code, sql in sql_checks.items():
        if connection.execute(sql).fetchone() is not None:
            issues.append(code)
    for row in connection.execute(
        "SELECT plan_version, canonical_json, plan_digest FROM flaky_probe_plan"
    ).fetchall():
        if row["plan_version"] == PROBE_PLAN_VERSION:
            try:
                payload = canonical_json(json.loads(str(row["canonical_json"])))
            except (TypeError, ValueError, json.JSONDecodeError):
                issues.append("probe_plan_invalid")
                continue
            if not hmac.compare_digest(sha256_text(payload), str(row["plan_digest"])):
                issues.append("probe_plan_digest_mismatch")
    return tuple(sorted(set(issues)))


def canonical_json(payload: object) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def sha256_text(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


def sha256_json(payload: object) -> str:
    return sha256_text(canonical_json(payload))


def stable_id(prefix: str, payload: object) -> str:
    return f"{prefix}-{hashlib.sha256(canonical_json(payload).encode('utf-8')).hexdigest()}"


def normalize_reason(value: str) -> str:
    text = unicodedata.normalize("NFC", str(value)).strip()
    if not 1 <= len(text) <= 500:
        raise ValueError("reason must contain 1 to 500 characters")
    if any(unicodedata.category(character).startswith("C") for character in text):
        raise ValueError("reason must not contain control characters")
    return text


def require_uuid4(value: str) -> str:
    text = str(value).strip()
    try:
        parsed = uuid.UUID(text)
    except (ValueError, AttributeError) as error:
        raise ValueError("request_id must be a canonical UUIDv4") from error
    if parsed.version != 4 or str(parsed) != text:
        raise ValueError("request_id must be a canonical UUIDv4")
    return text


def validate_jenkins_origin(value: object) -> str:
    text = _required(str(value or ""), "Jenkins origin").rstrip("/")
    parsed = urlparse(text)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("Jenkins origin must be an HTTPS origin without credentials or path")
    return text


def validate_job_full_name(value: object) -> str:
    text = _required(str(value or ""), "Jenkins Job full name")
    if len(text) > 256 or re.fullmatch(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*", text) is None:
        raise ValueError("invalid fixed Jenkins Job full name")
    return text


def validate_git_remote(value: object) -> str:
    text = _required(str(value or ""), "Git remote")
    if len(text) > 64 or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", text) is None:
        raise ValueError("invalid Git remote")
    return text


def validate_relative_artifact_path(value: str) -> str:
    text = str(value).replace("\\", "/")
    if (
        not text
        or text.startswith("/")
        or re.match(r"^[A-Za-z]:", text)
        or any(part in {"", ".", ".."} for part in text.split("/"))
    ):
        raise ValueError("P0 artifact path must be a normalized relative path")
    return text


def parse_plan_row(row: Mapping[str, object]) -> ProbePlan:
    if row["plan_version"] != PROBE_PLAN_VERSION:
        raise FlakyStoreError("legacy_probe_plan", "legacy Probe plan is not executable")
    try:
        plan = ProbePlan.model_validate(json.loads(str(row["canonical_json"])))
    except (ValueError, TypeError, json.JSONDecodeError) as error:
        raise FlakyStoreError("probe_plan_invalid", "Probe plan is invalid") from error
    if not hmac.compare_digest(plan.plan_digest, str(row["plan_digest"])):
        raise FlakyStoreError("probe_plan_digest_mismatch", "Probe plan digest is invalid")
    return plan


def _read_jenkins_credentials(path: Path | None) -> tuple[str, str]:
    if path is None:
        raise ValueError("Jenkins credential file is required")
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError) as error:
        raise ValueError("Jenkins credential file cannot be read") from error
    if "\n" in raw or "\r" in raw or ":" not in raw:
        raise ValueError("Jenkins credential file must contain username:token")
    username, token = raw.split(":", 1)
    if not username.strip() or not token.strip():
        raise ValueError("Jenkins credential file must contain username:token")
    return username.strip(), token.strip()


def _jenkins_job_url(origin: str, job_full_name: str) -> str:
    job_path = "/".join(
        f"job/{quote(segment, safe='')}" for segment in validate_job_full_name(job_full_name).split("/")
    )
    return f"{validate_jenkins_origin(origin)}/{job_path}"


def _validated_queue_location(origin: str, location: object) -> int | None:
    if not isinstance(location, str) or not location.strip():
        return None
    base = validate_jenkins_origin(origin)
    candidate = urljoin(f"{base}/", location.strip())
    expected = urlparse(base)
    parsed = urlparse(candidate)
    if (parsed.scheme, parsed.netloc) != (expected.scheme, expected.netloc):
        return None
    if parsed.query or parsed.fragment:
        return None
    matched = re.fullmatch(r"/queue/item/([1-9][0-9]*)/?", parsed.path)
    return int(matched.group(1)) if matched is not None else None


def _secret_path(values: Mapping[str, str], name: str, repository_root: Path | None) -> Path:
    raw = str(values.get(name, "")).strip()
    if not raw:
        raise ValueError(f"{name} is required")
    path = Path(raw)
    if not path.is_absolute() or not path.is_file():
        raise ValueError(f"{name} must be an existing absolute file")
    resolved = path.resolve()
    if repository_root is not None and (
        resolved == repository_root or repository_root in resolved.parents
    ):
        raise ValueError(f"{name} must be outside the repository")
    return resolved


def _require_sha(value: str, name: str) -> str:
    text = str(value).strip()
    if re.fullmatch(r"[0-9a-f]{40}", text) is None:
        raise ValueError(f"{name} must be 40 lowercase hexadecimal characters")
    return text


def _require_hex_digest(value: str, name: str) -> str:
    text = str(value).strip()
    if re.fullmatch(r"[0-9a-f]{64}", text) is None:
        raise ValueError(f"{name} must be 64 lowercase hexadecimal characters")
    return text


def _require_digest(value: str, name: str) -> str:
    text = str(value).strip()
    if re.fullmatch(r"sha256:[0-9a-f]{64}", text) is None:
        raise ValueError(f"{name} must be sha256:<64 lowercase hex>")
    return text


def _required(value: str, name: str) -> str:
    text = str(value).strip()
    if not text:
        raise ValueError(f"{name} is required")
    return text


def _aware(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return _aware(parsed, "stored timestamp")


__all__ = (
    "ACTIVE_TRIGGER_STATES",
    "DispatchResult",
    "DispatchResultKind",
    "FailureDisposition",
    "FixedJenkinsClient",
    "GitTargetResolver",
    "JenkinsGateway",
    "JenkinsObservation",
    "JenkinsObservationKind",
    "ProbeControlService",
    "ProbeCreateRequest",
    "ProbeEvidenceEnvelope",
    "ProbePlan",
    "ProbeRoundStatus",
    "ProbeRuntimeConfig",
    "TriggerStatus",
    "canonical_json",
    "build_probe_envelope",
    "load_probe_runtime_config",
    "load_probe_evidence_key",
    "load_probe_controller_runtime_config",
    "normalize_reason",
    "parse_plan_row",
    "sha256_json",
    "sha256_text",
    "sign_probe_envelope",
    "select_probe_nodeid",
    "restricted_target_environment",
    "validate_p0_bundle",
    "verify_probe_envelope",
    "CsrfProtector",
    "probe_invariant_issues",
    "stable_id",
    "validate_jenkins_origin",
    "validate_job_full_name",
    "validate_git_remote",
    "validate_relative_artifact_path",
)
