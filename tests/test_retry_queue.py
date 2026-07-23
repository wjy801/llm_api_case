from __future__ import annotations

import csv

import pytest

from governance.flaky_models import AttemptOutcome, AttemptResult, FlakyStatus, FlakyTestResult
from governance.retry_queue import (
    CSV_HEADER,
    RetryQueueMetadata,
    build_retry_queue_rows,
    build_retry_queue_rows_after_rerun,
    update_retry_queue_after_rerun,
    write_retry_queue,
)


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


class TestBuildRetryQueueRowsAfterRerun:
    def test_removes_passed_and_failed_results_from_next_queue(self):
        metadata = RetryQueueMetadata(source_run_id="rerun-001", generated_at="now")
        passed_nodeid = "module/test_demo.py::test_now_stable"
        failed_nodeid = "module/test_demo.py::test_product_failure"
        existing_rows = [
            _row(passed_nodeid, "retry_failed", "0"),
            _row(failed_nodeid, "retry_passed", "1"),
        ]

        rows = build_retry_queue_rows_after_rerun(
            existing_rows,
            [
                FlakyTestResult(passed_nodeid, FlakyStatus.PASSED),
                FlakyTestResult(failed_nodeid, FlakyStatus.FAILED),
            ],
            metadata,
        )

        assert rows == []

    def test_keeps_retry_statuses_and_updates_metadata(self):
        metadata = RetryQueueMetadata(source_run_id="rerun-001", source_git_commit="commit-2", generated_at="later")
        retry_failed_nodeid = "module/test_demo.py::test_retry_failed"
        retry_passed_nodeid = "module/test_demo.py::test_retry_passed"

        rows = build_retry_queue_rows_after_rerun(
            [
                _row(retry_failed_nodeid, "retry_passed", "1"),
                _row(retry_passed_nodeid, "retry_failed", "0"),
            ],
            [
                retry_result(retry_failed_nodeid, FlakyStatus.RETRY_FAILED, "ReadTimeout"),
                retry_result(retry_passed_nodeid, FlakyStatus.RETRY_PASSED, "ConnectTimeout"),
            ],
            metadata,
        )

        assert [row["nodeid"] for row in rows] == [retry_failed_nodeid, retry_passed_nodeid]
        assert [row["status"] for row in rows] == ["retry_failed", "retry_passed"]
        assert [row["priority"] for row in rows] == ["0", "1"]
        assert rows[0]["source_run_id"] == "rerun-001"
        assert rows[0]["first_failure_type"] == "ReadTimeout"

    def test_partial_rerun_keeps_unexecuted_queue_items(self):
        metadata = RetryQueueMetadata(source_run_id="rerun-001")
        executed_nodeid = "module/test_demo.py::test_executed"
        unexecuted_nodeid = "module/test_demo.py::test_not_selected"

        rows = build_retry_queue_rows_after_rerun(
            [
                _row(executed_nodeid, "retry_failed", "0"),
                _row(unexecuted_nodeid, "retry_passed", "1"),
            ],
            [FlakyTestResult(executed_nodeid, FlakyStatus.PASSED)],
            metadata,
        )

        assert [row["nodeid"] for row in rows] == [unexecuted_nodeid]


class TestWriteRetryQueue:
    def test_writes_fixed_header_and_latest_copy(self, tmp_path):
        report_dir = tmp_path / "reports" / "run"
        latest_path = tmp_path / "reports" / "flaky" / "latest-retry-nodeids.csv"
        root_output_dir = tmp_path / "flaky_retry_queues"
        metadata = RetryQueueMetadata(source_run_id="run-001", source_git_commit="commit", generated_at="now")
        nodeid = "module/test_demo.py::TestDemo::test_case[param, 中文]"

        write_retry_queue(
            report_dir,
            latest_path,
            [retry_result(nodeid, FlakyStatus.RETRY_FAILED, "AssertionError")],
            metadata,
            root_output_dir=root_output_dir,
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
        root_output_dir = tmp_path / "flaky_retry_queues"
        latest_path.parent.mkdir(parents=True)
        latest_path.write_text("old,data\n", encoding="utf-8")

        write_retry_queue(
            report_dir,
            latest_path,
            [],
            RetryQueueMetadata(source_run_id="run-001"),
            root_output_dir=root_output_dir,
        )

        assert (report_dir / "retry-nodeids.csv").read_text(encoding="utf-8").splitlines() == [",".join(CSV_HEADER)]
        assert latest_path.read_text(encoding="utf-8").splitlines() == [",".join(CSV_HEADER)]

    def test_writes_root_archive_partitioned_by_date_and_run_id(self, tmp_path):
        report_dir = tmp_path / "reports" / "run"
        latest_path = tmp_path / "reports" / "flaky" / "latest-retry-nodeids.csv"
        root_output_dir = tmp_path / "flaky_retry_queues"
        metadata = RetryQueueMetadata(
            source_run_id="20260723_143015",
            source_git_commit="commit",
            generated_at="2026-07-23T14:30:15+08:00",
        )

        write_retry_queue(
            report_dir,
            latest_path,
            [retry_result("module/test_demo.py::test_case", FlakyStatus.RETRY_FAILED)],
            metadata,
            root_output_dir=root_output_dir,
        )

        archive_dir = root_output_dir / "2026-07-23" / "20260723_143015"
        assert (archive_dir / "retry-nodeids.csv").exists()
        assert (archive_dir / "latest-retry-nodeids.csv").exists()
        assert _read_csv(archive_dir / "retry-nodeids.csv")[0]["nodeid"] == "module/test_demo.py::test_case"


class TestUpdateRetryQueueAfterRerun:
    def test_writes_updated_latest_to_root_archive(self, tmp_path):
        latest_path = tmp_path / "reports" / "flaky" / "latest-retry-nodeids.csv"
        root_output_dir = tmp_path / "flaky_retry_queues"
        metadata = RetryQueueMetadata(
            source_run_id="20260724_091500",
            generated_at="2026-07-24T09:15:00+08:00",
        )
        nodeid = "module/test_demo.py::test_still_flaky"

        update_retry_queue_after_rerun(
            latest_path,
            [_row(nodeid, "retry_passed", "1")],
            [retry_result(nodeid, FlakyStatus.RETRY_FAILED, "ReadTimeout")],
            metadata,
            root_output_dir=root_output_dir,
        )

        archived_latest = root_output_dir / "2026-07-24" / "20260724_091500" / "latest-retry-nodeids.csv"
        assert _read_csv(latest_path)[0]["status"] == "retry_failed"
        assert _read_csv(archived_latest)[0]["first_failure_type"] == "ReadTimeout"


def _read_csv(path):
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
