from __future__ import annotations

from datetime import UTC, datetime, timedelta
import os
from pathlib import Path

from master_service import CollectedTestCase
from quality.config import QualityRuntimeConfig, load_quality_config
from quality.flaky_identity import build_flaky_key
from quality.flaky_read import SnapshotCandidate, SnapshotSource
from quality.flaky_shadow import (
    build_decision_plan,
    generate_snapshot,
    reconcile_decision_plan,
    write_decision_plan,
)
from quality.identifiers import build_param_hash
from quality.storage import read_jsonl


pytest_plugins = ("pytester",)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 9, 2, 8, 0, tzinfo=UTC)


def _config(tmp_path, *, enabled=True, mode="enforce"):
    return QualityRuntimeConfig(
        enabled=True,
        run_id="run-enforce",
        execution_id=None,
        output_dir=tmp_path / "quality-output",
        flaky_database_path=(tmp_path / "flaky.sqlite3").resolve(),
        flaky_auto_skip_enabled=enabled,
        flaky_skip_mode_requested=mode,
        flaky_skip_mode_effective=mode if enabled else "off",
    )


def _candidate(case_id, *, param_hash="param-a", profile="parallel", suffix="1"):
    return SnapshotCandidate(
        flaky_key=build_flaky_key(
            case_id,
            param_hash,
            "overseas",
            profile,
            1,
        ),
        case_id=case_id,
        param_hash=param_hash,
        environment="overseas",
        execution_profile=profile,
        state_epoch=1,
        governance_id=f"governance-{suffix}",
        governance_status="ACTIVE",
        expires_at=NOW + timedelta(days=1),
    )


def _read_service(candidates):
    class ReadService:
        def __init__(self, _path):
            pass

        def snapshot_source(self):
            return SnapshotSource(
                database_schema_version=4,
                data_as_of=NOW,
                candidates=tuple(candidates),
            )

    return ReadService


def _plan(tmp_path, cases, candidates, *, profile="parallel"):
    snapshot = generate_snapshot(
        _config(tmp_path),
        run_id="run-enforce",
        branch="dev3",
        repository_root=tmp_path,
        now=NOW,
        read_service_factory=_read_service(candidates),
    )
    return build_decision_plan(
        snapshot,
        cases,
        run_id="run-enforce",
        branch="dev3",
        environment="overseas",
        execution_profiles={case.nodeid: profile for case in cases},
        collection_started_at=NOW + timedelta(seconds=1),
    )


def _prepare_plugin(pytester, monkeypatch, *, run_id="run-plugin"):
    pytester.makeconftest('pytest_plugins = ("quality.pytest_plugin",)')
    output_dir = pytester.path / "quality-output"
    monkeypatch.setenv("QUALITY_ENABLE", "1")
    monkeypatch.setenv("QUALITY_RUN_ID", run_id)
    monkeypatch.setenv("QUALITY_EXECUTION_ID", "manual-pytest")
    monkeypatch.setenv("QUALITY_OUTPUT_DIR", str(output_dir))
    monkeypatch.setenv("USE_CHINA_ENVIRONMENT", "FALSE")
    monkeypatch.setenv("OVERSEAS_TEST_BASE_URL", "https://example.com")
    monkeypatch.setenv("OVERSEAS_API_KEY", "test-key")
    pythonpath = os.environ.get("PYTHONPATH")
    monkeypatch.setenv(
        "PYTHONPATH",
        str(PROJECT_ROOT) if not pythonpath else f"{PROJECT_ROOT}{os.pathsep}{pythonpath}",
    )
    return output_dir


def _run_subprocess(pytester, *args):
    return pytester.runpytest_subprocess("-o", "addopts=", *args)


def test_enforce_requires_double_opt_in_and_kill_switch_is_immediate():
    enforce = load_quality_config(
        {
            "QUALITY_FLAKY_AUTO_SKIP_ENABLE": "1",
            "QUALITY_FLAKY_SKIP_MODE": "enforce",
        }
    )
    killed = load_quality_config(
        {
            "QUALITY_FLAKY_AUTO_SKIP_ENABLE": "0",
            "QUALITY_FLAKY_SKIP_MODE": "enforce",
        }
    )

    assert enforce.flaky_skip_mode_effective == "enforce"
    assert killed.flaky_skip_mode_requested == "enforce"
    assert killed.flaky_skip_mode_effective == "off"


def test_enforce_exact_match_plans_and_reconciles_governance_skip(tmp_path):
    test_file = tmp_path / "module" / "smoke" / "test_enforce.py"
    test_file.parent.mkdir(parents=True)
    test_file.write_text("def test_case(): pass\n", encoding="utf-8")
    case_id = "module/smoke/test_enforce.py::test_case"
    case = CollectedTestCase(
        nodeid=case_id,
        markers=frozenset(),
        case_id=case_id,
        param_hash="param-a",
        normalized_case_path="module/smoke/test_enforce.py",
    )
    plan = _plan(tmp_path, (case,), (_candidate(case_id),))

    applied = reconcile_decision_plan(
        plan,
        ({"nodeid": case.nodeid, "phase": "setup", "final_status": "skipped"},),
        now=NOW,
    )
    missing = reconcile_decision_plan(
        plan,
        ({"nodeid": case.nodeid, "phase": "call", "final_status": "passed"},),
        now=NOW,
    )

    assert plan.run_count == plan.would_skip_count == 0
    assert plan.skip_count == 1
    assert plan.decisions[0].decision == "SKIP"
    assert plan.decisions[0].primary_reason_code == "governance_enforce_match"
    assert applied.status == "OK"
    assert applied.actual_governance_skip_count == 1
    assert missing.status == "DEGRADED"
    assert missing.actual_governance_skip_count == 0
    assert "governance_skip_not_observed" in missing.diagnostic_codes


