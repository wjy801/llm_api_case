from __future__ import annotations

from collections.abc import Mapping
import os
from typing import Any, ClassVar

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

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


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True)

    timeout: float
    generate_allure_report: bool
    generate_history_report: bool
    history_report_keep_limit: int
    base_url: str
    api_key: str
    environment_name: str


class _EnvironmentSettingsInput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    use_china_environment: bool = Field(default=False, validation_alias="USE_CHINA_ENVIRONMENT")
    api_timeout: float = Field(default=600.0, validation_alias="API_TIMEOUT")
    generate_allure_report: bool = Field(default=True, validation_alias="GENERATE_ALLURE_REPORT")
    generate_history_report: bool = Field(default=False, validation_alias="GENERATE_HISTORY_REPORT")
    history_report_keep_limit: int = Field(default=30, validation_alias="HISTORY_REPORT_KEEP_LIMIT")
    china_base_url: str | None = Field(default=None, validation_alias="CHINA_TEST_ENVIRONMENT_BASE_URL")
    china_api_key: str | None = Field(default=None, validation_alias="CHINA_API_KEY")
    overseas_base_url: str | None = Field(default=None, validation_alias="OVERSEAS_TEST_BASE_URL")
    overseas_api_key: str | None = Field(default=None, validation_alias="OVERSEAS_API_KEY")

    BOOL_FIELDS: ClassVar[dict[str, str]] = {
        "use_china_environment": "USE_CHINA_ENVIRONMENT",
        "generate_allure_report": "GENERATE_ALLURE_REPORT",
        "generate_history_report": "GENERATE_HISTORY_REPORT",
    }

    @field_validator("use_china_environment", "generate_allure_report", "generate_history_report", mode="before")
    @classmethod
    def _validate_bool_env(cls, value: Any, info) -> bool:
        if isinstance(value, bool):
            return value
        field_name = cls.BOOL_FIELDS[info.field_name]
        return parse_bool(field_name, _optional_string(value), default=bool(cls.model_fields[info.field_name].default))

    @field_validator("api_timeout", mode="before")
    @classmethod
    def _validate_timeout(cls, value: Any) -> float:
        return parse_positive_float("API_TIMEOUT", _optional_string(value), default=600.0)

    @field_validator("history_report_keep_limit", mode="before")
    @classmethod
    def _validate_history_keep_limit(cls, value: Any) -> int:
        return parse_positive_int("HISTORY_REPORT_KEEP_LIMIT", _optional_string(value), default=30)

    @field_validator("china_base_url", mode="before")
    @classmethod
    def _normalize_china_base_url(cls, value: Any) -> str | None:
        return _normalize_optional_env_value(value)

    @field_validator("china_api_key", mode="before")
    @classmethod
    def _normalize_china_api_key(cls, value: Any) -> str | None:
        return _normalize_optional_env_value(value)

    @field_validator("overseas_base_url", mode="before")
    @classmethod
    def _normalize_overseas_base_url(cls, value: Any) -> str | None:
        return _normalize_optional_env_value(value)

    @field_validator("overseas_api_key", mode="before")
    @classmethod
    def _normalize_overseas_api_key(cls, value: Any) -> str | None:
        return _normalize_optional_env_value(value)

    @model_validator(mode="after")
    def _validate_selected_environment(self) -> _EnvironmentSettingsInput:
        errors: list[ConfigValidationError] = []
        if self.use_china_environment:
            _collect_config_error(errors, require_http_url, "CHINA_TEST_ENVIRONMENT_BASE_URL", self.china_base_url)
            _collect_config_error(errors, require_non_empty, "CHINA_API_KEY", self.china_api_key)
            if errors:
                raise aggregate_config_errors(errors)
            return self

        _collect_config_error(errors, require_http_url, "OVERSEAS_TEST_BASE_URL", self.overseas_base_url)
        _collect_config_error(errors, require_non_empty, "OVERSEAS_API_KEY", self.overseas_api_key)
        if errors:
            raise aggregate_config_errors(errors)
        return self

    def to_settings(self) -> Settings:
        if self.use_china_environment:
            return Settings(
                timeout=self.api_timeout,
                generate_allure_report=self.generate_allure_report,
                generate_history_report=self.generate_history_report,
                history_report_keep_limit=self.history_report_keep_limit,
                base_url=require_http_url("CHINA_TEST_ENVIRONMENT_BASE_URL", self.china_base_url),
                api_key=require_non_empty("CHINA_API_KEY", self.china_api_key),
                environment_name="china",
            )

        return Settings(
            timeout=self.api_timeout,
            generate_allure_report=self.generate_allure_report,
            generate_history_report=self.generate_history_report,
            history_report_keep_limit=self.history_report_keep_limit,
            base_url=require_http_url("OVERSEAS_TEST_BASE_URL", self.overseas_base_url),
            api_key=require_non_empty("OVERSEAS_API_KEY", self.overseas_api_key),
            environment_name="overseas",
        )


def load_settings(env: Mapping[str, str | None] | None = None) -> Settings:
    env_values = os.environ if env is None else env
    try:
        return _EnvironmentSettingsInput.model_validate(dict(env_values)).to_settings()
    except ValidationError as error:
        errors = _config_errors_from_pydantic(error)
        if errors:
            raise aggregate_config_errors(errors) from error
        raise


def _config_errors_from_pydantic(error: ValidationError) -> list[ConfigValidationError]:
    errors: list[ConfigValidationError] = []
    seen_messages: set[str] = set()
    for detail in error.errors(include_url=False, include_context=True):
        for message in _pydantic_error_messages(detail):
            if message in seen_messages:
                continue
            seen_messages.add(message)
            errors.append(ConfigValidationError(message))
    return errors


def _pydantic_error_messages(detail: dict[str, Any]) -> list[str]:
    context = detail.get("ctx") or {}
    error = context.get("error")
    if isinstance(error, ConfigValidationError):
        return _split_aggregate_error_message(str(error))
    if isinstance(error, ValueError):
        return _split_aggregate_error_message(str(error))
    return [str(detail.get("msg", "Invalid configuration."))]


def _split_aggregate_error_message(message: str) -> list[str]:
    prefix = "Configuration validation failed:"
    if not message.startswith(prefix):
        return [message]

    messages = []
    for line in message.splitlines()[1:]:
        stripped_line = line.strip()
        if stripped_line.startswith("- "):
            messages.append(stripped_line[2:])
    return messages or [message]


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _normalize_optional_env_value(value: Any) -> str | None:
    if value is None:
        return None
    stripped_value = str(value).strip()
    return stripped_value or None


def _collect_config_error(errors: list[ConfigValidationError], parser, *args, **kwargs) -> None:
    try:
        parser(*args, **kwargs)
    except ConfigValidationError as error:
        errors.append(error)


settings = load_settings()
USE_CHINA_ENVIRONMENT = settings.environment_name == "china"
