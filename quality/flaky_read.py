from __future__ import annotations

import base64
from collections import defaultdict
from contextlib import contextmanager
from datetime import UTC, datetime
import json
from pathlib import Path
import sqlite3
from typing import Callable, Generic, Iterator, Literal, TypeVar
import unicodedata

from pydantic import BaseModel, ConfigDict, Field

from quality.flaky_v3 import (
    DEFAULT_GOVERNANCE_POLICY,
    NORMAL_ADMISSION_RULE_VERSION,
    PROBE_EVIDENCE_RULE_VERSION,
)
from quality.models import SCHEMA_VERSION

from .flaky_store.contracts import DEFAULT_BUSY_TIMEOUT_MS, FlakyStoreError
from .flaky_store.migration import (
    MIGRATIONS_DIRECTORY,
    load_migrations,
    validate_applied_migrations,
)
from .flaky_store.repository import FlakyRepository
from .flaky_store.repository import quick_check


T = TypeVar("T")


class ReadModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Page(ReadModel, Generic[T]):
    schema_version: Literal["quality.flaky-page.v1"] = "quality.flaky-page.v1"
    items: tuple[T, ...]
    next_cursor: str | None = None
    page_size: int
    data_as_of: datetime


class DashboardSummary(ReadModel):
    schema_version: Literal["quality.flaky-dashboard-summary.v1"] = (
        "quality.flaky-dashboard-summary.v1"
    )
    database_health: str
    database_schema_version: int
    p0_schema_version: str
    policy_revision: str
    normal_admission_rule_version: str
    probe_evidence_rule_version: str
    data_as_of: datetime
    detection_counts: dict[str, int]
    governance_counts: dict[str, int]
    attempt_counts: dict[str, int]
    overdue_count: int
    mode_requested: str
    mode_effective: str


class DetectionProjectionSummary(ReadModel):
    comparability_fingerprint: str
    detection_state: str
    stable_outcome: str | None


class GovernanceListItem(ReadModel):
    governance_id: str
    row_version: int
    flaky_key: str
    case_id: str
    param_hash: str
    environment: str
    execution_profile: str
    state_epoch: int
    detection_generation: int
    detection_states: tuple[str, ...]
    detection_projections: tuple[DetectionProjectionSummary, ...]
    governance_status: str
    attempt_id: str | None
    attempt_status: str | None
    attempt_target_commit_sha: str | None
    attempt_counted_passes: int | None
    attempt_required_consecutive_passes: int | None
    attempt_non_counting_runs: int | None
    attempt_max_non_counting_runs: int | None
    owner: str
    reason: str
    created_at: datetime
    expires_at: datetime
    overdue: bool
    latest_evidence_at: datetime | None
    normalized_case_path: str | None


class TimelineItem(ReadModel):
    occurred_at: datetime
    event_kind: str
    event_id: str
    payload: dict[str, object]


class FlakyCaseDetail(ReadModel):
    schema_version: Literal["quality.flaky-case-detail.v1"] = (
        "quality.flaky-case-detail.v1"
    )
    data_as_of: datetime
    identity: dict[str, object]
    projections: tuple[dict[str, object], ...]
    governance: tuple[dict[str, object], ...]
    attempts: tuple[dict[str, object], ...]
    normal_evidence: tuple[dict[str, object], ...]
    probe_evidence: tuple[dict[str, object], ...]
    timeline: tuple[TimelineItem, ...]


class RunDecisionSummary(ReadModel):
    schema_version: Literal["quality.flaky-run-decision-summary.v1"] = (
        "quality.flaky-run-decision-summary.v1"
    )
    run_id: str
    snapshot_id: str | None
    mode_requested: str
    mode_effective: str
    run_count: int
    would_skip_count: int
    skip_count: int
    fail_open_count: int
    reason_counts: dict[str, int]


class SnapshotCandidate(ReadModel):
    flaky_key: str
    case_id: str
    param_hash: str
    environment: str
    execution_profile: str
    state_epoch: int
    governance_id: str
    governance_status: str
    expires_at: datetime


class SnapshotSource(ReadModel):
    database_schema_version: int
    data_as_of: datetime
    candidates: tuple[SnapshotCandidate, ...]


