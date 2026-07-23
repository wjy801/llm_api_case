from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from governance.flaky_models import FlakyStatus, FlakyTestResult


CSV_HEADER = [
    "schema_version",
    "source_run_id",
    "source_git_commit",
    "generated_at",
    "priority",
    "nodeid",
    "status",
    "attempt_count",
    "first_failure_type",
]
SCHEMA_VERSION = "1"
RETRY_STATUS_PRIORITY = {
    FlakyStatus.RETRY_FAILED: 0,
    FlakyStatus.RETRY_PASSED: 1,
}


@dataclass(frozen=True)
class RetryQueueMetadata:
    source_run_id: str
    source_git_commit: str = ""
    generated_at: str = ""

    @classmethod
    def create(cls) -> "RetryQueueMetadata":
        now = datetime.now().astimezone()
        return cls(
            source_run_id=now.strftime("%Y%m%d_%H%M%S"),
            generated_at=now.isoformat(timespec="seconds"),
        )


def write_retry_queue(
    report_dir: Path,
    latest_path: Path,
    results: list[FlakyTestResult],
    metadata: RetryQueueMetadata | None = None,
) -> None:
    metadata = metadata or RetryQueueMetadata.create()
    rows = build_retry_queue_rows(results, metadata)

    report_dir.mkdir(parents=True, exist_ok=True)
    _write_csv_atomic(report_dir / "retry-nodeids.csv", rows)

    latest_path.parent.mkdir(parents=True, exist_ok=True)
    _write_csv_atomic(latest_path, rows)


def build_retry_queue_rows(
    results: list[FlakyTestResult],
    metadata: RetryQueueMetadata,
) -> list[dict[str, str]]:
    rows_by_nodeid: dict[str, dict[str, str]] = {}
    for result in results:
        priority = RETRY_STATUS_PRIORITY.get(result.status)
        if priority is None:
            continue

        existing_row = rows_by_nodeid.get(result.nodeid)
        row = _result_to_row(result, priority, metadata)
        if existing_row is None or int(row["priority"]) < int(existing_row["priority"]):
            rows_by_nodeid[result.nodeid] = row

    return sorted(rows_by_nodeid.values(), key=lambda row: (int(row["priority"]), row["nodeid"]))


def _result_to_row(
    result: FlakyTestResult,
    priority: int,
    metadata: RetryQueueMetadata,
) -> dict[str, str]:
    return {
        "schema_version": SCHEMA_VERSION,
        "source_run_id": metadata.source_run_id,
        "source_git_commit": metadata.source_git_commit,
        "generated_at": metadata.generated_at,
        "priority": str(priority),
        "nodeid": result.nodeid,
        "status": result.status.value,
        "attempt_count": str(result.attempt_count),
        "first_failure_type": _first_failure_type(result),
    }


def _first_failure_type(result: FlakyTestResult) -> str:
    for attempt in result.attempts:
        if attempt.failure_type:
            return attempt.failure_type
    return ""


def _write_csv_atomic(path: Path, rows: list[dict[str, str]]) -> None:
    temporary_path = path.with_name(path.name + ".tmp")
    with temporary_path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=CSV_HEADER)
        writer.writeheader()
        writer.writerows(rows)
    temporary_path.replace(path)
