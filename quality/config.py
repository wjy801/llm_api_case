from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import os
from pathlib import Path


QUALITY_ENABLE_ENV = "QUALITY_ENABLE"
QUALITY_RUN_ID_ENV = "QUALITY_RUN_ID"
QUALITY_EXECUTION_ID_ENV = "QUALITY_EXECUTION_ID"
QUALITY_OUTPUT_DIR_ENV = "QUALITY_OUTPUT_DIR"
DEFAULT_QUALITY_OUTPUT_DIR = Path("reports/quality")

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"", "0", "false", "no", "off"})


@dataclass(frozen=True)
class QualityRuntimeConfig:
    enabled: bool
    run_id: str | None
    execution_id: str | None
    output_dir: Path


def load_quality_config(
    environ: Mapping[str, str] | None = None,
    *,
    default_output_dir: str | Path = DEFAULT_QUALITY_OUTPUT_DIR,
) -> QualityRuntimeConfig:
    values = os.environ if environ is None else environ
    output_dir = _optional_text(values.get(QUALITY_OUTPUT_DIR_ENV))
    return QualityRuntimeConfig(
        enabled=parse_quality_enabled(values.get(QUALITY_ENABLE_ENV)),
        run_id=_optional_text(values.get(QUALITY_RUN_ID_ENV)),
        execution_id=_optional_text(values.get(QUALITY_EXECUTION_ID_ENV)),
        output_dir=Path(output_dir or default_output_dir),
    )


def parse_quality_enabled(value: str | None) -> bool:
    normalized = "" if value is None else value.strip().lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    raise ValueError(f"invalid {QUALITY_ENABLE_ENV} value: {value!r}")


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None