class FlakyReadService:
    def __init__(
        self,
        database_path: str | Path,
        *,
        busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
        migrations_directory: str | Path = MIGRATIONS_DIRECTORY,
        clock: Callable[[], datetime] | None = None,
        mode_requested: str = "off",
        mode_effective: str = "off",
    ) -> None:
        self.database_path = Path(database_path)
        if not self.database_path.is_absolute():
            raise FlakyStoreError(
                "invalid_database_path", "Flaky database path must be absolute"
            )
        self.repository = FlakyRepository(
            self.database_path, busy_timeout_ms=busy_timeout_ms
        )
        self.migrations_directory = Path(migrations_directory)
        self._clock = clock or (lambda: datetime.now(UTC))
        self.mode_requested = _requested_mode(mode_requested)
        self.mode_effective = _effective_mode(mode_effective)

    def summary(self) -> DashboardSummary:
        with self._transaction() as (connection, schema_version, data_as_of):
            detection = _counts(
                connection,
                """SELECT COALESCE(projection.detection_state, 'UNOBSERVED') AS state,
                          COUNT(*) AS count
                   FROM flaky_identity AS identity
                   LEFT JOIN flaky_detection_projection AS projection
                     ON projection.flaky_key = identity.flaky_key
                    AND projection.detection_generation = identity.current_detection_generation
                   GROUP BY COALESCE(projection.detection_state, 'UNOBSERVED')""",
            )
            governance = _counts(
                connection,
                "SELECT status AS state, COUNT(*) AS count FROM flaky_governance GROUP BY status",
            )
            attempts = _counts(
                connection,
                """SELECT status AS state, COUNT(*) AS count
                   FROM flaky_verification_attempt GROUP BY status""",
            )
            overdue = int(
                connection.execute(
                    """SELECT COUNT(*) FROM flaky_governance
                       WHERE status IN ('ACTIVE','RECOVERING') AND expires_at <= ?""",
                    (data_as_of.isoformat(),),
                ).fetchone()[0]
            )
            return DashboardSummary(
                database_health="OK",
                database_schema_version=schema_version,
                p0_schema_version=SCHEMA_VERSION,
                policy_revision=DEFAULT_GOVERNANCE_POLICY.revision,
                normal_admission_rule_version=NORMAL_ADMISSION_RULE_VERSION,
                probe_evidence_rule_version=PROBE_EVIDENCE_RULE_VERSION,
                data_as_of=data_as_of,
                detection_counts=detection,
                governance_counts=governance,
                attempt_counts=attempts,
                overdue_count=overdue,
                mode_requested=self.mode_requested,
                mode_effective=self.mode_effective,
            )

    def readiness(self) -> dict[str, object]:
        with self._transaction() as (connection, schema_version, data_as_of):
            return {
                "status": "ready",
                "database_schema_version": schema_version,
                "quick_check": quick_check(connection),
                "data_as_of": data_as_of.isoformat(),
            }

    def governance_page(
        self,
        *,
        status: str | None = None,
        owner: str | None = None,
        overdue: bool | None = None,
        environment: str | None = None,
        execution_profile: str | None = None,
        case_path: str | None = None,
        keyword: str | None = None,
        cursor: str | None = None,
        page_size: int = 50,
    ) -> Page[GovernanceListItem]:
        if page_size < 1 or page_size > 100:
            raise FlakyStoreError("invalid_page_size", "page_size must be between 1 and 100")
        filters: list[str] = []
        parameters: list[object] = []
        fixed = {
            "status": ("governance.status", status),
            "owner": ("governance.owner", owner),
            "environment": ("identity.environment", environment),
            "execution_profile": ("identity.execution_profile", execution_profile),
        }
        for _name, (column, value) in fixed.items():
            if value is not None:
                filters.append(f"{column} = ?")
                parameters.append(_bounded_text(value, 128))
        if overdue is True:
            filters.append("governance.status IN ('ACTIVE','RECOVERING')")
            filters.append("governance.expires_at <= ?")
        elif overdue is False:
            filters.append(
                "NOT (governance.status IN ('ACTIVE','RECOVERING') AND governance.expires_at <= ?)"
            )
        if overdue is not None:
            parameters.append(None)
        if case_path is not None:
            value = _bounded_text(case_path, 128).replace("\\", "/").rstrip("/")
            escaped = _escape_like(value)
            filters.append(
                "(identity.case_id = ? OR identity.case_id LIKE ? ESCAPE '\\' "
                "OR identity.case_id LIKE ? ESCAPE '\\')"
            )
            parameters.extend((value, f"{escaped}::%", f"{escaped}/%"))
        if keyword is not None:
            value = f"%{_escape_like(_bounded_text(keyword, 128))}%"
            filters.append(
                "(identity.case_id LIKE ? ESCAPE '\\' OR governance.owner LIKE ? ESCAPE '\\')"
            )
            parameters.extend((value, value))
        cursor_value = _decode_cursor(cursor) if cursor else None
        if cursor_value is not None:
            filters.append(
                "(governance.created_at > ? OR "
                "(governance.created_at = ? AND governance.governance_id > ?))"
            )
            parameters.extend(
                (cursor_value[0], cursor_value[0], cursor_value[1])
            )
        where = f"WHERE {' AND '.join(filters)}" if filters else ""
        sql = f"""
            SELECT governance.*, identity.case_id, identity.param_hash,
                   identity.environment, identity.execution_profile,
                   identity.state_epoch, identity.current_detection_generation,
                   latest_attempt.attempt_id AS attempt_id,
                   latest_attempt.status AS attempt_status,
                   latest_attempt.target_commit_sha AS attempt_target_commit_sha,
                   latest_attempt.counted_passes AS attempt_counted_passes,
                   latest_attempt.required_consecutive_passes
                       AS attempt_required_consecutive_passes,
                   latest_attempt.non_counting_runs AS attempt_non_counting_runs,
                   latest_attempt.max_non_counting_runs
                       AS attempt_max_non_counting_runs,
                   (SELECT MAX(evidence_time) FROM (
                        SELECT observation.observed_at AS evidence_time
                        FROM flaky_normal_observation AS observation
                        WHERE observation.flaky_key = identity.flaky_key
                        UNION ALL
                        SELECT evidence.trusted_started_at AS evidence_time
                        FROM flaky_probe_evidence AS evidence
                        JOIN flaky_verification_attempt AS evidence_attempt
                          ON evidence_attempt.attempt_id = evidence.attempt_id
                        WHERE evidence_attempt.governance_id = governance.governance_id
                   )) AS latest_evidence_at
            FROM flaky_governance AS governance
            JOIN flaky_identity AS identity USING (flaky_key)
            LEFT JOIN flaky_verification_attempt AS latest_attempt
              ON latest_attempt.attempt_id = (
                   SELECT attempt.attempt_id
                   FROM flaky_verification_attempt AS attempt
                   WHERE attempt.governance_id = governance.governance_id
                   ORDER BY attempt.attempt_no DESC LIMIT 1
              )
            {where}
            ORDER BY governance.created_at, governance.governance_id
            LIMIT ?
        """
        with self._transaction() as (connection, _schema, data_as_of):
            parameters = [
                data_as_of.isoformat() if value is None else value
                for value in parameters
            ]
            rows = connection.execute(sql, (*parameters, page_size + 1)).fetchall()
            page_rows = rows[:page_size]
            projections = _page_projections(connection, page_rows)
            items = tuple(
                _governance_item(
                    row,
                    data_as_of,
                    projections.get(str(row["flaky_key"]), ()),
                )
                for row in page_rows
            )
            next_cursor = None
            if len(rows) > page_size:
                last = page_rows[-1]
                next_cursor = _encode_cursor(last["created_at"], last["governance_id"])
            return Page(
                items=items,
                next_cursor=next_cursor,
                page_size=page_size,
                data_as_of=data_as_of,
            )

    def case_detail(self, flaky_key: str) -> FlakyCaseDetail:
        key = _bounded_text(flaky_key, 256)
        with self._transaction() as (connection, _schema, data_as_of):
            identity = connection.execute(
                "SELECT * FROM flaky_identity WHERE flaky_key = ?", (key,)
            ).fetchone()
            if identity is None:
                raise FlakyStoreError("identity_not_found", "Flaky identity does not exist")
            projections = _rows(
                connection,
                """SELECT * FROM flaky_detection_projection WHERE flaky_key = ?
                   ORDER BY detection_generation, comparability_fingerprint""",
                (key,),
            )
            governance = _rows(
                connection,
                """SELECT * FROM flaky_governance WHERE flaky_key = ?
                   ORDER BY created_at, governance_id""",
                (key,),
            )
            attempts = _rows(
                connection,
                """SELECT attempt.* FROM flaky_verification_attempt AS attempt
                   JOIN flaky_governance AS governance
                     ON governance.governance_id = attempt.governance_id
                   WHERE governance.flaky_key = ?
                   ORDER BY attempt.started_at, attempt.attempt_id""",
                (key,),
            )
            normal = _rows(
                connection,
                """SELECT * FROM flaky_normal_observation WHERE flaky_key = ?
                   ORDER BY observed_at, observation_id""",
                (key,),
            )
            probe = _rows(
                connection,
                """SELECT evidence.* FROM flaky_probe_evidence AS evidence
                   JOIN flaky_verification_attempt AS attempt
                     ON attempt.attempt_id = evidence.attempt_id
                   JOIN flaky_governance AS governance
                     ON governance.governance_id = attempt.governance_id
                   WHERE governance.flaky_key = ?
                   ORDER BY evidence.trusted_started_at, evidence.evidence_id""",
                (key,),
            )
            timeline: list[TimelineItem] = []
            for row in attempts:
                timeline.append(
                    TimelineItem(
                        occurred_at=_time(row["started_at"]),
                        event_kind="verification_attempt",
                        event_id=str(row["attempt_id"]),
                        payload=row,
                    )
                )
            for row in normal:
                timeline.append(
                    TimelineItem(
                        occurred_at=_time(row["observed_at"]),
                        event_kind="normal_evidence",
                        event_id=str(row["observation_id"]),
                        payload=row,
                    )
                )
            for row in _rows(
                connection,
                """SELECT * FROM flaky_governance_event WHERE governance_id IN (
                       SELECT governance_id FROM flaky_governance WHERE flaky_key = ?)
                   ORDER BY created_at, event_id""",
                (key,),
            ):
                timeline.append(
                    TimelineItem(
                        occurred_at=_time(row["created_at"]),
                        event_kind="governance_event",
                        event_id=str(row["event_id"]),
                        payload=row,
                    )
                )
            for row in _rows(
                connection,
                """SELECT * FROM flaky_detection_transition WHERE flaky_key = ?
                   ORDER BY created_at, transition_id""",
                (key,),
            ):
                timeline.append(
                    TimelineItem(
                        occurred_at=_time(row["created_at"]),
                        event_kind="detection_transition",
                        event_id=str(row["transition_id"]),
                        payload=row,
                    )
                )
            for row in probe:
                timeline.append(
                    TimelineItem(
                        occurred_at=_time(row["trusted_started_at"]),
                        event_kind="probe_evidence",
                        event_id=str(row["evidence_id"]),
                        payload=row,
                    )
                )
            ordered = tuple(
                sorted(
                    timeline,
                    key=lambda item: (
                        item.occurred_at,
                        item.event_kind,
                        item.event_id,
                    ),
                )
            )
            return FlakyCaseDetail(
                data_as_of=data_as_of,
                identity=dict(identity),
                projections=projections,
                governance=governance,
                attempts=attempts,
                normal_evidence=normal,
                probe_evidence=probe,
                timeline=ordered,
            )

    def snapshot_candidates(self) -> tuple[SnapshotCandidate, ...]:
        return self.snapshot_source().candidates

    def snapshot_source(self) -> SnapshotSource:
        with self._transaction() as (connection, schema, data_as_of):
            rows = connection.execute(
                """SELECT identity.flaky_key, identity.case_id, identity.param_hash,
                          identity.environment, identity.execution_profile,
                          identity.state_epoch, governance.governance_id,
                          governance.status AS governance_status,
                          governance.expires_at
                   FROM flaky_governance AS governance
                   JOIN flaky_identity AS identity USING (flaky_key)
                   WHERE governance.status IN ('ACTIVE','RECOVERING')
                   ORDER BY identity.flaky_key"""
            ).fetchall()
            return SnapshotSource(
                database_schema_version=schema,
                data_as_of=data_as_of,
                candidates=tuple(SnapshotCandidate(**dict(row)) for row in rows),
            )

    def run_decisions(
        self, run_id: str, artifact_directory: str | Path
    ) -> RunDecisionSummary:
        from quality.flaky_shadow import read_decision_plan

        path = Path(artifact_directory) / "flaky-skip-decisions.json"
        if not path.is_file():
            raise FlakyStoreError("run_not_found", "Run decision artifact does not exist")
        plan = read_decision_plan(
            path,
            expected_run_id=_bounded_text(run_id, 256),
        )
        return RunDecisionSummary(
            run_id=plan.run_id,
            snapshot_id=plan.snapshot_id,
            mode_requested=plan.mode_requested,
            mode_effective=plan.mode_effective,
            run_count=plan.run_count,
            would_skip_count=plan.would_skip_count,
            skip_count=plan.skip_count,
            fail_open_count=plan.fail_open_count,
            reason_counts=plan.reason_counts,
        )

    @contextmanager
    def _transaction(self) -> Iterator[tuple[sqlite3.Connection, int, datetime]]:
        with self.repository.connection(require_existing=True, read_only=True) as connection:
            connection.execute("BEGIN")
            try:
                schema_version = _validate_read_schema(
                    connection,
                    self.repository,
                    self.migrations_directory,
                )
                data_as_of = self._clock()
                if data_as_of.tzinfo is None or data_as_of.utcoffset() is None:
                    raise FlakyStoreError(
                        "invalid_clock", "read service clock must be timezone-aware"
                    )
                yield connection, schema_version, data_as_of
            finally:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")


