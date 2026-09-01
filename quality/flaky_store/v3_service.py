from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Iterator, Sequence
import uuid

from quality.flaky_identity import (
    build_epoch_scope_key,
    build_flaky_key,
    normalize_flaky_environment,
    normalize_stored_execution_profile,
)
from quality.flaky_models import FlakyRuleConfig
from quality.flaky_v3 import (
    AdmissionStatus,
    AttemptEvidence,
    AttemptStatus,
    ComparabilityFacts,
    DEFAULT_GOVERNANCE_POLICY,
    DetectionObservation,
    DetectionState,
    GovernancePolicy,
    NormalCaseAdmissionFacts,
    NormalRunAdmissionFacts,
    ProbeAdmissionFacts,
    ProbeClassification,
    ProbeEffectStatus,
    ProbeOutcome,
    build_governance_event_id,
    classify_probe_evidence,
    comparability_fingerprint,
    evaluate_normal_case_admission,
    evaluate_normal_run_admission,
    recalculate_attempt,
    replay_detection_cohort,
)
from quality.models import RunKind, RunRecord

from .contracts import DEFAULT_BUSY_TIMEOUT_MS, FlakyStoreError
from .migration import MIGRATIONS_DIRECTORY, validate_store_schema
from .repository import FlakyRepository, utc_text
from .writer_lock import database_writer_lock


V3_IMPORTER_VERSION = "flaky-v3-import.v1"


@dataclass(frozen=True)
class NormalCaseEvidence:
    case_id: str
    param_hash: str
    environment: str
    execution_profile: str
    state_epoch: int
    comparability: ComparabilityFacts
    admission_facts: NormalCaseAdmissionFacts
    observed_at: datetime
    failure_fingerprint: str | None = None


@dataclass(frozen=True)
class NormalImportRequest:
    run: RunRecord
    manifest: dict[str, object]
    source_digest: str
    admission_facts: NormalRunAdmissionFacts
    cases: tuple[NormalCaseEvidence, ...]


@dataclass(frozen=True)
class ProbeImportRequest:
    run: RunRecord
    manifest: dict[str, object]
    source_digest: str
    outcome: ProbeOutcome
    trusted_started_at: datetime
    p0_trusted: bool
    rerun_supported: bool = True
    trusted_failure: bool = False
    diagnostic_codes: tuple[str, ...] = ()


@dataclass(frozen=True)
class RecoveryStartRequest:
    flaky_key: str
    target_commit_sha: str
    actor: str
    reason: str
    request_id: str
    expected_row_version: int
    policy: GovernancePolicy = DEFAULT_GOVERNANCE_POLICY


@dataclass(frozen=True)
class RecoveryCloseRequest:
    attempt_id: str
    actor: str
    reason: str
    expected_row_version: int
    verified_branch_head: str


@dataclass(frozen=True)
class RecoveryCancelRequest:
    attempt_id: str
    actor: str
    reason: str
    expected_row_version: int


