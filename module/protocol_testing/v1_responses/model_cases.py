from __future__ import annotations

import csv
from pathlib import Path


MODEL_ID_CSV_PATH = Path(__file__).resolve().parent / "../anthropic.csv"
MODEL_ID_COLUMN_NAME = "model_id"


def load_response_model_ids(csv_path: str | Path = MODEL_ID_CSV_PATH) -> list[str]:
    path = Path(csv_path)
    if not path.exists() or path.stat().st_size == 0:
        return []

    with path.open("r", encoding="utf-8-sig", newline="") as file:
        rows = list(csv.reader(file))

    if not rows:
        return []

    header = [cell.strip() for cell in rows[0]]
    if MODEL_ID_COLUMN_NAME in header:
        model_id_index = header.index(MODEL_ID_COLUMN_NAME)
        model_ids = [_cell_at(row, model_id_index) for row in rows[1:]]
    else:
        model_ids = [_cell_at(row, 0) for row in rows]

    return _deduplicate_model_ids(model_ids)


def _cell_at(row: list[str], index: int) -> str:
    if index >= len(row):
        return ""
    return row[index].strip()


def _deduplicate_model_ids(model_ids: list[str]) -> list[str]:
    model_ids = [model_id for model_id in model_ids if model_id]
    return list(dict.fromkeys(model_ids))
