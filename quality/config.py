from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import os
from pathlib import Path


QUALITY_ENABLE_ENV = "QUALITY_ENABLE"
QUALITY_SEMANTIC_ENABLE_ENV = "QUALITY_SEMANTIC_ENABLE"
QUALITY_METRICS_ENABLE_ENV = "QUALITY_METRICS_ENABLE"
QUALITY_FLAKY_HISTORY_ENABLE_ENV = "QUALITY_FLAKY_HISTORY_ENABLE"
QUALITY_FLAKY_STATE_ENABLE_ENV = "QUALITY_FLAKY_STATE_ENABLE"
QUALITY_FLAKY_DB_PATH_ENV = "QUALITY_FLAKY_DB_PATH"
QUALITY_FLAKY_AUTO_SKIP_ENABLE_ENV = "QUALITY_FLAKY_AUTO_SKIP_ENABLE"
QUALITY_FLAKY_SKIP_MODE_ENV = "QUALITY_FLAKY_SKIP_MODE"
QUALITY_FLAKY_SNAPSHOT_MAX_AGE_MINUTES_ENV = (
    "QUALITY_FLAKY_SNAPSHOT_MAX_AGE_MINUTES"
)
QUALITY_FLAKY_DECISION_PLAN_PATH_ENV = "QUALITY_FLAKY_DECISION_PLAN_PATH"
QUALITY_FLAKY_DECISION_CHECKSUM_ENV = "QUALITY_FLAKY_DECISION_CHECKSUM"
QUALITY_FLAKY_DASHBOARD_HOST_ENV = "QUALITY_FLAKY_DASHBOARD_HOST"
QUALITY_FLAKY_DASHBOARD_PORT_ENV = "QUALITY_FLAKY_DASHBOARD_PORT"
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
    semantic_enabled: bool = False
    semantic_warning: str | None = None
    metrics_enabled: bool = False
    metrics_warning: str | None = None
    flaky_history_enabled: bool = False
    flaky_database_path: Path | None = None
    flaky_history_warning: str | None = None
    flaky_state_enabled: bool = False
    flaky_state_warning: str | None = None
    flaky_auto_skip_enabled: bool = False
    flaky_skip_mode_requested: str = "off"
    flaky_skip_mode_effective: str = "off"
    flaky_skip_warning: str | None = None
    flaky_snapshot_max_age_minutes: int = 15
    flaky_decision_plan_path: Path | None = None
    flaky_decision_checksum: str | None = None
    flaky_dashboard_host: str = "127.0.0.1"
    flaky_dashboard_port: int = 8765
    flaky_dashboard_warning: str | None = None


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
    flaky_requested, flaky_history_warning = _parse_flaky_history_setting(
        values.get(QUALITY_FLAKY_HISTORY_ENABLE_ENV),
    )
    flaky_state_requested, flaky_state_warning = _parse_flaky_state_setting(
        values.get(QUALITY_FLAKY_STATE_ENABLE_ENV),
    )
    auto_skip_enabled, auto_skip_warning = _parse_fail_closed_boolean(
        values.get(QUALITY_FLAKY_AUTO_SKIP_ENABLE_ENV),
        name=QUALITY_FLAKY_AUTO_SKIP_ENABLE_ENV,
    )
    mode_requested, mode_effective, mode_warning = _parse_skip_mode(
        values.get(QUALITY_FLAKY_SKIP_MODE_ENV),
        enabled=auto_skip_enabled,
    )
    snapshot_age, age_warning = _parse_snapshot_age(
        values.get(QUALITY_FLAKY_SNAPSHOT_MAX_AGE_MINUTES_ENV)
    )
    dashboard_host, dashboard_port, dashboard_warning = _parse_dashboard_binding(
        values.get(QUALITY_FLAKY_DASHBOARD_HOST_ENV),
        values.get(QUALITY_FLAKY_DASHBOARD_PORT_ENV),
    )
    flaky_database_path_text = _optional_text(values.get(QUALITY_FLAKY_DB_PATH_ENV))
    flaky_database_path = (
        Path(flaky_database_path_text) if flaky_database_path_text is not None else None
    )
    decision_plan_text = _optional_text(
        values.get(QUALITY_FLAKY_DECISION_PLAN_PATH_ENV)
    )
    flaky_history_enabled = enabled and flaky_requested
    metrics_enabled = enabled and semantic_enabled and metrics_requested
    if metrics_requested and not metrics_enabled and metrics_warning is None:
        metrics_warning = (
            f"{QUALITY_METRICS_ENABLE_ENV} requires QUALITY_ENABLE=1 and "
            f"{QUALITY_SEMANTIC_ENABLE_ENV}=1"
        )
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
        flaky_history_enabled=flaky_history_enabled,
        flaky_database_path=flaky_database_path,
        flaky_history_warning=flaky_history_warning,
        flaky_state_enabled=flaky_state_enabled,
        flaky_state_warning=flaky_state_warning,
        flaky_auto_skip_enabled=auto_skip_enabled,
        flaky_skip_mode_requested=mode_requested,
        flaky_skip_mode_effective=mode_effective,
        flaky_skip_warning=_join_warnings(
            auto_skip_warning,
            mode_warning,
            age_warning,
        ),
        flaky_snapshot_max_age_minutes=snapshot_age,
        flaky_decision_plan_path=(
            Path(decision_plan_text) if decision_plan_text is not None else None
        ),
        flaky_decision_checksum=_optional_text(
            values.get(QUALITY_FLAKY_DECISION_CHECKSUM_ENV)
        ),
        flaky_dashboard_host=dashboard_host,
        flaky_dashboard_port=dashboard_port,
        flaky_dashboard_warning=dashboard_warning,
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


