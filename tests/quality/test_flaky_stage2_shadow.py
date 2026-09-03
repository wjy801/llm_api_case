from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json

import pytest

from master_service import CollectedTestCase
from quality.config import QualityRuntimeConfig
from quality.config import load_quality_config
from quality.flaky_identity import build_flaky_key
from quality.flaky_read import SnapshotCandidate, SnapshotSource
from quality.flaky_shadow import (
    build_decision_plan,
    generate_snapshot,
    read_decision_plan,
    read_snapshot,
    reconcile_decision_plan,
    write_decision_plan,
    write_reconciliation,
    write_snapshot,
)
from quality.flaky_store import FlakyStoreError
from quality.flaky_v3 import GovernancePolicy
from quality.pytest_identity import normalize_case_path, path_has_directory_prefix
from run_orchestration import pytest_execution, runner
from pipeline_reporting.builder import build_pipeline_report
from pipeline_reporting.contracts import LoadedPipelineSources, PipelineContext
from pipeline_reporting.quality_sources import load_quality_sources
from pipeline_reporting.renderer import render_pipeline_summary


NOW = datetime(2026, 9, 1, 8, 0, tzinfo=UTC)


def _config(tmp_path, *, enabled=True, mode="shadow"):
    return QualityRuntimeConfig(
        enabled=True,
        run_id="run-2",
        execution_id=None,
        output_dir=tmp_path / "quality",
        flaky_database_path=(tmp_path / "flaky.sqlite3").resolve(),
        flaky_auto_skip_enabled=enabled,
        flaky_skip_mode_requested=mode,
        flaky_skip_mode_effective=mode if enabled and mode == "shadow" else "off",
    )


def _candidate(case_id: str, *, profile="parallel", key=None):
    param_hash = "param-a"
    flaky_key = key or build_flaky_key(
        case_id, param_hash, "overseas", profile, 1
    )
    return SnapshotCandidate(
        flaky_key=flaky_key,
        case_id=case_id,
        param_hash=param_hash,
        environment="overseas",
        execution_profile=profile,
        state_epoch=1,
        governance_id="governance-1",
        governance_status="ACTIVE",
        expires_at=NOW + timedelta(days=1),
    )


class _ReadService:
    candidates = ()
    schema_version = 3

    def __init__(self, _path):
        pass

    def snapshot_source(self):
        return SnapshotSource(
            database_schema_version=self.schema_version,
            data_as_of=NOW,
            candidates=self.candidates,
        )


def test_disabled_snapshot_does_not_open_database_and_is_verifiable(tmp_path):
    called = False

    def fail_if_called(_path):
        nonlocal called
        called = True
        raise AssertionError("database should not be opened")

    snapshot = generate_snapshot(
        _config(tmp_path, enabled=False),
        run_id="run-2",
        branch="dev3",
        repository_root=tmp_path,
        now=NOW,
        read_service_factory=fail_if_called,
    )
    path = write_snapshot(snapshot, tmp_path)

    assert snapshot.status == "DISABLED"
    assert snapshot.entries == ()
    assert called is False
    assert read_snapshot(path, expected_run_id="run-2") == snapshot


def test_disabled_snapshot_contract_mismatch_fails_open(tmp_path):
    snapshot = generate_snapshot(
        _config(tmp_path, enabled=False),
        run_id="run-2",
        branch="dev3",
        repository_root=tmp_path,
        now=NOW,
    ).model_copy(update={"run_id": "other-run"})
    case = CollectedTestCase("module/test_a.py::test_a", frozenset())

    plan = build_decision_plan(
        snapshot,
        (case,),
        run_id="run-2",
        branch="dev3",
        environment="overseas",
        execution_profiles={case.nodeid: "serial"},
        collection_started_at=NOW,
    )

    assert plan.run_count == plan.fail_open_count == 1
    assert plan.decisions[0].primary_reason_code == "snapshot_invalid"
    assert "snapshot_run_mismatch" in plan.decisions[0].diagnostic_codes


