from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import os
from pathlib import Path


QUALITY_ENABLE_ENV = "QUALITY_ENABLE"
QUALITY_RUN_ID_ENV = "QUALITY_RUN_ID"
QUALITY_EXECUTION_ID_ENV = "QUALITY_EXECUTION_ID"
QUALITY_OUTPUT_DIR_ENV = "QUALITY_OUTPUT_DIR"
QUALITY_SHADOW_GATE_ENV = "QUALITY_SHADOW_GATE"
QUALITY_MIN_REQUEST_SAMPLES_ENV = "QUALITY_MIN_REQUEST_SAMPLES"
QUALITY_HTTP_5XX_WARN_RATE_ENV = "QUALITY_HTTP_5XX_WARN_RATE"
QUALITY_TIMEOUT_WARN_RATE_ENV = "QUALITY_TIMEOUT_WARN_RATE"
DEFAULT_QUALITY_OUTPUT_DIR = Path("reports/quality")
DEFAULT_QUALITY_SHADOW_GATE = True
DEFAULT_QUALITY_MIN_REQUEST_SAMPLES = 20
DEFAULT_QUALITY_HTTP_5XX_WARN_RATE = 0.02
DEFAULT_QUALITY_TIMEOUT_WARN_RATE = 0.05

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"", "0", "false", "no", "off"})


@dataclass(frozen=True)
class QualityRuntimeConfig:
    enabled: bool
    run_id: str | None
    execution_id: str | None
    output_dir: Path


@dataclass(frozen=True)
class QualityReportConfig:
    shadow_gate: bool = DEFAULT_QUALITY_SHADOW_GATE
    min_request_samples: int = DEFAULT_QUALITY_MIN_REQUEST_SAMPLES
    http_5xx_warn_rate: float = DEFAULT_QUALITY_HTTP_5XX_WARN_RATE
    timeout_warn_rate: float = DEFAULT_QUALITY_TIMEOUT_WARN_RATE


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
    return parse_boolean_setting(value, name=QUALITY_ENABLE_ENV, default=False)


def load_quality_report_config(
    environ: Mapping[str, str] | None = None,
) -> QualityReportConfig:
    values = os.environ if environ is None else environ
    return QualityReportConfig(
        shadow_gate=parse_boolean_setting(
            values.get(QUALITY_SHADOW_GATE_ENV),
            name=QUALITY_SHADOW_GATE_ENV,
            default=DEFAULT_QUALITY_SHADOW_GATE,
        ),
        min_request_samples=_parse_nonnegative_int(
            values.get(QUALITY_MIN_REQUEST_SAMPLES_ENV),
            name=QUALITY_MIN_REQUEST_SAMPLES_ENV,
            default=DEFAULT_QUALITY_MIN_REQUEST_SAMPLES,
        ),
        http_5xx_warn_rate=_parse_rate(
            values.get(QUALITY_HTTP_5XX_WARN_RATE_ENV),
            name=QUALITY_HTTP_5XX_WARN_RATE_ENV,
            default=DEFAULT_QUALITY_HTTP_5XX_WARN_RATE,
        ),
        timeout_warn_rate=_parse_rate(
            values.get(QUALITY_TIMEOUT_WARN_RATE_ENV),
            name=QUALITY_TIMEOUT_WARN_RATE_ENV,
            default=DEFAULT_QUALITY_TIMEOUT_WARN_RATE,
        ),
    )


def parse_boolean_setting(value: str | None, *, name: str, default: bool) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    raise ValueError(f"invalid {name} value: {value!r}")


def _parse_nonnegative_int(value: str | None, *, name: str, default: int) -> int:
    if value is None or not value.strip():
        return default
    try:
        parsed = int(value)
    except ValueError as error:
        raise ValueError(f"invalid {name} value: {value!r}") from error
    if parsed < 0:
        raise ValueError(f"{name} must be greater than or equal to 0")
    return parsed


def _parse_rate(value: str | None, *, name: str, default: float) -> float:
    if value is None or not value.strip():
        return default
    try:
        parsed = float(value)
    except ValueError as error:
        raise ValueError(f"invalid {name} value: {value!r}") from error
    if not 0 <= parsed <= 1:
        raise ValueError(f"{name} must be between 0 and 1")
    return parsed


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None
