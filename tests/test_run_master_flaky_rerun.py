from __future__ import annotations

import csv
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
    assert captured_args == [retry_passed_nodeid, "-q"]


def test_all_stale_nodeids_returns_non_zero(tmp_path, monkeypatch):
    queue_path = tmp_path / "retry-nodeids.csv"
    _write_queue(queue_path, [_row("module/test_demo.py::test_deleted", "retry_failed", "0")])
    monkeypatch.setattr(run_master, "STALE_RETRY_QUEUE_PATH", tmp_path / "stale-retry-nodeids.csv")
    monkeypatch.setattr(run_master, "collect_test_cases", lambda test_path: [])
    monkeypatch.setattr(run_master.pytest, "main", lambda args: pytest.fail("pytest.main should not run"))

    exit_code = run_master.main(["--rerun-from", str(queue_path)])

    assert exit_code == 1


def _write_queue(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=CSV_HEADER)
        writer.writeheader()
        writer.writerows(rows)


def _read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as file:
        return list(csv.DictReader(file))


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
