from __future__ import annotations

from datetime import UTC, datetime
import sqlite3
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from quality.flaky_dashboard import create_app, validate_loopback_host
from quality.flaky_probe import CsrfProtector
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


def test_dashboard_is_loopback_only_and_has_only_governance_write_routes(tmp_path):
    assert validate_loopback_host("127.0.0.1") == "127.0.0.1"
    assert validate_loopback_host("::1") == "::1"
    for host in ("localhost", "0.0.0.0", "192.168.1.10"):
        with pytest.raises(ValueError):
            validate_loopback_host(host)

    app = create_app(_database(tmp_path))
    write_routes = {
        route.path
        for route in app.routes
        if "POST" in (getattr(route, "methods", None) or set())
    }
    assert write_routes == {
        "/api/v1/governances/{governance_id}/probe-attempts",
        "/api/v1/probe-attempts/{attempt_id}/merge-and-close",
    }


def test_dashboard_api_html_escaping_head_and_stable_errors(tmp_path):
    database = _database(tmp_path)
    client = TestClient(create_app(database, artifact_directory=tmp_path / "runs"))

    assert client.get("/health/live").json() == {"status": "live"}
    assert client.get("/health/ready").status_code == 200
    summary = client.get("/api/v1/summary")
    assert summary.status_code == 200
    assert summary.json()["database_schema_version"] == 4
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


def test_dashboard_home_is_an_unfolded_governance_workbench(tmp_path):
    client = TestClient(create_app(_database(tmp_path)))

    page = client.get("/")

    assert page.status_code == 200
    assert "Flaky 治理工作台" in page.text
    assert "检测状态" in page.text
    assert "治理状态" in page.text
    assert "待治理用例" in page.text
    assert "module/smoke/test_html.py::test_case" in page.text
    assert "推送修复分支" in page.text
    assert "验证修复提交" in page.text
    assert "允许合并 dev3" in page.text
    assert "合并并自动关闭" in page.text
    assert "验证开关已关闭" in page.text
    assert "<details" not in page.text
    assert "module/smoke/test_pass.py::test_case" not in page.text
    assert "<script>alert(1)</script>" not in page.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page.text
    for hidden_technical_detail in ("数据库", "Schema", "策略", "Shadow 模式"):
        assert hidden_technical_detail not in page.text


def test_dashboard_home_enables_probe_interaction_without_login(tmp_path):
    probe_control = SimpleNamespace(runtime=SimpleNamespace(enabled=True))
    csrf = CsrfProtector(
        b"csrf-secret-material-for-dashboard-demo",
        clock=lambda: datetime(2026, 9, 1, tzinfo=UTC),
    )
    client = TestClient(
        create_app(
            _database(tmp_path),
            probe_control=probe_control,
            csrf_protector=csrf,
        )
    )

    page = client.get("/")

    assert page.status_code == 200
    assert 'data-probe-governance="governance-html"' in page.text
    assert 'id="probe-dialog"' in page.text
    assert 'id="probe-branch"' in page.text
    assert "target_branch: branch.value.trim()" in page.text
    assert "登录" not in page.text
    assert "RBAC" not in page.text
    assert client.cookies.get("flaky_probe_csrf")


def test_ready_attempt_exposes_merge_and_automatic_close_interaction(tmp_path):
    database = _database(tmp_path)
    now = "2026-09-01T00:00:00+00:00"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """UPDATE flaky_governance
               SET status='RECOVERING', row_version=2,
                   recovery_started_by='dashboard-anonymous',
                   recovery_started_at=?, recovery_reason='verify fix'
               WHERE governance_id='governance-html'""",
            (now,),
        )
        connection.execute(
            """INSERT INTO flaky_verification_attempt(
                   attempt_id, governance_id, attempt_no, status, target_commit_sha,
                   policy_revision, required_consecutive_passes, min_interval_minutes,
                   max_non_counting_runs, counted_passes, non_counting_runs,
                   started_by, start_reason, started_at, expires_at, created_at, updated_at
               ) VALUES(
                   'attempt-ready', 'governance-html', 1, 'READY_TO_CLOSE', ?,
                   'flaky-governance.v1', 5, 30, 3, 5, 0,
                   'dashboard-anonymous', 'verify fix', ?, ?, ?, ?
               )""",
            ("a" * 40, now, "2026-09-04T00:00:00+00:00", now, now),
        )

    class FakeMergeClose:
        def execute(self, **kwargs):
            return {
                "schema_version": "quality.flaky-merge-close.v1",
                "status": "CLOSED",
                "merge_status": "MERGED",
                "target_branch": "dev3",
                "target_commit_sha": "a" * 40,
            }

    csrf = CsrfProtector(
        b"csrf-secret-material-for-dashboard-merge",
        clock=lambda: datetime(2026, 9, 1, tzinfo=UTC),
    )
    client = TestClient(
        create_app(
            database,
            merge_close_service=FakeMergeClose(),
            csrf_protector=csrf,
            dashboard_origin="http://dashboard.test",
        ),
        base_url="http://dashboard.test",
    )

    page = client.get("/")
    token = client.cookies.get("flaky_probe_csrf")
    assert 'data-merge-attempt="attempt-ready"' in page.text
    assert "合并 dev3 并关闭" in page.text
    assert "自动调用 CLI" in page.text

    path = "/api/v1/probe-attempts/attempt-ready/merge-and-close"
    headers = {"Origin": "http://dashboard.test", "X-CSRF-Token": token}
    assert client.post(path, json={"row_version": 2}, headers={**headers, "Origin": "http://evil.test"}).status_code == 403
    assert client.post(path, json={"row_version": 2, "extra": True}, headers=headers).status_code == 400
    response = client.post(path, json={"row_version": 2}, headers=headers)
    assert response.status_code == 200
    assert response.json()["status"] == "CLOSED"


def test_dashboard_governance_filter_form_accepts_empty_options(tmp_path):
    client = TestClient(create_app(_database(tmp_path)))

    page = client.get("/governance?keyword=&status=&overdue=")

    assert page.status_code == 200
    assert "module/smoke/test_html.py::test_case" in page.text
    invalid = client.get("/governance?overdue=maybe")
    assert invalid.status_code == 400
    assert invalid.json()["error"]["code"] == "invalid_query"