def _counts(connection: sqlite3.Connection, sql: str) -> dict[str, int]:
    return {
        str(row["state"]): int(row["count"])
        for row in connection.execute(sql).fetchall()
    }


def _validate_read_schema(
    connection: sqlite3.Connection,
    repository: FlakyRepository,
    migrations_directory: Path,
) -> int:
    migrations = load_migrations(migrations_directory)
    applied = repository.read_applied_migrations(connection)
    validate_applied_migrations(applied, migrations)
    pending = [item for item in migrations if item.version not in applied]
    if pending:
        raise FlakyStoreError(
            "schema_migration_required",
            f"database schema requires migration {pending[0].version}",
        )
    return max(applied, default=0)


def _rows(
    connection: sqlite3.Connection, sql: str, parameters: tuple[object, ...]
) -> tuple[dict[str, object], ...]:
    return tuple(dict(row) for row in connection.execute(sql, parameters).fetchall())


def _governance_item(
    row: sqlite3.Row,
    now: datetime,
    projections: tuple[DetectionProjectionSummary, ...],
) -> GovernanceListItem:
    states = tuple(sorted({item.detection_state for item in projections}))
    return GovernanceListItem(
        governance_id=row["governance_id"],
        row_version=int(row["row_version"]),
        flaky_key=row["flaky_key"],
        case_id=row["case_id"],
        param_hash=row["param_hash"],
        environment=row["environment"],
        execution_profile=row["execution_profile"],
        state_epoch=int(row["state_epoch"]),
        detection_generation=int(row["current_detection_generation"]),
        detection_states=states or ("UNOBSERVED",),
        detection_projections=projections,
        governance_status=row["status"],
        attempt_id=row["attempt_id"],
        attempt_status=row["attempt_status"],
        attempt_target_commit_sha=row["attempt_target_commit_sha"],
        attempt_counted_passes=(
            int(row["attempt_counted_passes"])
            if row["attempt_counted_passes"] is not None
            else None
        ),
        attempt_required_consecutive_passes=(
            int(row["attempt_required_consecutive_passes"])
            if row["attempt_required_consecutive_passes"] is not None
            else None
        ),
        attempt_non_counting_runs=(
            int(row["attempt_non_counting_runs"])
            if row["attempt_non_counting_runs"] is not None
            else None
        ),
        attempt_max_non_counting_runs=(
            int(row["attempt_max_non_counting_runs"])
            if row["attempt_max_non_counting_runs"] is not None
            else None
        ),
        owner=row["owner"],
        reason=row["reason"],
        created_at=_time(row["created_at"]),
        expires_at=_time(row["expires_at"]),
        overdue=(
            row["status"] in {"ACTIVE", "RECOVERING"}
            and _time(row["expires_at"]) <= now
        ),
        latest_evidence_at=(
            _time(row["latest_evidence_at"])
            if row["latest_evidence_at"] is not None
            else None
        ),
        normalized_case_path=_case_path(row["case_id"]),
    )


