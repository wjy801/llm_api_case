from __future__ import annotations

import csv
from pathlib import Path

import pytest


PROTOCOL_TESTING_ROOT = Path(__file__).resolve().parent
MODEL_ID_COLUMN_NAME = "model_id"
PROTOCOL_MODEL_CSV_OPTION = "protocol_model_csv"


def load_model_ids_from_csv(csv_path: str | Path, column_name: str = MODEL_ID_COLUMN_NAME) -> list[str]:
    path = resolve_protocol_testing_path(csv_path)
    if not path.exists() or path.stat().st_size == 0:
        return []

    with path.open("r", encoding="utf-8-sig", newline="") as file:
        rows = list(csv.reader(file))

    if not rows:
        return []

    header = [cell.strip() for cell in rows[0]]
    if column_name in header:
        model_id_index = header.index(column_name)
        model_ids = [_cell_at(row, model_id_index) for row in rows[1:]]
    else:
        model_ids = [_cell_at(row, 0) for row in rows]

    return _deduplicate_model_ids(model_ids)


def load_protocol_model_ids(config: pytest.Config, protocol: str) -> list[str]:
    csv_path = get_protocol_model_csv_path(config)
    if csv_path is None:
        return []
    return load_model_ids_from_csv(csv_path)


def get_protocol_model_csv_path(config: pytest.Config) -> Path | None:
    csv_path = _get_config_option(config, PROTOCOL_MODEL_CSV_OPTION)
    if not csv_path:
        return None
    return resolve_protocol_testing_path(csv_path)


def resolve_protocol_testing_path(path_value: str | Path) -> Path:
    path = Path(path_value)
    if path.is_absolute():
        return path
    return (PROTOCOL_TESTING_ROOT / path).resolve()


def _get_config_option(config: pytest.Config, option_name: str) -> str | None:
    value = config.getoption(option_name, default=None)
    if value is None:
        return None

    value = str(value).strip()
    return value or None


def _cell_at(row: list[str], index: int) -> str:
    if index >= len(row):
        return ""
    return row[index].strip()


def _deduplicate_model_ids(model_ids: list[str]) -> list[str]:
    model_ids = [model_id for model_id in model_ids if model_id]
    return list(dict.fromkeys(model_ids))
