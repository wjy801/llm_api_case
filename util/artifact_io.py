"""Domain-neutral readers and byte-level integrity helpers for artifacts."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from collections.abc import Iterator, Mapping
from typing import Any


class ArtifactFormatError(ValueError):
    """An artifact is readable but does not have the required JSON shape."""


class ArtifactJsonLineError(ArtifactFormatError):
    def __init__(self, line_number: int, error: Exception) -> None:
        super().__init__(f"invalid JSON on line {line_number}: {type(error).__name__}")
        self.line_number = line_number
        self.error = error


@dataclass(frozen=True)
class JsonLine:
    number: int
    value: Any


@dataclass(frozen=True)
class HashComparison:
    expected: str | None
    actual: str

    @property
    def matches(self) -> bool:
        return self.expected is not None and self.expected == self.actual


def read_json_object(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    value = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ArtifactFormatError(f"JSON object required: {source}")
    return value


def read_jsonl_values(path: str | Path) -> Iterator[JsonLine]:
    source = Path(path)
    with source.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as error:
                raise ArtifactJsonLineError(line_number, error) from error
            yield JsonLine(line_number, value)


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def compare_file_sha256(
    path: str | Path,
    expected: object,
) -> HashComparison:
    normalized = expected if isinstance(expected, str) and expected else None
    return HashComparison(expected=normalized, actual=file_sha256(path))


def exact_field_mismatches(
    payload: Mapping[str, Any],
    expected_fields: Mapping[str, Any],
) -> tuple[str, ...]:
    return tuple(
        name
        for name, expected in expected_fields.items()
        if payload.get(name) != expected
    )


__all__ = (
    "ArtifactFormatError",
    "ArtifactJsonLineError",
    "HashComparison",
    "JsonLine",
    "compare_file_sha256",
    "exact_field_mismatches",
    "file_sha256",
    "read_json_object",
    "read_jsonl_values",
)
