from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from typing import Iterator, Sequence
import uuid

from quality.flaky_models import (
    CaseObservation,
    CaseObservationCandidate,
    EpochResetRequest,
    EpochResetResult,
    FLAKY_ENVIRONMENT_RULE_VERSION,
    FLAKY_EXECUTION_PROFILE_RULE_VERSION,
    FLAKY_IDENTITY_RULE_VERSION,
    FLAKY_PROJECTION_VERSION,
    FLAKY_STATE_RULE_VERSION,
    FlakyDatabaseCheck,
    FlakyEvaluationResult,
    FlakyEvaluationStatus,
    FlakyGovernanceRecord,
    FlakyHistoryEntry,
    FlakyImportIssue,
    FlakyManualActionRequest,
    FlakyQuarantineRequest,
    FlakyRuleConfig,
    FlakyRunMetadata,
    FlakyState,
    FlakyStateRecord,
    FlakyStateSummary,
    FlakyTransitionRecord,
    GovernanceResolution,
    GovernanceStatus,
    ObservationOutcome,
    ProjectionStatus,
    TransitionTrigger,
)
from quality.models import IssueSeverity
from quality.flaky import (
    DEFAULT_FLAKY_RULE_CONFIG,
    build_result_signature,
    build_transition_id,
    derive_evidence_window,
    evaluate_recovery,
    replay_observations,
)


DEFAULT_BUSY_TIMEOUT_MS = 5000
MIGRATIONS_DIRECTORY = Path(__file__).resolve().parent / "migrations" / "flaky"


class FlakyStoreError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class StoreInitialization:
    schema_version: int
    quick_check: str
    migration_applied: bool
    backup_created: bool


@dataclass(frozen=True)
class StoreImportOutcome:
    imported: bool
    inserted_count: int
    initialization: StoreInitialization


@dataclass(frozen=True)
class _ProjectionPlan:
    state: FlakyStateRecord
    transitions: tuple[FlakyTransitionRecord, ...]
    changed: bool
    close_governance_id: str | None = None
    governance_resolution: GovernanceResolution | None = None


