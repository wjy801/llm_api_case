from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

import run_master
from governance.retry_queue import CSV_HEADER


pytestmark = pytest.mark.flaky_governance


def test_list_rerun_targets_validates_without_pytest_execution(tmp_path, monkeypatch, capsys):
    valid_nodeid = "module/test_demo.py::test_valid"
    stale_nodeid = "module/test_demo.py::test_deleted"
    queue_path = tmp_path / "retry-nodeids.csv"
    _write_queue(queue_path, [_row(valid_nodeid, "retry_failed", "0"), _row(stale_nodeid, "retry_passed", "1")])
    stale_path = tmp_path / "stale-retry-nodeids.csv"
    monkeypatch.setattr(run_master, "STALE_RETRY_QUEUE_PATH", stale_path)
    monkeypatch.setattr(run_master, "collect_test_cases", lambda test_path: [valid_nodeid])
    monkeypatch.setattr(run_master.pytest, "main", lambda args: pytest.fail("pytest.main should not run"))

    exit_code = run_master.main(["--rerun-from", str(queue_path), "--list-rerun-targets"])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "有效 nodeid: 1" in output
    assert "失效 nodeid: 1" in output
    assert f"- {valid_nodeid}" in output
    assert _read_rows(stale_path)[0]["nodeid"] == stale_nodeid


def test_strict_nodeids_blocks_when_stale_exists(tmp_path, monkeypatch):
    valid_nodeid = "module/test_demo.py::test_valid"
    stale_nodeid = "module/test_demo.py::test_deleted"
    queue_path = tmp_path / "retry-nodeids.csv"
    _write_queue(queue_path, [_row(valid_nodeid, "retry_failed", "0"), _row(stale_nodeid, "retry_failed", "0")])
    monkeypatch.setattr(run_master, "STALE_RETRY_QUEUE_PATH", tmp_path / "stale-retry-nodeids.csv")
    monkeypatch.setattr(run_master, "collect_test_cases", lambda test_path: [valid_nodeid])
    monkeypatch.setattr(run_master.pytest, "main", lambda args: pytest.fail("pytest.main should not run"))

    exit_code = run_master.main(["--rerun-from", str(queue_path), "--strict-nodeids"])

    assert exit_code == 1


def test_rerun_passes_only_valid_filtered_nodeids_to_pytest(tmp_path, monkeypatch):
    retry_failed_nodeid = "module/test_demo.py::test_retry_failed"
    retry_passed_nodeid = "module/test_demo.py::test_retry_passed[param, 中文]"
    queue_path = tmp_path / "retry-nodeids.csv"
    _write_queue(
        queue_path,
        [
            _row(retry_failed_nodeid, "retry_failed", "0"),
            _row(retry_passed_nodeid, "retry_passed", "1"),
        ],
    )
    captured_args = []
    monkeypatch.setattr(run_master, "STALE_RETRY_QUEUE_PATH", tmp_path / "stale-retry-nodeids.csv")
    monkeypatch.setattr(run_master, "collect_test_cases", lambda test_path: [retry_failed_nodeid, retry_passed_nodeid])
    monkeypatch.setattr(run_master.pytest, "main", lambda args: captured_args.extend(args) or 0)

    exit_code = run_master.main(["--rerun-from", str(queue_path), "--rerun-status", "retry_passed", "-q"])

    assert exit_code == 0
    assert captured_args[0] == retry_passed_nodeid
    assert "-q" in captured_args
    assert captured_args[captured_args.index("--flaky-governance-report-dir") + 1] == str(run_master.DEFAULT_FLAKY_REPORT_DIR)


