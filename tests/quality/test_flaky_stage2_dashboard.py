from __future__ import annotations

from datetime import UTC, datetime
import sqlite3

import pytest
from fastapi.testclient import TestClient

from quality.flaky_dashboard import create_app, validate_loopback_host
from quality.flaky_store import migrate_store


def _database(tmp_path):
    database = (tmp_path / "flaky.sqlite3").resolve()
    migrate_store(database)
    now = "2026-09-01T00:00:00+00:00"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """INSERT INTO flaky_identity (
                   flaky_key, epoch_scope_key, case_id, param_hash,
                   environment, execution_profile, state_epoch,
                   current_detection_generation, created_at, updated_at
               ) VALUES (
                   'flaky-html', 'scope-html',
                   'module/smoke/test_html.py::test_case', 'param-html',
                   'overseas', 'serial', 1, 1, ?, ?
               )""",
            (now, now),
        )
        connection.execute(
            """INSERT INTO flaky_governance (
                   governance_id, flaky_key, status, owner, reason,
                   created_by, created_at, expires_at
               ) VALUES (
                   'governance-html', 'flaky-html', 'ACTIVE',
                   '<script>alert(1)</script>', '<b>reason</b>', 'actor', ?,
                   '2026-10-01T00:00:00+00:00'
               )""",
            (now,),
        )
        for suffix, outcome in (("pass", "pass"), ("fail", "fail")):
            connection.execute(
                """INSERT INTO flaky_identity (
                       flaky_key, epoch_scope_key, case_id, param_hash,
                       environment, execution_profile, state_epoch,
                       current_detection_generation, created_at, updated_at
                   ) VALUES (?, ?, ?, ?, 'overseas', 'serial', 1, 1, ?, ?)""",
                (
                    f"flaky-{suffix}",
                    f"scope-{suffix}",
                    f"module/smoke/test_{suffix}.py::test_case",
                    f"param-{suffix}",
                    now,
                    now,
                ),
            )
            connection.execute(
                """INSERT INTO flaky_detection_projection (
                       flaky_key, detection_generation, comparability_fingerprint,
                       detection_state, sample_size, pass_count, fail_count,
                       outcome_switch_count, signature_switch_count,
                       distinct_failure_fingerprint_count,
                       trailing_same_signature_count, stable_outcome,
                       stable_failure_fingerprint, latest_observation_id,
                       rule_version, created_at, updated_at
                   ) VALUES (?, 1, ?, 'STABLE', 3, ?, ?, 0, 0, ?, 3, ?, ?, ?,
                             'flaky-detection.v1', ?, ?)""",
                (
                    f"flaky-{suffix}",
                    f"fingerprint-{suffix}",
                    3 if outcome == "pass" else 0,
                    3 if outcome == "fail" else 0,
                    1 if outcome == "fail" else 0,
                    outcome,
                    "failure-a" if outcome == "fail" else None,
                    f"observation-{suffix}",
                    now,
                    now,
                ),
            )
    return database


def test_dashboard_is_loopback_only_and_has_no_write_routes(tmp_path):
    assert validate_loopback_host("127.0.0.1") == "127.0.0.1"
    assert validate_loopback_host("::1") == "::1"
    for host in ("localhost", "0.0.0.0", "192.168.1.10"):
        with pytest.raises(ValueError):
            validate_loopback_host(host)

    app = create_app(_database(tmp_path))
    methods = {
        method
        for route in app.routes
        for method in (getattr(route, "methods", None) or set())
    }
    assert methods <= {"GET", "HEAD"}


def test_dashboard_api_html_escaping_head_and_stable_errors(tmp_path):
    database = _database(tmp_path)
    client = TestClient(create_app(database, artifact_directory=tmp_path / "runs"))

    assert client.get("/health/live").json() == {"status": "live"}
    assert client.get("/health/ready").status_code == 200
    summary = client.get("/api/v1/summary")
    assert summary.status_code == 200
    assert summary.json()["database_schema_version"] == 3
    assert client.head("/api/v1/summary").status_code == 200

    page = client.get("/governance")
    assert page.status_code == 200
    assert "<script>alert(1)</script>" not in page.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page.text

    invalid = client.get("/api/v1/governance?page_size=101")
    assert invalid.status_code == 400
    assert invalid.json()["error"]["code"] == "invalid_query"
    unknown = client.get("/api/v1/governance?sort=owner")
    assert unknown.status_code == 400
    missing = client.get("/api/v1/cases/missing")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "identity_not_found"
    assert str(database) not in missing.text
    missing_run = client.get("/api/v1/runs/missing/decisions")
    assert missing_run.status_code == 404
    assert missing_run.json()["error"]["code"] == "run_not_found"

    stable_pass = client.get("/cases/flaky-pass")
    stable_fail = client.get("/cases/flaky-fail")
    assert "稳定通过" in stable_pass.text
    assert "稳定失败" in stable_fail.text
    assert client.get("/api/v1/cases/flaky-pass").json()["projections"][0][
        "stable_outcome"
    ] == "pass"
