from __future__ import annotations

from collections.abc import Mapping
import os
from typing import Any

from util.redaction import redact_sensitive_data


TRUE_VALUE = "TRUE"
FALSE_VALUE = "FALSE"
REQUIRED_VALUE_MESSAGE = "Missing required config {name}."


class ConfigValidationError(RuntimeError):
    pass


def parse_bool(name: str, value: str | None, *, default: bool | None = None) -> bool:
    normalized_value = _normalize_optional_value(value)
    if normalized_value is None:
        if default is not None:
            return default
        raise ConfigValidationError(REQUIRED_VALUE_MESSAGE.format(name=name))

    upper_value = normalized_value.upper()
    if upper_value == TRUE_VALUE:
        return True
    if upper_value == FALSE_VALUE:
        return False
    raise ConfigValidationError(f"Invalid config {name}={normalized_value!r}. Expected TRUE or FALSE.")


def parse_positive_float(name: str, value: str | None, *, default: float | None = None) -> float:
    normalized_value = _normalize_optional_value(value)
    if normalized_value is None:
        if default is not None:
            return default
        raise ConfigValidationError(REQUIRED_VALUE_MESSAGE.format(name=name))

    try:
        parsed_value = float(normalized_value)
    except ValueError as exc:
        raise ConfigValidationError(
            f"Invalid config {name}={normalized_value!r}. Expected positive number."
        ) from exc

    if parsed_value <= 0:
        raise ConfigValidationError(
            f"Invalid config {name}={normalized_value!r}. Expected positive number."
        )
    return parsed_value


def parse_positive_int(name: str, value: str | None, *, default: int | None = None) -> int:
    normalized_value = _normalize_optional_value(value)
    if normalized_value is None:
        if default is not None:
            return default
        raise ConfigValidationError(REQUIRED_VALUE_MESSAGE.format(name=name))

    try:
        parsed_value = int(normalized_value)
    except ValueError as exc:
        raise ConfigValidationError(
            f"Invalid config {name}={normalized_value!r}. Expected integer >= 1."
        ) from exc

    if parsed_value < 1:
        raise ConfigValidationError(
            f"Invalid config {name}={normalized_value!r}. Expected integer >= 1."
        )
    return parsed_value


def require_non_empty(name: str, value: str | None) -> str:
    normalized_value = _normalize_optional_value(value)
    if normalized_value is None:
        raise ConfigValidationError(REQUIRED_VALUE_MESSAGE.format(name=name))
    return normalized_value


def require_http_url(name: str, value: str | None) -> str:
    normalized_value = require_non_empty(name, value).rstrip("/")
    if not normalized_value.startswith(("http://", "https://")):
        raise ConfigValidationError(
            f"Invalid config {name}={normalized_value!r}. Expected http(s) URL."
        )
    return normalized_value


def aggregate_config_errors(errors: list[ConfigValidationError]) -> ConfigValidationError:
    if not errors:
        raise ValueError("errors must not be empty")

    lines = ["Configuration validation failed:"]
    lines.extend(f"- {error}" for error in errors)
    return ConfigValidationError("\n".join(lines))


def is_enabled(name: str, env: Mapping[str, str | None] | None = None) -> bool:
    env_values = os.environ if env is None else env
    return parse_bool(name, env_values.get(name), default=False)


def redact_config_summary(summary: Mapping[str, Any]) -> dict[str, Any]:
    redacted_summary = redact_sensitive_data(dict(summary))
    if not isinstance(redacted_summary, dict):
        return dict(summary)
    return redacted_summary


def _normalize_optional_value(value: str | None) -> str | None:
    if value is None:
        return None
    stripped_value = value.strip()
    return stripped_value or None
