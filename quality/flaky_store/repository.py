from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import sqlite3
from typing import Iterator, Sequence
import uuid

from quality.flaky_models import (
    CaseObservation,
    CaseObservationCandidate,
    FLAKY_PROJECTION_VERSION,
    FLAKY_STATE_RULE_VERSION,
    FlakyDatabaseCheck,
    FlakyGovernanceRecord,
    FlakyHistoryEntry,
    FlakyRunMetadata,
    FlakyState,
    FlakyStateRecord,
    FlakyStateSummary,
    FlakyTransitionRecord,
    GovernanceStatus,
    ProjectionStatus,
)

from .contracts import FlakyStoreError, StoreInitialization, required_text


class FlakyRepository:
    def __init__(
        self,
        database_path: Path,
        *,
        busy_timeout_ms: int,
    ) -> None:
        self.database_path = database_path
        self.busy_timeout_ms = busy_timeout_ms

    @contextmanager
    def connection(
        self,
        *,
        require_existing: bool,
        read_only: bool = False,
    ) -> Iterator[sqlite3.Connection]:
        self.validate_path(require_existing=require_existing, read_only=read_only)
        try:
            target: str | Path
            if read_only:
                target = f"{self.database_path.as_uri()}?mode=ro"
            else:
                target = self.database_path
            connection = sqlite3.connect(
                target,
                timeout=self.busy_timeout_ms / 1000,
                isolation_level=None,
                uri=read_only,
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms}")
            if read_only:
                connection.execute("PRAGMA query_only = ON")
            else:
                connection.execute("PRAGMA journal_mode = WAL")
                connection.execute("PRAGMA synchronous = FULL")
        except sqlite3.Error as error:
            raise translate_sqlite_error(error) from error
        try:
            yield connection
        except sqlite3.Error as error:
            raise translate_sqlite_error(error) from error
        finally:
            connection.close()

    @contextmanager
    def in_memory_copy(
        self, source: sqlite3.Connection
    ) -> Iterator[sqlite3.Connection]:
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(":memory:", isolation_level=None)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            source.backup(connection)
        except sqlite3.Error as error:
            if connection is not None:
                connection.close()
            raise translate_sqlite_error(error) from error
        try:
            yield connection
        except sqlite3.Error as error:
            raise translate_sqlite_error(error) from error
        finally:
            connection.close()

    def validate_path(self, *, require_existing: bool, read_only: bool = False) -> None:
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
        if not read_only and not os.access(parent, os.W_OK):
            raise FlakyStoreError(
                "database_parent_not_writable",
                "Flaky history database parent directory is not writable",
            )

    @contextmanager
    def transaction(
        self, connection: sqlite3.Connection
    ) -> Iterator[sqlite3.Connection]:
        connection.execute("BEGIN IMMEDIATE")
        try:
            yield connection
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        else:
            if connection.in_transaction:
                connection.execute("COMMIT")


    def upsert_state(
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
            utc_text(state.latest_observed_at),
            state.last_transition_id,
            state.rule_version,
            state.projection_version,
            state.projection_status.value,
            utc_text(state.created_at),
            utc_text(state.updated_at),
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

    def insert_transition(
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
                canonical_json(transition.evidence_observation_ids),
                canonical_json(transition.evidence_run_ids),
                transition.actor,
                utc_text(transition.created_at),
            ),
        )

    def insert_override(
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
                utc_text(now),
            ),
        )

    def require_state(
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
        state = state_record(row)
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

    def open_governance(
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
        return governance_record(row) if row is not None else None

    def require_no_open_governance(
        self,
        connection: sqlite3.Connection,
        flaky_key: str,
    ) -> None:
        if self.open_governance(connection, flaky_key) is not None:
            raise FlakyStoreError(
                "active_governance_exists",
                "an ACTIVE/RECOVERING governance lifecycle already exists",
            )

    def require_open_governance(
        self,
        connection: sqlite3.Connection,
        flaky_key: str,
        status: GovernanceStatus,
    ) -> FlakyGovernanceRecord:
        governance = self.open_governance(connection, flaky_key)
        if governance is None or governance.status is not status:
            raise FlakyStoreError(
                "governance_state_mismatch",
                f"open governance with status={status.value} is required",
            )
        return governance

    def observations_for_key(
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
        return tuple(history_entry(row) for row in rows)

    def state_summary(
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
        return state_summary(row)

    def overdue_summaries(
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
            (utc_text(query_time),),
        ).fetchall()
        return tuple(state_summary(row) for row in rows)

    def read_applied_migrations(self, connection: sqlite3.Connection) -> dict[int, str]:
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

    @staticmethod
    def insert_import_run(
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
                utc_text(metadata.run_start_time),
                utc_text(metadata.run_end_time),
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
                utc_text(imported_at),
            ),
        )

    @staticmethod
    def insert_observation(
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
                utc_text(observation.observed_at),
                observation.identity_rule_version,
                observation.environment_rule_version,
                observation.execution_profile_rule_version,
                observation.observation_rule_version,
                observation.fingerprint_version,
            ),
        )

    def source_digest_for_run(
        self, connection: sqlite3.Connection, run_id: str
    ) -> str | None:
        row = connection.execute(
            "SELECT source_digest FROM flaky_import_run WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        return row["source_digest"] if row is not None else None

    def run_id_for_source_digest(
        self, connection: sqlite3.Connection, source_digest: str
    ) -> str | None:
        row = connection.execute(
            "SELECT run_id FROM flaky_import_run WHERE source_digest = ?",
            (source_digest,),
        ).fetchone()
        return row["run_id"] if row is not None else None

    def observation_count_for_run(
        self, connection: sqlite3.Connection, run_id: str
    ) -> int:
        return int(
            connection.execute(
                "SELECT COUNT(*) AS count FROM case_observation WHERE run_id = ?",
                (run_id,),
            ).fetchone()["count"]
        )

    def epoch_scope(
        self, connection: sqlite3.Connection, epoch_scope_key: str
    ) -> sqlite3.Row | None:
        return connection.execute(
            "SELECT * FROM flaky_case_epoch WHERE epoch_scope_key = ?",
            (epoch_scope_key,),
        ).fetchone()

    def insert_epoch_scope(
        self,
        connection: sqlite3.Connection,
        epoch_scope_key: str,
        candidate: CaseObservationCandidate,
        now: datetime,
    ) -> None:
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
                utc_text(now),
                utc_text(now),
            ),
        )

    def epoch_rule_versions(
        self,
        connection: sqlite3.Connection,
        epoch_scope_key: str,
        state_epoch: int,
    ) -> tuple[tuple[object, ...], ...]:
        rows = connection.execute(
            """
            SELECT DISTINCT identity_rule_version, environment_rule_version,
                   execution_profile_rule_version, observation_rule_version,
                   fingerprint_version
            FROM case_observation
            WHERE epoch_scope_key = ? AND state_epoch = ?
            """,
            (epoch_scope_key, state_epoch),
        ).fetchall()
        return tuple(tuple(row) for row in rows)

    def observation_conflict_exists(
        self,
        connection: sqlite3.Connection,
        observation_id: str,
        run_id: str,
        flaky_key: str,
    ) -> bool:
        return (
            connection.execute(
                """
                SELECT observation_id FROM case_observation
                WHERE observation_id = ? OR (run_id = ? AND flaky_key = ?)
                """,
                (observation_id, run_id, flaky_key),
            ).fetchone()
            is not None
        )

    def imported_run_exists(
        self, connection: sqlite3.Connection, run_id: str
    ) -> bool:
        return (
            connection.execute(
                "SELECT run_id FROM flaky_import_run WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            is not None
        )

    def flaky_keys_for_run(
        self, connection: sqlite3.Connection, run_id: str
    ) -> tuple[str, ...]:
        return tuple(
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

    def all_flaky_keys(self, connection: sqlite3.Connection) -> tuple[str, ...]:
        return tuple(
            row["flaky_key"]
            for row in connection.execute(
                "SELECT DISTINCT flaky_key FROM case_observation ORDER BY flaky_key"
            ).fetchall()
        )

    def state_or_none(
        self, connection: sqlite3.Connection, flaky_key: str
    ) -> FlakyStateRecord | None:
        row = connection.execute(
            "SELECT * FROM flaky_state WHERE flaky_key = ?",
            (flaky_key,),
        ).fetchone()
        return state_record(row) if row is not None else None

    def transition_exists(
        self, connection: sqlite3.Connection, transition_id: str
    ) -> bool:
        return (
            connection.execute(
                "SELECT 1 FROM flaky_transition WHERE transition_id = ?",
                (transition_id,),
            ).fetchone()
            is not None
        )

    def close_recovering_governance(
        self,
        connection: sqlite3.Connection,
        governance_id: str,
        *,
        closed_at: datetime,
        resolution: str,
    ) -> None:
        connection.execute(
            """
            UPDATE flaky_governance
            SET status = 'CLOSED', closed_at = ?, resolution = ?
            WHERE governance_id = ? AND status = 'RECOVERING'
            """,
            (utc_text(closed_at), resolution, governance_id),
        )

    def insert_governance(
        self,
        connection: sqlite3.Connection,
        *,
        governance_id: str,
        flaky_key: str,
        owner: str,
        reason: str,
        actor: str,
        created_at: datetime,
        expires_at: datetime,
    ) -> None:
        connection.execute(
            """
            INSERT INTO flaky_governance (
                governance_id, flaky_key, status, owner, reason,
                created_by, created_at, expires_at
            ) VALUES (?, ?, 'ACTIVE', ?, ?, ?, ?, ?)
            """,
            (
                governance_id,
                flaky_key,
                owner,
                reason,
                actor,
                utc_text(created_at),
                utc_text(expires_at),
            ),
        )

    def start_governance_recovery(
        self,
        connection: sqlite3.Connection,
        governance_id: str,
        *,
        actor: str,
        started_at: datetime,
        reason: str,
        anchor_observation_id: str,
    ) -> None:
        connection.execute(
            """
            UPDATE flaky_governance
            SET status = 'RECOVERING', recovery_started_by = ?,
                recovery_started_at = ?, recovery_reason = ?,
                recovery_anchor_observation_id = ?
            WHERE governance_id = ?
            """,
            (
                actor,
                utc_text(started_at),
                reason,
                anchor_observation_id,
                governance_id,
            ),
        )

    def update_state_transition(
        self,
        connection: sqlite3.Connection,
        flaky_key: str,
        *,
        state: FlakyState,
        transition_id: str,
        updated_at: datetime,
    ) -> None:
        connection.execute(
            """
            UPDATE flaky_state
            SET current_state = ?, last_transition_id = ?, updated_at = ?
            WHERE flaky_key = ?
            """,
            (state.value, transition_id, utc_text(updated_at), flaky_key),
        )

    def close_governance(
        self,
        connection: sqlite3.Connection,
        governance_id: str,
        *,
        closed_at: datetime,
        resolution: str,
    ) -> None:
        connection.execute(
            """
            UPDATE flaky_governance
            SET status = 'CLOSED', closed_at = ?, resolution = ?
            WHERE governance_id = ?
            """,
            (utc_text(closed_at), resolution, governance_id),
        )

    def update_state_after_cancel(
        self,
        connection: sqlite3.Connection,
        flaky_key: str,
        *,
        transition_id: str,
        updated_at: datetime,
    ) -> None:
        connection.execute(
            """
            UPDATE flaky_state
            SET current_state = 'CONFIRMED', detected_state = 'CONFIRMED',
                stable_outcome = NULL, stable_failure_id = NULL,
                last_transition_id = ?, updated_at = ?
            WHERE flaky_key = ?
            """,
            (transition_id, utc_text(updated_at), flaky_key),
        )

    def update_state_after_override(
        self,
        connection: sqlite3.Connection,
        flaky_key: str,
        *,
        target_state: FlakyState,
        stable_outcome: str | None,
        stable_failure_id: str | None,
        anchor: str | None,
        transition_id: str,
        updated_at: datetime,
    ) -> None:
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
                transition_id,
                utc_text(updated_at),
                flaky_key,
            ),
        )

    def governance_by_id(
        self, connection: sqlite3.Connection, governance_id: str
    ) -> FlakyGovernanceRecord:
        row = connection.execute(
            "SELECT * FROM flaky_governance WHERE governance_id = ?",
            (governance_id,),
        ).fetchone()
        return governance_record(row)

    def state_by_key(
        self, connection: sqlite3.Connection, flaky_key: str
    ) -> FlakyStateRecord:
        row = connection.execute(
            "SELECT * FROM flaky_state WHERE flaky_key = ?",
            (flaky_key,),
        ).fetchone()
        return state_record(row)

    def epoch_current(
        self,
        connection: sqlite3.Connection,
        *,
        epoch_scope_key: str,
        case_id: str,
        environment: str,
        execution_profile: str,
    ) -> int | None:
        row = connection.execute(
            """
            SELECT current_epoch
            FROM flaky_case_epoch
            WHERE epoch_scope_key = ?
              AND case_id = ?
              AND environment = ?
              AND execution_profile = ?
            """,
            (epoch_scope_key, case_id, environment, execution_profile),
        ).fetchone()
        return int(row["current_epoch"]) if row is not None else None

    def has_governance_table(self, connection: sqlite3.Connection) -> bool:
        return (
            connection.execute(
                """
                SELECT 1 FROM sqlite_master
                WHERE type = 'table' AND name = 'flaky_governance'
                """
            ).fetchone()
            is not None
        )

    def active_governance_for_epoch(
        self,
        connection: sqlite3.Connection,
        epoch_scope_key: str,
        state_epoch: int,
    ) -> bool:
        return (
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
                (epoch_scope_key, state_epoch),
            ).fetchone()
            is not None
        )

    def update_epoch_scope(
        self,
        connection: sqlite3.Connection,
        epoch_scope_key: str,
        *,
        new_epoch: int,
        identity_rule_version: str,
        environment_rule_version: str,
        execution_profile_rule_version: str,
        updated_at: datetime,
    ) -> None:
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
                identity_rule_version,
                environment_rule_version,
                execution_profile_rule_version,
                utc_text(updated_at),
                epoch_scope_key,
            ),
        )

    def insert_epoch_reset_override(
        self,
        connection: sqlite3.Connection,
        *,
        override_id: str,
        epoch_scope_key: str,
        previous_epoch: int,
        new_epoch: int,
        actor: str,
        reason: str,
        created_at: datetime,
    ) -> None:
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
                actor,
                reason,
                utc_text(created_at),
            ),
        )

    def states(
        self,
        connection: sqlite3.Connection,
        *,
        case_id: str,
        param_hash: str | None = None,
        environment: str | None = None,
        execution_profile: str | None = None,
        state_epoch: int | None = None,
    ) -> tuple[FlakyStateRecord, ...]:
        filters = ["case_id = ?"]
        parameters: list[object] = [required_text(case_id, "case_id")]
        for column, value in (
            ("param_hash", param_hash),
            ("environment", environment),
            ("execution_profile", execution_profile),
            ("state_epoch", state_epoch),
        ):
            if value is not None:
                filters.append(f"{column} = ?")
                parameters.append(value)
        rows = connection.execute(
            f"SELECT * FROM flaky_state WHERE {' AND '.join(filters)} "
            "ORDER BY param_hash, environment, execution_profile, state_epoch",
            tuple(parameters),
        ).fetchall()
        return tuple(state_record(row) for row in rows)

    def governance(
        self,
        connection: sqlite3.Connection,
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
            parameters.append(utc_text(query_time or datetime.now(UTC)))
        where = f"WHERE {' AND '.join(filters)}" if filters else ""
        rows = connection.execute(
            f"SELECT * FROM flaky_governance {where} ORDER BY created_at, governance_id",
            tuple(parameters),
        ).fetchall()
        return tuple(governance_record(row) for row in rows)

    def history(
        self,
        connection: sqlite3.Connection,
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
        return tuple(history_entry(row) for row in rows)

    def check_database(
        self,
        connection: sqlite3.Connection,
        initialization: StoreInitialization,
    ) -> FlakyDatabaseCheck:
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


def quick_check(connection: sqlite3.Connection) -> str:
    try:
        rows = connection.execute("PRAGMA quick_check").fetchall()
    except sqlite3.Error as error:
        raise translate_sqlite_error(error, code="database_corrupted") from error
    messages = [str(row[0]) for row in rows]
    if messages != ["ok"]:
        summary = "; ".join(messages[:3]) or "unknown quick_check result"
        raise FlakyStoreError(
            "database_corrupted",
            f"SQLite quick_check failed: {summary}",
        )
    return "ok"


def translate_sqlite_error(
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

def history_entry(row: sqlite3.Row) -> FlakyHistoryEntry:
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


def state_record(row: sqlite3.Row) -> FlakyStateRecord:
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


def governance_record(row: sqlite3.Row) -> FlakyGovernanceRecord:
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


def state_summary(row: sqlite3.Row) -> FlakyStateSummary:
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

def canonical_json(values: Sequence[str]) -> str:
    return json.dumps(
        list(values),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
    )

def utc_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must include timezone information")
    return value.astimezone(UTC).isoformat()
