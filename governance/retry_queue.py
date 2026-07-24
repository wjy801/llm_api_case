from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import re

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
ROOT_RETRY_QUEUE_OUTPUT_DIR = Path(__file__).resolve().parents[1] / "flaky_retry_queues"


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
    root_output_dir: Path | None = None,
) -> None:
    metadata = metadata or RetryQueueMetadata.create()
    rows = build_retry_queue_rows(results, metadata)

    report_dir.mkdir(parents=True, exist_ok=True)
    _write_csv_atomic(report_dir / "retry-nodeids.csv", rows)

    latest_path.parent.mkdir(parents=True, exist_ok=True)
    _write_csv_atomic(latest_path, rows)
    _write_root_csv_outputs(
        root_output_dir or ROOT_RETRY_QUEUE_OUTPUT_DIR,
        metadata,
        {
            "retry-nodeids.csv": rows,
            "latest-retry-nodeids.csv": rows,
        },
    )


def update_retry_queue_after_rerun(
    latest_path: Path,
    existing_rows: list[dict[str, str]],
    results: list[FlakyTestResult],
    metadata: RetryQueueMetadata | None = None,
    root_output_dir: Path | None = None,
) -> None:
    metadata = metadata or RetryQueueMetadata.create()
    rows = build_retry_queue_rows_after_rerun(existing_rows, results, metadata)
    latest_path.parent.mkdir(parents=True, exist_ok=True)
    _write_csv_atomic(latest_path, rows)
    _write_root_csv_outputs(
        root_output_dir or ROOT_RETRY_QUEUE_OUTPUT_DIR,
        metadata,
        {"latest-retry-nodeids.csv": rows},
    )


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


def build_retry_queue_rows_after_rerun(
    existing_rows: list[dict[str, str]],
    results: list[FlakyTestResult],
    metadata: RetryQueueMetadata,
) -> list[dict[str, str]]:
    rows_by_nodeid = _deduplicate_rows(existing_rows)

    for result in results:
        priority = RETRY_STATUS_PRIORITY.get(result.status)
        if priority is not None:
            rows_by_nodeid[result.nodeid] = _result_to_row(result, priority, metadata)
            continue

        if result.status in {FlakyStatus.PASSED, FlakyStatus.FAILED}:
            rows_by_nodeid.pop(result.nodeid, None)

    return sorted(rows_by_nodeid.values(), key=lambda row: (_priority_value(row), row["nodeid"]))


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


def _deduplicate_rows(rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    rows_by_nodeid: dict[str, dict[str, str]] = {}
    for row in rows:
        normalized_row = {field: row.get(field, "") for field in CSV_HEADER}
        nodeid = normalized_row["nodeid"]
        existing_row = rows_by_nodeid.get(nodeid)
        if existing_row is None or _priority_value(normalized_row) < _priority_value(existing_row):
            rows_by_nodeid[nodeid] = normalized_row
    return rows_by_nodeid


def _priority_value(row: dict[str, str]) -> int:
    try:
        return int(row["priority"])
    except ValueError:
        return 999


def _write_root_csv_outputs(
    root_output_dir: Path,
    metadata: RetryQueueMetadata,
    files: dict[str, list[dict[str, str]]],
) -> None:
    output_dir = root_output_dir / _date_partition(metadata) / _safe_path_part(metadata.source_run_id)
    output_dir.mkdir(parents=True, exist_ok=True)
    for filename, rows in files.items():
        _write_csv_atomic(output_dir / filename, rows)


def _date_partition(metadata: RetryQueueMetadata) -> str:
    if metadata.generated_at:
        try:
            return datetime.fromisoformat(metadata.generated_at).date().isoformat()
        except ValueError:
            pass

    if re.fullmatch(r"\d{8}.*", metadata.source_run_id):
        return (
            f"{metadata.source_run_id[0:4]}-"
            f"{metadata.source_run_id[4:6]}-"
            f"{metadata.source_run_id[6:8]}"
        )

    return datetime.now().astimezone().date().isoformat()


def _safe_path_part(value: str) -> str:
    safe_value = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._")
    return safe_value or "unknown-run"


def _write_csv_atomic(path: Path, rows: list[dict[str, str]]) -> None:
    temporary_path = path.with_name(path.name + ".tmp")
    with temporary_path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=CSV_HEADER)
        writer.writeheader()
        writer.writerows(rows)
    temporary_path.replace(path)