@pytest.mark.parametrize(
    "environment,expected_diagnostic",
    (
        (
            {"QUALITY_FLAKY_AUTO_SKIP_ENABLE": "sometimes"},
            "config_auto_skip_invalid",
        ),
        (
            {
                "QUALITY_FLAKY_AUTO_SKIP_ENABLE": "1",
                "QUALITY_FLAKY_SKIP_MODE": "unsafe",
            },
            "config_skip_mode_invalid",
        ),
        (
            {
                "QUALITY_FLAKY_AUTO_SKIP_ENABLE": "1",
                "QUALITY_FLAKY_SKIP_MODE": "shadow",
                "QUALITY_FLAKY_SNAPSHOT_MAX_AGE_MINUTES": "invalid",
            },
            "config_snapshot_age_invalid",
        ),
    ),
)
def test_invalid_skip_configuration_is_counted_as_fail_open(
    tmp_path,
    environment,
    expected_diagnostic,
):
    case_file = tmp_path / "module" / "smoke" / "test_demo.py"
    case_file.parent.mkdir(parents=True)
    case_file.write_text("", encoding="utf-8")
    values = {
        "QUALITY_ENABLE": "1",
        "QUALITY_FLAKY_DB_PATH": str((tmp_path / "flaky.sqlite3").resolve()),
        **environment,
    }
    config = load_quality_config(values, default_output_dir=tmp_path / "quality")
    snapshot = generate_snapshot(
        config,
        run_id="run-2",
        branch="dev3",
        repository_root=tmp_path,
        now=NOW,
        read_service_factory=_ReadService,
    )
    case = CollectedTestCase(
        nodeid="module/smoke/test_demo.py::test_case",
        markers=frozenset(),
        case_id="module/smoke/test_demo.py::test_case",
        param_hash="param-a",
        normalized_case_path="module/smoke/test_demo.py",
    )
    plan = build_decision_plan(
        snapshot,
        (case,),
        run_id="run-2",
        branch="dev3",
        environment="overseas",
        execution_profiles={case.nodeid: "parallel"},
        collection_started_at=NOW,
    )

    assert plan.run_count == plan.fail_open_count == 1
    assert plan.integrity_status == "DEGRADED"
    assert expected_diagnostic in plan.decisions[0].diagnostic_codes


def test_ready_snapshot_and_shadow_plan_use_exact_full_identity(tmp_path):
    case_file = tmp_path / "module" / "smoke" / "test_demo.py"
    case_file.parent.mkdir(parents=True)
    case_file.write_text("def test_case(): pass\n", encoding="utf-8")
    case_id = "module/smoke/test_demo.py::test_case"
    _ReadService.candidates = (_candidate(case_id),)
    snapshot = generate_snapshot(
        _config(tmp_path),
        run_id="run-2",
        branch="dev3",
        repository_root=tmp_path,
        now=NOW,
        read_service_factory=_ReadService,
    )
    case = CollectedTestCase(
        nodeid=case_id + "[a]",
        markers=frozenset(),
        case_id=case_id,
        param_hash="param-a",
        normalized_case_path="module/smoke/test_demo.py",
    )

    plan = build_decision_plan(
        snapshot,
        (case,),
        run_id="run-2",
        branch="dev3",
        environment="overseas",
        execution_profiles={case.nodeid: "parallel"},
        collection_started_at=NOW + timedelta(seconds=1),
        now=NOW + timedelta(seconds=2),
    )

    assert snapshot.status == "READY"
    assert plan.would_skip_count == 1
    assert plan.skip_count == 0
    assert plan.decisions[0].decision == "WOULD_SKIP"
    assert plan.decisions[0].flaky_key == _ReadService.candidates[0].flaky_key
    path = write_decision_plan(plan, tmp_path)
    assert read_decision_plan(
        path,
        expected_run_id="run-2",
        expected_checksum=plan.content_checksum,
    ) == plan
    with pytest.raises(FileExistsError):
        write_decision_plan(plan, tmp_path)

    unknown_environment = build_decision_plan(
        snapshot,
        (case,),
        run_id="run-2",
        branch="dev3",
        environment="unknown",
        execution_profiles={case.nodeid: "parallel"},
        collection_started_at=NOW + timedelta(seconds=1),
    )
    assert unknown_environment.run_count == 1
    assert unknown_environment.fail_open_count == 1
    assert (
        "collection_environment_invalid"
        in unknown_environment.decisions[0].diagnostic_codes
    )


