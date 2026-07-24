from __future__ import annotations

import csv

import pytest

from governance.flaky_models import FlakyStatus
from governance.nodeid_validator import (
    read_retry_queue,
    validate_nodeids,
    write_stale_retry_queue,
)
from governance.retry_queue import CSV_HEADER


pytestmark = pytest.mark.flaky_governance


def test_reads_retry_queue_with_status_filter(tmp_path):
    queue_path = tmp_path / "retry-nodeids.csv"
    _write_queue(
        queue_path,
        [
            _row("module/test_demo.py::test_failed", "retry_failed", "0"),
            _row("module/test_demo.py::test_passed", "retry_passed", "1"),
        ],
    )

    entries = read_retry_queue(queue_path, status_filter=FlakyStatus.RETRY_FAILED)

    assert [entry.nodeid for entry in entries] == ["module/test_demo.py::test_failed"]


def test_validates_valid_and_stale_nodeids(tmp_path):
    queue_path = tmp_path / "retry-nodeids.csv"
    valid_nodeid = "module/test_demo.py::test_valid[param, 中文]"
    stale_nodeid = "module/test_demo.py::test_deleted"
    _write_queue(queue_path, [_row(valid_nodeid, "retry_failed", "0"), _row(stale_nodeid, "retry_passed", "1")])

    validation = validate_nodeids(read_retry_queue(queue_path), [valid_nodeid])

    assert validation.valid_nodeids == [valid_nodeid]
    assert [entry.nodeid for entry in validation.stale_entries] == [stale_nodeid]


def test_writes_stale_retry_queue_with_fixed_header(tmp_path):
    queue_path = tmp_path / "retry-nodeids.csv"
    stale_path = tmp_path / "reports" / "flaky" / "current" / "stale-retry-nodeids.csv"
    stale_nodeid = "module/test_demo.py::test_deleted"
    _write_queue(queue_path, [_row(stale_nodeid, "retry_failed", "0")])
    validation = validate_nodeids(read_retry_queue(queue_path), [])

    write_stale_retry_queue(stale_path, validation.stale_entries)

    with stale_path.open(newline="", encoding="utf-8") as file:
        rows = list(csv.DictReader(file))

    assert rows[0]["nodeid"] == stale_nodeid
    assert list(rows[0]) == CSV_HEADER


def test_rejects_unexpected_csv_header(tmp_path):
    queue_path = tmp_path / "retry-nodeids.csv"
    queue_path.write_text("nodeid,status\nmodule/test_demo.py::test_case,retry_failed\n", encoding="utf-8")

    with pytest.raises(ValueError, match="retry queue csv header"):
        read_retry_queue(queue_path)


def test_reads_utf8_sig_retry_queue(tmp_path):
    queue_path = tmp_path / "retry-nodeids.csv"
    queue_path.write_text(
        ",".join(CSV_HEADER)
        + "\n"
        + "1,run-001,commit,now,0,module/test_demo.py::test_case,retry_failed,2,TimeoutError\n",
        encoding="utf-8-sig",
    )

    entries = read_retry_queue(queue_path)

    assert [entry.nodeid for entry in entries] == ["module/test_demo.py::test_case"]


def _write_queue(path, rows):
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=CSV_HEADER)
        writer.writeheader()
        writer.writerows(rows)


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
