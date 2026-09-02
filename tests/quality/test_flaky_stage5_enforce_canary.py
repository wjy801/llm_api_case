from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import run_master

from master_service import collect_test_case_items
from quality.config import QualityRuntimeConfig
from quality.flaky_enforce_gate import (
    CANARY_CASE_COUNT,
    CANARY_RELATIVE_PATH,
    evaluate_enforce_gate,
    main,
    prepare_nonproduction_canary,
)
from quality.flaky_read import FlakyReadService
from quality.flaky_shadow import (
    build_decision_plan,
    generate_snapshot,
    reconcile_decision_plan,
    write_decision_plan,
    write_reconciliation,
    write_snapshot,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)


def _prepare(tmp_path):
    database = (tmp_path / "stage5-canary.sqlite3").resolve()
    result = prepare_nonproduction_canary(
        database,
        repository_root=PROJECT_ROOT,
        environment="overseas",
        execution_profile="parallel",
        now=NOW,
    )
    cases = tuple(collect_test_case_items(PROJECT_ROOT / CANARY_RELATIVE_PATH))
    return database, result, cases


def _artifacts(
    tmp_path,
    database,
    cases,
    *,
    run_id,
    enabled,
    collect_only,
):
    output = tmp_path / run_id
    mode = "enforce"
    config = QualityRuntimeConfig(
        enabled=True,
        run_id=run_id,
        execution_id=None,
        output_dir=output,
        flaky_database_path=database,
        flaky_auto_skip_enabled=enabled,
        flaky_skip_mode_requested=mode,
        flaky_skip_mode_effective=mode if enabled else "off",
    )
    snapshot = generate_snapshot(
        config,
        run_id=run_id,
        branch="dev3",
        repository_root=PROJECT_ROOT,
        now=NOW + timedelta(minutes=1),
    )
    plan = build_decision_plan(
        snapshot,
        cases,
        run_id=run_id,
        branch="dev3",
        environment="overseas",
        execution_profiles={case.nodeid: "parallel" for case in cases},
        collection_started_at=NOW + timedelta(minutes=1, seconds=1),
    )
    if collect_only:
        reconciliation = reconcile_decision_plan(
            plan,
            (),
            collect_only=True,
            now=NOW + timedelta(minutes=2),
        )
    else:
        status = "skipped" if enabled else "passed"
        phase = "setup" if enabled else "call"
        reconciliation = reconcile_decision_plan(
            plan,
            tuple(
                {
                    "nodeid": case.nodeid,
                    "phase": phase,
                    "final_status": status,
                }
                for case in cases
            ),
            now=NOW + timedelta(minutes=2),
        )
    write_snapshot(snapshot, output)
    write_decision_plan(plan, output)
    write_reconciliation(reconciliation, output)
    return output


def test_prepare_canary_creates_six_scoped_active_governances(tmp_path):
    database, result, cases = _prepare(tmp_path)
    source = FlakyReadService(database).snapshot_source()

    assert result.status == "READY"
    assert result.database_schema_version == 4
    assert result.case_count == result.governance_count == CANARY_CASE_COUNT
    assert len(cases) == len(source.candidates) == CANARY_CASE_COUNT
    assert {case.normalized_case_path for case in cases} == {
        CANARY_RELATIVE_PATH.as_posix()
    }
    assert {candidate.execution_profile for candidate in source.candidates} == {
        "parallel"
    }


def test_canary_prepare_requires_ack_and_external_new_database(tmp_path, capsys):
    database = (tmp_path / "blocked.sqlite3").resolve()

    exit_code = main(
        [
            "prepare",
            "--db",
            str(database),
            "--repository-root",
            str(PROJECT_ROOT),
            "--environment",
            "overseas",
            "--execution-profile",
            "parallel",
        ]
    )

    assert exit_code == 2
    assert not database.exists()
    assert "canary_nonproduction_ack_required" in capsys.readouterr().out


def test_gate_accepts_plan_execution_and_kill_switch_rollback(tmp_path):
    database, _prepared, cases = _prepare(tmp_path)
    plan_only = _artifacts(
        tmp_path,
        database,
        cases,
        run_id="stage5-plan",
        enabled=True,
        collect_only=True,
    )
    executed = _artifacts(
        tmp_path,
        database,
        cases,
        run_id="stage5-executed",
        enabled=True,
        collect_only=False,
    )
    rollback = _artifacts(
        tmp_path,
        database,
        cases,
        run_id="stage5-rollback",
        enabled=False,
        collect_only=False,
    )

    planned_gate = evaluate_enforce_gate(
        plan_only,
        run_id="stage5-plan",
        expected_mode="enforce",
        max_skip_count=6,
        require_executed=False,
    )
    executed_gate = evaluate_enforce_gate(
        executed,
        run_id="stage5-executed",
        expected_mode="enforce",
        max_skip_count=6,
        require_executed=True,
    )
    rollback_gate = evaluate_enforce_gate(
        rollback,
        run_id="stage5-rollback",
        expected_mode="off",
        max_skip_count=0,
        require_executed=True,
    )

    assert planned_gate.status == "READY"
    assert planned_gate.planned_skip_count == 6
    assert planned_gate.actual_governance_skip_count == 0
    assert executed_gate.status == "PASSED"
    assert executed_gate.actual_governance_skip_count == 6
    assert rollback_gate.status == "PASSED"
    assert rollback_gate.run_count == 6
    assert rollback_gate.planned_skip_count == 0