def test_snapshot_expiry_scope_and_collection_identity_conflict_fail_open(tmp_path):
    case_file = tmp_path / "module" / "smoke" / "test_demo.py"
    case_file.parent.mkdir(parents=True)
    case_file.write_text("def test_case(): pass\n", encoding="utf-8")
    case_id = "module/smoke/test_demo.py::test_case"
    _ReadService.candidates = (_candidate(case_id),)
    snapshot = generate_snapshot(
        _config(tmp_path),
        run_id="run-2",
        branch="dev3",
        repository_root=tmp_path,
        now=NOW,
        read_service_factory=_ReadService,
    )
    cases = tuple(
        CollectedTestCase(
            nodeid=f"{case_id}[{suffix}]",
            markers=frozenset(),
            case_id=case_id,
            param_hash="param-a",
            normalized_case_path="module/smoke/test_demo.py",
        )
        for suffix in ("a", "b")
    )
    conflict = build_decision_plan(
        snapshot,
        cases,
        run_id="run-2",
        branch="dev3",
        environment="overseas",
        execution_profiles={item.nodeid: "parallel" for item in cases},
        collection_started_at=NOW + timedelta(minutes=1),
    )
    expired = build_decision_plan(
        snapshot,
        cases[:1],
        run_id="run-2",
        branch="dev3",
        environment="overseas",
        execution_profiles={cases[0].nodeid: "parallel"},
        collection_started_at=NOW + timedelta(minutes=16),
    )
    boundary = build_decision_plan(
        snapshot,
        cases[:1],
        run_id="run-2",
        branch="dev3",
        environment="overseas",
        execution_profiles={cases[0].nodeid: "parallel"},
        collection_started_at=NOW + timedelta(minutes=15),
    )

    assert conflict.run_count == 2
    assert conflict.fail_open_count == 2
    assert all(
        "collection_identity_conflict" in item.diagnostic_codes
        for item in conflict.decisions
    )
    assert expired.decisions[0].primary_reason_code == "snapshot_invalid"
    assert "snapshot_time_window_invalid" in expired.decisions[0].diagnostic_codes
    assert boundary.would_skip_count == 1


def test_run_branch_policy_schema_and_sibling_identity_mismatches_fail_open(tmp_path):
    case_file = tmp_path / "module" / "smoke" / "test_demo.py"
    case_file.parent.mkdir(parents=True)
    case_file.write_text("", encoding="utf-8")
    case_id = "module/smoke/test_demo.py::test_case"
    _ReadService.candidates = (_candidate(case_id),)
    _ReadService.schema_version = 3
    alternate_policy = GovernancePolicy(required_consecutive_passes=6)
    snapshot = generate_snapshot(
        _config(tmp_path),
        run_id="run-2",
        branch="dev3",
        repository_root=tmp_path,
        now=NOW,
        policy=alternate_policy,
        read_service_factory=_ReadService,
    )
    case = CollectedTestCase(
        nodeid=case_id + "[a]",
        markers=frozenset(),
        case_id=case_id,
        param_hash="param-a",
        normalized_case_path="module/smoke/test_demo.py",
    )
    mismatched = build_decision_plan(
        snapshot,
        (case,),
        run_id="other-run",
        branch="main",
        environment="overseas",
        execution_profiles={case.nodeid: "parallel"},
        collection_started_at=NOW,
    )
    assert mismatched.run_count == mismatched.fail_open_count == 1
    assert {
        "snapshot_run_mismatch",
        "snapshot_branch_mismatch",
        "snapshot_policy_mismatch",
    } <= set(mismatched.decisions[0].diagnostic_codes)

    current = generate_snapshot(
        _config(tmp_path),
        run_id="run-2",
        branch="dev3",
        repository_root=tmp_path,
        now=NOW,
        read_service_factory=_ReadService,
    )
    sibling = CollectedTestCase(
        nodeid=case_id + "[b]",
        markers=frozenset(),
        case_id=case_id,
        param_hash="param-b",
        normalized_case_path="module/smoke/test_demo.py",
    )
    sibling_plan = build_decision_plan(
        current,
        (sibling,),
        run_id="run-2",
        branch="dev3",
        environment="overseas",
        execution_profiles={sibling.nodeid: "parallel"},
        collection_started_at=NOW,
    )
    assert sibling_plan.decisions[0].primary_reason_code == "governance_not_matched"
    assert sibling_plan.fail_open_count == 0

    _ReadService.schema_version = 4
    unavailable = generate_snapshot(
        _config(tmp_path),
        run_id="run-2",
        branch="dev3",
        repository_root=tmp_path,
        now=NOW,
        read_service_factory=_ReadService,
    )
    assert unavailable.status == "UNAVAILABLE"
    assert unavailable.error_code == "snapshot_version_incompatible"
    _ReadService.schema_version = 3


