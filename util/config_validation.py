from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import os
from typing import Any

from util.redaction import redact_sensitive_data


TRUE_VALUE = "TRUE"
FALSE_VALUE = "FALSE"
REQUIRED_VALUE_MESSAGE = "Missing required config {name}."


class ConfigValidationError(ValueError):
    pass


@dataclass(frozen=True)
class ValidatedSettingsValues:
    timeout: float
    generate_allure_report: bool
    generate_history_report: bool
    history_report_keep_limit: int
    base_url: str
    api_key: str
    environment_name: str


def validate_settings_values(
    environment: Mapping[str, str | None] | None = None,
) -> ValidatedSettingsValues:
    values = os.environ if environment is None else environment
    scalar_errors: list[ConfigValidationError] = []
    use_china_environment = _parsed_or_default(
        scalar_errors,
        _parse_bool_value,
        False,
        "USE_CHINA_ENVIRONMENT",
        values.get("USE_CHINA_ENVIRONMENT"),
        default=False,
    )
    timeout = _parsed_or_default(
        scalar_errors,
        parse_positive_float,
        600.0,
        "API_TIMEOUT",
        _optional_string(values.get("API_TIMEOUT")),
        default=600.0,
    )
    generate_allure_report = _parsed_or_default(
        scalar_errors,
        _parse_bool_value,
        True,
        "GENERATE_ALLURE_REPORT",
        values.get("GENERATE_ALLURE_REPORT"),
        default=True,
    )
    generate_history_report = _parsed_or_default(
        scalar_errors,
        _parse_bool_value,
        False,
        "GENERATE_HISTORY_REPORT",
        values.get("GENERATE_HISTORY_REPORT"),
        default=False,
    )
    history_report_keep_limit = _parsed_or_default(
        scalar_errors,
        parse_positive_int,
        30,
        "HISTORY_REPORT_KEEP_LIMIT",
        _optional_string(values.get("HISTORY_REPORT_KEEP_LIMIT")),
        default=30,
    )
    if scalar_errors:
        raise aggregate_config_errors(scalar_errors)

    if use_china_environment:
        environment_name = "china"
        base_url_name = "CHINA_TEST_ENVIRONMENT_BASE_URL"
        api_key_name = "CHINA_API_KEY"
    else:
        environment_name = "overseas"
        base_url_name = "OVERSEAS_TEST_BASE_URL"
        api_key_name = "OVERSEAS_API_KEY"

    selected_errors: list[ConfigValidationError] = []
    base_url = _parsed_or_default(
        selected_errors,
        require_http_url,
        "",
        base_url_name,
        _optional_string(values.get(base_url_name)),
    )
    api_key = _parsed_or_default(
        selected_errors,
        require_non_empty,
        "",
        api_key_name,
        _optional_string(values.get(api_key_name)),
    )
    if selected_errors:
        raise aggregate_config_errors(selected_errors)

    return ValidatedSettingsValues(
        timeout=timeout,
        generate_allure_report=generate_allure_report,
        generate_history_report=generate_history_report,
        history_report_keep_limit=history_report_keep_limit,
        base_url=base_url,
        api_key=api_key,
        environment_name=environment_name,
    )


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


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _parse_bool_value(
    name: str,
    value: object,
    *,
    default: bool,
) -> bool:
    if isinstance(value, bool):
        return value
    return parse_bool(name, _optional_string(value), default=default)


def _parsed_or_default(errors, parser, fallback, *args, **kwargs):
    try:
        return parser(*args, **kwargs)
    except ConfigValidationError as error:
        errors.append(error)
        return fallback