def _case_path(case_id: object) -> str | None:
    value = unicodedata.normalize("NFC", str(case_id)).split("::", 1)[0]
    value = value.replace("\\", "/")
    parts = value.split("/")
    if not value or Path(value).is_absolute() or any(part in {"", ".", ".."} for part in parts):
        return None
    return "/".join(parts)


def _page_projections(
    connection: sqlite3.Connection,
    rows: list[sqlite3.Row],
) -> dict[str, tuple[DetectionProjectionSummary, ...]]:
    if not rows:
        return {}
    keys = tuple(str(row["flaky_key"]) for row in rows)
    placeholders = ",".join("?" for _ in keys)
    result: dict[str, list[DetectionProjectionSummary]] = defaultdict(list)
    projection_rows = connection.execute(
        f"""SELECT projection.flaky_key,
                   projection.comparability_fingerprint,
                   projection.detection_state,
                   projection.stable_outcome
            FROM flaky_detection_projection AS projection
            JOIN flaky_identity AS identity USING (flaky_key)
            WHERE projection.flaky_key IN ({placeholders})
              AND projection.detection_generation = identity.current_detection_generation
            ORDER BY projection.flaky_key, projection.comparability_fingerprint""",
        keys,
    ).fetchall()
    for row in projection_rows:
        result[str(row["flaky_key"])].append(
            DetectionProjectionSummary(
                comparability_fingerprint=str(row["comparability_fingerprint"]),
                detection_state=str(row["detection_state"]),
                stable_outcome=(
                    str(row["stable_outcome"])
                    if row["stable_outcome"] is not None
                    else None
                ),
            )
        )
    return {key: tuple(values) for key, values in result.items()}


