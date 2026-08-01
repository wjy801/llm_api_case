from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import os
from pathlib import Path


QUALITY_ENABLE_ENV = "QUALITY_ENABLE"
QUALITY_SEMANTIC_ENABLE_ENV = "QUALITY_SEMANTIC_ENABLE"
QUALITY_METRICS_ENABLE_ENV = "QUALITY_METRICS_ENABLE"
QUALITY_P1_REPORT_ENABLE_ENV = "QUALITY_P1_REPORT_ENABLE"
QUALITY_FLAKY_HISTORY_ENABLE_ENV = "QUALITY_FLAKY_HISTORY_ENABLE"
QUALITY_FLAKY_STATE_ENABLE_ENV = "QUALITY_FLAKY_STATE_ENABLE"
QUALITY_FLAKY_DB_PATH_ENV = "QUALITY_FLAKY_DB_PATH"
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
    semantic_enabled: bool = False
    semantic_warning: str | None = None
    metrics_enabled: bool = False
    metrics_warning: str | None = None
    p1_report_enabled: bool = False
    p1_report_warning: str | None = None
    flaky_history_enabled: bool = False
    flaky_database_path: Path | None = None
    flaky_history_warning: str | None = None
    flaky_state_enabled: bool = False
    flaky_state_warning: str | None = None


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
    enabled = parse_quality_enabled(values.get(QUALITY_ENABLE_ENV))
    semantic_enabled, semantic_warning = _parse_semantic_setting(
        values.get(QUALITY_SEMANTIC_ENABLE_ENV),
    )
    metrics_requested, metrics_warning = _parse_metrics_setting(
        values.get(QUALITY_METRICS_ENABLE_ENV),
    )
    p1_report_requested, p1_report_warning = _parse_p1_report_setting(
        values.get(QUALITY_P1_REPORT_ENABLE_ENV),
    )
    flaky_requested, flaky_history_warning = _parse_flaky_history_setting(
        values.get(QUALITY_FLAKY_HISTORY_ENABLE_ENV),
    )
    flaky_state_requested, flaky_state_warning = _parse_flaky_state_setting(
        values.get(QUALITY_FLAKY_STATE_ENABLE_ENV),
    )
    flaky_database_path_text = _optional_text(values.get(QUALITY_FLAKY_DB_PATH_ENV))
    flaky_database_path = (
        Path(flaky_database_path_text) if flaky_database_path_text is not None else None
    )
    flaky_history_enabled = enabled and flaky_requested
    metrics_enabled = enabled and semantic_enabled and metrics_requested
    if metrics_requested and not metrics_enabled and metrics_warning is None:
        metrics_warning = (
            f"{QUALITY_METRICS_ENABLE_ENV} requires QUALITY_ENABLE=1 and "
            f"{QUALITY_SEMANTIC_ENABLE_ENV}=1"
        )
    p1_report_enabled = enabled and p1_report_requested
    if p1_report_requested and not p1_report_enabled and p1_report_warning is None:
        p1_report_warning = f"{QUALITY_P1_REPORT_ENABLE_ENV} requires QUALITY_ENABLE=1"
    flaky_state_enabled = flaky_history_enabled and flaky_state_requested
    if flaky_state_requested and not flaky_state_enabled and flaky_state_warning is None:
        flaky_state_warning = (
            f"{QUALITY_FLAKY_STATE_ENABLE_ENV} requires QUALITY_ENABLE=1 and "
            f"{QUALITY_FLAKY_HISTORY_ENABLE_ENV}=1"
        )
    if flaky_history_enabled and flaky_history_warning is None:
        flaky_history_warning = _validate_flaky_database_path(flaky_database_path)
    return QualityRuntimeConfig(
        enabled=enabled,
        run_id=_optional_text(values.get(QUALITY_RUN_ID_ENV)),
        execution_id=_optional_text(values.get(QUALITY_EXECUTION_ID_ENV)),
        output_dir=Path(output_dir or default_output_dir),
        semantic_enabled=enabled and semantic_enabled,
        semantic_warning=semantic_warning,
        metrics_enabled=metrics_enabled,
        metrics_warning=metrics_warning,
        p1_report_enabled=p1_report_enabled,
        p1_report_warning=p1_report_warning,
        flaky_history_enabled=flaky_history_enabled,
        flaky_database_path=flaky_database_path,
        flaky_history_warning=flaky_history_warning,
        flaky_state_enabled=flaky_state_enabled,
        flaky_state_warning=flaky_state_warning,
    )


def parse_quality_enabled(value: str | None) -> bool:
    return parse_boolean_setting(value, name=QUALITY_ENABLE_ENV, default=False)


def _parse_semantic_setting(value: str | None) -> tuple[bool, str | None]:
    try:
        return (
            parse_boolean_setting(
                value,
                name=QUALITY_SEMANTIC_ENABLE_ENV,
                default=False,
            ),
            None,
        )
    except ValueError as error:
        return False, str(error)


def _parse_metrics_setting(value: str | None) -> tuple[bool, str | None]:
    try:
        return (
            parse_boolean_setting(
                value,
                name=QUALITY_METRICS_ENABLE_ENV,
                default=False,
            ),
            None,
        )
    except ValueError as error:
        return False, str(error)


def _parse_p1_report_setting(value: str | None) -> tuple[bool, str | None]:
    try:
        return (
            parse_boolean_setting(
                value,
                name=QUALITY_P1_REPORT_ENABLE_ENV,
                default=False,
            ),
            None,
        )
    except ValueError as error:
        return False, str(error)


def _parse_flaky_history_setting(value: str | None) -> tuple[bool, str | None]:
    try:
        return (
            parse_boolean_setting(
                value,
                name=QUALITY_FLAKY_HISTORY_ENABLE_ENV,
                default=False,
            ),
            None,
        )
    except ValueError as error:
        return False, str(error)


def _parse_flaky_state_setting(value: str | None) -> tuple[bool, str | None]:
    try:
        return (
            parse_boolean_setting(
                value,
                name=QUALITY_FLAKY_STATE_ENABLE_ENV,
                default=False,
            ),
            None,
        )
    except ValueError as error:
        return False, str(error)


def _validate_flaky_database_path(path: Path | None) -> str | None:
    if path is None:
        return f"{QUALITY_FLAKY_DB_PATH_ENV} is required when Flaky history is enabled"
    if not path.is_absolute():
        return f"{QUALITY_FLAKY_DB_PATH_ENV} must be an absolute persistent path"
    if str(path).startswith(("\\\\", "//")):
        return (
            f"{QUALITY_FLAKY_DB_PATH_ENV} network share requires an explicit "
            "SQLite locking review"
        )
    parent = path.parent
    if not parent.exists() or not parent.is_dir():
        return f"{QUALITY_FLAKY_DB_PATH_ENV} parent directory must already exist"
    if path.exists() and not path.is_file():
        return f"{QUALITY_FLAKY_DB_PATH_ENV} must point to a regular file"
    if not os.access(parent, os.W_OK):
        return f"{QUALITY_FLAKY_DB_PATH_ENV} parent directory is not writable"
    return None


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
