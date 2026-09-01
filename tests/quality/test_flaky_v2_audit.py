from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from quality.flaky_importer import FlakyImportRequest, import_flaky_history
from quality.flaky_store import FlakyStore
from quality.flaky_v2_audit import (
    FlakyV2AuditError,
    audit_flaky_v2_database,
    main,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_v2_audit_is_read_only_and_redacts_job_names(
    p0_artifact_factory,
    tmp_path,
):
    artifacts = p0_artifact_factory(job_name="private-folder/private-job")
    database_path = (tmp_path / "flaky-v2.sqlite3").resolve()
    result = import_flaky_history(
        FlakyImportRequest(
            run_id=artifacts.run.run_id,
            quality_output_dir=artifacts.output_dir,
            database_path=database_path,
        )
    )
    assert result.inserted_count == 1
    FlakyStore(database_path).evaluate_run(artifacts.run.run_id)
    connection = sqlite3.connect(database_path)
    state_row = connection.execute(
        "SELECT flaky_key, latest_observation_id, latest_observed_at "
        "FROM flaky_state"
    ).fetchone()
    flaky_key, observation_id, observed_at = state_row
    connection.execute(
        "INSERT INTO flaky_governance ("
        "governance_id, flaky_key, status, owner, reason, created_by, "
        "created_at, expires_at, recovery_started_by, recovery_started_at, "
        "recovery_reason, recovery_anchor_observation_id"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "governance-v1-recovering",
            flaky_key,
            "RECOVERING",
            "private-owner",
            "private-reason",
            "private-actor",
            "2026-08-01T00:00:00.000000Z",
            "2026-08-02T00:00:00.000000Z",
            "private-recovery-actor",
            "2026-08-01T01:30:00.000000Z",
            "private-recovery-reason",
            observation_id,
        ),
    )
    connection.execute(
        "INSERT INTO flaky_governance ("
        "governance_id, flaky_key, status, owner, reason, created_by, "
        "created_at, expires_at, recovery_started_by, recovery_started_at, "
        "recovery_reason, recovery_anchor_observation_id, closed_at, resolution"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "governance-v1-closed",
            flaky_key,
            "CLOSED",
            "private-closed-owner",
            "private-closed-reason",
            "private-closed-actor",
            "2026-07-01T00:00:00.000000Z",
            "2026-07-02T00:00:00.000000Z",
            "private-closed-recovery-actor",
            "2026-07-01T01:00:00.000000Z",
            "private-closed-recovery-reason",
            observation_id,
            "2026-07-01T02:00:00.000000Z",
            "recovered",
        ),
    )
    connection.commit()
    connection.close()
    before = _sha256(database_path)

    report = audit_flaky_v2_database(database_path)

    assert _sha256(database_path) == before
    assert report["database"]["opened_read_only"] is True
    assert report["integrity"]["quick_check"] == "ok"
    assert report["counts"]["flaky_import_run"] == 1
    assert report["legacy_review"]["v2_runs_to_mark_legacy_unknown"] == 1
    assert report["records"]["states"] == [
        {
            "flaky_key": flaky_key,
            "current_state": "OBSERVING",
            "detected_state": "OBSERVING",
            "stable_outcome": None,
            "projection_status": "CURRENT",
            "latest_evidence": {
                "observation_id": observation_id,
                "run_id": artifacts.run.run_id,
                "outcome": "pass",
                "failure_category": None,
                "observed_at": observed_at,
            },
        }
    ]
    governance = {
        item["governance_id"]: item
        for item in report["records"]["governances"]
    }
    recovering = governance["governance-v1-recovering"]
    assert recovering["owner_id"].startswith("owner-sha256-")
    assert recovering["created_by_id"].startswith("actor-sha256-")
    assert recovering["expires_at"] == "2026-08-02T00:00:00.000000Z"
    assert recovering["recovery_anchor_observation_id"] == observation_id
    assert recovering["resolution"] is None
    assert recovering["reason_redacted"] is True
    closed = governance["governance-v1-closed"]
    assert closed["recovery_anchor_observation_id"] == observation_id
    assert closed["closed_at"] == "2026-07-01T02:00:00.000000Z"
    assert closed["resolution"] == "recovered"
    serialized = json.dumps(report, ensure_ascii=False, sort_keys=True)
    assert "private-folder" not in serialized
    assert "private-owner" not in serialized
    assert "private-reason" not in serialized
    assert "private-actor" not in serialized
    assert "private-closed-owner" not in serialized
    assert "private-closed-reason" not in serialized
    assert "private-closed-actor" not in serialized
    assert str(database_path) not in serialized
    assert list(report["distributions"]["run_job"])[0].startswith(
        "job-sha256-"
    )


def test_v2_audit_rejects_incomplete_schema_without_modifying_it(tmp_path):
    database_path = (tmp_path / "not-flaky.sqlite3").resolve()
    connection = sqlite3.connect(database_path)
    connection.execute("CREATE TABLE unrelated (value TEXT)")
    connection.commit()
    connection.close()
    before = _sha256(database_path)

    with pytest.raises(FlakyV2AuditError, match="missing tables"):
        audit_flaky_v2_database(database_path)

    assert _sha256(database_path) == before


def test_v2_audit_cli_writes_structured_report(
    p0_artifact_factory,
    tmp_path,
):
    artifacts = p0_artifact_factory()
    database_path = (tmp_path / "flaky-v2.sqlite3").resolve()
    output_path = tmp_path / "audit.json"
    import_flaky_history(
        FlakyImportRequest(
            run_id=artifacts.run.run_id,
            quality_output_dir=artifacts.output_dir,
            database_path=database_path,
        )
    )

    assert main(["--db", str(database_path), "--output", str(output_path)]) == 0
    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["schema_version"] == "quality.flaky-v2-audit.v1"
    assert report["integrity"]["foreign_key_violation_count"] == 0