def test_duplicate_candidate_and_smoke_extra_scope_are_rejected(tmp_path):
    outside = tmp_path / "module" / "smoke_extra" / "test_demo.py"
    outside.parent.mkdir(parents=True)
    outside.write_text("", encoding="utf-8")
    case_id = "module/smoke_extra/test_demo.py::test_case"
    candidate = _candidate(case_id)
    _ReadService.candidates = (candidate, candidate)
    duplicate = generate_snapshot(
        _config(tmp_path),
        run_id="run-2",
        branch="dev3",
        repository_root=tmp_path,
        now=NOW,
        read_service_factory=_ReadService,
    )
    assert duplicate.status == "UNAVAILABLE"
    assert duplicate.error_code == "snapshot_candidate_duplicate"

    _ReadService.candidates = (candidate,)
    snapshot = generate_snapshot(
        _config(tmp_path),
        run_id="run-2",
        branch="dev3",
        repository_root=tmp_path,
        now=NOW,
        read_service_factory=_ReadService,
    )
    case = CollectedTestCase(
        nodeid=case_id,
        markers=frozenset(),
        case_id=case_id,
        param_hash="param-a",
        normalized_case_path="module/smoke_extra/test_demo.py",
    )
    plan = build_decision_plan(
        snapshot,
        (case,),
        run_id="run-2",
        branch="dev3",
        environment="overseas",
        execution_profiles={case.nodeid: "parallel"},
        collection_started_at=NOW,
    )
    assert plan.run_count == plan.fail_open_count == 1
    assert "path_out_of_scope" in plan.decisions[0].diagnostic_codes


def test_tampered_snapshot_and_plan_are_rejected(tmp_path):
    snapshot = generate_snapshot(
        _config(tmp_path, enabled=False),
        run_id="run-2",
        branch="dev3",
        repository_root=tmp_path,
        now=NOW,
    )
    snapshot_path = write_snapshot(snapshot, tmp_path)
    payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    payload["branch"] = "main"
    snapshot_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(FlakyStoreError) as captured:
        read_snapshot(snapshot_path)
    assert captured.value.code == "artifact_checksum_mismatch"


def test_shadow_reconciliation_never_reports_governance_skip(tmp_path):
    snapshot = generate_snapshot(
        _config(tmp_path, enabled=False),
        run_id="run-2",
        branch="dev3",
        repository_root=tmp_path,
        now=NOW,
    )
    case = CollectedTestCase("module/test_a.py::test_a", frozenset())
    plan = build_decision_plan(
        snapshot,
        (case,),
        run_id="run-2",
        branch="dev3",
        environment="overseas",
        execution_profiles={case.nodeid: "serial"},
        collection_started_at=NOW,
    )
    result = reconcile_decision_plan(
        plan,
        (
            {
                "nodeid": case.nodeid,
                "phase": "call",
                "final_status": "passed",
            },
        ),
        now=NOW,
    )
    audit_only = reconcile_decision_plan(plan, (), collect_only=True, now=NOW)

    assert result.status == "OK"
    assert result.actual_governance_skip_count == 0
    assert audit_only.status == "NOT_EXECUTED"


