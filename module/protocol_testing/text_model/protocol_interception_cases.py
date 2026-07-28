from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from module.protocol_testing.model_csv_loader import resolve_protocol_testing_path


PROTOCOL_INTERCEPTION_CSV_PATH = Path("text_model/protocol_interception.csv")
REQUIRED_COLUMNS = ("case_id", "protocol_path", "body_protocol", "model_id", "expected")
SUPPORTED_PROTOCOL_PATHS = {"openai_chat_completions", "anthropic_messages"}
SUPPORTED_BODY_PROTOCOLS = {"openai", "anthropic"}
SUPPORTED_EXPECTED_RESULTS = {"allow", "block"}


@dataclass(frozen=True)
class ProtocolInterceptionCase:
    case_id: str
    protocol_path: str
    body_protocol: str
    model_id: str
    expected: str


def load_protocol_interception_cases(
    csv_path: str | Path = PROTOCOL_INTERCEPTION_CSV_PATH,
) -> list[ProtocolInterceptionCase]:
    path = resolve_protocol_testing_path(csv_path)
    if not path.exists() or path.stat().st_size == 0:
        return []

    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        _validate_header(reader.fieldnames, path)
        cases = [_build_case(row, path, line_number) for line_number, row in enumerate(reader, start=2)]

    return cases


def _validate_header(fieldnames: list[str] | None, path: Path) -> None:
    if not fieldnames:
        raise ValueError(f"协议拦截 CSV 缺少表头: {path}")

    missing_columns = [column for column in REQUIRED_COLUMNS if column not in fieldnames]
    if missing_columns:
        raise ValueError(f"协议拦截 CSV 缺少字段 {missing_columns!r}: {path}")


def _build_case(row: dict[str, str], path: Path, line_number: int) -> ProtocolInterceptionCase:
    case = ProtocolInterceptionCase(
        case_id=_required_cell(row, "case_id", path, line_number),
        protocol_path=_required_cell(row, "protocol_path", path, line_number),
        body_protocol=_required_cell(row, "body_protocol", path, line_number),
        model_id=_required_cell(row, "model_id", path, line_number),
        expected=_required_cell(row, "expected", path, line_number),
    )
    _validate_case(case, path, line_number)
    return case


def _required_cell(row: dict[str, str], column_name: str, path: Path, line_number: int) -> str:
    value = (row.get(column_name) or "").strip()
    if not value:
        raise ValueError(f"协议拦截 CSV 第 {line_number} 行字段 {column_name!r} 为空: {path}")
    return value


def _validate_case(case: ProtocolInterceptionCase, path: Path, line_number: int) -> None:
    if case.protocol_path not in SUPPORTED_PROTOCOL_PATHS:
        raise ValueError(
            f"协议拦截 CSV 第 {line_number} 行 protocol_path 不支持: {case.protocol_path!r}, path={path}"
        )
    if case.body_protocol not in SUPPORTED_BODY_PROTOCOLS:
        raise ValueError(
            f"协议拦截 CSV 第 {line_number} 行 body_protocol 不支持: {case.body_protocol!r}, path={path}"
        )
    if case.expected not in SUPPORTED_EXPECTED_RESULTS:
        raise ValueError(f"协议拦截 CSV 第 {line_number} 行 expected 不支持: {case.expected!r}, path={path}")