def test_gate_requires_rollback_when_planned_skip_is_not_observed(tmp_path):
    database, _prepared, cases = _prepare(tmp_path)
    output = _artifacts(
        tmp_path,
        database,
        cases,
        run_id="stage5-mismatch",
        enabled=True,
        collect_only=True,
    )

    gate = evaluate_enforce_gate(
        output,
        run_id="stage5-mismatch",
        expected_mode="enforce",
        max_skip_count=6,
        require_executed=True,
    )

    assert gate.status == "ROLLBACK_REQUIRED"
    assert gate.rollback_required is True
    assert "gate_reconciliation_degraded" in gate.diagnostic_codes
    assert "gate_actual_skip_mismatch" in gate.diagnostic_codes


def test_real_runner_enforces_six_cases_then_kill_switch_runs_all(
    tmp_path,
    monkeypatch,
):
    database, _prepared, _cases = _prepare(tmp_path)
    enforce_output = (tmp_path / "runner-enforce").resolve()
    enforce_markers = (tmp_path / "markers-enforce").resolve()
    monkeypatch.chdir(PROJECT_ROOT)
    monkeypatch.setenv("BRANCH_NAME", "dev3")
    monkeypatch.setenv("USE_CHINA_ENVIRONMENT", "FALSE")
    monkeypatch.setenv("QUALITY_ENABLE", "1")
    monkeypatch.setenv("QUALITY_SEMANTIC_ENABLE", "0")
    monkeypatch.setenv("QUALITY_METRICS_ENABLE", "0")
    monkeypatch.setenv("QUALITY_FLAKY_HISTORY_ENABLE", "0")
    monkeypatch.setenv("QUALITY_FLAKY_STATE_ENABLE", "0")
    monkeypatch.setenv("QUALITY_FLAKY_DB_PATH", str(database))
    monkeypatch.setenv("QUALITY_FLAKY_AUTO_SKIP_ENABLE", "1")
    monkeypatch.setenv("QUALITY_FLAKY_SKIP_MODE", "enforce")
    monkeypatch.setenv("QUALITY_RUN_ID", "stage5-runner-enforce")
    monkeypatch.setenv("QUALITY_OUTPUT_DIR", str(enforce_output))
    monkeypatch.setenv(
        "QUALITY_FLAKY_ENFORCE_CANARY_MARKER_DIR",
        str(enforce_markers),
    )

    enforce_exit = run_master.run(
        CANARY_RELATIVE_PATH.as_posix(),
        ["-q", f"--alluredir={tmp_path / 'allure-enforce'}"],
        numprocesses="2",
    )
    enforce_gate = evaluate_enforce_gate(
        enforce_output,
        run_id="stage5-runner-enforce",
        expected_mode="enforce",
        max_skip_count=6,
        require_executed=True,
    )

    assert enforce_exit == 0
    assert enforce_gate.status == "PASSED"
    assert enforce_gate.actual_governance_skip_count == 6
    assert not enforce_markers.exists()

    rollback_output = (tmp_path / "runner-rollback").resolve()
    rollback_markers = (tmp_path / "markers-rollback").resolve()
    monkeypatch.setenv("QUALITY_FLAKY_AUTO_SKIP_ENABLE", "0")
    monkeypatch.setenv("QUALITY_RUN_ID", "stage5-runner-rollback")
    monkeypatch.setenv("QUALITY_OUTPUT_DIR", str(rollback_output))
    monkeypatch.setenv(
        "QUALITY_FLAKY_ENFORCE_CANARY_MARKER_DIR",
        str(rollback_markers),
    )

    rollback_exit = run_master.run(
        CANARY_RELATIVE_PATH.as_posix(),
        ["-q", f"--alluredir={tmp_path / 'allure-rollback'}"],
        numprocesses="2",
    )
    rollback_gate = evaluate_enforce_gate(
        rollback_output,
        run_id="stage5-runner-rollback",
        expected_mode="off",
        max_skip_count=0,
        require_executed=True,
    )

    assert rollback_exit == 0
    assert rollback_gate.status == "PASSED"
    assert rollback_gate.actual_governance_skip_count == 0
    assert len(tuple(rollback_markers.glob("*.executed"))) == 6


def test_dedicated_jenkinsfile_keeps_canary_scope_and_rollback_fixed():
    source = (PROJECT_ROOT / "Jenkinsfile.enforce").read_text(encoding="utf-8")

    assert "agent { label 'probe-controller' }" in source
    assert "branches: [[name: '*/dev3']]" in source
    assert "url: 'http://localhost:8929/root/llm_api_case.git'" in source
    assert "credentialsId: 'scm'" in source
    assert "Where-Object { -not $_.TrimStart().StartsWith('--index-url ') }" in source
    assert "--index-url https://pypi.org/simple" in source
    assert "OVERSEAS_TEST_BASE_URL = 'https://offline.invalid'" in source
    assert "OVERSEAS_API_KEY = 'stage5-canary-offline-placeholder'" in source
    assert "Remove-Item -LiteralPath $reports -Recurse -Force" in source
    assert "triggers {" not in source
    assert "SMOKE_TARGET" not in source
    assert source.count("module/smoke/test_flaky_enforce_canary.py") == 3
    assert "QUALITY_FLAKY_AUTO_SKIP_ENABLE = '0'" in source
    assert "$env:QUALITY_FLAKY_AUTO_SKIP_ENABLE = '1'" in source
    assert "$env:QUALITY_FLAKY_AUTO_SKIP_ENABLE = '0'" in source
    assert "--max-skip-count 6" in source
    assert "--max-skip-count 0" in source
    assert "--acknowledge-nonproduction" in source
    assert "Remove-Item -LiteralPath $target -Recurse -Force" in source