class FlakyV3Service:
    def __init__(
        self,
        database_path: str | Path,
        *,
        busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
        migrations_directory: str | Path = MIGRATIONS_DIRECTORY,
    ) -> None:
        self.database_path = Path(database_path)
        if not self.database_path.is_absolute():
            raise FlakyStoreError(
                "invalid_database_path", "Flaky database path must be absolute"
            )
        self.busy_timeout_ms = busy_timeout_ms
        self.migrations_directory = Path(migrations_directory)
        self.repository = FlakyRepository(
            self.database_path, busy_timeout_ms=busy_timeout_ms
        )

    def import_normal(
        self,
        request: NormalImportRequest,
        *,
        now: datetime,
        detection_config: FlakyRuleConfig = FlakyRuleConfig(),
    ) -> dict[str, object]:
        _require_time(now, "now")
        _validate_run_manifest(request.run, request.manifest)
        source_digest = _require_digest(request.source_digest, "source_digest")
        with self._write() as connection:
            duplicate = self._existing_run(connection, request.run.run_id, source_digest)
            if duplicate:
                return {
                    "schema_version": "quality.flaky-v3-result.v1",
                    "status": "NOOP",
                    "run_id": request.run.run_id,
                    "observation_count": 0,
                }
            run_admission = evaluate_normal_run_admission(
                _verified_normal_admission_facts(request),
                policy_revision=request.run.policy_revision or "",
            )
            self._insert_import_run(
                connection,
                request.run,
                request.manifest,
                source_digest,
                now,
                eligible_count=0,
                excluded_count=len(request.cases),
            )
            self._insert_admission(
                connection,
                run_id=request.run.run_id,
                scope="RUN",
                case_key="__run__",
                flaky_key=None,
                result=run_admission,
                now=now,
            )
            inserted = 0
            affected: set[tuple[str, int, str]] = set()
            for case in sorted(
                request.cases,
                key=lambda item: (
                    item.case_id,
                    item.param_hash,
                    item.environment,
                    item.execution_profile,
                ),
            ):
                _require_time(case.observed_at, "observed_at")
                flaky_key = build_flaky_key(
                    case.case_id,
                    case.param_hash,
                    case.environment,
                    case.execution_profile,
                    case.state_epoch,
                )
                case_admission = evaluate_normal_case_admission(
                    case.admission_facts,
                    policy_revision=request.run.policy_revision or "",
                )
                effective_status = (
                    AdmissionStatus.ELIGIBLE
                    if run_admission.status is AdmissionStatus.ELIGIBLE
                    and case_admission.status is AdmissionStatus.ELIGIBLE
                    else AdmissionStatus.INELIGIBLE
                )
                self._insert_admission(
                    connection,
                    run_id=request.run.run_id,
                    scope="CASE",
                    case_key=f"{case.case_id}\0{case.param_hash}",
                    flaky_key=flaky_key,
                    result=case_admission,
                    now=now,
                )
                if effective_status is AdmissionStatus.INELIGIBLE:
                    continue
                generation = self._ensure_identity(connection, case, flaky_key, now)
                fingerprint = comparability_fingerprint(case.comparability)
                observation_id = _stable_id(
                    "normal-observation-v1",
                    {"flaky_key": flaky_key, "run_id": request.run.run_id},
                )
                outcome = case.admission_facts.outcome
                if outcome == "fail" and not case.failure_fingerprint:
                    raise FlakyStoreError(
                        "case_failure_evidence_invalid",
                        "eligible fail evidence requires a failure fingerprint",
                    )
                connection.execute(
                    """
                    INSERT INTO flaky_normal_observation (
                        observation_id, run_id, flaky_key, detection_generation,
                        comparability_fingerprint, outcome, failure_fingerprint,
                        observed_at, policy_revision, admission_rule_version,
                        detection_rule_version, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        observation_id,
                        request.run.run_id,
                        flaky_key,
                        generation,
                        fingerprint,
                        outcome,
                        case.failure_fingerprint,
                        utc_text(case.observed_at),
                        request.run.policy_revision,
                        run_admission.rule_version,
                        detection_config.rule_version,
                        utc_text(now),
                    ),
                )
                inserted += 1
                affected.add((flaky_key, generation, fingerprint))
            connection.execute(
                """
                UPDATE flaky_import_run
                SET eligible_count = ?, excluded_count = ?
                WHERE run_id = ?
                """,
                (inserted, len(request.cases) - inserted, request.run.run_id),
            )
            for cohort in sorted(affected):
                self._reproject(connection, *cohort, now=now, config=detection_config)
            return {
                "schema_version": "quality.flaky-v3-result.v1",
                "status": "IMPORTED",
                "run_id": request.run.run_id,
                "run_admission": run_admission.model_dump(mode="json"),
                "observation_count": inserted,
                "excluded_count": len(request.cases) - inserted,
            }

    def quarantine(
        self,
        *,
        flaky_key: str,
        owner: str,
        actor: str,
        reason: str,
        request_id: str,
        expires_at: datetime,
        now: datetime,
    ) -> dict[str, object]:
        _require_time(now, "now")
        _require_time(expires_at, "expires_at")
        if expires_at <= now:
            raise FlakyStoreError("invalid_expiry", "expires_at must be in the future")
        with self._write() as connection:
            self._require_identity(connection, flaky_key)
            existing = connection.execute(
                """SELECT governance_id FROM flaky_governance
                   WHERE flaky_key = ? AND status IN ('ACTIVE', 'RECOVERING')""",
                (flaky_key,),
            ).fetchone()
            if existing is not None:
                raise FlakyStoreError(
                    "active_governance_exists", "an open governance already exists"
                )
            governance_id = _stable_id(
                "governance-v3", {"flaky_key": flaky_key, "request_id": request_id}
            )
            connection.execute(
                """
                INSERT INTO flaky_governance (
                    governance_id, flaky_key, status, owner, reason, created_by,
                    created_at, expires_at, row_version, legacy_governance
                ) VALUES (?, ?, 'ACTIVE', ?, ?, ?, ?, ?, 1, 0)
                """,
                (
                    governance_id,
                    flaky_key,
                    _required(owner, "owner"),
                    _required(reason, "reason"),
                    _required(actor, "actor"),
                    utc_text(now),
                    utc_text(expires_at),
                ),
            )
            self._insert_event(
                connection,
                governance_id=governance_id,
                attempt_id=None,
                event_type="governance_opened",
                causal_id=_required(request_id, "request_id"),
                from_status=None,
                to_status="ACTIVE",
                actor=actor,
                reason=reason,
                now=now,
            )
            return self._status_row(connection, flaky_key)

    def recovery_start(
        self, request: RecoveryStartRequest, *, now: datetime
    ) -> dict[str, object]:
        _require_time(now, "now")
        _require_sha(request.target_commit_sha, "target_commit_sha")
        request_id = _require_uuid(request.request_id, "request_id")
        if request.expected_row_version < 1:
            raise FlakyStoreError("row_version_conflict", "row version must be positive")
        actor = _required(request.actor, "actor")
        reason = _required(request.reason, "reason")
        with self._write() as connection:
            duplicate = connection.execute(
                """SELECT trigger.attempt_id, attempt.target_commit_sha,
                          attempt.policy_revision, attempt.started_by,
                          attempt.start_reason, governance.flaky_key
                   FROM flaky_probe_trigger AS trigger
                   JOIN flaky_verification_attempt AS attempt
                     ON attempt.attempt_id = trigger.attempt_id
                   JOIN flaky_governance AS governance
                     ON governance.governance_id = attempt.governance_id
                   WHERE trigger.request_id = ?""",
                (request_id,),
            ).fetchone()
            if duplicate is not None:
                expected_payload = (
                    request.flaky_key,
                    request.target_commit_sha,
                    request.policy.revision,
                    actor,
                    reason,
                )
                stored_payload = (
                    duplicate["flaky_key"],
                    duplicate["target_commit_sha"],
                    duplicate["policy_revision"],
                    duplicate["started_by"],
                    duplicate["start_reason"],
                )
                if stored_payload != expected_payload:
                    raise FlakyStoreError(
                        "idempotency_conflict",
                        "request_id was already used with a different recovery request",
                    )
                return self._attempt_status(connection, duplicate["attempt_id"])
            governance = connection.execute(
                """
                SELECT * FROM flaky_governance
                WHERE flaky_key = ? AND status IN ('ACTIVE', 'RECOVERING')
                """,
                (request.flaky_key,),
            ).fetchone()
            if governance is None:
                raise FlakyStoreError(
                    "governance_not_active", "ACTIVE governance is required"
                )
            if governance["status"] != "ACTIVE":
                raise FlakyStoreError(
                    "attempt_already_active", "governance already has a live attempt"
                )
            if governance["row_version"] != request.expected_row_version:
                raise FlakyStoreError("row_version_conflict", "governance row changed")
            attempt_no = int(
                connection.execute(
                    """SELECT COALESCE(MAX(attempt_no), 0) + 1 AS value
                       FROM flaky_verification_attempt WHERE governance_id = ?""",
                    (governance["governance_id"],),
                ).fetchone()["value"]
            )
            attempt_id = _stable_id(
                "attempt-v1",
                {
                    "governance_id": governance["governance_id"],
                    "request_id": request_id,
                },
            )
            plan_digest = f"sha256:{_hash_payload({'attempt_id': attempt_id, 'flaky_key': request.flaky_key, 'policy_revision': request.policy.revision, 'target_commit_sha': request.target_commit_sha})}"
            trigger_id = _stable_id(
                "trigger-v1", {"attempt_id": attempt_id, "request_id": request_id}
            )
            updated = connection.execute(
                """
                UPDATE flaky_governance
                SET status = 'RECOVERING', row_version = row_version + 1,
                    recovery_started_by = ?, recovery_started_at = ?,
                    recovery_reason = ?
                WHERE governance_id = ? AND status = 'ACTIVE' AND row_version = ?
                """,
                (
                    actor,
                    utc_text(now),
                    reason,
                    governance["governance_id"],
                    request.expected_row_version,
                ),
            )
            if updated.rowcount != 1:
                raise FlakyStoreError("row_version_conflict", "governance row changed")
            expires_at = now + timedelta(hours=request.policy.max_attempt_age_hours)
            connection.execute(
                """
                INSERT INTO flaky_verification_attempt (
                    attempt_id, governance_id, attempt_no, status,
                    target_commit_sha, policy_revision,
                    required_consecutive_passes, min_interval_minutes,
                    max_non_counting_runs, counted_passes, non_counting_runs,
                    started_by, start_reason, started_at, expires_at,
                    created_at, updated_at
                ) VALUES (?, ?, ?, 'ACTIVE', ?, ?, ?, ?, ?, 0, 0, ?, ?, ?, ?, ?, ?)
                """,
                (
                    attempt_id,
                    governance["governance_id"],
                    attempt_no,
                    request.target_commit_sha,
                    request.policy.revision,
                    request.policy.required_consecutive_passes,
                    request.policy.min_interval_minutes,
                    request.policy.max_non_counting_runs,
                    actor,
                    reason,
                    utc_text(now),
                    utc_text(expires_at),
                    utc_text(now),
                    utc_text(now),
                ),
            )
            connection.execute(
                """
                INSERT INTO flaky_probe_trigger (
                    trigger_id, attempt_id, request_id, plan_digest,
                    target_commit_sha, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'PENDING', ?, ?)
                """,
                (
                    trigger_id,
                    attempt_id,
                    request_id,
                    plan_digest,
                    request.target_commit_sha,
                    utc_text(now),
                    utc_text(now),
                ),
            )
            self._insert_event(
                connection,
                governance_id=governance["governance_id"],
                attempt_id=attempt_id,
                event_type="recovery_started",
                causal_id=request_id,
                from_status="ACTIVE",
                to_status="RECOVERING",
                actor=actor,
                reason=reason,
                now=now,
            )
            return self._attempt_status(connection, attempt_id)

    def import_probe(
        self, request: ProbeImportRequest, *, now: datetime
    ) -> dict[str, object]:
        _require_time(now, "now")
        _require_time(request.trusted_started_at, "trusted_started_at")
        _validate_run_manifest(request.run, request.manifest)
        _require_digest(request.source_digest, "source_digest")
        if request.run.run_kind is not RunKind.FLAKY_PROBE:
            raise FlakyStoreError("probe_plan_mismatch", "FLAKY_PROBE run is required")
        if request.trusted_failure and request.outcome is not ProbeOutcome.FAIL:
            raise FlakyStoreError(
                "probe_evidence_invalid",
                "trusted_failure is only valid for a FAIL outcome",
            )
        with self._write() as connection:
            existing = connection.execute(
                "SELECT * FROM flaky_probe_evidence WHERE run_id = ?",
                (request.run.run_id,),
            ).fetchone()
            if existing is not None:
                imported = connection.execute(
                    "SELECT source_digest FROM flaky_import_run WHERE run_id = ?",
                    (request.run.run_id,),
                ).fetchone()
                if imported is None or imported["source_digest"] != request.source_digest:
                    raise FlakyStoreError(
                        "run_source_conflict",
                        "run_id already has a different source digest",
                    )
                return self._probe_result(existing, status="NOOP")
            attempt = connection.execute(
                "SELECT * FROM flaky_verification_attempt WHERE attempt_id = ?",
                (request.run.attempt_id,),
            ).fetchone()
            if attempt is None:
                raise FlakyStoreError("attempt_not_found", "verification attempt not found")
            reported_trigger = connection.execute(
                "SELECT * FROM flaky_probe_trigger WHERE trigger_id = ?",
                (request.run.trigger_id,),
            ).fetchone()
            trigger = connection.execute(
                """SELECT * FROM flaky_probe_trigger WHERE attempt_id = ?
                   ORDER BY created_at, trigger_id LIMIT 1""",
                (attempt["attempt_id"],),
            ).fetchone()
            if trigger is None:
                raise FlakyStoreError(
                    "probe_trigger_not_found", "attempt has no local Probe trigger"
                )
            active = attempt["status"] in {"ACTIVE", "READY_TO_CLOSE"}
            plan_matches = bool(
                reported_trigger is not None
                and reported_trigger["trigger_id"] == trigger["trigger_id"]
                and trigger["attempt_id"] == attempt["attempt_id"]
                and request.run.plan_digest == trigger["plan_digest"]
                and request.run.target_commit_sha == attempt["target_commit_sha"]
                and request.run.policy_revision == attempt["policy_revision"]
            )
            p0_trusted = bool(
                request.p0_trusted
                and request.run.status.value == "finished"
                and request.run.end_time is not None
                and request.run.integrity_status.value == "complete"
            )
            classified = classify_probe_evidence(
                ProbeAdmissionFacts(
                    attempt_active=active,
                    plan_matches=plan_matches,
                    evidence_trusted=p0_trusted,
                    rerun_supported=request.rerun_supported,
                    outcome=request.outcome,
                    trusted_failure=request.trusted_failure and p0_trusted,
                    interval_satisfied=True,
                    diagnostic_codes=request.diagnostic_codes,
                )
            )
            self._insert_import_run(
                connection,
                request.run,
                request.manifest,
                request.source_digest,
                now,
                eligible_count=0,
                excluded_count=0,
            )
            evidence_id = _stable_id(
                "probe-evidence-v1", {"run_id": request.run.run_id}
            )
            connection.execute(
                """
                INSERT INTO flaky_probe_evidence (
                    evidence_id, run_id, attempt_id, trigger_id,
                    reported_trigger_id, round_no,
                    trusted_started_at, raw_outcome, p0_trusted,
                    rerun_supported, trusted_failure, plan_matches,
                    arrived_after_terminal,
                    classification, primary_reason_code, diagnostic_codes_json,
                    consumes_non_counting_quota, effect_status,
                    admission_rule_version, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    evidence_id,
                    request.run.run_id,
                    attempt["attempt_id"],
                    trigger["trigger_id"],
                    request.run.trigger_id,
                    request.run.round_no,
                    utc_text(request.trusted_started_at),
                    request.outcome.value,
                    int(p0_trusted),
                    int(request.rerun_supported),
                    int(request.trusted_failure),
                    int(plan_matches),
                    int(not active),
                    classified.classification.value,
                    classified.reason_code,
                    _canonical_json(classified.diagnostic_codes),
                    int(classified.consumes_non_counting_quota),
                    ProbeEffectStatus.AUDIT_ONLY.value,
                    classified.rule_version,
                    utc_text(now),
                ),
            )
            if active:
                self._reclassify_probe_evidence(connection, attempt)
                self._recalculate_attempt(connection, attempt["attempt_id"], now)
            row = connection.execute(
                "SELECT * FROM flaky_probe_evidence WHERE evidence_id = ?",
                (evidence_id,),
            ).fetchone()
            return self._probe_result(row, status="IMPORTED")

    def recovery_status(self, flaky_key: str) -> dict[str, object]:
        with self._read() as connection:
            return self._status_row(connection, _required(flaky_key, "flaky_key"))

    def recovery_cancel(
        self, request: RecoveryCancelRequest, *, now: datetime
    ) -> dict[str, object]:
        _require_time(now, "now")
        with self._write() as connection:
            attempt, governance = self._attempt_and_governance(
                connection, request.attempt_id
            )
            self._require_row_version(governance, request.expected_row_version)
            if attempt["status"] not in {"ACTIVE", "READY_TO_CLOSE"}:
                raise FlakyStoreError("attempt_not_active", "attempt is not live")
            connection.execute(
                """UPDATE flaky_verification_attempt
                   SET status = 'CANCELLED', ended_at = ?, end_reason = ?, updated_at = ?
                   WHERE attempt_id = ?""",
                (utc_text(now), request.reason, utc_text(now), request.attempt_id),
            )
            connection.execute(
                """UPDATE flaky_probe_trigger SET status = 'CANCELLED', updated_at = ?
                   WHERE attempt_id = ? AND status = 'PENDING'""",
                (utc_text(now), request.attempt_id),
            )
            self._activate_governance(
                connection, governance, request.expected_row_version
            )
            self._insert_event(
                connection,
                governance_id=governance["governance_id"],
                attempt_id=request.attempt_id,
                event_type="recovery_cancelled",
                causal_id=request.attempt_id,
                from_status="RECOVERING",
                to_status="ACTIVE",
                actor=request.actor,
                reason=request.reason,
                now=now,
            )
            return self._attempt_status(connection, request.attempt_id)

    def recovery_close(
        self, request: RecoveryCloseRequest, *, now: datetime
    ) -> dict[str, object]:
        _require_time(now, "now")
        _require_sha(request.verified_branch_head, "verified_branch_head")
        with self._write() as connection:
            attempt, governance = self._attempt_and_governance(
                connection, request.attempt_id
            )
            self._require_row_version(governance, request.expected_row_version)
            if attempt["status"] != "READY_TO_CLOSE":
                raise FlakyStoreError(
                    "attempt_not_ready", "attempt must be READY_TO_CLOSE"
                )
            if request.verified_branch_head != attempt["target_commit_sha"]:
                raise FlakyStoreError(
                    "verified_branch_head_mismatch", "verified branch HEAD changed"
                )
            pending = connection.execute(
                """SELECT 1 FROM flaky_probe_trigger
                   WHERE attempt_id = ? AND status != 'EVIDENCE_COMPLETE' LIMIT 1""",
                (request.attempt_id,),
            ).fetchone()
            if pending is not None:
                raise FlakyStoreError(
                    "probe_trigger_not_terminal", "Probe trigger is still active or unknown"
                )
            rounds = {
                int(row["round_no"])
                for row in connection.execute(
                    """SELECT round_no FROM flaky_probe_evidence
                       WHERE attempt_id = ? AND effect_status = 'APPLIED'
                         AND classification = 'COUNT_PASS'""",
                    (request.attempt_id,),
                ).fetchall()
            }
            required = int(attempt["required_consecutive_passes"])
            if not set(range(1, required + 1)).issubset(rounds):
                raise FlakyStoreError(
                    "probe_evidence_gap", "Probe evidence rounds are incomplete"
                )
            connection.execute(
                """UPDATE flaky_verification_attempt
                   SET status = 'CLOSED', ended_at = ?, end_reason = ?, updated_at = ?
                   WHERE attempt_id = ?""",
                (utc_text(now), request.reason, utc_text(now), request.attempt_id),
            )
            updated = connection.execute(
                """
                UPDATE flaky_governance
                SET status = 'CLOSED', row_version = row_version + 1,
                    closed_at = ?, closed_by = ?, close_reason = ?,
                    close_attempt_id = ?, resolution = 'recovered'
                WHERE governance_id = ? AND status = 'RECOVERING' AND row_version = ?
                """,
                (
                    utc_text(now),
                    _required(request.actor, "actor"),
                    _required(request.reason, "reason"),
                    request.attempt_id,
                    governance["governance_id"],
                    request.expected_row_version,
                ),
            )
            if updated.rowcount != 1:
                raise FlakyStoreError("row_version_conflict", "governance row changed")
            connection.execute(
                """UPDATE flaky_identity
                   SET current_detection_generation = current_detection_generation + 1,
                       updated_at = ? WHERE flaky_key = ?""",
                (utc_text(now), governance["flaky_key"]),
            )
            self._insert_event(
                connection,
                governance_id=governance["governance_id"],
                attempt_id=request.attempt_id,
                event_type="recovery_closed",
                causal_id=request.attempt_id,
                from_status="RECOVERING",
                to_status="CLOSED",
                actor=request.actor,
                reason=request.reason,
                now=now,
            )
            return self._attempt_status(connection, request.attempt_id)

    def override_detection(
        self,
        *,
        flaky_key: str,
        detection_generation: int,
        fingerprint: str,
        action: str,
        actor: str,
        reason: str,
        idempotency_key: str,
        now: datetime,
    ) -> dict[str, object]:
        _require_time(now, "now")
        targets = {"confirm_flaky": "CONFIRMED", "mark_not_flaky": "STABLE"}
        if action not in targets:
            raise FlakyStoreError("invalid_override_action", "unknown detection action")
        with self._write() as connection:
            existing = connection.execute(
                "SELECT override_id FROM flaky_detection_override WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if existing is not None:
                return {"status": "NOOP", "override_id": existing["override_id"]}
            projection = connection.execute(
                """SELECT * FROM flaky_detection_projection
                   WHERE flaky_key = ? AND detection_generation = ?
                     AND comparability_fingerprint = ?""",
                (flaky_key, detection_generation, fingerprint),
            ).fetchone()
            if projection is None:
                raise FlakyStoreError(
                    "projection_not_found", "full projection identity does not exist"
                )
            override_id = _stable_id(
                "detection-override-v1",
                {"idempotency_key": idempotency_key, "projection": [flaky_key, detection_generation, fingerprint]},
            )
            target = targets[action]
            connection.execute(
                """INSERT INTO flaky_detection_override (
                       override_id, idempotency_key, flaky_key, detection_generation,
                       comparability_fingerprint, action, from_state, to_state,
                       actor, reason, created_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    override_id,
                    idempotency_key,
                    flaky_key,
                    detection_generation,
                    fingerprint,
                    action,
                    projection["detection_state"],
                    target,
                    _required(actor, "actor"),
                    _required(reason, "reason"),
                    utc_text(now),
                ),
            )
            transition_id = _stable_id(
                "transition-v2",
                {"override_id": override_id, "to_state": target},
            )
            connection.execute(
                """INSERT INTO flaky_detection_transition (
                       transition_id, flaky_key, detection_generation,
                       comparability_fingerprint, from_state, to_state,
                       reason_code, transition_version, trigger_observation_id,
                       override_id, rule_version, created_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, 'transition-v2', NULL, ?, ?, ?)""",
                (
                    transition_id,
                    flaky_key,
                    detection_generation,
                    fingerprint,
                    projection["detection_state"],
                    target,
                    action,
                    override_id,
                    projection["rule_version"],
                    utc_text(now),
                ),
            )
            connection.execute(
                """UPDATE flaky_detection_projection
                   SET detection_state = ?, stable_outcome = NULL,
                       stable_failure_fingerprint = NULL,
                       last_transition_id = ?, updated_at = ?
                   WHERE flaky_key = ? AND detection_generation = ?
                     AND comparability_fingerprint = ?""",
                (
                    target,
                    transition_id,
                    utc_text(now),
                    flaky_key,
                    detection_generation,
                    fingerprint,
                ),
            )
            return {
                "status": "APPLIED",
                "override_id": override_id,
                "transition_id": transition_id,
                "detection_state": target,
            }

    def check_invariants(self) -> dict[str, object]:
        with self._read() as connection:
            issues: list[str] = []
            if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                issues.append("foreign_key_violation")
            checks = {
                "duplicate_open_governance": """
                    SELECT 1 FROM flaky_governance WHERE status IN ('ACTIVE','RECOVERING')
                    GROUP BY flaky_key HAVING COUNT(*) > 1 LIMIT 1""",
                "duplicate_live_attempt": """
                    SELECT 1 FROM flaky_verification_attempt
                    WHERE status IN ('ACTIVE','READY_TO_CLOSE')
                    GROUP BY governance_id HAVING COUNT(*) > 1 LIMIT 1""",
                "governance_attempt_state_mismatch": """
                    SELECT 1 FROM flaky_verification_attempt AS attempt
                    JOIN flaky_governance AS governance
                      ON governance.governance_id = attempt.governance_id
                    WHERE attempt.status IN ('ACTIVE','READY_TO_CLOSE')
                      AND governance.status != 'RECOVERING' LIMIT 1""",
                "recovering_governance_without_live_attempt": """
                    SELECT 1
                    FROM flaky_governance AS governance
                    LEFT JOIN flaky_verification_attempt AS attempt
                      ON attempt.governance_id = governance.governance_id
                     AND attempt.status IN ('ACTIVE','READY_TO_CLOSE')
                    WHERE governance.status = 'RECOVERING'
                    GROUP BY governance.governance_id
                    HAVING COUNT(attempt.attempt_id) != 1
                    LIMIT 1""",
                "illegal_applied_probe_evidence": """
                    SELECT 1 FROM flaky_probe_evidence
                    WHERE effect_status = 'APPLIED'
                      AND (
                          plan_matches != 1
                          OR arrived_after_terminal != 0
                          OR admission_rule_version != 'flaky-probe-evidence.v1'
                          OR NOT (
                              (classification = 'COUNT_PASS'
                               AND raw_outcome = 'PASS'
                               AND p0_trusted = 1
                               AND rerun_supported = 1
                               AND trusted_failure = 0
                               AND consumes_non_counting_quota = 0
                               AND primary_reason_code = 'probe_count_pass')
                              OR
                              (classification = 'TRUSTED_FAIL'
                               AND raw_outcome = 'FAIL'
                               AND p0_trusted = 1
                               AND rerun_supported = 1
                               AND trusted_failure = 1
                               AND consumes_non_counting_quota = 0
                               AND primary_reason_code = 'probe_trusted_fail')
                              OR
                              (classification = 'NON_COUNTING'
                               AND consumes_non_counting_quota = 1
                               AND (
                                   (p0_trusted = 0
                                    AND primary_reason_code = 'probe_evidence_untrusted')
                                   OR
                                   (p0_trusted = 1
                                    AND rerun_supported = 0
                                    AND primary_reason_code = 'probe_rerun_unsupported')
                                   OR
                                   (p0_trusted = 1
                                    AND rerun_supported = 1
                                    AND raw_outcome IN ('SKIP','XFAIL','XPASS','NO_DATA')
                                    AND primary_reason_code = 'probe_outcome_not_countable')
                                   OR
                                   (p0_trusted = 1
                                    AND rerun_supported = 1
                                    AND raw_outcome = 'FAIL'
                                    AND trusted_failure = 0
                                    AND primary_reason_code = 'probe_evidence_untrusted')
                               ))
                          )
                      ) LIMIT 1""",
                "projection_generation_ahead": """
                    SELECT 1 FROM flaky_detection_projection AS projection
                    JOIN flaky_identity AS identity USING (flaky_key)
                    WHERE projection.detection_generation > identity.current_detection_generation
                    LIMIT 1""",
            }
            for code, sql in checks.items():
                if connection.execute(sql).fetchone() is not None:
                    issues.append(code)
            return {
                "schema_version": "quality.flaky-db-check.v3",
                "database_schema_version": 3,
                "status": "OK" if not issues else "FAILED",
                "issue_codes": sorted(issues),
            }

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        if not self.database_path.is_file():
            raise FlakyStoreError(
                "schema_migration_required", "run flaky-db-migrate before writing"
            )
        with database_writer_lock(self.database_path, timeout_ms=self.busy_timeout_ms):
            with self.repository.connection(require_existing=True) as connection:
                validate_store_schema(
                    connection, self.repository, self.migrations_directory
                )
                with self.repository.transaction(connection):
                    yield connection

    @contextmanager
    def _read(self) -> Iterator[sqlite3.Connection]:
        with self.repository.connection(require_existing=True, read_only=True) as connection:
            validate_store_schema(connection, self.repository, self.migrations_directory)
            yield connection

    def _existing_run(
        self, connection: sqlite3.Connection, run_id: str, source_digest: str
    ) -> bool:
        row = connection.execute(
            "SELECT source_digest FROM flaky_import_run WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None:
            return False
        if row["source_digest"] != source_digest:
            raise FlakyStoreError(
                "run_source_conflict", "run_id already has a different source digest"
            )
        return True

    def _insert_import_run(
        self,
        connection: sqlite3.Connection,
        run: RunRecord,
        manifest: dict[str, object],
        source_digest: str,
        now: datetime,
        *,
        eligible_count: int,
        excluded_count: int,
    ) -> None:
        owner = connection.execute(
            "SELECT run_id FROM flaky_import_run WHERE source_digest = ?", (source_digest,)
        ).fetchone()
        if owner is not None:
            raise FlakyStoreError(
                "source_digest_conflict", "source digest belongs to another run"
            )
        end_time = run.end_time or run.start_time
        values = {
            "run_id": run.run_id,
            "source_digest": source_digest,
            "source_kind": "v3",
            "artifact_ref": f"v3:{run.run_id}",
            "job_name": run.job_name,
            "build_number": run.build_number,
            "branch": run.branch,
            "commit_sha": run.commit_sha,
            "environment": run.environment,
            "run_status": run.status.value,
            "p0_integrity_status": run.integrity_status.value,
            "run_start_time": utc_text(run.start_time),
            "run_end_time": utc_text(end_time),
            "p0_schema_version": run.schema_version,
            "p0_merge_version": str(manifest.get("merge_version", "p0-merge.v1")),
            "fingerprint_version": str(manifest.get("fingerprint_version", "unknown")),
            "digest": source_digest,
            "imported_at": utc_text(now),
            "eligible_count": eligible_count,
            "excluded_count": excluded_count,
            "run_kind": run.run_kind.value if run.run_kind else None,
            "policy_revision": run.policy_revision,
            "controller_commit_sha": run.controller_commit_sha,
            "attempt_id": run.attempt_id,
            "trigger_id": run.trigger_id,
            "plan_digest": run.plan_digest,
            "round_no": run.round_no,
            "target_commit_sha": run.target_commit_sha,
            "jenkins_job_name": run.jenkins_job_name,
            "jenkins_build_number": run.jenkins_build_number,
            "fact_schema_version": run.fact_schema_version,
            "plugin_version": run.plugin_version,
        }
        connection.execute(
            """
            INSERT INTO flaky_import_run (
                run_id, source_digest, source_kind, artifact_ref, job_name,
                build_number, branch, commit_sha, environment, run_status,
                p0_integrity_status, run_start_time, run_end_time,
                p0_schema_version, p0_merge_version, fingerprint_version,
                run_record_sha256, manifest_sha256, case_results_sha256,
                failures_sha256, integrity_issues_sha256, importer_version,
                identity_rule_version, environment_rule_version,
                execution_profile_rule_version, observation_rule_version,
                eligible_count, excluded_count, imported_at,
                run_kind, policy_revision, controller_commit_sha, attempt_id,
                trigger_id, plan_digest, round_no, target_commit_sha,
                jenkins_job_name, jenkins_build_number, fact_schema_version,
                plugin_version, legacy_record
            ) VALUES (
                :run_id, :source_digest, :source_kind, :artifact_ref, :job_name,
                :build_number, :branch, :commit_sha, :environment, :run_status,
                :p0_integrity_status, :run_start_time, :run_end_time,
                :p0_schema_version, :p0_merge_version, :fingerprint_version,
                :digest, :digest, :digest, :digest, :digest, :importer_version,
                'flaky-identity.v1', 'flaky-environment.v1',
                'flaky-execution-profile.v1', 'flaky-observation.v1',
                :eligible_count, :excluded_count, :imported_at,
                :run_kind, :policy_revision, :controller_commit_sha, :attempt_id,
                :trigger_id, :plan_digest, :round_no, :target_commit_sha,
                :jenkins_job_name, :jenkins_build_number, :fact_schema_version,
                :plugin_version, 0
            )
            """,
            {**values, "importer_version": V3_IMPORTER_VERSION},
        )

    def _insert_admission(
        self,
        connection: sqlite3.Connection,
        *,
        run_id: str,
        scope: str,
        case_key: str,
        flaky_key: str | None,
        result,
        now: datetime,
    ) -> None:
        admission_id = _stable_id(
            "admission-v1", {"case_key": case_key, "run_id": run_id, "scope": scope}
        )
        connection.execute(
            """INSERT INTO flaky_evidence_admission (
                   admission_id, run_id, scope, case_key, flaky_key, status,
                   primary_reason_code, reason_codes_json, policy_revision,
                   rule_version, created_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                admission_id,
                run_id,
                scope,
                case_key,
                flaky_key,
                result.status.value,
                result.primary_reason_code,
                _canonical_json(result.reason_codes),
                result.policy_revision,
                result.rule_version,
                utc_text(now),
            ),
        )

    def _ensure_identity(
        self,
        connection: sqlite3.Connection,
        case: NormalCaseEvidence,
        flaky_key: str,
        now: datetime,
    ) -> int:
        epoch_scope_key = build_epoch_scope_key(
            case.case_id, case.environment, case.execution_profile
        )
        scope = connection.execute(
            "SELECT * FROM flaky_case_epoch WHERE epoch_scope_key = ?",
            (epoch_scope_key,),
        ).fetchone()
        if scope is None:
            connection.execute(
                """INSERT INTO flaky_case_epoch (
                       epoch_scope_key, case_id, environment, execution_profile,
                       current_epoch, identity_rule_version,
                       environment_rule_version, execution_profile_rule_version,
                       created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, 'flaky-identity.v1',
                             'flaky-environment.v1',
                             'flaky-execution-profile.v1', ?, ?)""",
                (
                    epoch_scope_key,
                    case.case_id,
                    case.environment,
                    case.execution_profile,
                    case.state_epoch,
                    utc_text(now),
                    utc_text(now),
                ),
            )
        elif int(scope["current_epoch"]) != case.state_epoch:
            raise FlakyStoreError(
                "identity_epoch_conflict", "Case state_epoch is not current"
            )
        identity = connection.execute(
            "SELECT * FROM flaky_identity WHERE flaky_key = ?", (flaky_key,)
        ).fetchone()
        expected = (
            epoch_scope_key,
            case.case_id,
            case.param_hash,
            case.environment,
            case.execution_profile,
            case.state_epoch,
        )
        if identity is None:
            connection.execute(
                """INSERT INTO flaky_identity (
                       flaky_key, epoch_scope_key, case_id, param_hash,
                       environment, execution_profile, state_epoch,
                       current_detection_generation, legacy_detected_state,
                       created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, NULL, ?, ?)""",
                (flaky_key, *expected, utc_text(now), utc_text(now)),
            )
            return 1
        actual = tuple(
            identity[name]
            for name in (
                "epoch_scope_key",
                "case_id",
                "param_hash",
                "environment",
                "execution_profile",
                "state_epoch",
            )
        )
        if actual != expected:
            raise FlakyStoreError(
                "flaky_identity_conflict", "flaky_key maps to another identity"
            )
        return int(identity["current_detection_generation"])

    def _reproject(
        self,
        connection: sqlite3.Connection,
        flaky_key: str,
        generation: int,
        fingerprint: str,
        *,
        now: datetime,
        config: FlakyRuleConfig,
    ) -> None:
        rows = connection.execute(
            """SELECT * FROM flaky_normal_observation
               WHERE flaky_key = ? AND detection_generation = ?
                 AND comparability_fingerprint = ?""",
            (flaky_key, generation, fingerprint),
        ).fetchall()
        observations = tuple(
            DetectionObservation(
                observation_id=row["observation_id"],
                run_id=row["run_id"],
                observed_at=_parse_time(row["observed_at"]),
                outcome=row["outcome"],
                failure_fingerprint=row["failure_fingerprint"],
            )
            for row in rows
        )
        projection = replay_detection_cohort(
            observations,
            flaky_key=flaky_key,
            detection_generation=generation,
            fingerprint=fingerprint,
            config=config,
        )
        existing = connection.execute(
            """SELECT created_at FROM flaky_detection_projection
               WHERE flaky_key = ? AND detection_generation = ?
                 AND comparability_fingerprint = ?""",
            (flaky_key, generation, fingerprint),
        ).fetchone()
        created_at = existing["created_at"] if existing else utc_text(now)
        connection.execute(
            """INSERT INTO flaky_detection_projection (
                   flaky_key, detection_generation, comparability_fingerprint,
                   detection_state, sample_size, pass_count, fail_count,
                   outcome_switch_count, signature_switch_count,
                   distinct_failure_fingerprint_count,
                   trailing_same_signature_count, stable_outcome,
                   stable_failure_fingerprint, latest_observation_id,
                   last_transition_id, rule_version, created_at, updated_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?)
               ON CONFLICT(flaky_key, detection_generation, comparability_fingerprint)
               DO UPDATE SET
                   detection_state=excluded.detection_state,
                   sample_size=excluded.sample_size,
                   pass_count=excluded.pass_count,
                   fail_count=excluded.fail_count,
                   outcome_switch_count=excluded.outcome_switch_count,
                   signature_switch_count=excluded.signature_switch_count,
                   distinct_failure_fingerprint_count=excluded.distinct_failure_fingerprint_count,
                   trailing_same_signature_count=excluded.trailing_same_signature_count,
                   stable_outcome=excluded.stable_outcome,
                   stable_failure_fingerprint=excluded.stable_failure_fingerprint,
                   latest_observation_id=excluded.latest_observation_id,
                   rule_version=excluded.rule_version,
                   updated_at=excluded.updated_at""",
            (
                flaky_key,
                generation,
                fingerprint,
                projection.state.value,
                projection.sample_size,
                projection.pass_count,
                projection.fail_count,
                projection.outcome_switch_count,
                projection.signature_switch_count,
                projection.distinct_failure_fingerprint_count,
                projection.trailing_same_signature_count,
                projection.stable_outcome,
                projection.stable_failure_fingerprint,
                projection.latest_observation_id,
                config.rule_version,
                created_at,
                utc_text(now),
            ),
        )
        for transition in projection.transitions:
            connection.execute(
                """INSERT OR IGNORE INTO flaky_detection_transition (
                       transition_id, flaky_key, detection_generation,
                       comparability_fingerprint, from_state, to_state,
                       reason_code, transition_version, trigger_observation_id,
                       override_id, rule_version, created_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, 'transition-v1', ?, NULL, ?, ?)""",
                (
                    transition.transition_id,
                    flaky_key,
                    generation,
                    fingerprint,
                    transition.from_state.value if transition.from_state else None,
                    transition.to_state.value,
                    transition.reason_code,
                    transition.trigger_observation_id,
                    config.rule_version,
                    utc_text(now),
                ),
            )
        last_transition = projection.transitions[-1].transition_id
        connection.execute(
            """UPDATE flaky_detection_projection SET last_transition_id = ?
               WHERE flaky_key = ? AND detection_generation = ?
                 AND comparability_fingerprint = ?""",
            (last_transition, flaky_key, generation, fingerprint),
        )

    def _recalculate_attempt(
        self, connection: sqlite3.Connection, attempt_id: str, now: datetime
    ) -> None:
        attempt = connection.execute(
            "SELECT * FROM flaky_verification_attempt WHERE attempt_id = ?",
            (attempt_id,),
        ).fetchone()
        rows = connection.execute(
            "SELECT * FROM flaky_probe_evidence WHERE attempt_id = ?",
            (attempt_id,),
        ).fetchall()
        evidence = tuple(
            AttemptEvidence(
                run_id=row["run_id"],
                round_no=int(row["round_no"]),
                trusted_started_at=_parse_time(row["trusted_started_at"]),
                classification=ProbeClassification(row["classification"]),
                consumes_non_counting_quota=bool(row["consumes_non_counting_quota"]),
                effect_status=ProbeEffectStatus(row["effect_status"]),
            )
            for row in rows
        )
        result = recalculate_attempt(
            evidence,
            now=now,
            expires_at=_parse_time(attempt["expires_at"]),
            required_consecutive_passes=int(attempt["required_consecutive_passes"]),
            max_non_counting_runs=int(attempt["max_non_counting_runs"]),
        )
        previous = attempt["status"]
        terminal = result.status not in {
            AttemptStatus.ACTIVE,
            AttemptStatus.READY_TO_CLOSE,
        }
        connection.execute(
            """UPDATE flaky_verification_attempt
               SET status = ?, counted_passes = ?, non_counting_runs = ?,
                   ended_at = ?, end_reason = ?, updated_at = ?
               WHERE attempt_id = ?""",
            (
                result.status.value,
                result.counted_passes,
                result.non_counting_runs,
                utc_text(now) if terminal else None,
                result.end_reason,
                utc_text(now),
                attempt_id,
            ),
        )
        governance = connection.execute(
            "SELECT * FROM flaky_governance WHERE governance_id = ?",
            (attempt["governance_id"],),
        ).fetchone()
        causal_row = connection.execute(
            """SELECT run_id FROM flaky_probe_evidence WHERE attempt_id = ?
               ORDER BY created_at DESC, run_id DESC LIMIT 1""",
            (attempt_id,),
        ).fetchone()
        causal_id = causal_row["run_id"]
        if terminal:
            connection.execute(
                """UPDATE flaky_probe_trigger
                   SET status = 'EVIDENCE_COMPLETE', updated_at = ?
                   WHERE attempt_id = ? AND status = 'PENDING'""",
                (utc_text(now), attempt_id),
            )
            self._activate_governance(
                connection, governance, int(governance["row_version"])
            )
            self._insert_event(
                connection,
                governance_id=governance["governance_id"],
                attempt_id=attempt_id,
                event_type=f"attempt_{result.status.value.casefold()}",
                causal_id=causal_id,
                from_status="RECOVERING",
                to_status="ACTIVE",
                actor=None,
                reason=result.end_reason,
                now=now,
            )
        elif result.status is AttemptStatus.READY_TO_CLOSE:
            connection.execute(
                """UPDATE flaky_probe_trigger
                   SET status = 'EVIDENCE_COMPLETE', updated_at = ?
                   WHERE attempt_id = ? AND status = 'PENDING'""",
                (utc_text(now), attempt_id),
            )
            if previous != result.status.value:
                self._insert_event(
                    connection,
                    governance_id=governance["governance_id"],
                    attempt_id=attempt_id,
                    event_type="attempt_ready_to_close",
                    causal_id=causal_id,
                    from_status="RECOVERING",
                    to_status="RECOVERING",
                    actor=None,
                    reason="required_consecutive_passes_met",
                    now=now,
                )

    def _reclassify_probe_evidence(
        self,
        connection: sqlite3.Connection,
        attempt: sqlite3.Row,
    ) -> None:
        rows = connection.execute(
            """SELECT * FROM flaky_probe_evidence WHERE attempt_id = ?
               ORDER BY round_no, trusted_started_at, run_id""",
            (attempt["attempt_id"],),
        ).fetchall()
        connection.execute(
            """UPDATE flaky_probe_evidence SET effect_status = 'AUDIT_ONLY'
               WHERE attempt_id = ?""",
            (attempt["attempt_id"],),
        )
        used_rounds: set[int] = set()
        last_counted_at: datetime | None = None
        logical_terminal = False
        non_counting = 0
        expires_at = _parse_time(attempt["expires_at"])
        for row in rows:
            started_at = _parse_time(row["trusted_started_at"])
            arrived_after_terminal = bool(row["arrived_after_terminal"])
            if started_at >= expires_at:
                logical_terminal = True
            interval_ok = (
                last_counted_at is None
                or started_at
                >= last_counted_at
                + timedelta(minutes=int(attempt["min_interval_minutes"]))
            )
            result = classify_probe_evidence(
                ProbeAdmissionFacts(
                    attempt_active=not arrived_after_terminal and not logical_terminal,
                    plan_matches=bool(row["plan_matches"]),
                    evidence_trusted=bool(row["p0_trusted"]),
                    rerun_supported=bool(row["rerun_supported"]),
                    outcome=ProbeOutcome(row["raw_outcome"]),
                    trusted_failure=bool(row["trusted_failure"]),
                    interval_satisfied=interval_ok,
                    diagnostic_codes=tuple(json.loads(row["diagnostic_codes_json"])),
                )
            )
            effect = result.effect_status
            diagnostics = set(result.diagnostic_codes)
            round_no = int(row["round_no"])
            if effect is ProbeEffectStatus.APPLIED and round_no in used_rounds:
                effect = ProbeEffectStatus.AUDIT_ONLY
                diagnostics.add("probe_duplicate_round")
            if effect is ProbeEffectStatus.APPLIED:
                used_rounds.add(round_no)
                if result.classification is ProbeClassification.COUNT_PASS:
                    last_counted_at = started_at
                elif result.classification is ProbeClassification.TRUSTED_FAIL:
                    logical_terminal = True
                elif result.consumes_non_counting_quota:
                    non_counting += 1
                    if non_counting >= int(attempt["max_non_counting_runs"]):
                        logical_terminal = True
            connection.execute(
                """UPDATE flaky_probe_evidence
                   SET classification = ?, primary_reason_code = ?,
                       diagnostic_codes_json = ?,
                       consumes_non_counting_quota = ?, effect_status = ?
                   WHERE evidence_id = ?""",
                (
                    result.classification.value,
                    result.reason_code,
                    _canonical_json(sorted(diagnostics)),
                    int(result.consumes_non_counting_quota),
                    effect.value,
                    row["evidence_id"],
                ),
            )

    def _activate_governance(
        self,
        connection: sqlite3.Connection,
        governance: sqlite3.Row,
        expected_row_version: int,
    ) -> None:
        updated = connection.execute(
            """UPDATE flaky_governance
               SET status = 'ACTIVE', row_version = row_version + 1,
                   recovery_started_by = NULL, recovery_started_at = NULL,
                   recovery_reason = NULL
               WHERE governance_id = ? AND status = 'RECOVERING' AND row_version = ?""",
            (governance["governance_id"], expected_row_version),
        )
        if updated.rowcount != 1:
            raise FlakyStoreError("row_version_conflict", "governance row changed")

    def _attempt_and_governance(
        self, connection: sqlite3.Connection, attempt_id: str
    ) -> tuple[sqlite3.Row, sqlite3.Row]:
        attempt = connection.execute(
            "SELECT * FROM flaky_verification_attempt WHERE attempt_id = ?",
            (_required(attempt_id, "attempt_id"),),
        ).fetchone()
        if attempt is None:
            raise FlakyStoreError("attempt_not_found", "verification attempt not found")
        governance = connection.execute(
            "SELECT * FROM flaky_governance WHERE governance_id = ?",
            (attempt["governance_id"],),
        ).fetchone()
        return attempt, governance

    def _require_row_version(self, governance: sqlite3.Row, expected: int) -> None:
        if int(governance["row_version"]) != expected:
            raise FlakyStoreError("row_version_conflict", "governance row changed")

    def _require_identity(self, connection: sqlite3.Connection, flaky_key: str) -> None:
        if connection.execute(
            "SELECT 1 FROM flaky_identity WHERE flaky_key = ?", (flaky_key,)
        ).fetchone() is None:
            raise FlakyStoreError("identity_not_found", "Flaky identity does not exist")

    def _insert_event(
        self,
        connection: sqlite3.Connection,
        *,
        governance_id: str,
        attempt_id: str | None,
        event_type: str,
        causal_id: str,
        from_status: str | None,
        to_status: str,
        actor: str | None,
        reason: str | None,
        now: datetime,
    ) -> None:
        event_id = build_governance_event_id(
            governance_id=governance_id,
            event_type=event_type,
            causal_id=causal_id,
        )
        connection.execute(
            """INSERT OR IGNORE INTO flaky_governance_event (
                   event_id, governance_id, attempt_id, event_type, causal_id,
                   from_status, to_status, actor, reason, created_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event_id,
                governance_id,
                attempt_id,
                event_type,
                causal_id,
                from_status,
                to_status,
                actor,
                reason,
                utc_text(now),
            ),
        )

    def _status_row(
        self, connection: sqlite3.Connection, flaky_key: str
    ) -> dict[str, object]:
        identity = connection.execute(
            "SELECT * FROM flaky_identity WHERE flaky_key = ?", (flaky_key,)
        ).fetchone()
        if identity is None:
            raise FlakyStoreError("identity_not_found", "Flaky identity does not exist")
        governance = connection.execute(
            """SELECT * FROM flaky_governance WHERE flaky_key = ?
               ORDER BY created_at DESC, governance_id DESC LIMIT 1""",
            (flaky_key,),
        ).fetchone()
        attempt = None
        if governance is not None:
            attempt = connection.execute(
                """SELECT * FROM flaky_verification_attempt WHERE governance_id = ?
                   ORDER BY attempt_no DESC LIMIT 1""",
                (governance["governance_id"],),
            ).fetchone()
        projections = connection.execute(
            """SELECT comparability_fingerprint, detection_state, sample_size
               FROM flaky_detection_projection
               WHERE flaky_key = ? AND detection_generation = ?
               ORDER BY comparability_fingerprint""",
            (flaky_key, identity["current_detection_generation"]),
        ).fetchall()
        return {
            "schema_version": "quality.flaky-recovery-status.v1",
            "flaky_key": flaky_key,
            "detection_generation": int(identity["current_detection_generation"]),
            "detection_state": "UNOBSERVED" if not projections else None,
            "projections": [dict(row) for row in projections],
            "governance": _public_row(governance),
            "attempt": _public_row(attempt),
        }

    def _attempt_status(
        self, connection: sqlite3.Connection, attempt_id: str
    ) -> dict[str, object]:
        attempt, governance = self._attempt_and_governance(connection, attempt_id)
        trigger = connection.execute(
            "SELECT * FROM flaky_probe_trigger WHERE attempt_id = ?",
            (attempt_id,),
        ).fetchone()
        return {
            "schema_version": "quality.flaky-recovery-command.v1",
            "status": "OK",
            "governance": _public_row(governance),
            "attempt": _public_row(attempt),
            "trigger": _public_row(trigger),
        }

    @staticmethod
    def _probe_result(row: sqlite3.Row, *, status: str) -> dict[str, object]:
        return {
            "schema_version": "quality.flaky-probe-import.v1",
            "status": status,
            "run_id": row["run_id"],
            "classification": row["classification"],
            "reason_code": row["primary_reason_code"],
            "effect_status": row["effect_status"],
        }


def _verified_normal_admission_facts(
    request: NormalImportRequest,
) -> NormalRunAdmissionFacts:
    facts = request.admission_facts
    run = request.run
    try:
        run_environment = normalize_flaky_environment(run.environment)
        environment_valid = all(
            normalize_flaky_environment(case.environment) == run_environment
            and case.comparability.environment == run_environment
            for case in request.cases
        )
    except ValueError:
        environment_valid = False
    try:
        profiles_valid = all(
            normalize_stored_execution_profile(case.execution_profile)
            == case.comparability.execution_profile
            for case in request.cases
        )
    except ValueError:
        profiles_valid = False
    source_identified = all(
        value is not None
        for value in (run.job_name, run.build_number, run.branch, run.commit_sha)
    )
    versions_valid = all(
        (
            run.policy_revision == DEFAULT_GOVERNANCE_POLICY.revision,
            run.controller_commit_sha is not None,
            run.fact_schema_version == "quality.fact.v1",
            run.plugin_version == "quality-plugin.v1",
        )
    )
    return facts.model_copy(
        update={
            "run_kind": run.run_kind or RunKind.LEGACY_UNKNOWN,
            "source_job_allowed": facts.source_job_allowed and source_identified,
            "branch_allowed": (
                facts.branch_allowed
                and run.branch in DEFAULT_GOVERNANCE_POLICY.allowed_branches
            ),
            "environment_allowed": facts.environment_allowed and environment_valid,
            "execution_profile_allowed": (
                facts.execution_profile_allowed and profiles_valid
            ),
            "run_finished": (
                facts.run_finished
                and run.status.value == "finished"
                and run.end_time is not None
            ),
            "versions_compatible": facts.versions_compatible and versions_valid,
            "integrity_eligible": (
                facts.integrity_eligible and run.integrity_status.value == "complete"
            ),
            "comparability_valid": (
                facts.comparability_valid and environment_valid and profiles_valid
            ),
        }
    )


_RUN_MANIFEST_FIELDS = (
    "run_kind",
    "policy_revision",
    "controller_commit_sha",
    "attempt_id",
    "trigger_id",
    "plan_digest",
    "round_no",
    "target_commit_sha",
    "jenkins_job_name",
    "jenkins_build_number",
    "fact_schema_version",
    "plugin_version",
)


def _validate_run_manifest(run: RunRecord, manifest: dict[str, object]) -> None:
    if run.schema_version != "quality.v2":
        raise FlakyStoreError(
            "legacy_run_not_admitted", "quality.v1 is read-only LEGACY_UNKNOWN evidence"
        )
    required_values = {
        "manifest_version": "quality.merge.v2",
        "schema_version": "quality.v2",
        "run_id": run.run_id,
        "status": "complete",
        "merge_version": "p0-merge.v1",
        "fingerprint_version": "failure-fingerprint.v1",
        "integrity_status": run.integrity_status.value,
    }
    for field, value in required_values.items():
        if manifest.get(field) != value:
            raise FlakyStoreError(
                "manifest_schema_invalid",
                f"manifest field {field!r} is missing or invalid",
            )
    expected = run.model_dump(mode="json")
    for field in _RUN_MANIFEST_FIELDS:
        if manifest.get(field) != expected.get(field):
            raise FlakyStoreError(
                "run_manifest_mismatch", f"manifest field {field!r} differs from run"
            )
    output_hashes = manifest.get("output_hashes")
    if not isinstance(output_hashes, dict):
        raise FlakyStoreError(
            "manifest_hashes_missing", "manifest output_hashes is missing"
        )
    for field in ("case-results", "failures", "integrity-issues"):
        _require_digest(output_hashes.get(field), f"output_hashes.{field}")


def _stable_id(prefix: str, payload: object) -> str:
    return f"{prefix}-{_hash_payload(payload)}"


def _hash_payload(payload: object) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _canonical_json(payload: object) -> str:
    return json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _required(value: str, name: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise FlakyStoreError("invalid_request", f"{name} must not be empty")
    return normalized


def _require_sha(value: str, name: str) -> str:
    normalized = _required(value, name)
    if len(normalized) != 40 or any(char not in "0123456789abcdef" for char in normalized):
        raise FlakyStoreError(
            "invalid_request", f"{name} must be a 40-character lowercase hex SHA"
        )
    return normalized


def _require_digest(value: object, name: str) -> str:
    normalized = _required(str(value) if value is not None else "", name)
    if len(normalized) != 64 or any(
        char not in "0123456789abcdef" for char in normalized
    ):
        raise FlakyStoreError(
            "invalid_request", f"{name} must be a 64-character lowercase hex digest"
        )
    return normalized


def _require_uuid(value: str, name: str) -> str:
    normalized = _required(value, name)
    try:
        parsed = uuid.UUID(normalized)
    except ValueError as error:
        raise FlakyStoreError("invalid_request", f"{name} must be a UUID") from error
    if str(parsed) != normalized.casefold():
        raise FlakyStoreError("invalid_request", f"{name} must be a canonical UUID")
    return str(parsed)


def _require_time(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise FlakyStoreError("invalid_request", f"{name} must include a timezone")
    return value


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _public_row(row: sqlite3.Row | None) -> dict[str, object] | None:
    if row is None:
        return None
    blocked = {"reason", "recovery_reason", "start_reason", "close_reason"}
    return {key: row[key] for key in row.keys() if key not in blocked}
