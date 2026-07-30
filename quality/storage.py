from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass
from datetime import datetime
from enum import Enum
import json
import os
from pathlib import Path
import tempfile
from typing import Any

from pydantic import BaseModel


@dataclass(frozen=True)
class QualityDirectoryLayout:
    root: Path
    shards: Path
    merged: Path


def write_json_atomic(path: str | Path, data: Any) -> Path:
    target_path = Path(path)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(
        _to_jsonable(data),
        allow_nan=False,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )

    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix=f".{target_path.name}.",
            suffix=".tmp",
            dir=target_path.parent,
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            temporary_file.write(serialized)
            temporary_file.write("\n")
            temporary_file.flush()
            os.fsync(temporary_file.fileno())

        os.replace(temporary_path, target_path)
        temporary_path = None
        return target_path
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def append_jsonl(path: str | Path, record: Any) -> Path:
    target_path = Path(path)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(
        _to_jsonable(record),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    with target_path.open("a", encoding="utf-8", newline="\n") as file_handle:
        file_handle.write(serialized)
        file_handle.write("\n")
        file_handle.flush()
    return target_path


def read_jsonl(path: str | Path) -> list[Any]:
    target_path = Path(path)
    records: list[Any] = []
    with target_path.open("r", encoding="utf-8") as file_handle:
        for line_number, line in enumerate(file_handle, start=1):
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as error:
                raise ValueError(
                    f"Invalid JSONL at {target_path}:{line_number}: {error.msg}"
                ) from error
    return records


def ensure_quality_dirs(output_dir: str | Path) -> QualityDirectoryLayout:
    root = Path(output_dir)
    shards = root / "shards"
    merged = root / "merged"
    for directory in (root, shards, merged):
        directory.mkdir(parents=True, exist_ok=True)
    return QualityDirectoryLayout(root=root, shards=shards, merged=merged)


def _to_jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")

    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _to_jsonable(getattr(value, field.name))
            for field in fields(value)
        }

    if isinstance(value, Mapping):
        return {str(key): _to_jsonable(item) for key, item in value.items()}

    if isinstance(value, (list, tuple)):
        return [_to_jsonable(item) for item in value]

    if isinstance(value, (set, frozenset)):
        jsonable_items = [_to_jsonable(item) for item in value]
        return sorted(
            jsonable_items,
            key=lambda item: json.dumps(
                item,
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ),
        )

    if isinstance(value, Enum):
        return _to_jsonable(value.value)

    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("datetime must include timezone information")
        return value.isoformat()

    if isinstance(value, Path):
        return value.as_posix()

    if value is None or isinstance(value, (str, int, float, bool)):
        return value

    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")
