from __future__ import annotations

from pathlib import Path

from module.protocol_testing.model_csv_loader import load_model_ids_from_csv


def load_anthropic_message_model_ids(csv_path: str | Path) -> list[str]:
    return load_model_ids_from_csv(csv_path)