def test_runner_collect_only_writes_one_auditable_shadow_plan(
    tmp_path,
    monkeypatch,
):
    output = tmp_path / "quality"
    monkeypatch.setenv("QUALITY_ENABLE", "1")
    monkeypatch.setenv("QUALITY_RUN_ID", "run-collect-only")
    monkeypatch.setenv("QUALITY_OUTPUT_DIR", str(output))
    monkeypatch.setenv("QUALITY_FLAKY_AUTO_SKIP_ENABLE", "0")
    monkeypatch.setenv("QUALITY_FLAKY_SKIP_MODE", "off")
    case = CollectedTestCase(
        nodeid="module/smoke/test_demo.py::test_case",
        markers=frozenset(),
        case_id="module/smoke/test_demo.py::test_case",
        param_hash="param-a",
        normalized_case_path="module/smoke/test_demo.py",
    )
    monkeypatch.setattr(
        pytest_execution,
        "collect_test_case_items",
        lambda _path, _args=(): pytest_execution.CollectionResult(
            raw_pytest_exit_code=0,
            cases=(case,),
            stdout="",
            stderr="",
        ),
    )

    assert runner.run(extra_pytest_args=("--collect-only",)) == 0

    snapshot = read_snapshot(
        output / "flaky-skip-snapshot.json",
        expected_run_id="run-collect-only",
    )
    plan = read_decision_plan(
        output / "flaky-skip-decisions.json",
        expected_run_id="run-collect-only",
    )
    reconciliation = json.loads(
        (output / "flaky-skip-reconciliation.json").read_text(encoding="utf-8")
    )
    assert snapshot.status == "DISABLED"
    assert plan.run_count == 1
    assert plan.skip_count == 0
    assert reconciliation["status"] == "NOT_EXECUTED"


def test_collection_failure_keeps_pytest_exit_and_does_not_forge_plan(
    tmp_path,
    monkeypatch,
):
    output = tmp_path / "quality"
    monkeypatch.setenv("QUALITY_ENABLE", "1")
    monkeypatch.setenv("QUALITY_RUN_ID", "run-collection-failed")
    monkeypatch.setenv("QUALITY_OUTPUT_DIR", str(output))
    monkeypatch.setattr(
        pytest_execution,
        "collect_test_case_items",
        lambda _path, _args=(): pytest_execution.CollectionResult(
            raw_pytest_exit_code=pytest_execution.PYTEST_EXIT_USAGE_ERROR,
            cases=(),
            stdout="",
            stderr="bad option",
        ),
    )

    assert runner.run() == pytest_execution.PYTEST_EXIT_USAGE_ERROR
    assert (output / "flaky-skip-snapshot.json").is_file()
    assert not (output / "flaky-skip-decisions.json").exists()
    reconciliation = json.loads(
        (output / "flaky-skip-reconciliation.json").read_text(encoding="utf-8")
    )
    assert reconciliation["status"] == "DEGRADED"
    assert reconciliation["diagnostic_codes"] == [
        "authoritative_collection_failed"
    ]


def test_pipeline_summary_consumes_the_same_versioned_decision_artifact(tmp_path):
    quality_dir = tmp_path / "quality"
    snapshot = generate_snapshot(
        _config(tmp_path, enabled=False),
        run_id="run-2",
        branch="dev3",
        repository_root=tmp_path,
        now=NOW,
    )
    case = CollectedTestCase("module/test_a.py::test_a", frozenset())
    plan = build_decision_plan(
        snapshot,
        (case,),
        run_id="run-2",
        branch="dev3",
        environment="overseas",
        execution_profiles={case.nodeid: "serial"},
        collection_started_at=NOW,
    )
    write_decision_plan(plan, quality_dir)
    write_reconciliation(
        reconcile_decision_plan(plan, (), collect_only=True, now=NOW),
        quality_dir,
    )
    warnings = []
    loaded = load_quality_sources(quality_dir, warnings=warnings)
    report = build_pipeline_report(
        PipelineContext(real_smoke_enabled=True, quality_enabled=True),
        LoadedPipelineSources(shadow=loaded.shadow),
    )
    rendered = render_pipeline_summary(report)

    assert loaded.shadow.available is True
    assert loaded.shadow.error_code is None
    assert loaded.shadow.run_count == 1
    assert loaded.shadow.skip_count == 0
    assert "Flaky Shadow 决策" in rendered
    assert "实际治理 Skip" in rendered