def test_rerun_updates_latest_queue_without_deleting_unexecuted_items(tmp_path, monkeypatch):
    executed_nodeid = "module/test_demo.py::test_selected"
    unexecuted_nodeid = "module/test_demo.py::test_not_selected"
    queue_path = tmp_path / "retry-nodeids.csv"
    latest_path = tmp_path / "reports" / "flaky" / "latest-retry-nodeids.csv"
    report_dir = tmp_path / "reports" / "flaky" / "current"
    results_path = report_dir / "flaky-results.json"
    _write_queue(
        queue_path,
        [
            _row(executed_nodeid, "retry_failed", "0"),
            _row(unexecuted_nodeid, "retry_passed", "1"),
        ],
    )
    _write_queue(
        latest_path,
        [
            _row(executed_nodeid, "retry_failed", "0"),
            _row(unexecuted_nodeid, "retry_passed", "1"),
        ],
    )
    monkeypatch.setattr(run_master, "LATEST_RETRY_QUEUE_PATH", latest_path)
    monkeypatch.setattr(run_master, "STALE_RETRY_QUEUE_PATH", tmp_path / "stale-retry-nodeids.csv")
    monkeypatch.setattr(run_master, "collect_test_cases", lambda test_path: [executed_nodeid, unexecuted_nodeid])

    def fake_pytest_main(args):
        assert args[0] == executed_nodeid
        assert args[args.index("--flaky-governance-report-dir") + 1] == str(report_dir)
        _write_flaky_results(results_path, [{"nodeid": executed_nodeid, "status": "passed"}])
        _write_summary(report_dir, failed=0, retry_failed=0, retry_passed=0)
        return 0

    monkeypatch.setattr(run_master.pytest, "main", fake_pytest_main)

    exit_code = run_master.main(
        [
            "--rerun-from",
            str(queue_path),
            "--rerun-status",
            "retry_failed",
            "--flaky-report-dir",
            str(report_dir),
        ]
    )

    latest_rows = _read_rows(latest_path)
    assert exit_code == 0
    assert [row["nodeid"] for row in latest_rows] == [unexecuted_nodeid]


def test_rerun_keeps_retry_results_in_latest_queue(tmp_path, monkeypatch):
    nodeid = "module/test_demo.py::test_still_flaky"
    queue_path = tmp_path / "retry-nodeids.csv"
    latest_path = tmp_path / "reports" / "flaky" / "latest-retry-nodeids.csv"
    report_dir = tmp_path / "reports" / "flaky" / "current"
    results_path = report_dir / "flaky-results.json"
    _write_queue(queue_path, [_row(nodeid, "retry_passed", "1")])
    _write_queue(latest_path, [_row(nodeid, "retry_passed", "1")])
    monkeypatch.setattr(run_master, "LATEST_RETRY_QUEUE_PATH", latest_path)
    monkeypatch.setattr(run_master, "STALE_RETRY_QUEUE_PATH", tmp_path / "stale-retry-nodeids.csv")
    monkeypatch.setattr(run_master, "collect_test_cases", lambda test_path: [nodeid])

    def fake_pytest_main(args):
        assert args[0] == nodeid
        assert args[args.index("--flaky-governance-report-dir") + 1] == str(report_dir)
        _write_flaky_results(results_path, [{"nodeid": nodeid, "status": "retry_failed", "first_failure_type": "ReadTimeout"}])
        _write_summary(report_dir, failed=0, retry_failed=1, retry_passed=0)
        return 1

    monkeypatch.setattr(run_master.pytest, "main", fake_pytest_main)

    exit_code = run_master.main(["--rerun-from", str(queue_path), "--flaky-report-dir", str(report_dir)])

    latest_rows = _read_rows(latest_path)
    assert exit_code == 1
    assert latest_rows[0]["nodeid"] == nodeid
    assert latest_rows[0]["status"] == "retry_failed"
    assert latest_rows[0]["priority"] == "0"
    assert latest_rows[0]["first_failure_type"] == "ReadTimeout"


def test_all_stale_nodeids_returns_non_zero(tmp_path, monkeypatch):
    queue_path = tmp_path / "retry-nodeids.csv"
    _write_queue(queue_path, [_row("module/test_demo.py::test_deleted", "retry_failed", "0")])
    monkeypatch.setattr(run_master, "STALE_RETRY_QUEUE_PATH", tmp_path / "stale-retry-nodeids.csv")
    monkeypatch.setattr(run_master, "collect_test_cases", lambda test_path: [])
    monkeypatch.setattr(run_master.pytest, "main", lambda args: pytest.fail("pytest.main should not run"))

    exit_code = run_master.main(["--rerun-from", str(queue_path)])

    assert exit_code == 1