def _bounded_text(value: object, limit: int) -> str:
    text = str(value).strip()
    if not text or len(text) > limit:
        raise FlakyStoreError("invalid_query", f"query text must contain 1..{limit} characters")
    return text


def _requested_mode(value: object) -> str:
    text = str(value).strip().casefold()
    if not text:
        raise FlakyStoreError("invalid_query", "mode_requested must not be empty")
    return text


def _effective_mode(value: object) -> str:
    text = str(value).strip().casefold()
    if text not in {"off", "shadow", "enforce"}:
        raise FlakyStoreError(
            "invalid_query",
            "mode_effective must be off, shadow, or enforce",
        )
    return text


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _encode_cursor(created_at: str, governance_id: str) -> str:
    raw = json.dumps([created_at, governance_id], separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(value: str) -> tuple[str, str]:
    try:
        padded = value + "=" * (-len(value) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode())
        if not isinstance(payload, list) or len(payload) != 2:
            raise ValueError
        return _bounded_text(payload[0], 64), _bounded_text(payload[1], 256)
    except (ValueError, UnicodeError, json.JSONDecodeError) as error:
        raise FlakyStoreError("invalid_cursor", "cursor is invalid") from error


def _time(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise FlakyStoreError("invalid_database_time", "database time must include timezone")
    return parsed


__all__ = (
    "DashboardSummary",
    "DetectionProjectionSummary",
    "FlakyCaseDetail",
    "FlakyReadService",
    "GovernanceListItem",
    "Page",
    "RunDecisionSummary",
    "SnapshotCandidate",
    "SnapshotSource",
    "TimelineItem",
)
