from __future__ import annotations

from datetime import UTC, datetime

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