def test_fail_on_retry_passed_blocks_after_pytest_success(tmp_path, monkeypatch):
    report_dir = tmp_path / "flaky-report"
    captured_args = []
    monkeypatch.setattr(run_master, "collect_test_cases", lambda test_path: ["module/test_demo.py::test_case"])

    def fake_pytest_main(args):
        captured_args.extend(args)
        _write_summary(report_dir, failed=0, retry_failed=0, retry_passed=1)
        return 0

    monkeypatch.setattr(run_master.pytest, "main", fake_pytest_main)

    exit_code = run_master.main(["module", "--flaky-report-dir", str(report_dir), "--fail-on-retry-passed"])

    assert exit_code == 1
    assert captured_args == [
        "module/test_demo.py::test_case",
        "--flaky-governance-report-dir",
        str(report_dir),
    ]


def test_default_gate_warns_retry_passed_without_changing_success_exit_code(tmp_path, monkeypatch):
    report_dir = tmp_path / "flaky-report"
    monkeypatch.setattr(run_master, "collect_test_cases", lambda test_path: ["module/test_demo.py::test_case"])
    monkeypatch.setattr(
        run_master.pytest,
        "main",
        lambda args: _write_summary(report_dir, failed=0, retry_failed=0, retry_passed=1) or 0,
    )

    exit_code = run_master.main(["module", "--flaky-report-dir", str(report_dir)])

    assert exit_code == 0


def test_failed_flaky_summary_blocks_even_when_pytest_succeeds(tmp_path, monkeypatch):
    report_dir = tmp_path / "flaky-report"
    monkeypatch.setattr(run_master, "collect_test_cases", lambda test_path: ["module/test_demo.py::test_case"])
    monkeypatch.setattr(
        run_master.pytest,
        "main",
        lambda args: _write_summary(report_dir, failed=1, retry_failed=0, retry_passed=0) or 0,
    )

    exit_code = run_master.main(["module", "--flaky-report-dir", str(report_dir)])

    assert exit_code == 1


def test_stale_summary_is_removed_before_pytest_execution(tmp_path, monkeypatch):
    report_dir = tmp_path / "flaky-report"
    _write_summary(report_dir, failed=1, retry_failed=0, retry_passed=0)
    monkeypatch.setattr(run_master, "collect_test_cases", lambda test_path: ["module/test_demo.py::test_case"])
    monkeypatch.setattr(run_master.pytest, "main", lambda args: 0)

    exit_code = run_master.main(["module", "--flaky-report-dir", str(report_dir)])

    assert exit_code == 0
    assert not (report_dir / "flaky-summary.json").exists()


def _write_queue(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=CSV_HEADER)
        writer.writeheader()
        writer.writerows(rows)


def _read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as file:
        return list(csv.DictReader(file))


def _write_flaky_results(path: Path, results: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"results": [_flaky_result(result) for result in results]}
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_summary(report_dir: Path, *, failed: int, retry_failed: int, retry_passed: int) -> None:
    report_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "total": failed + retry_failed + retry_passed,
        "passed": 0,
        "retry_passed": retry_passed,
        "retry_failed": retry_failed,
        "failed": failed,
        "first_pass_rate": 0.0,
        "final_success_rate": 0.0,
        "retry_recovery_rate": 0.0,
    }
    (report_dir / "flaky-summary.json").write_text(json.dumps(summary), encoding="utf-8")


def _flaky_result(result: dict[str, str]) -> dict:
    first_failure_type = result.get("first_failure_type")
    attempts = []
    if first_failure_type:
        attempts.append(
            {
                "index": 1,
                "outcome": "failed",
                "duration": 0.1,
                "failure_type": first_failure_type,
                "failure_message": "failure",
            }
        )

    if result["status"] in {"passed", "retry_passed"}:
        attempts.append({"index": len(attempts) + 1, "outcome": "passed", "duration": 0.1})
    elif result["status"] in {"failed", "retry_failed"} and not attempts:
        attempts.append({"index": 1, "outcome": "failed", "duration": 0.1, "failure_type": "AssertionError"})

    return {
        "nodeid": result["nodeid"],
        "status": result["status"],
        "attempt_count": len(attempts),
        "attempts": attempts,
        "total_duration": 0.1,
    }


def _row(nodeid: str, status: str, priority: str) -> dict[str, str]:
    return {
        "schema_version": "1",
        "source_run_id": "run-001",
        "source_git_commit": "commit",
        "generated_at": "now",
        "priority": priority,
        "nodeid": nodeid,
        "status": status,
        "attempt_count": "2",
        "first_failure_type": "TimeoutError",
    }
