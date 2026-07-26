from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import os

from dotenv import load_dotenv

from util.config_validation import (
    ConfigValidationError,
    aggregate_config_errors,
    parse_bool,
    parse_positive_float,
    parse_positive_int,
    require_http_url,
    require_non_empty,
)


load_dotenv()


@dataclass(frozen=True)
class Settings:
    timeout: float
    generate_allure_report: bool
    generate_history_report: bool
    history_report_keep_limit: int
    base_url: str
    api_key: str
    environment_name: str


def load_settings(env: Mapping[str, str | None] | None = None) -> Settings:
    env_values = os.environ if env is None else env
    errors: list[ConfigValidationError] = []

    use_china_environment = _parse_config(
        errors,
        parse_bool,
        "USE_CHINA_ENVIRONMENT",
        env_values.get("USE_CHINA_ENVIRONMENT"),
        default=False,
    )
    timeout = _parse_config(
        errors,
        parse_positive_float,
        "API_TIMEOUT",
        env_values.get("API_TIMEOUT"),
        default=600.0,
    )
    generate_allure_report = _parse_config(
        errors,
        parse_bool,
        "GENERATE_ALLURE_REPORT",
        env_values.get("GENERATE_ALLURE_REPORT"),
        default=True,
    )
    generate_history_report = _parse_config(
        errors,
        parse_bool,
        "GENERATE_HISTORY_REPORT",
        env_values.get("GENERATE_HISTORY_REPORT"),
        default=False,
    )
    history_report_keep_limit = _parse_config(
        errors,
        parse_positive_int,
        "HISTORY_REPORT_KEEP_LIMIT",
        env_values.get("HISTORY_REPORT_KEEP_LIMIT"),
        default=30,
    )

    if use_china_environment:
        environment_name = "china"
        base_url_name = "CHINA_TEST_ENVIRONMENT_BASE_URL"
        api_key_name = "CHINA_API_KEY"
    else:
        environment_name = "overseas"
        base_url_name = "OVERSEAS_TEST_BASE_URL"
        api_key_name = "OVERSEAS_API_KEY"

    base_url = _parse_config(errors, require_http_url, base_url_name, env_values.get(base_url_name))
    api_key = _parse_config(errors, require_non_empty, api_key_name, env_values.get(api_key_name))

    if errors:
        raise aggregate_config_errors(errors)

    return Settings(
        timeout=timeout,
        generate_allure_report=generate_allure_report,
        generate_history_report=generate_history_report,
        history_report_keep_limit=history_report_keep_limit,
        base_url=base_url,
        api_key=api_key,
        environment_name=environment_name,
    )


def _parse_config(errors: list[ConfigValidationError], parser, *args, **kwargs):
    try:
        return parser(*args, **kwargs)
    except ConfigValidationError as error:
        errors.append(error)
        return None


settings = load_settings()
USE_CHINA_ENVIRONMENT = settings.environment_name == "china"