def test_business_skip_is_not_counted_as_governance_skip(tmp_path):
    test_file = tmp_path / "module" / "smoke" / "test_business.py"
    test_file.parent.mkdir(parents=True)
    test_file.write_text("", encoding="utf-8")
    case_id = "module/smoke/test_business.py::test_case"
    case = CollectedTestCase(
        nodeid=case_id,
        markers=frozenset({"skip"}),
        case_id=case_id,
        param_hash="param-a",
        normalized_case_path="module/smoke/test_business.py",
    )
    plan = _plan(tmp_path, (case,), (_candidate(case_id),))

    result = reconcile_decision_plan(
        plan,
        ({"nodeid": case.nodeid, "phase": "setup", "final_status": "skipped"},),
        now=NOW,
    )

    assert plan.skip_count == 1
    assert result.status == "OK"
    assert result.actual_governance_skip_count == 0
    assert result.unexpected_skipped_nodeids == ()


def test_pytest_enforce_applies_six_governance_skips_with_xdist(
    pytester,
    monkeypatch,
):
    output_dir = _prepare_plugin(pytester, monkeypatch)
    test_file = pytester.path / "module" / "smoke" / "test_enforce.py"
    test_file.parent.mkdir(parents=True)
    test_file.write_text(
        "\n".join(f"def test_case_{index}(): assert False" for index in range(6)),
        encoding="utf-8",
    )
    param_hash = build_param_hash(None)
    cases = tuple(
        CollectedTestCase(
            nodeid=f"module/smoke/test_enforce.py::test_case_{index}",
            markers=frozenset(),
            case_id=f"module/smoke/test_enforce.py::test_case_{index}",
            param_hash=param_hash,
            normalized_case_path="module/smoke/test_enforce.py",
        )
        for index in range(6)
    )
    candidates = tuple(
        _candidate(
            case.case_id,
            param_hash=param_hash,
            profile="manual-parallel",
            suffix=str(index),
        )
        for index, case in enumerate(cases)
    )
    snapshot = generate_snapshot(
        _config(pytester.path),
        run_id="run-plugin",
        branch="dev3",
        repository_root=pytester.path,
        now=NOW,
        read_service_factory=_read_service(candidates),
    )
    plan = build_decision_plan(
        snapshot,
        cases,
        run_id="run-plugin",
        branch="dev3",
        environment="overseas",
        execution_profiles={case.nodeid: "manual-parallel" for case in cases},
        collection_started_at=NOW + timedelta(seconds=1),
    )
    path = write_decision_plan(plan, output_dir)
    monkeypatch.setenv("QUALITY_FLAKY_DECISION_PLAN_PATH", str(path))
    monkeypatch.setenv("QUALITY_FLAKY_DECISION_CHECKSUM", plan.content_checksum)

    result = _run_subprocess(pytester, "-n", "2", "-q", "module/smoke")

    result.assert_outcomes(skipped=6)


def test_invalid_collected_identity_fails_open_before_any_mark_is_applied(
    pytester,
    monkeypatch,
):
    output_dir = _prepare_plugin(pytester, monkeypatch)
    test_file = pytester.path / "module" / "smoke" / "test_atomic.py"
    test_file.parent.mkdir(parents=True)
    test_file.write_text(
        "def test_first(): pass\n\ndef test_second(): pass\n",
        encoding="utf-8",
    )
    param_hash = build_param_hash(None)
    path = "module/smoke/test_atomic.py"
    first_id = f"{path}::test_first"
    wrong_second_id = f"{path}::test_second_wrong"
    cases = (
        CollectedTestCase(first_id, frozenset(), first_id, param_hash, path),
        CollectedTestCase(
            f"{path}::test_second",
            frozenset(),
            wrong_second_id,
            param_hash,
            path,
        ),
    )
    candidates = (
        _candidate(first_id, param_hash=param_hash, profile="manual-serial", suffix="1"),
        _candidate(
            wrong_second_id,
            param_hash=param_hash,
            profile="manual-serial",
            suffix="2",
        ),
    )
    snapshot = generate_snapshot(
        _config(pytester.path),
        run_id="run-plugin",
        branch="dev3",
        repository_root=pytester.path,
        now=NOW,
        read_service_factory=_read_service(candidates),
    )
    plan = build_decision_plan(
        snapshot,
        cases,
        run_id="run-plugin",
        branch="dev3",
        environment="overseas",
        execution_profiles={case.nodeid: "manual-serial" for case in cases},
        collection_started_at=NOW + timedelta(seconds=1),
    )
    plan_path = write_decision_plan(plan, output_dir)
    monkeypatch.setenv("QUALITY_FLAKY_DECISION_PLAN_PATH", str(plan_path))
    monkeypatch.setenv("QUALITY_FLAKY_DECISION_CHECKSUM", plan.content_checksum)

    result = _run_subprocess(pytester, "-q", "module/smoke")

    result.assert_outcomes(passed=2)
    issues = read_jsonl(output_dir / "shards/integrity-manual-pytest-master.jsonl")
    assert any(issue["code"] == "flaky_decision_plan_invalid" for issue in issues)