def _parse_fail_closed_boolean(
    value: str | None,
    *,
    name: str,
) -> tuple[bool, str | None]:
    try:
        return parse_boolean_setting(value, name=name, default=False), None
    except ValueError as error:
        return False, str(error)


def _parse_skip_mode(
    value: str | None,
    *,
    enabled: bool,
) -> tuple[str, str, str | None]:
    requested = (value or "off").strip().casefold() or "off"
    if requested not in {"off", "shadow", "enforce"}:
        return requested, "off", f"invalid {QUALITY_FLAKY_SKIP_MODE_ENV} value: {value!r}"
    if requested == "enforce":
        return requested, "off", "skip_enforce_not_available"
    if not enabled:
        return requested, "off", None
    return requested, requested, None


def _parse_snapshot_age(value: str | None) -> tuple[int, str | None]:
    if value is None or not value.strip():
        return 15, None
    try:
        parsed = int(value)
    except ValueError:
        return 15, f"invalid {QUALITY_FLAKY_SNAPSHOT_MAX_AGE_MINUTES_ENV} value: {value!r}"
    if not 1 <= parsed <= 1440:
        return 15, f"{QUALITY_FLAKY_SNAPSHOT_MAX_AGE_MINUTES_ENV} must be between 1 and 1440"
    return parsed, None


def _join_warnings(*warnings: str | None) -> str | None:
    values = tuple(value for value in warnings if value)
    return "; ".join(values) if values else None


def _parse_dashboard_binding(
    host_value: str | None,
    port_value: str | None,
) -> tuple[str, int, str | None]:
    host = (host_value or "127.0.0.1").strip()
    warnings: list[str] = []
    if host not in {"127.0.0.1", "::1"}:
        warnings.append(f"invalid {QUALITY_FLAKY_DASHBOARD_HOST_ENV} value")
        host = "127.0.0.1"
    try:
        port = int((port_value or "8765").strip())
    except ValueError:
        port = 8765
        warnings.append(f"invalid {QUALITY_FLAKY_DASHBOARD_PORT_ENV} value")
    if not 1 <= port <= 65535:
        port = 8765
        warnings.append(f"invalid {QUALITY_FLAKY_DASHBOARD_PORT_ENV} value")
    return host, port, "; ".join(warnings) if warnings else None


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


def parse_boolean_setting(value: str | None, *, name: str, default: bool) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    raise ValueError(f"invalid {name} value: {value!r}")


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None