def test_pipeline_summary_marks_missing_reconciliation_degraded(tmp_path):
    quality_dir = tmp_path / "quality"
    snapshot = generate_snapshot(
        _config(tmp_path, enabled=False),
        run_id="run-2",
        branch="dev3",
        repository_root=tmp_path,
        now=NOW,
    )
    case = CollectedTestCase("module/test_a.py::test_a", frozenset())
    plan = build_decision_plan(
        snapshot,
        (case,),
        run_id="run-2",
        branch="dev3",
        environment="overseas",
        execution_profiles={case.nodeid: "serial"},
        collection_started_at=NOW,
    )
    write_decision_plan(plan, quality_dir)

    loaded = load_quality_sources(quality_dir, warnings=[])
    report = build_pipeline_report(
        PipelineContext(real_smoke_enabled=True, quality_enabled=True),
        LoadedPipelineSources(shadow=loaded.shadow),
    )
    rendered = render_pipeline_summary(report)

    assert loaded.shadow.available is True
    assert loaded.shadow.integrity_status == "DEGRADED"
    assert loaded.shadow.reconciliation_status == "UNKNOWN"
    assert loaded.shadow.error_code == "reconciliation_artifact_missing"
    assert "reconciliation_artifact_missing" in rendered


def test_pipeline_summary_reports_stable_error_for_missing_and_invalid_plan(tmp_path):
    missing_warnings = []
    missing = load_quality_sources(tmp_path / "missing", warnings=missing_warnings)
    missing_report = build_pipeline_report(
        PipelineContext(real_smoke_enabled=True, quality_enabled=True),
        LoadedPipelineSources(shadow=missing.shadow),
    )
    missing_rendered = render_pipeline_summary(missing_report)

    assert missing.shadow.available is False
    assert missing.shadow.run_count is None
    assert missing.shadow.error_code == "decision_artifact_missing"
    assert "decision_artifact_missing" in missing_rendered

    invalid_dir = tmp_path / "invalid"
    invalid_dir.mkdir()
    (invalid_dir / "flaky-skip-decisions.json").write_text("{}", encoding="utf-8")
    invalid_warnings = []
    invalid = load_quality_sources(invalid_dir, warnings=invalid_warnings)
    invalid_report = build_pipeline_report(
        PipelineContext(real_smoke_enabled=True, quality_enabled=True),
        LoadedPipelineSources(shadow=invalid.shadow),
    )
    invalid_rendered = render_pipeline_summary(invalid_report)

    assert invalid.shadow.available is False
    assert invalid.shadow.run_count is None
    assert invalid.shadow.error_code == "decision_artifact_invalid"
    assert "decision_artifact_invalid" in invalid_rendered


def test_case_path_rejects_escape_and_prefix_confusion(tmp_path):
    smoke = tmp_path / "module" / "smoke" / "test_a.py"
    smoke.parent.mkdir(parents=True)
    smoke.write_text("", encoding="utf-8")
    extra = tmp_path / "module" / "smoke_extra" / "test_b.py"
    extra.parent.mkdir(parents=True)
    extra.write_text("", encoding="utf-8")

    assert normalize_case_path(
        "module/smoke/test_a.py::test_a", tmp_path
    ) == "module/smoke/test_a.py"
    assert path_has_directory_prefix("module/smoke/test_a.py", "module/smoke/")
    assert not path_has_directory_prefix(
        "module/smoke_extra/test_b.py", "module/smoke/"
    )
    for case_id in (
        "../outside.py::test_a",
        "module/./smoke/test_a.py::test_a",
        "module/smoke/../smoke/test_a.py::test_a",
        "module//smoke/test_a.py::test_a",
        str(smoke.resolve()) + "::test_a",
    ):
        with pytest.raises(ValueError):
            normalize_case_path(case_id, tmp_path)
