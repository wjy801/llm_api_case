from __future__ import annotations

import argparse
from collections.abc import Callable
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any

from quality.flaky_store.migration import MIGRATIONS_DIRECTORY
from quality.storage import write_json_atomic


FLAKY_V2_AUDIT_SCHEMA_VERSION = "quality.flaky-v2-audit.v1"

_REQUIRED_TABLES = frozenset(
    {
        "schema_migration",
        "flaky_import_run",
        "flaky_case_epoch",
        "case_observation",
        "flaky_override",
        "flaky_state",
        "flaky_transition",
        "flaky_governance",
    }
)


class FlakyV2AuditError(RuntimeError):
    pass


def audit_flaky_v2_database(database_path: str | Path) -> dict[str, Any]:
    path = Path(database_path)
    if not path.is_absolute():
        raise FlakyV2AuditError("database path must be absolute")
    if not path.is_file():
        raise FlakyV2AuditError("database file does not exist")

    connection = sqlite3.connect(
        f"{path.resolve().as_uri()}?mode=ro",
        uri=True,
        timeout=5,
    )
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only = ON")
        tables = _table_names(connection)
        missing_tables = sorted(_REQUIRED_TABLES - tables)
        if missing_tables:
            raise FlakyV2AuditError(
                "database is not a complete v2 store; missing tables: "
                + ", ".join(missing_tables)
            )

        quick_check_rows = [
            str(row[0]) for row in connection.execute("PRAGMA quick_check")
        ]
        foreign_key_violation_count = len(
            connection.execute("PRAGMA foreign_key_check").fetchall()
        )
        counts = {
            table: _scalar(connection, f"SELECT COUNT(*) FROM {table}")
            for table in (
                "flaky_import_run",
                "case_observation",
                "flaky_state",
                "flaky_transition",
                "flaky_governance",
            )
        }
        consistency = _consistency_checks(connection)
        active_governance_count = _scalar(
            connection,
            "SELECT COUNT(*) FROM flaky_governance "
            "WHERE status IN ('ACTIVE', 'RECOVERING')",
        )
        recovering_ids = [
            _stable_audit_id("governance", row[0])
            for row in connection.execute(
                "SELECT governance_id FROM flaky_governance "
                "WHERE status = 'RECOVERING' ORDER BY governance_id"
            )
        ]

        return {
            "schema_version": FLAKY_V2_AUDIT_SCHEMA_VERSION,
            "database": {
                "size_bytes": path.stat().st_size,
                "sha256": _file_sha256(path),
                "path_redacted": True,
                "opened_read_only": True,
            },
            "integrity": {
                "quick_check": (
                    "ok" if quick_check_rows == ["ok"] else "failed"
                ),
                "quick_check_result_count": len(quick_check_rows),
                "foreign_key_violation_count": foreign_key_violation_count,
                "migrations": _migration_audit(connection),
            },
            "counts": counts,
            "distributions": {
                "run_source_kind": _distribution(
                    connection, "flaky_import_run", "source_kind"
                ),
                "run_job": _distribution(
                    connection,
                    "flaky_import_run",
                    "job_name",
                    transform=lambda value: _stable_audit_id("job", value),
                ),
                "run_branch": _distribution(
                    connection, "flaky_import_run", "branch"
                ),
                "run_environment": _distribution(
                    connection, "flaky_import_run", "environment"
                ),
                "run_status": _distribution(
                    connection, "flaky_import_run", "run_status"
                ),
                "run_integrity": _distribution(
                    connection, "flaky_import_run", "p0_integrity_status"
                ),
                "observation_profile": _distribution(
                    connection, "case_observation", "execution_profile"
                ),
                "observation_outcome": _distribution(
                    connection, "case_observation", "observation_outcome"
                ),
                "observation_failure_category": _distribution(
                    connection, "case_observation", "failure_category"
                ),
                "state_current": _distribution(
                    connection, "flaky_state", "current_state"
                ),
                "state_detected": _distribution(
                    connection, "flaky_state", "detected_state"
                ),
                "state_projection": _distribution(
                    connection, "flaky_state", "projection_status"
                ),
                "governance_status": _distribution(
                    connection, "flaky_governance", "status"
                ),
                "governance_resolution": _distribution(
                    connection, "flaky_governance", "resolution"
                ),
            },
            "records": {
                "states": _state_records(connection),
                "governances": _governance_records(connection),
            },
            "consistency": consistency,
            "legacy_review": {
                "v2_runs_to_mark_legacy_unknown": counts["flaky_import_run"],
                "active_governance_count": active_governance_count,
                "recovering_governance_ids": recovering_ids,
                "infrastructure_observation_count": _scalar(
                    connection,
                    "SELECT COUNT(*) FROM case_observation "
                    "WHERE failure_category IN "
                    "('FRAMEWORK_DEFECT', 'ENVIRONMENT', 'CONFIGURATION', 'TRANSIENT')",
                ),
                "unknown_failure_observation_count": _scalar(
                    connection,
                    "SELECT COUNT(*) FROM case_observation "
                    "WHERE failure_category = 'UNKNOWN'",
                ),
                "requires_manual_record_review": bool(
                    recovering_ids
                    or foreign_key_violation_count
                    or any(consistency.values())
                ),
            },
        }
    finally:
        connection.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit a copied Flaky v2 SQLite database without modifying it."
    )
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        report = audit_flaky_v2_database(args.db)
        if args.output is not None:
            write_json_atomic(args.output, report)
        else:
            print(
                json.dumps(
                    report,
                    allow_nan=False,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
            )
    except (FlakyV2AuditError, OSError, sqlite3.Error) as error:
        print(f"Flaky v2 audit failed: {error}", file=sys.stderr)
        return 2
    return 0


def _table_names(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    }


def _migration_audit(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    expected = {
        int(path.name.split("_", 1)[0]): (
            path.name,
            hashlib.sha256(
                path.read_text(encoding="utf-8").encode("utf-8")
            ).hexdigest(),
        )
        for path in MIGRATIONS_DIRECTORY.glob("*.sql")
    }
    records = []
    for row in connection.execute(
        "SELECT version, name, checksum FROM schema_migration ORDER BY version"
    ):
        version = int(row["version"])
        expected_record = expected.get(version)
        records.append(
            {
                "version": version,
                "name": str(row["name"]),
                "checksum_matches_repository": bool(
                    expected_record
                    and expected_record[0] == row["name"]
                    and expected_record[1] == row["checksum"]
                ),
            }
        )
    return records


def _distribution(
    connection: sqlite3.Connection,
    table: str,
    column: str,
    *,
    transform: Callable[[str], str] | None = None,
) -> dict[str, int]:
    values: dict[str, int] = {}
    for row in connection.execute(
        f"SELECT {column}, COUNT(*) AS count FROM {table} "
        f"GROUP BY {column} ORDER BY {column}"
    ):
        value = "<NULL>" if row[0] is None else str(row[0])
        if transform is not None and value != "<NULL>":
            value = transform(value)
        values[value] = int(row[1])
    return values


def _consistency_checks(connection: sqlite3.Connection) -> dict[str, int]:
    checks = {
        "missing_projection": (
            "SELECT COUNT(DISTINCT observation.flaky_key) "
            "FROM case_observation AS observation "
            "LEFT JOIN flaky_state AS state "
            "ON state.flaky_key = observation.flaky_key "
            "WHERE state.flaky_key IS NULL"
        ),
        "stale_projection": (
            "SELECT COUNT(*) FROM flaky_state WHERE projection_status = 'STALE'"
        ),
        "orphan_transition": (
            "SELECT COUNT(*) FROM flaky_transition AS transition_record "
            "LEFT JOIN flaky_state AS state "
            "ON state.flaky_key = transition_record.flaky_key "
            "WHERE state.flaky_key IS NULL"
        ),
        "orphan_governance": (
            "SELECT COUNT(*) FROM flaky_governance AS governance "
            "LEFT JOIN flaky_state AS state "
            "ON state.flaky_key = governance.flaky_key "
            "WHERE state.flaky_key IS NULL"
        ),
        "duplicate_open_governance": (
            "SELECT COUNT(*) FROM ("
            "SELECT flaky_key FROM flaky_governance "
            "WHERE status IN ('ACTIVE', 'RECOVERING') "
            "GROUP BY flaky_key HAVING COUNT(*) > 1)"
        ),
        "state_latest_observation_mismatch": (
            "SELECT COUNT(*) FROM flaky_state AS state "
            "LEFT JOIN case_observation AS observation "
            "ON observation.observation_id = state.latest_observation_id "
            "AND observation.flaky_key = state.flaky_key "
            "WHERE observation.observation_id IS NULL"
        ),
        "closed_without_resolution": (
            "SELECT COUNT(*) FROM flaky_governance "
            "WHERE status = 'CLOSED' "
            "AND (resolution IS NULL OR closed_at IS NULL)"
        ),
    }
    return {name: _scalar(connection, sql) for name, sql in checks.items()}


def _state_records(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    records = []
    for row in connection.execute(
        "SELECT state.flaky_key, state.current_state, state.detected_state, "
        "state.stable_outcome, state.projection_status, "
        "observation.observation_id, observation.run_id, "
        "observation.observation_outcome, observation.failure_category, "
        "observation.observed_at "
        "FROM flaky_state AS state "
        "LEFT JOIN case_observation AS observation "
        "ON observation.observation_id = state.latest_observation_id "
        "AND observation.flaky_key = state.flaky_key "
        "ORDER BY state.flaky_key"
    ):
        records.append(
            {
                "flaky_key": str(row["flaky_key"]),
                "current_state": str(row["current_state"]),
                "detected_state": str(row["detected_state"]),
                "stable_outcome": row["stable_outcome"],
                "projection_status": str(row["projection_status"]),
                "latest_evidence": {
                    "observation_id": row["observation_id"],
                    "run_id": row["run_id"],
                    "outcome": row["observation_outcome"],
                    "failure_category": row["failure_category"],
                    "observed_at": row["observed_at"],
                },
            }
        )
    return records


def _governance_records(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    records = []
    for row in connection.execute(
        "SELECT governance_id, flaky_key, status, owner, created_by, "
        "created_at, expires_at, recovery_started_at, "
        "recovery_anchor_observation_id, closed_at, resolution "
        "FROM flaky_governance ORDER BY governance_id"
    ):
        records.append(
            {
                "governance_id": str(row["governance_id"]),
                "flaky_key": str(row["flaky_key"]),
                "status": str(row["status"]),
                "owner_id": _stable_audit_id("owner", row["owner"]),
                "created_by_id": _stable_audit_id("actor", row["created_by"]),
                "created_at": str(row["created_at"]),
                "expires_at": str(row["expires_at"]),
                "recovery_started_at": row["recovery_started_at"],
                "recovery_anchor_observation_id": row[
                    "recovery_anchor_observation_id"
                ],
                "closed_at": row["closed_at"],
                "resolution": row["resolution"],
                "reason_redacted": True,
            }
        )
    return records


def _scalar(connection: sqlite3.Connection, sql: str) -> int:
    row = connection.execute(sql).fetchone()
    if row is None:
        raise FlakyV2AuditError("audit query returned no result")
    return int(row[0])


def _stable_audit_id(kind: str, value: str) -> str:
    digest = hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:12]
    return f"{kind}-sha256-{digest}"


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
