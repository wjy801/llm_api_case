from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from governance.flaky_models import FlakyStatus
from governance.retry_queue import CSV_HEADER


@dataclass(frozen=True)
class RetryQueueEntry:
    row: dict[str, str]

    @property
    def nodeid(self) -> str:
        return self.row["nodeid"]

    @property
    def status(self) -> str:
        return self.row["status"]


@dataclass(frozen=True)
class NodeIdValidationResult:
    valid_entries: tuple[RetryQueueEntry, ...]
    stale_entries: tuple[RetryQueueEntry, ...]

    @property
    def valid_nodeids(self) -> list[str]:
        return [entry.nodeid for entry in self.valid_entries]

    @property
    def valid_count(self) -> int:
        return len(self.valid_entries)

    @property
    def stale_count(self) -> int:
        return len(self.stale_entries)


def read_retry_queue(
    path: Path,
    *,
    status_filter: FlakyStatus | None = None,
) -> list[RetryQueueEntry]:
    with path.open(newline="", encoding="utf-8-sig") as file:
        reader = csv.DictReader(file)
        _validate_header(reader.fieldnames)
        entries = []
        for row in reader:
            normalized_row = {field: row.get(field, "") for field in CSV_HEADER}
            if status_filter is not None and normalized_row["status"] != status_filter.value:
                continue
            entries.append(RetryQueueEntry(normalized_row))
        return entries


def validate_nodeids(
    entries: Iterable[RetryQueueEntry],
    collected_nodeids: Iterable[str],
) -> NodeIdValidationResult:
    collected = set(collected_nodeids)
    valid_entries: list[RetryQueueEntry] = []
    stale_entries: list[RetryQueueEntry] = []
    seen_valid_nodeids: set[str] = set()
    seen_stale_nodeids: set[str] = set()

    for entry in entries:
        if entry.nodeid in collected:
            if entry.nodeid not in seen_valid_nodeids:
                valid_entries.append(entry)
                seen_valid_nodeids.add(entry.nodeid)
            continue

        if entry.nodeid not in seen_stale_nodeids:
            stale_entries.append(entry)
            seen_stale_nodeids.add(entry.nodeid)

    return NodeIdValidationResult(
        valid_entries=tuple(valid_entries),
        stale_entries=tuple(stale_entries),
    )


def write_stale_retry_queue(path: Path, stale_entries: Iterable[RetryQueueEntry]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(path.name + ".tmp")
    with temporary_path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=CSV_HEADER)
        writer.writeheader()
        for entry in stale_entries:
            writer.writerow(entry.row)
    temporary_path.replace(path)


def _validate_header(fieldnames: list[str] | None) -> None:
    if fieldnames != CSV_HEADER:
        raise ValueError(f"retry queue csv header must be: {','.join(CSV_HEADER)}")
