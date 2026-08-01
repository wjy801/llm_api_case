from __future__ import annotations

from datetime import UTC, datetime
import json

from quality.cli import main
from quality.models import CasePhase, CaseResult, CaseStatus
from quality.storage import append_jsonl


def test_quality_cli_merge_returns_zero_for_degraded_but_written_manifest(tmp_path):
    output_dir = tmp_path / "quality"
    append_jsonl(
        output_dir / "shards" / "cases-serial-pool-master.jsonl",
        CaseResult(
            run_id="run-1",
            execution_id="serial-pool",
            worker_id="master",
            case_id="module/test_demo.py::test_case",
            invocation_id="inv-1",
            nodeid="module/test_demo.py::test_case",
            param_hash="param",
            phase=CasePhase.CALL,
            raw_status=CaseStatus.PASSED,
            final_status=CaseStatus.PASSED,
            duration_ms=1,
            start_time=datetime(2026, 7, 31, 8, 0, tzinfo=UTC),
            end_time=datetime(2026, 7, 31, 8, 0, tzinfo=UTC),
        ),
    )

    result = main([
        "merge",
        "--run-id",
        "run-1",
        "--output-dir",
        str(output_dir),
        "--expected-case-count",
        "2",
    ])

    assert result == 0
    assert (output_dir / "merged" / "manifest.json").exists()


def test_quality_cli_rejects_negative_expected_count(tmp_path):
    result = main([
        "merge",
        "--run-id",
        "run-1",
        "--output-dir",
        str(tmp_path),
        "--expected-case-count",
        "-1",
    ])

    assert result == 2


def test_quality_cli_report_writes_no_data_report_when_manifest_is_missing(tmp_path):
    output_dir = tmp_path / "quality"

    result = main([
        "report",
        "--run-id",
        "run-1",
        "--output-dir",
        str(output_dir),
    ])

    assert result == 0
    assert (output_dir / "summary.json").exists()
    gate = json.loads((output_dir / "gate-report.json").read_text(encoding="utf-8"))
    assert gate["overall"] == "NO_DATA"


def test_quality_cli_report_rejects_invalid_threshold(tmp_path):
    result = main([
        "report",
        "--run-id",
        "run-1",
        "--output-dir",
        str(tmp_path),
        "--timeout-warn-rate",
        "1.1",
    ])

    assert result == 2


def test_quality_cli_no_shadow_gate_keeps_stable_report_paths(tmp_path):
    output_dir = tmp_path / "quality"

    result = main([
        "report",
        "--run-id",
        "run-1",
        "--output-dir",
        str(output_dir),
        "--no-shadow-gate",
    ])

    assert result == 0
    gate = json.loads((output_dir / "gate-report.json").read_text(encoding="utf-8"))
    assert gate["overall"] == "NO_DATA"
    assert gate["rules"][0]["rule_id"] == "p0.shadow_gate.enabled"


def test_quality_cli_argument_overrides_invalid_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("QUALITY_TIMEOUT_WARN_RATE", "invalid")

    result = main([
        "report",
        "--run-id",
        "run-1",
        "--output-dir",
        str(tmp_path),
        "--timeout-warn-rate",
        "0.1",
    ])

    assert result == 0


def test_quality_cli_semantic_merge_reports_tool_failure_when_artifacts_are_missing(tmp_path):
    result = main([
        "semantic-merge",
        "--run-id",
        "run-1",
        "--output-dir",
        str(tmp_path / "quality"),
    ])

    assert result == 2
    assert (tmp_path / "quality" / "semantic" / "merged" / "manifest.json").exists()
