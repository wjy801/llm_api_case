from __future__ import annotations

import csv

import pytest

from governance.flaky_models import AttemptOutcome, AttemptResult, FlakyStatus, FlakyTestResult
from governance.retry_queue import CSV_HEADER, RetryQueueMetadata, build_retry_queue_rows, write_retry_queue


pytestmark = pytest.mark.flaky_governance


def retry_result(nodeid: str, status: FlakyStatus, failure_type: str = "TimeoutError") -> FlakyTestResult:
    return FlakyTestResult(
        nodeid=nodeid,
        status=status,
        attempts=(
            AttemptResult(
                index=1,
                outcome=AttemptOutcome.FAILED,
                failure_type=failure_type,
            ),
            AttemptResult(index=2, outcome=AttemptOutcome.PASSED if status == FlakyStatus.RETRY_PASSED else AttemptOutcome.FAILED),
        ),
    )


class TestBuildRetryQueueRows:
    def test_includes_only_retry_statuses_and_orders_by_priority_then_nodeid(self):
        metadata = RetryQueueMetadata(source_run_id="run-001", source_git_commit="commit", generated_at="now")
        results = [
            FlakyTestResult("module/test_c.py::test_passed", FlakyStatus.PASSED),
            retry_result("module/test_b.py::test_retry_passed", FlakyStatus.RETRY_PASSED, "ReadTimeout"),
            retry_result("module/test_a.py::test_retry_failed", FlakyStatus.RETRY_FAILED, "TimeoutError"),
            FlakyTestResult("module/test_d.py::test_failed", FlakyStatus.FAILED),
        ]

        rows = build_retry_queue_rows(results, metadata)

        assert [row["nodeid"] for row in rows] == [
            "module/test_a.py::test_retry_failed",
            "module/test_b.py::test_retry_passed",
        ]
        assert [row["priority"] for row in rows] == ["0", "1"]
        assert rows[0]["first_failure_type"] == "TimeoutError"
        assert rows[1]["first_failure_type"] == "ReadTimeout"

    def test_deduplicates_by_nodeid_with_highest_priority(self):
        metadata = RetryQueueMetadata(source_run_id="run-001")
        nodeid = "module/test_demo.py::test_same"

        rows = build_retry_queue_rows(
            [
                retry_result(nodeid, FlakyStatus.RETRY_PASSED),
                retry_result(nodeid, FlakyStatus.RETRY_FAILED),
            ],
            metadata,
        )

        assert len(rows) == 1
        assert rows[0]["status"] == "retry_failed"
        assert rows[0]["priority"] == "0"


class TestWriteRetryQueue:
    def test_writes_fixed_header_and_latest_copy(self, tmp_path):
        report_dir = tmp_path / "reports" / "run"
        latest_path = tmp_path / "reports" / "flaky" / "latest-retry-nodeids.csv"
        metadata = RetryQueueMetadata(source_run_id="run-001", source_git_commit="commit", generated_at="now")
        nodeid = "module/test_demo.py::TestDemo::test_case[param, 中文]"

        write_retry_queue(
            report_dir,
            latest_path,
            [retry_result(nodeid, FlakyStatus.RETRY_FAILED, "AssertionError")],
            metadata,
        )

        snapshot_rows = _read_csv(report_dir / "retry-nodeids.csv")
        latest_rows = _read_csv(latest_path)

        assert list(snapshot_rows[0]) == CSV_HEADER
        assert snapshot_rows == latest_rows
        assert snapshot_rows[0]["nodeid"] == nodeid
        assert snapshot_rows[0]["status"] == "retry_failed"

    def test_empty_queue_overwrites_old_latest_with_header_only(self, tmp_path):
        report_dir = tmp_path / "reports" / "run"
        latest_path = tmp_path / "reports" / "flaky" / "latest-retry-nodeids.csv"
        latest_path.parent.mkdir(parents=True)
        latest_path.write_text("old,data\n", encoding="utf-8")

        write_retry_queue(report_dir, latest_path, [], RetryQueueMetadata(source_run_id="run-001"))

        assert (report_dir / "retry-nodeids.csv").read_text(encoding="utf-8").splitlines() == [",".join(CSV_HEADER)]
        assert latest_path.read_text(encoding="utf-8").splitlines() == [",".join(CSV_HEADER)]


def _read_csv(path):
    with path.open(newline="", encoding="utf-8") as file:
        return list(csv.DictReader(file))