class FlakyStore:
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
                "invalid_database_path",
                "Flaky history database path must be absolute",
            )
        if busy_timeout_ms < 0:
            raise ValueError("busy_timeout_ms must be greater than or equal to 0")
        self.busy_timeout_ms = busy_timeout_ms
        self.migrations_directory = Path(migrations_directory)

    def import_run(
        self,
        metadata: FlakyRunMetadata,
        candidates: Sequence[CaseObservationCandidate],
    ) -> StoreImportOutcome:
        if len(candidates) != metadata.eligible_count:
            raise FlakyStoreError(
                "eligible_count_mismatch",
                "candidate count does not match metadata eligible_count",
            )
        with self._connection(require_existing=False) as connection:
            initialization = self._initialize(connection)
            try:
                connection.execute("BEGIN IMMEDIATE")
                existing = connection.execute(
                    "SELECT source_digest FROM flaky_import_run WHERE run_id = ?",
                    (metadata.run_id,),
                ).fetchone()
                if existing is not None:
                    if existing["source_digest"] == metadata.source_digest:
                        connection.execute("ROLLBACK")
                        return StoreImportOutcome(
                            imported=False,
                            inserted_count=0,
                            initialization=initialization,
                        )
                    raise FlakyStoreError(
                        "run_source_conflict",
                        f"run_id {metadata.run_id!r} already has a different source digest",
                    )

                digest_owner = connection.execute(
                    "SELECT run_id FROM flaky_import_run WHERE source_digest = ?",
                    (metadata.source_digest,),
                ).fetchone()
                if digest_owner is not None:
                    raise FlakyStoreError(
                        "source_digest_conflict",
                        "source digest is already associated with another run_id",
                    )

                imported_at = datetime.now(UTC)
                observations = [
                    self._materialize_observation(connection, candidate, imported_at)
                    for candidate in sorted(
                        candidates,
                        key=lambda item: (
                            item.case_id,
                            item.param_hash,
                            item.execution_profile,
                            item.invocation_id,
                        ),
                    )
                ]
                self._insert_import_run(connection, metadata, imported_at)
                for observation in observations:
                    self._insert_observation(connection, observation)

                inserted = connection.execute(
                    "SELECT COUNT(*) AS count FROM case_observation WHERE run_id = ?",
                    (metadata.run_id,),
                ).fetchone()["count"]
                if inserted != len(observations):
                    raise FlakyStoreError(
                        "observation_count_mismatch",
                        "committed observation count would not match eligible count",
                    )
                connection.execute("COMMIT")
                return StoreImportOutcome(
                    imported=True,
                    inserted_count=inserted,
                    initialization=initialization,
                )
            except Exception:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise

    def evaluate_run(
        self,
        run_id: str,
        *,
        config: FlakyRuleConfig = DEFAULT_FLAKY_RULE_CONFIG,
    ) -> FlakyEvaluationResult:
        run_id = _required_text(run_id, "run_id")
        evaluated_at = datetime.now(UTC)
        with self._connection(require_existing=True) as connection:
            initialization = self._initialize(connection)
            run = connection.execute(
                "SELECT run_id FROM flaky_import_run WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if run is None:
                raise FlakyStoreError("run_not_found", f"run_id {run_id!r} is not imported")
            flaky_keys = tuple(
                row["flaky_key"]
                for row in connection.execute(
                    """
                    SELECT DISTINCT flaky_key
                    FROM case_observation
                    WHERE run_id = ?
                    ORDER BY flaky_key
                    """,
                    (run_id,),
                ).fetchall()
            )
            if not flaky_keys:
                return FlakyEvaluationResult(
                    run_id=run_id,
                    status=FlakyEvaluationStatus.NO_DATA,
                    evaluated_at=evaluated_at,
                    database_schema_version=initialization.schema_version,
                    quick_check=initialization.quick_check,
                    issues=(
                        FlakyImportIssue(
                            severity=IssueSeverity.INFO,
                            code="run_has_no_observations",
                            summary="The imported run has no eligible Case observations.",
                        ),
                    ),
                )
            try:
                connection.execute("BEGIN IMMEDIATE")
                plans = tuple(
                    self._build_projection_plan(
                        connection,
                        flaky_key,
                        now=evaluated_at,
                        config=config,
                        trigger_run_id=run_id,
                    )
                    for flaky_key in flaky_keys
                )
                for plan in plans:
                    self._write_projection_plan(connection, plan)
                connection.execute("COMMIT")
            except Exception:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise

            summaries = {
                key: self._state_summary(connection, key)
                for key in flaky_keys
            }
            transitioned = tuple(
                transition
                for plan in plans
                for transition in plan.transitions
            )
            newly_suspected = tuple(
                summaries[item.flaky_key]
                for item in transitioned
                if item.to_state is FlakyState.SUSPECTED
            )
            newly_confirmed = tuple(
                summaries[item.flaky_key]
                for item in transitioned
                if item.to_state is FlakyState.CONFIRMED
            )
            recovered = tuple(
                summaries[item.flaky_key]
                for item in transitioned
                if item.reason_code == "recovery_stable_streak_met"
            )
            ongoing_confirmed = tuple(
                summary
                for plan, summary in zip(plans, summaries.values())
                if not plan.transitions
                and summary.current_state is FlakyState.CONFIRMED
            )
            quarantined = tuple(
                summary
                for summary in summaries.values()
                if summary.current_state is FlakyState.QUARANTINED
            )
            recovering = tuple(
                summary
                for summary in summaries.values()
                if summary.current_state is FlakyState.RECOVERING
            )
            overdue = self._overdue_summaries(connection, evaluated_at)
            changed_count = sum(plan.changed for plan in plans)
            stale_count = sum(
                plan.state.projection_status is ProjectionStatus.STALE for plan in plans
            )
            return FlakyEvaluationResult(
                run_id=run_id,
                status=(
                    FlakyEvaluationStatus.EVALUATED
                    if changed_count
                    else FlakyEvaluationStatus.NOOP
                ),
                evaluated_at=evaluated_at,
                affected_count=len(flaky_keys),
                evaluated_count=len(plans),
                transitioned_count=len({item.flaky_key for item in transitioned}),
                stale_count=stale_count,
                newly_suspected=newly_suspected,
                newly_confirmed=newly_confirmed,
                ongoing_confirmed=ongoing_confirmed,
                quarantined=quarantined,
                recovering=recovering,
                recovered=recovered,
                overdue=overdue,
                transitions=transitioned,
                database_schema_version=initialization.schema_version,
                quick_check=initialization.quick_check,
            )

    def states(
        self,
        *,
        case_id: str,
        param_hash: str | None = None,
        environment: str | None = None,
        execution_profile: str | None = None,
        state_epoch: int | None = None,
    ) -> tuple[FlakyStateRecord, ...]:
        filters = ["case_id = ?"]
        parameters: list[object] = [_required_text(case_id, "case_id")]
        for column, value in (
            ("param_hash", param_hash),
            ("environment", environment),
            ("execution_profile", execution_profile),
            ("state_epoch", state_epoch),
        ):
            if value is not None:
                filters.append(f"{column} = ?")
                parameters.append(value)
        with self._connection(require_existing=True) as connection:
            self._initialize(connection)
            rows = connection.execute(
                f"SELECT * FROM flaky_state WHERE {' AND '.join(filters)} "
                "ORDER BY param_hash, environment, execution_profile, state_epoch",
                tuple(parameters),
            ).fetchall()
        return tuple(_state_record(row) for row in rows)

    def confirm_flaky(self, request: FlakyManualActionRequest) -> FlakyStateRecord:
        return self._manual_override(
            request,
            action="confirm_flaky",
            allowed_states=(FlakyState.SUSPECTED,),
            target_state=FlakyState.CONFIRMED,
        )

    def mark_not_flaky(self, request: FlakyManualActionRequest) -> FlakyStateRecord:
        return self._manual_override(
            request,
            action="mark_not_flaky",
            allowed_states=(FlakyState.SUSPECTED, FlakyState.CONFIRMED),
            target_state=FlakyState.STABLE,
        )

    def quarantine(self, request: FlakyQuarantineRequest) -> FlakyGovernanceRecord:
        now = datetime.now(UTC)
        if request.expires_at.astimezone(UTC) <= now:
            raise FlakyStoreError("invalid_expiry", "expires_at must be in the future")
        with self._connection(require_existing=True) as connection:
            self._initialize(connection)
            try:
                connection.execute("BEGIN IMMEDIATE")
                state = self._require_state(connection, request.flaky_key)
                if state.current_state is not FlakyState.CONFIRMED:
                    raise FlakyStoreError(
                        "invalid_state_transition",
                        "quarantine requires current_state=CONFIRMED",
                    )
                self._require_no_open_governance(connection, request.flaky_key)
                governance_id = f"governance-v1-{uuid.uuid4().hex}"
                connection.execute(
                    """
                    INSERT INTO flaky_governance (
                        governance_id, flaky_key, status, owner, reason,
                        created_by, created_at, expires_at
                    ) VALUES (?, ?, 'ACTIVE', ?, ?, ?, ?, ?)
                    """,
                    (
                        governance_id,
                        request.flaky_key,
                        request.owner,
                        request.reason,
                        request.actor,
                        _utc_text(now),
                        _utc_text(request.expires_at),
                    ),
                )
                transition = self._manual_transition(
                    connection,
                    state,
                    to_state=FlakyState.QUARANTINED,
                    reason_code="manual_quarantine",
                    actor=request.actor,
                    now=now,
                )
                connection.execute(
                    """
                    UPDATE flaky_state
                    SET current_state = 'QUARANTINED', last_transition_id = ?, updated_at = ?
                    WHERE flaky_key = ?
                    """,
                    (transition.transition_id, _utc_text(now), request.flaky_key),
                )
                connection.execute("COMMIT")
            except Exception:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise
            row = connection.execute(
                "SELECT * FROM flaky_governance WHERE governance_id = ?",
                (governance_id,),
            ).fetchone()
        return _governance_record(row)

    def start_recovery(
        self,
        request: FlakyManualActionRequest,
    ) -> FlakyGovernanceRecord:
        now = datetime.now(UTC)
        with self._connection(require_existing=True) as connection:
            self._initialize(connection)
            try:
                connection.execute("BEGIN IMMEDIATE")
                state = self._require_state(connection, request.flaky_key)
                if state.current_state is not FlakyState.QUARANTINED:
                    raise FlakyStoreError(
                        "invalid_state_transition",
                        "start recovery requires current_state=QUARANTINED",
                    )
                governance = self._require_open_governance(
                    connection,
                    request.flaky_key,
                    GovernanceStatus.ACTIVE,
                )
                connection.execute(
                    """
                    UPDATE flaky_governance
                    SET status = 'RECOVERING', recovery_started_by = ?,
                        recovery_started_at = ?, recovery_reason = ?,
                        recovery_anchor_observation_id = ?
                    WHERE governance_id = ?
                    """,
                    (
                        request.actor,
                        _utc_text(now),
                        request.reason,
                        state.latest_observation_id,
                        governance.governance_id,
                    ),
                )
                transition = self._manual_transition(
                    connection,
                    state,
                    to_state=FlakyState.RECOVERING,
                    reason_code="manual_recovery_started",
                    actor=request.actor,
                    now=now,
                )
                connection.execute(
                    """
                    UPDATE flaky_state
                    SET current_state = 'RECOVERING', last_transition_id = ?, updated_at = ?
                    WHERE flaky_key = ?
                    """,
                    (transition.transition_id, _utc_text(now), request.flaky_key),
                )
                connection.execute("COMMIT")
            except Exception:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise
            row = connection.execute(
                "SELECT * FROM flaky_governance WHERE governance_id = ?",
                (governance.governance_id,),
            ).fetchone()
        return _governance_record(row)

    def cancel_quarantine(
        self,
        request: FlakyManualActionRequest,
    ) -> FlakyStateRecord:
        now = datetime.now(UTC)
        with self._connection(require_existing=True) as connection:
            self._initialize(connection)
            try:
                connection.execute("BEGIN IMMEDIATE")
                state = self._require_state(connection, request.flaky_key)
                if state.current_state is not FlakyState.QUARANTINED:
                    raise FlakyStoreError(
                        "invalid_state_transition",
                        "cancel quarantine requires current_state=QUARANTINED",
                    )
                governance = self._require_open_governance(
                    connection,
                    request.flaky_key,
                    GovernanceStatus.ACTIVE,
                )
                transition = self._manual_transition(
                    connection,
                    state,
                    to_state=FlakyState.CONFIRMED,
                    reason_code="manual_quarantine_cancelled",
                    actor=request.actor,
                    now=now,
                )
                connection.execute(
                    """
                    UPDATE flaky_governance
                    SET status = 'CLOSED', closed_at = ?, resolution = 'cancelled'
                    WHERE governance_id = ?
                    """,
                    (_utc_text(now), governance.governance_id),
                )
                self._insert_override(
                    connection,
                    state,
                    action="cancel_quarantine",
                    to_state=FlakyState.CONFIRMED,
                    actor=request.actor,
                    reason=request.reason,
                    now=now,
                )
                connection.execute(
                    """
                    UPDATE flaky_state
                    SET current_state = 'CONFIRMED', detected_state = 'CONFIRMED',
                        stable_outcome = NULL, stable_failure_id = NULL,
                        last_transition_id = ?, updated_at = ?
                    WHERE flaky_key = ?
                    """,
                    (transition.transition_id, _utc_text(now), request.flaky_key),
                )
                connection.execute("COMMIT")
            except Exception:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise
            row = connection.execute(
                "SELECT * FROM flaky_state WHERE flaky_key = ?",
                (request.flaky_key,),
            ).fetchone()
        return _state_record(row)

    def governance(
        self,
        *,
        status: GovernanceStatus | None = None,
        overdue: bool = False,
        query_time: datetime | None = None,
    ) -> tuple[FlakyGovernanceRecord, ...]:
        filters: list[str] = []
        parameters: list[object] = []
        if status is not None:
            filters.append("status = ?")
            parameters.append(status.value)
        if overdue:
            filters.append("status IN ('ACTIVE', 'RECOVERING')")
            filters.append("expires_at < ?")
            parameters.append(_utc_text(query_time or datetime.now(UTC)))
        where = f"WHERE {' AND '.join(filters)}" if filters else ""
        with self._connection(require_existing=True) as connection:
            self._initialize(connection)
            rows = connection.execute(
                f"SELECT * FROM flaky_governance {where} ORDER BY created_at, governance_id",
                tuple(parameters),
            ).fetchall()
        return tuple(_governance_record(row) for row in rows)

    def rebuild_states(
        self,
        *,
        apply: bool,
        config: FlakyRuleConfig = DEFAULT_FLAKY_RULE_CONFIG,
    ) -> dict[str, object]:
        now = datetime.now(UTC)
        with self._connection(require_existing=True) as connection:
            initialization = self._initialize(connection)
            keys = tuple(
                row["flaky_key"]
                for row in connection.execute(
                    "SELECT DISTINCT flaky_key FROM case_observation ORDER BY flaky_key"
                ).fetchall()
            )
            try:
                if apply:
                    connection.execute("BEGIN IMMEDIATE")
                plans = tuple(
                    self._build_projection_plan(
                        connection,
                        key,
                        now=now,
                        config=config,
                        trigger_run_id=None,
                    )
                    for key in keys
                )
                if apply:
                    for plan in plans:
                        self._write_projection_plan(connection, plan)
                    connection.execute("COMMIT")
            except Exception:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise
        return {
            "mode": "apply" if apply else "dry-run",
            "schema_version": initialization.schema_version,
            "quick_check": initialization.quick_check,
            "key_count": len(keys),
            "changed_count": sum(plan.changed for plan in plans),
            "transition_count": sum(len(plan.transitions) for plan in plans),
            "rule_version": config.rule_version,
            "projection_version": config.projection_version,
        }

    def _build_projection_plan(
        self,
        connection: sqlite3.Connection,
        flaky_key: str,
        *,
        now: datetime,
        config: FlakyRuleConfig,
        trigger_run_id: str | None,
    ) -> _ProjectionPlan:
        observations = self._observations_for_key(connection, flaky_key)
        if not observations:
            raise FlakyStoreError(
                "projection_has_no_observations",
                f"flaky_key {flaky_key!r} has no observations",
            )
        existing_row = connection.execute(
            "SELECT * FROM flaky_state WHERE flaky_key = ?",
            (flaky_key,),
        ).fetchone()
        existing = _state_record(existing_row) if existing_row is not None else None
        if existing is not None and (
            existing.rule_version != config.rule_version
            or existing.projection_version != config.projection_version
        ):
            raise FlakyStoreError(
                "incompatible_projection_version",
                "stored Flaky state requires an explicit versioned rebuild",
            )

        automatic = replay_observations(observations, config)
        latest = observations[-1]
        evidence = automatic.evidence
        current_state = automatic.current_state
        detected_state = automatic.detected_state
        stable_outcome = automatic.stable_outcome
        stable_failure_id = automatic.stable_failure_id
        evaluation_anchor = (
            existing.evaluation_anchor_observation_id if existing is not None else None
        )
        reason_code: str | None = None
        close_governance_id: str | None = None
        governance_resolution: GovernanceResolution | None = None

        open_governance = self._open_governance(connection, flaky_key)
        if open_governance is not None and open_governance.status is GovernanceStatus.RECOVERING:
            after_anchor = _observations_after_anchor(
                observations,
                open_governance.recovery_anchor_observation_id,
            )
            recovery = evaluate_recovery(after_anchor, config)
            if recovery.evidence is not None:
                evidence = recovery.evidence
            elif existing is not None:
                evidence = _evidence_from_state(existing, observations, config)
            if recovery.target_state is None:
                current_state = FlakyState.RECOVERING
                detected_state = (
                    existing.detected_state if existing is not None else FlakyState.CONFIRMED
                )
                stable_outcome = existing.stable_outcome if existing is not None else None
                stable_failure_id = (
                    existing.stable_failure_id if existing is not None else None
                )
            else:
                current_state = recovery.target_state
                detected_state = recovery.target_state
                stable_outcome = recovery.stable_outcome
                stable_failure_id = recovery.stable_failure_id
                reason_code = recovery.reason_code
                close_governance_id = open_governance.governance_id
                evaluation_anchor = (
                    latest.observation_id
                    if recovery.target_state is FlakyState.STABLE
                    else existing.evaluation_anchor_observation_id
                    if existing is not None
                    else None
                )
                governance_resolution = (
                    GovernanceResolution.RECOVERED
                    if recovery.target_state is FlakyState.STABLE
                    else GovernanceResolution.REGRESSED
                )
        elif open_governance is not None:
            current_state = FlakyState.QUARANTINED
            detected_state = (
                FlakyState.CONFIRMED
                if existing is not None
                and existing.detected_state is FlakyState.CONFIRMED
                else automatic.detected_state
            )
            stable_outcome = existing.stable_outcome if existing is not None else None
            stable_failure_id = (
                existing.stable_failure_id if existing is not None else None
            )
        elif existing is not None and existing.current_state is FlakyState.CONFIRMED:
            current_state = FlakyState.CONFIRMED
            detected_state = FlakyState.CONFIRMED
            stable_outcome = None
            stable_failure_id = None
        elif existing is not None and existing.evaluation_anchor_observation_id is not None:
            (
                current_state,
                detected_state,
                evidence,
                stable_outcome,
                stable_failure_id,
                reason_code,
            ) = _evaluate_from_manual_anchor(existing, observations, config)

        transitions: list[FlakyTransitionRecord] = []
        if existing is None:
            for decision in automatic.transitions:
                transitions.append(
                    _transition_record(
                        flaky_key=flaky_key,
                        from_state=decision.from_state,
                        to_state=decision.to_state,
                        trigger_type=TransitionTrigger.BOOTSTRAP,
                        reason_code=decision.reason_code,
                        trigger_observation_id=decision.trigger_observation_id,
                        evidence=decision.evidence,
                        actor=None,
                        created_at=now,
                        config=config,
                    )
                )
        elif existing.current_state is not current_state:
            old_latest_index = next(
                (
                    index
                    for index, item in enumerate(observations)
                    if item.observation_id == existing.latest_observation_id
                ),
                None,
            )
            has_new_observation = (
                existing.total_observation_count < len(observations)
            )
            late = has_new_observation and (
                old_latest_index is None
                or old_latest_index == len(observations) - 1
                or old_latest_index < len(observations) - 2
            )
            trigger_type = (
                TransitionTrigger.REPROJECTION if late else TransitionTrigger.OBSERVATION
            )
            if late:
                reason_code = "late_observation_reprojection"
            if reason_code is None:
                reason_code = _projection_reason(automatic, current_state)
            trigger_observation = next(
                (
                    observation
                    for observation in observations
                    if trigger_run_id is not None
                    and observation.run_id == trigger_run_id
                ),
                latest,
            )
            transitions.append(
                _transition_record(
                    flaky_key=flaky_key,
                    from_state=existing.current_state,
                    to_state=current_state,
                    trigger_type=trigger_type,
                    reason_code=reason_code,
                    trigger_observation_id=trigger_observation.observation_id,
                    evidence=evidence,
                    actor=None,
                    created_at=now,
                    config=config,
                )
            )

        transitions = [
            transition
            for transition in transitions
            if connection.execute(
                "SELECT 1 FROM flaky_transition WHERE transition_id = ?",
                (transition.transition_id,),
            ).fetchone()
            is None
        ]
        last_transition_id = (
            transitions[-1].transition_id
            if transitions
            else existing.last_transition_id if existing is not None else None
        )
        first = observations[0]
        state = FlakyStateRecord(
            flaky_key=flaky_key,
            epoch_scope_key=first.epoch_scope_key,
            case_id=first.case_id,
            param_hash=first.param_hash,
            environment=first.environment,
            execution_profile=first.execution_profile,
            state_epoch=first.state_epoch,
            current_state=current_state,
            detected_state=detected_state,
            stable_outcome=stable_outcome,
            stable_failure_id=stable_failure_id,
            total_observation_count=len(observations),
            sample_size=evidence.sample_size,
            evidence_window_size=evidence.evidence_window_size,
            pass_count=evidence.pass_count,
            fail_count=evidence.fail_count,
            outcome_switch_count=evidence.outcome_switch_count,
            signature_switch_count=evidence.signature_switch_count,
            distinct_failure_fingerprint_count=(
                evidence.distinct_failure_fingerprint_count
            ),
            trailing_same_signature_count=evidence.trailing_same_signature_count,
            evaluation_anchor_observation_id=evaluation_anchor,
            latest_observation_id=latest.observation_id,
            latest_run_id=latest.run_id,
            latest_observed_at=latest.observed_at,
            last_transition_id=last_transition_id,
            rule_version=config.rule_version,
            projection_version=config.projection_version,
            projection_status=ProjectionStatus.CURRENT,
            created_at=existing.created_at if existing is not None else now,
            updated_at=now,
        )
        changed = existing is None or _state_payload(state) != _state_payload(existing)
        return _ProjectionPlan(
            state=state,
            transitions=tuple(transitions),
            changed=changed,
            close_governance_id=close_governance_id,
            governance_resolution=governance_resolution,
        )

    def _write_projection_plan(
        self,
        connection: sqlite3.Connection,
        plan: _ProjectionPlan,
    ) -> None:
        if plan.changed:
            self._upsert_state(connection, plan.state)
        for transition in plan.transitions:
            self._insert_transition(connection, transition)
        if plan.close_governance_id is not None:
            connection.execute(
                """
                UPDATE flaky_governance
                SET status = 'CLOSED', closed_at = ?, resolution = ?
                WHERE governance_id = ? AND status = 'RECOVERING'
                """,
                (
                    _utc_text(plan.state.updated_at),
                    plan.governance_resolution.value,
                    plan.close_governance_id,
                ),
            )

    def _upsert_state(
        self,
        connection: sqlite3.Connection,
        state: FlakyStateRecord,
    ) -> None:
        values = (
            state.flaky_key,
            state.epoch_scope_key,
            state.case_id,
            state.param_hash,
            state.environment,
            state.execution_profile,
            state.state_epoch,
            state.current_state.value,
            state.detected_state.value,
            state.stable_outcome.value if state.stable_outcome else None,
            state.stable_failure_id,
            state.total_observation_count,
            state.sample_size,
            state.evidence_window_size,
            state.pass_count,
            state.fail_count,
            state.outcome_switch_count,
            state.signature_switch_count,
            state.distinct_failure_fingerprint_count,
            state.trailing_same_signature_count,
            state.evaluation_anchor_observation_id,
            state.latest_observation_id,
            state.latest_run_id,
            _utc_text(state.latest_observed_at),
            state.last_transition_id,
            state.rule_version,
            state.projection_version,
            state.projection_status.value,
            _utc_text(state.created_at),
            _utc_text(state.updated_at),
        )
        connection.execute(
            """
            INSERT INTO flaky_state (
                flaky_key, epoch_scope_key, case_id, param_hash, environment,
                execution_profile, state_epoch, current_state, detected_state,
                stable_outcome, stable_failure_id, total_observation_count,
                sample_size, evidence_window_size, pass_count, fail_count,
                outcome_switch_count, signature_switch_count,
                distinct_failure_fingerprint_count, trailing_same_signature_count,
                evaluation_anchor_observation_id, latest_observation_id,
                latest_run_id, latest_observed_at, last_transition_id,
                rule_version, projection_version, projection_status,
                created_at, updated_at
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            ON CONFLICT(flaky_key) DO UPDATE SET
                current_state=excluded.current_state,
                detected_state=excluded.detected_state,
                stable_outcome=excluded.stable_outcome,
                stable_failure_id=excluded.stable_failure_id,
                total_observation_count=excluded.total_observation_count,
                sample_size=excluded.sample_size,
                evidence_window_size=excluded.evidence_window_size,
                pass_count=excluded.pass_count,
                fail_count=excluded.fail_count,
                outcome_switch_count=excluded.outcome_switch_count,
                signature_switch_count=excluded.signature_switch_count,
                distinct_failure_fingerprint_count=excluded.distinct_failure_fingerprint_count,
                trailing_same_signature_count=excluded.trailing_same_signature_count,
                evaluation_anchor_observation_id=excluded.evaluation_anchor_observation_id,
                latest_observation_id=excluded.latest_observation_id,
                latest_run_id=excluded.latest_run_id,
                latest_observed_at=excluded.latest_observed_at,
                last_transition_id=excluded.last_transition_id,
                rule_version=excluded.rule_version,
                projection_version=excluded.projection_version,
                projection_status=excluded.projection_status,
                updated_at=excluded.updated_at
            """,
            values,
        )

    def _insert_transition(
        self,
        connection: sqlite3.Connection,
        transition: FlakyTransitionRecord,
    ) -> None:
        connection.execute(
            """
            INSERT OR IGNORE INTO flaky_transition (
                transition_id, flaky_key, from_state, to_state, trigger_type,
                reason_code, rule_version, projection_version, sample_size,
                trigger_observation_id, evidence_observation_ids_json,
                evidence_run_ids_json, actor, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                transition.transition_id,
                transition.flaky_key,
                transition.from_state.value if transition.from_state else None,
                transition.to_state.value,
                transition.trigger_type.value,
                transition.reason_code,
                transition.rule_version,
                transition.projection_version,
                transition.sample_size,
                transition.trigger_observation_id,
                _canonical_json(transition.evidence_observation_ids),
                _canonical_json(transition.evidence_run_ids),
                transition.actor,
                _utc_text(transition.created_at),
            ),
        )

    def _manual_override(
        self,
        request: FlakyManualActionRequest,
        *,
        action: str,
        allowed_states: tuple[FlakyState, ...],
        target_state: FlakyState,
    ) -> FlakyStateRecord:
        now = datetime.now(UTC)
        with self._connection(require_existing=True) as connection:
            self._initialize(connection)
            try:
                connection.execute("BEGIN IMMEDIATE")
                state = self._require_state(connection, request.flaky_key)
                if state.current_state not in allowed_states:
                    allowed = ", ".join(item.value for item in allowed_states)
                    raise FlakyStoreError(
                        "invalid_state_transition",
                        f"{action} requires current_state in [{allowed}]",
                    )
                self._require_no_open_governance(connection, request.flaky_key)
                transition = self._manual_transition(
                    connection,
                    state,
                    to_state=target_state,
                    reason_code=f"manual_{action}",
                    actor=request.actor,
                    now=now,
                )
                self._insert_override(
                    connection,
                    state,
                    action=action,
                    to_state=target_state,
                    actor=request.actor,
                    reason=request.reason,
                    now=now,
                )
                stable_outcome: str | None = None
                stable_failure_id: str | None = None
                anchor: str | None = state.evaluation_anchor_observation_id
                if target_state is FlakyState.STABLE:
                    latest = self._observations_for_key(connection, state.flaky_key)[-1]
                    stable_outcome = latest.observation_outcome.value
                    stable_failure_id = latest.failure_id
                    anchor = latest.observation_id
                connection.execute(
                    """
                    UPDATE flaky_state
                    SET current_state = ?, detected_state = ?, stable_outcome = ?,
                        stable_failure_id = ?, evaluation_anchor_observation_id = ?,
                        last_transition_id = ?, updated_at = ?
                    WHERE flaky_key = ?
                    """,
                    (
                        target_state.value,
                        target_state.value,
                        stable_outcome,
                        stable_failure_id,
                        anchor,
                        transition.transition_id,
                        _utc_text(now),
                        state.flaky_key,
                    ),
                )
                connection.execute("COMMIT")
            except Exception:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise
            row = connection.execute(
                "SELECT * FROM flaky_state WHERE flaky_key = ?",
                (request.flaky_key,),
            ).fetchone()
        return _state_record(row)

    def _manual_transition(
        self,
        connection: sqlite3.Connection,
        state: FlakyStateRecord,
        *,
        to_state: FlakyState,
        reason_code: str,
        actor: str,
        now: datetime,
    ) -> FlakyTransitionRecord:
        observations = self._observations_for_key(connection, state.flaky_key)
        evidence = derive_evidence_window(observations, DEFAULT_FLAKY_RULE_CONFIG)
        transition = _transition_record(
            flaky_key=state.flaky_key,
            from_state=state.current_state,
            to_state=to_state,
            trigger_type=TransitionTrigger.MANUAL,
            reason_code=reason_code,
            trigger_observation_id=state.latest_observation_id,
            evidence=evidence,
            actor=actor,
            created_at=now,
            config=DEFAULT_FLAKY_RULE_CONFIG,
        )
        self._insert_transition(connection, transition)
        return transition

    def _insert_override(
        self,
        connection: sqlite3.Connection,
        state: FlakyStateRecord,
        *,
        action: str,
        to_state: FlakyState,
        actor: str,
        reason: str,
        now: datetime,
    ) -> None:
        connection.execute(
            """
            INSERT INTO flaky_override (
                override_id, epoch_scope_key, flaky_key, action,
                previous_epoch, new_epoch, from_state, to_state,
                trigger_observation_id, actor, reason, created_at
            ) VALUES (?, ?, ?, ?, NULL, NULL, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"override-v2-{uuid.uuid4().hex}",
                state.epoch_scope_key,
                state.flaky_key,
                action,
                state.current_state.value,
                to_state.value,
                state.latest_observation_id,
                actor,
                reason,
                _utc_text(now),
            ),
        )

    def _require_state(
        self,
        connection: sqlite3.Connection,
        flaky_key: str,
    ) -> FlakyStateRecord:
        row = connection.execute(
            "SELECT * FROM flaky_state WHERE flaky_key = ?",
            (flaky_key,),
        ).fetchone()
        if row is None:
            raise FlakyStoreError("state_not_found", "Flaky state does not exist")
        state = _state_record(row)
        if state.projection_status is not ProjectionStatus.CURRENT:
            raise FlakyStoreError("stale_projection", "Flaky state projection is stale")
        if (
            state.rule_version != FLAKY_STATE_RULE_VERSION
            or state.projection_version != FLAKY_PROJECTION_VERSION
        ):
            raise FlakyStoreError(
                "incompatible_projection_version",
                "Flaky state rule/projection version is incompatible",
            )
        return state

    def _open_governance(
        self,
        connection: sqlite3.Connection,
        flaky_key: str,
    ) -> FlakyGovernanceRecord | None:
        row = connection.execute(
            """
            SELECT * FROM flaky_governance
            WHERE flaky_key = ? AND status IN ('ACTIVE', 'RECOVERING')
            """,
            (flaky_key,),
        ).fetchone()
        return _governance_record(row) if row is not None else None

    def _require_no_open_governance(
        self,
        connection: sqlite3.Connection,
        flaky_key: str,
    ) -> None:
        if self._open_governance(connection, flaky_key) is not None:
            raise FlakyStoreError(
                "active_governance_exists",
                "an ACTIVE/RECOVERING governance lifecycle already exists",
            )

    def _require_open_governance(
        self,
        connection: sqlite3.Connection,
        flaky_key: str,
        status: GovernanceStatus,
    ) -> FlakyGovernanceRecord:
        governance = self._open_governance(connection, flaky_key)
        if governance is None or governance.status is not status:
            raise FlakyStoreError(
                "governance_state_mismatch",
                f"open governance with status={status.value} is required",
            )
        return governance

    def _observations_for_key(
        self,
        connection: sqlite3.Connection,
        flaky_key: str,
    ) -> tuple[FlakyHistoryEntry, ...]:
        rows = connection.execute(
            """
            SELECT observation.*, import_run.artifact_ref,
                   import_run.source_digest, import_run.run_end_time,
                   import_run.imported_at
            FROM case_observation AS observation
            JOIN flaky_import_run AS import_run
              ON import_run.run_id = observation.run_id
            WHERE observation.flaky_key = ?
            ORDER BY observation.observed_at, import_run.run_end_time,
                     observation.run_id, observation.observation_id
            """,
            (flaky_key,),
        ).fetchall()
        return tuple(_history_entry(row) for row in rows)

    def _state_summary(
        self,
        connection: sqlite3.Connection,
        flaky_key: str,
    ) -> FlakyStateSummary:
        row = connection.execute(
            """
            SELECT state.*,
                   transition.reason_code AS transition_reason,
                   governance.governance_id,
                   governance.owner,
                   governance.expires_at
            FROM flaky_state AS state
            LEFT JOIN flaky_transition AS transition
              ON transition.transition_id = state.last_transition_id
            LEFT JOIN flaky_governance AS governance
              ON governance.flaky_key = state.flaky_key
             AND governance.status IN ('ACTIVE', 'RECOVERING')
            WHERE state.flaky_key = ?
            """,
            (flaky_key,),
        ).fetchone()
        if row is None:
            raise FlakyStoreError("state_not_found", "Flaky state does not exist")
        return _state_summary(row)

    def _overdue_summaries(
        self,
        connection: sqlite3.Connection,
        query_time: datetime,
    ) -> tuple[FlakyStateSummary, ...]:
        rows = connection.execute(
            """
            SELECT state.*,
                   transition.reason_code AS transition_reason,
                   governance.governance_id,
                   governance.owner,
                   governance.expires_at
            FROM flaky_governance AS governance
            JOIN flaky_state AS state ON state.flaky_key = governance.flaky_key
            LEFT JOIN flaky_transition AS transition
              ON transition.transition_id = state.last_transition_id
            WHERE governance.status IN ('ACTIVE', 'RECOVERING')
              AND governance.expires_at < ?
            ORDER BY governance.expires_at, state.flaky_key
            """,
            (_utc_text(query_time),),
        ).fetchall()
        return tuple(_state_summary(row) for row in rows)

    def reset_epoch(
        self,
        request: EpochResetRequest,
        *,
        epoch_scope_key: str,
    ) -> EpochResetResult:
        with self._connection(require_existing=True) as connection:
            self._initialize(connection)
            try:
                connection.execute("BEGIN IMMEDIATE")
                scope = connection.execute(
                    """
                    SELECT current_epoch
                    FROM flaky_case_epoch
                    WHERE epoch_scope_key = ?
                      AND case_id = ?
                      AND environment = ?
                      AND execution_profile = ?
                    """,
                    (
                        epoch_scope_key,
                        request.case_id,
                        request.environment,
                        request.execution_profile,
                    ),
                ).fetchone()
                if scope is None:
                    raise FlakyStoreError(
                        "epoch_scope_not_found",
                        "epoch scope does not exist; no placeholder scope was created",
                    )
                previous_epoch = int(scope["current_epoch"])
                has_governance_table = connection.execute(
                    """
                    SELECT 1 FROM sqlite_master
                    WHERE type = 'table' AND name = 'flaky_governance'
                    """
                ).fetchone()
                active_governance = (
                    connection.execute(
                        """
                        SELECT governance.governance_id
                        FROM flaky_governance AS governance
                        JOIN flaky_state AS state
                          ON state.flaky_key = governance.flaky_key
                        WHERE state.epoch_scope_key = ?
                          AND state.state_epoch = ?
                          AND governance.status IN ('ACTIVE', 'RECOVERING')
                        LIMIT 1
                        """,
                        (epoch_scope_key, previous_epoch),
                    ).fetchone()
                    if has_governance_table is not None
                    else None
                )
                if active_governance is not None:
                    raise FlakyStoreError(
                        "active_governance_exists",
                        "epoch reset is blocked by ACTIVE/RECOVERING governance",
                    )
                new_epoch = previous_epoch + 1
                created_at = datetime.now(UTC)
                override_id = f"override-v1-{uuid.uuid4().hex}"
                connection.execute(
                    """
                    UPDATE flaky_case_epoch
                    SET current_epoch = ?,
                        identity_rule_version = ?,
                        environment_rule_version = ?,
                        execution_profile_rule_version = ?,
                        updated_at = ?
                    WHERE epoch_scope_key = ?
                    """,
                    (
                        new_epoch,
                        FLAKY_IDENTITY_RULE_VERSION,
                        FLAKY_ENVIRONMENT_RULE_VERSION,
                        FLAKY_EXECUTION_PROFILE_RULE_VERSION,
                        _utc_text(created_at),
                        epoch_scope_key,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO flaky_override (
                        override_id, epoch_scope_key, action, previous_epoch,
                        new_epoch, actor, reason, created_at
                    ) VALUES (?, ?, 'reset_epoch', ?, ?, ?, ?, ?)
                    """,
                    (
                        override_id,
                        epoch_scope_key,
                        previous_epoch,
                        new_epoch,
                        request.actor,
                        request.reason,
                        _utc_text(created_at),
                    ),
                )
                connection.execute("COMMIT")
                return EpochResetResult(
                    override_id=override_id,
                    epoch_scope_key=epoch_scope_key,
                    case_id=request.case_id,
                    environment=request.environment,
                    execution_profile=request.execution_profile,
                    previous_epoch=previous_epoch,
                    new_epoch=new_epoch,
                    actor=request.actor,
                    reason=request.reason,
                    created_at=created_at,
                )
            except Exception:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise

    def history(
        self,
        *,
        case_id: str,
        param_hash: str | None = None,
        environment: str | None = None,
        execution_profile: str | None = None,
        state_epoch: int | None = None,
    ) -> tuple[FlakyHistoryEntry, ...]:
        filters = ["observation.case_id = ?"]
        parameters: list[object] = [case_id]
        for column, value in (
            ("param_hash", param_hash),
            ("environment", environment),
            ("execution_profile", execution_profile),
            ("state_epoch", state_epoch),
        ):
            if value is not None:
                filters.append(f"observation.{column} = ?")
                parameters.append(value)
        where = " AND ".join(filters)
        with self._connection(require_existing=True) as connection:
            self._initialize(connection)
            rows = connection.execute(
                f"""
                SELECT observation.*, import_run.artifact_ref,
                       import_run.source_digest, import_run.run_end_time,
                       import_run.imported_at
                FROM case_observation AS observation
                JOIN flaky_import_run AS import_run
                  ON import_run.run_id = observation.run_id
                WHERE {where}
                ORDER BY observation.observed_at, observation.run_id
                """,
                tuple(parameters),
            ).fetchall()
        return tuple(_history_entry(row) for row in rows)

    def check_database(self) -> FlakyDatabaseCheck:
        with self._connection(require_existing=True) as connection:
            initialization = self._initialize(connection)
            migrations = {
                int(row["version"]): row["checksum"]
                for row in connection.execute(
                    "SELECT version, checksum FROM schema_migration ORDER BY version"
                ).fetchall()
            }
            run_count = int(
                connection.execute("SELECT COUNT(*) AS count FROM flaky_import_run").fetchone()[
                    "count"
                ]
            )
            observation_count = int(
                connection.execute("SELECT COUNT(*) AS count FROM case_observation").fetchone()[
                    "count"
                ]
            )
            state_count = int(
                connection.execute("SELECT COUNT(*) AS count FROM flaky_state").fetchone()[
                    "count"
                ]
            )
            transition_count = int(
                connection.execute(
                    "SELECT COUNT(*) AS count FROM flaky_transition"
                ).fetchone()["count"]
            )
            open_governance_count = int(
                connection.execute(
                    """
                    SELECT COUNT(*) AS count FROM flaky_governance
                    WHERE status IN ('ACTIVE', 'RECOVERING')
                    """
                ).fetchone()["count"]
            )
            missing_projection_count = int(
                connection.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM (
                        SELECT DISTINCT observation.flaky_key
                        FROM case_observation AS observation
                        LEFT JOIN flaky_state AS state
                          ON state.flaky_key = observation.flaky_key
                        WHERE state.flaky_key IS NULL
                    )
                    """
                ).fetchone()["count"]
            )
            stale_projection_count = int(
                connection.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM flaky_state AS state
                    WHERE state.projection_status = 'STALE'
                       OR state.latest_observation_id != (
                            SELECT observation.observation_id
                            FROM case_observation AS observation
                            JOIN flaky_import_run AS import_run
                              ON import_run.run_id = observation.run_id
                            WHERE observation.flaky_key = state.flaky_key
                            ORDER BY observation.observed_at DESC,
                                     import_run.run_end_time DESC,
                                     observation.run_id DESC,
                                     observation.observation_id DESC
                            LIMIT 1
                       )
                    """
                ).fetchone()["count"]
            )
            incompatible_rule_version_count = int(
                connection.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM flaky_state
                    WHERE rule_version != ? OR projection_version != ?
                    """,
                    (FLAKY_STATE_RULE_VERSION, FLAKY_PROJECTION_VERSION),
                ).fetchone()["count"]
            )
            orphan_transition_count = int(
                connection.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM flaky_transition AS transition_record
                    LEFT JOIN flaky_state AS state
                      ON state.flaky_key = transition_record.flaky_key
                    WHERE state.flaky_key IS NULL
                    """
                ).fetchone()["count"]
            )
            orphan_governance_count = int(
                connection.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM flaky_governance AS governance
                    LEFT JOIN flaky_state AS state
                      ON state.flaky_key = governance.flaky_key
                    WHERE state.flaky_key IS NULL
                    """
                ).fetchone()["count"]
            )
        return FlakyDatabaseCheck(
            database_name=self.database_path.name,
            schema_version=initialization.schema_version,
            migrations=migrations,
            quick_check=initialization.quick_check,
            run_count=run_count,
            observation_count=observation_count,
            state_count=state_count,
            transition_count=transition_count,
            open_governance_count=open_governance_count,
            missing_projection_count=missing_projection_count,
            stale_projection_count=stale_projection_count,
            incompatible_rule_version_count=incompatible_rule_version_count,
            orphan_transition_count=orphan_transition_count,
            orphan_governance_count=orphan_governance_count,
        )

    @contextmanager
    def _connection(self, *, require_existing: bool) -> Iterator[sqlite3.Connection]:
        self._validate_path(require_existing=require_existing)
        try:
            connection = sqlite3.connect(
                self.database_path,
                timeout=self.busy_timeout_ms / 1000,
                isolation_level=None,
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms}")
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = FULL")
        except sqlite3.Error as error:
            raise _translate_sqlite_error(error) from error
        try:
            yield connection
        except sqlite3.Error as error:
            raise _translate_sqlite_error(error) from error
        finally:
            connection.close()

    def _validate_path(self, *, require_existing: bool) -> None:
        if str(self.database_path).startswith(("\\\\", "//")):
            raise FlakyStoreError(
                "unverified_network_database_path",
                "network share paths require an explicit SQLite locking review before use",
            )
        parent = self.database_path.parent
        if not parent.exists() or not parent.is_dir():
            raise FlakyStoreError(
                "invalid_database_path",
                "Flaky history database parent directory must already exist",
            )
        if require_existing and not self.database_path.is_file():
            raise FlakyStoreError(
                "database_not_found",
                "Flaky history database does not exist",
            )
        if self.database_path.exists() and not self.database_path.is_file():
            raise FlakyStoreError(
                "invalid_database_path",
                "Flaky history database path is not a regular file",
            )
        if not os.access(parent, os.W_OK):
            raise FlakyStoreError(
                "database_parent_not_writable",
                "Flaky history database parent directory is not writable",
            )

    def _initialize(self, connection: sqlite3.Connection) -> StoreInitialization:
        quick_check = _quick_check(connection)
        migrations = _load_migrations(self.migrations_directory)
        applied = self._read_applied_migrations(connection)
        _validate_applied_migrations(applied, migrations)
        pending = [migration for migration in migrations if migration.version not in applied]
        backup_created = False
        if pending:
            self._create_backup(connection)
            backup_created = True
            self._apply_migrations(connection, pending)
            applied = self._read_applied_migrations(connection)
            _validate_applied_migrations(applied, migrations)
            quick_check = _quick_check(connection)
        schema_version = max(applied, default=0)
        return StoreInitialization(
            schema_version=schema_version,
            quick_check=quick_check,
            migration_applied=bool(pending),
            backup_created=backup_created,
        )

    def _read_applied_migrations(self, connection: sqlite3.Connection) -> dict[int, str]:
        tables = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
            if not str(row["name"]).startswith("sqlite_")
        }
        if "schema_migration" not in tables:
            if tables:
                raise FlakyStoreError(
                    "unmanaged_database",
                    "database contains tables but has no schema_migration history",
                )
            return {}
        return {
            int(row["version"]): row["checksum"]
            for row in connection.execute(
                "SELECT version, checksum FROM schema_migration ORDER BY version"
            ).fetchall()
        }

    def _create_backup(self, connection: sqlite3.Connection) -> Path:
        backup_path = self.database_path.with_name(
            f"{self.database_path.name}.pre-migration.bak"
        )
        temporary_path = backup_path.with_name(
            f".{backup_path.name}.{uuid.uuid4().hex}.tmp"
        )
        destination: sqlite3.Connection | None = None
        try:
            destination = sqlite3.connect(temporary_path)
            connection.backup(destination)
            if _quick_check(destination) != "ok":
                raise FlakyStoreError(
                    "backup_check_failed",
                    "pre-migration backup did not pass quick_check",
                )
            destination.close()
            destination = None
            os.replace(temporary_path, backup_path)
            return backup_path
        except sqlite3.Error as error:
            raise _translate_sqlite_error(error, code="backup_failed") from error
        finally:
            if destination is not None:
                destination.close()
            temporary_path.unlink(missing_ok=True)

    def _apply_migrations(
        self,
        connection: sqlite3.Connection,
        migrations: Sequence[_Migration],
    ) -> None:
        statements = ["BEGIN IMMEDIATE;"]
        for migration in migrations:
            name_literal = migration.name.replace("'", "''")
            checksum_literal = migration.checksum.replace("'", "''")
            applied_at_literal = _utc_text(datetime.now(UTC)).replace("'", "''")
            statements.append(
                f"{migration.sql}\n"
                "INSERT INTO schema_migration (version, name, checksum, applied_at) "
                f"VALUES ({migration.version}, '{name_literal}', "
                f"'{checksum_literal}', '{applied_at_literal}');"
            )
        statements.append("COMMIT;")
        try:
            connection.executescript("\n".join(statements))
        except sqlite3.Error as error:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            names = ", ".join(migration.name for migration in migrations)
            raise FlakyStoreError(
                "migration_failed",
                f"migration batch [{names}] failed: {error}",
            ) from error

    def _materialize_observation(
        self,
        connection: sqlite3.Connection,
        candidate: CaseObservationCandidate,
        now: datetime,
    ) -> CaseObservation:
        from quality.flaky_importer import (
            build_epoch_scope_key,
            build_flaky_key,
            build_observation_id,
        )

        epoch_scope_key = build_epoch_scope_key(
            candidate.case_id,
            candidate.environment,
            candidate.execution_profile,
        )
        scope = connection.execute(
            "SELECT * FROM flaky_case_epoch WHERE epoch_scope_key = ?",
            (epoch_scope_key,),
        ).fetchone()
        if scope is None:
            connection.execute(
                """
                INSERT INTO flaky_case_epoch (
                    epoch_scope_key, case_id, environment, execution_profile,
                    current_epoch, identity_rule_version, environment_rule_version,
                    execution_profile_rule_version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?)
                """,
                (
                    epoch_scope_key,
                    candidate.case_id,
                    candidate.environment,
                    candidate.execution_profile,
                    candidate.identity_rule_version,
                    candidate.environment_rule_version,
                    candidate.execution_profile_rule_version,
                    _utc_text(now),
                    _utc_text(now),
                ),
            )
            state_epoch = 1
        else:
            expected = {
                "case_id": candidate.case_id,
                "environment": candidate.environment,
                "execution_profile": candidate.execution_profile,
                "identity_rule_version": candidate.identity_rule_version,
                "environment_rule_version": candidate.environment_rule_version,
                "execution_profile_rule_version": candidate.execution_profile_rule_version,
            }
            for field, value in expected.items():
                if scope[field] != value:
                    raise FlakyStoreError(
                        "epoch_scope_conflict",
                        f"epoch scope field {field!r} is incompatible with current rules",
                    )
            state_epoch = int(scope["current_epoch"])
            current_versions = connection.execute(
                """
                SELECT DISTINCT identity_rule_version, environment_rule_version,
                       execution_profile_rule_version, observation_rule_version,
                       fingerprint_version
                FROM case_observation
                WHERE epoch_scope_key = ? AND state_epoch = ?
                """,
                (epoch_scope_key, state_epoch),
            ).fetchall()
            desired_versions = (
                candidate.identity_rule_version,
                candidate.environment_rule_version,
                candidate.execution_profile_rule_version,
                candidate.observation_rule_version,
                candidate.fingerprint_version,
            )
            for versions in current_versions:
                if tuple(versions) != desired_versions:
                    raise FlakyStoreError(
                        "epoch_rule_version_conflict",
                        "current epoch contains observations produced by incompatible rules; reset epoch explicitly",
                    )

        flaky_key = build_flaky_key(
            candidate.case_id,
            candidate.param_hash,
            candidate.environment,
            candidate.execution_profile,
            state_epoch,
        )
        observation_id = build_observation_id(candidate.run_id, flaky_key)
        conflict = connection.execute(
            """
            SELECT observation_id FROM case_observation
            WHERE observation_id = ? OR (run_id = ? AND flaky_key = ?)
            """,
            (observation_id, candidate.run_id, flaky_key),
        ).fetchone()
        if conflict is not None:
            raise FlakyStoreError(
                "observation_conflict",
                "observation identity already exists outside a run-level no-op",
            )
        return CaseObservation(
            **candidate.model_dump(),
            observation_id=observation_id,
            flaky_key=flaky_key,
            epoch_scope_key=epoch_scope_key,
            state_epoch=state_epoch,
        )

    @staticmethod
    def _insert_import_run(
        connection: sqlite3.Connection,
        metadata: FlakyRunMetadata,
        imported_at: datetime,
    ) -> None:
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
                eligible_count, excluded_count, imported_at
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                metadata.run_id,
                metadata.source_digest,
                metadata.source_kind,
                metadata.artifact_ref,
                metadata.job_name,
                metadata.build_number,
                metadata.branch,
                metadata.commit_sha,
                metadata.environment,
                metadata.run_status,
                metadata.p0_integrity_status,
                _utc_text(metadata.run_start_time),
                _utc_text(metadata.run_end_time),
                metadata.p0_schema_version,
                metadata.p0_merge_version,
                metadata.fingerprint_version,
                metadata.run_record_sha256,
                metadata.manifest_sha256,
                metadata.case_results_sha256,
                metadata.failures_sha256,
                metadata.integrity_issues_sha256,
                metadata.importer_version,
                metadata.identity_rule_version,
                metadata.environment_rule_version,
                metadata.execution_profile_rule_version,
                metadata.observation_rule_version,
                metadata.eligible_count,
                metadata.excluded_count,
                _utc_text(imported_at),
            ),
        )

    @staticmethod
    def _insert_observation(
        connection: sqlite3.Connection,
        observation: CaseObservation,
    ) -> None:
        connection.execute(
            """
            INSERT INTO case_observation (
                observation_id, run_id, invocation_id, flaky_key,
                epoch_scope_key, case_id, param_hash, environment,
                execution_profile, state_epoch, decisive_phase, raw_status,
                final_status, observation_outcome, failure_id,
                failure_category, observed_at, identity_rule_version,
                environment_rule_version, execution_profile_rule_version,
                observation_rule_version, fingerprint_version
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                observation.observation_id,
                observation.run_id,
                observation.invocation_id,
                observation.flaky_key,
                observation.epoch_scope_key,
                observation.case_id,
                observation.param_hash,
                observation.environment,
                observation.execution_profile,
                observation.state_epoch,
                observation.decisive_phase.value,
                observation.raw_status.value,
                observation.final_status.value,
                observation.observation_outcome.value,
                observation.failure_id,
                observation.failure_category,
                _utc_text(observation.observed_at),
                observation.identity_rule_version,
                observation.environment_rule_version,
                observation.execution_profile_rule_version,
                observation.observation_rule_version,
                observation.fingerprint_version,
            ),
        )


@dataclass(frozen=True)
class _Migration:
    version: int
    name: str
    checksum: str
    sql: str


def _load_migrations(directory: Path) -> tuple[_Migration, ...]:
    if not directory.is_dir():
        raise FlakyStoreError(
            "migration_directory_missing",
            "Flaky history migration directory does not exist",
        )
    migrations: list[_Migration] = []
    for path in sorted(directory.glob("[0-9][0-9][0-9][0-9]_*.sql")):
        try:
            version = int(path.name.split("_", 1)[0])
        except ValueError as error:
            raise FlakyStoreError("invalid_migration_name", path.name) from error
        raw = path.read_bytes()
        try:
            sql = raw.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
        except UnicodeDecodeError as error:
            raise FlakyStoreError(
                "invalid_migration_encoding",
                f"migration {path.name!r} must be UTF-8",
            ) from error
        migrations.append(
            _Migration(
                version=version,
                name=path.name,
                checksum=hashlib.sha256(sql.encode("utf-8")).hexdigest(),
                sql=sql,
            )
        )
    versions = [migration.version for migration in migrations]
    if not migrations or versions != list(range(1, len(migrations) + 1)):
        raise FlakyStoreError(
            "invalid_migration_sequence",
            "migration versions must be contiguous and start at 1",
        )
    return tuple(migrations)


def _validate_applied_migrations(
    applied: dict[int, str],
    available: Sequence[_Migration],
) -> None:
    available_by_version = {migration.version: migration for migration in available}
    for version, checksum in applied.items():
        migration = available_by_version.get(version)
        if migration is None:
            raise FlakyStoreError(
                "schema_too_new",
                f"database migration version {version} is not supported by this code",
            )
        if migration.checksum != checksum:
            raise FlakyStoreError(
                "migration_checksum_mismatch",
                f"migration checksum mismatch for version {version}",
            )
    if applied and sorted(applied) != list(range(1, max(applied) + 1)):
        raise FlakyStoreError(
            "migration_history_gap",
            "database migration history is not contiguous",
        )


def _quick_check(connection: sqlite3.Connection) -> str:
    try:
        rows = connection.execute("PRAGMA quick_check").fetchall()
    except sqlite3.Error as error:
        raise _translate_sqlite_error(error, code="database_corrupted") from error
    messages = [str(row[0]) for row in rows]
    if messages != ["ok"]:
        summary = "; ".join(messages[:3]) or "unknown quick_check result"
        raise FlakyStoreError(
            "database_corrupted",
            f"SQLite quick_check failed: {summary}",
        )
    return "ok"


def _translate_sqlite_error(
    error: sqlite3.Error,
    *,
    code: str | None = None,
) -> FlakyStoreError:
    message = str(error)
    lowered = message.casefold()
    if "locked" in lowered or "busy" in lowered:
        return FlakyStoreError("db_busy", message)
    if "malformed" in lowered or "not a database" in lowered:
        return FlakyStoreError("database_corrupted", message)
    return FlakyStoreError(code or "database_error", message)


def _history_entry(row: sqlite3.Row) -> FlakyHistoryEntry:
    return FlakyHistoryEntry(
        observation_id=row["observation_id"],
        run_id=row["run_id"],
        invocation_id=row["invocation_id"],
        flaky_key=row["flaky_key"],
        epoch_scope_key=row["epoch_scope_key"],
        case_id=row["case_id"],
        param_hash=row["param_hash"],
        environment=row["environment"],
        execution_profile=row["execution_profile"],
        state_epoch=row["state_epoch"],
        decisive_phase=row["decisive_phase"],
        raw_status=row["raw_status"],
        final_status=row["final_status"],
        observation_outcome=row["observation_outcome"],
        failure_id=row["failure_id"],
        failure_category=row["failure_category"],
        observed_at=datetime.fromisoformat(row["observed_at"]),
        identity_rule_version=row["identity_rule_version"],
        environment_rule_version=row["environment_rule_version"],
        execution_profile_rule_version=row["execution_profile_rule_version"],
        observation_rule_version=row["observation_rule_version"],
        fingerprint_version=row["fingerprint_version"],
        artifact_ref=row["artifact_ref"],
        source_digest=row["source_digest"],
        run_end_time=datetime.fromisoformat(row["run_end_time"]),
        imported_at=datetime.fromisoformat(row["imported_at"]),
    )


def _state_record(row: sqlite3.Row) -> FlakyStateRecord:
    return FlakyStateRecord(
        flaky_key=row["flaky_key"],
        epoch_scope_key=row["epoch_scope_key"],
        case_id=row["case_id"],
        param_hash=row["param_hash"],
        environment=row["environment"],
        execution_profile=row["execution_profile"],
        state_epoch=int(row["state_epoch"]),
        current_state=row["current_state"],
        detected_state=row["detected_state"],
        stable_outcome=row["stable_outcome"],
        stable_failure_id=row["stable_failure_id"],
        total_observation_count=int(row["total_observation_count"]),
        sample_size=int(row["sample_size"]),
        evidence_window_size=int(row["evidence_window_size"]),
        pass_count=int(row["pass_count"]),
        fail_count=int(row["fail_count"]),
        outcome_switch_count=int(row["outcome_switch_count"]),
        signature_switch_count=int(row["signature_switch_count"]),
        distinct_failure_fingerprint_count=int(
            row["distinct_failure_fingerprint_count"]
        ),
        trailing_same_signature_count=int(row["trailing_same_signature_count"]),
        evaluation_anchor_observation_id=row["evaluation_anchor_observation_id"],
        latest_observation_id=row["latest_observation_id"],
        latest_run_id=row["latest_run_id"],
        latest_observed_at=datetime.fromisoformat(row["latest_observed_at"]),
        last_transition_id=row["last_transition_id"],
        rule_version=row["rule_version"],
        projection_version=row["projection_version"],
        projection_status=row["projection_status"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def _governance_record(row: sqlite3.Row) -> FlakyGovernanceRecord:
    return FlakyGovernanceRecord(
        governance_id=row["governance_id"],
        flaky_key=row["flaky_key"],
        status=row["status"],
        owner=row["owner"],
        reason=row["reason"],
        created_by=row["created_by"],
        created_at=datetime.fromisoformat(row["created_at"]),
        expires_at=datetime.fromisoformat(row["expires_at"]),
        recovery_started_by=row["recovery_started_by"],
        recovery_started_at=(
            datetime.fromisoformat(row["recovery_started_at"])
            if row["recovery_started_at"] is not None
            else None
        ),
        recovery_reason=row["recovery_reason"],
        recovery_anchor_observation_id=row["recovery_anchor_observation_id"],
        closed_at=(
            datetime.fromisoformat(row["closed_at"])
            if row["closed_at"] is not None
            else None
        ),
        resolution=row["resolution"],
    )


def _state_summary(row: sqlite3.Row) -> FlakyStateSummary:
    return FlakyStateSummary(
        flaky_key=row["flaky_key"],
        case_id=row["case_id"],
        param_hash=row["param_hash"],
        environment=row["environment"],
        execution_profile=row["execution_profile"],
        state_epoch=int(row["state_epoch"]),
        current_state=row["current_state"],
        detected_state=row["detected_state"],
        sample_size=int(row["sample_size"]),
        projection_status=row["projection_status"],
        latest_run_id=row["latest_run_id"],
        latest_observation_id=row["latest_observation_id"],
        transition_reason=row["transition_reason"],
        governance_id=row["governance_id"],
        owner=row["owner"],
        expires_at=(
            datetime.fromisoformat(row["expires_at"])
            if row["expires_at"] is not None
            else None
        ),
    )


def _transition_record(
    *,
    flaky_key: str,
    from_state: FlakyState | None,
    to_state: FlakyState,
    trigger_type: TransitionTrigger,
    reason_code: str,
    trigger_observation_id: str | None,
    evidence,
    actor: str | None,
    created_at: datetime,
    config: FlakyRuleConfig,
) -> FlakyTransitionRecord:
    transition_id = build_transition_id(
        flaky_key=flaky_key,
        from_state=from_state,
        to_state=to_state,
        trigger_type=trigger_type.value,
        reason_code=reason_code,
        trigger_observation_id=trigger_observation_id,
        rule_version=config.rule_version,
        projection_version=config.projection_version,
    )
    return FlakyTransitionRecord(
        transition_id=transition_id,
        flaky_key=flaky_key,
        from_state=from_state,
        to_state=to_state,
        trigger_type=trigger_type,
        reason_code=reason_code,
        rule_version=config.rule_version,
        projection_version=config.projection_version,
        sample_size=evidence.sample_size,
        trigger_observation_id=trigger_observation_id,
        evidence_observation_ids=evidence.observation_ids,
        evidence_run_ids=evidence.run_ids,
        actor=actor,
        created_at=created_at,
    )


def _observations_after_anchor(
    observations: Sequence[FlakyHistoryEntry],
    anchor_id: str | None,
) -> tuple[FlakyHistoryEntry, ...]:
    if anchor_id is None:
        raise FlakyStoreError(
            "recovery_anchor_missing",
            "RECOVERING governance has no recovery observation anchor",
        )
    for index, observation in enumerate(observations):
        if observation.observation_id == anchor_id:
            return tuple(observations[index + 1 :])
    raise FlakyStoreError(
        "recovery_anchor_not_found",
        "recovery observation anchor is not present in the current epoch",
    )


def _evaluate_from_manual_anchor(
    existing: FlakyStateRecord,
    observations: Sequence[FlakyHistoryEntry],
    config: FlakyRuleConfig,
):
    anchor = existing.evaluation_anchor_observation_id
    anchor_index = next(
        (
            index
            for index, observation in enumerate(observations)
            if observation.observation_id == anchor
        ),
        None,
    )
    if anchor_index is None:
        raise FlakyStoreError(
            "evaluation_anchor_not_found",
            "manual evaluation anchor is not present in the current epoch",
        )
    scoped = tuple(observations[anchor_index:])
    evidence = derive_evidence_window(scoped, config)
    current = existing.current_state
    stable_outcome = existing.stable_outcome
    stable_failure_id = existing.stable_failure_id
    reason: str | None = None
    stable_signature = (
        "pass"
        if stable_outcome is ObservationOutcome.PASS
        else f"fail:{stable_failure_id}"
        if stable_outcome is ObservationOutcome.FAIL and stable_failure_id
        else None
    )
    if current is FlakyState.STABLE:
        if stable_signature is None:
            raise FlakyStoreError(
                "stable_signature_missing",
                "anchored STABLE state has no stable result signature",
            )
        if evidence.latest_signature != stable_signature:
            current = FlakyState.SUSPECTED
            stable_outcome = None
            stable_failure_id = None
            reason = "stable_signature_broken"
    elif current is FlakyState.SUSPECTED:
        confirmation_met = (
            evidence.sample_size >= config.confirmed_min_samples
            and evidence.pass_count >= config.confirmed_min_pass_count
            and evidence.fail_count >= config.confirmed_min_fail_count
            and evidence.outcome_switch_count
            >= config.confirmed_min_outcome_switches
        )
        if confirmation_met:
            current = FlakyState.CONFIRMED
            stable_outcome = None
            stable_failure_id = None
            reason = "confirmation_threshold_met"
        elif (
            evidence.trailing_same_signature_count
            >= config.suspected_clear_signature_streak
        ):
            current = FlakyState.STABLE
            stable_outcome, stable_failure_id = _signature_values(
                evidence.latest_signature
            )
            reason = "suspected_cleared_by_streak"
    elif current is FlakyState.CONFIRMED:
        stable_outcome = None
        stable_failure_id = None
    else:
        raise FlakyStoreError(
            "invalid_anchored_state",
            f"manual anchor cannot be evaluated from {current.value}",
        )
    return (
        current,
        current,
        evidence,
        stable_outcome,
        stable_failure_id,
        reason,
    )


def _signature_values(
    signature: str,
) -> tuple[ObservationOutcome, str | None]:
    if signature == "pass":
        return ObservationOutcome.PASS, None
    if signature.startswith("fail:") and len(signature) > len("fail:"):
        return ObservationOutcome.FAIL, signature[len("fail:") :]
    raise ValueError(f"invalid result signature: {signature!r}")


def _projection_reason(projection, target_state: FlakyState) -> str:
    for transition in reversed(projection.transitions):
        if transition.to_state is target_state:
            return transition.reason_code
    return "projection_state_changed"


def _evidence_from_state(
    state: FlakyStateRecord,
    observations: Sequence[FlakyHistoryEntry],
    config: FlakyRuleConfig,
):
    del state
    return derive_evidence_window(observations, config)


def _state_payload(state: FlakyStateRecord) -> dict[str, object]:
    return state.model_dump(exclude={"updated_at"}, mode="python")


def _canonical_json(values: Sequence[str]) -> str:
    return json.dumps(
        list(values),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _required_text(value: str, name: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise ValueError(f"{name} must not be empty")
    return stripped


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must include timezone information")
    return value.astimezone(UTC).isoformat()
