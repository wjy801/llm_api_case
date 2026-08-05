from __future__ import annotations

from collections.abc import Mapping
import os

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict

from util.config_validation import validate_settings_values


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


def load_settings(env: Mapping[str, str | None] | None = None) -> Settings:
    values = validate_settings_values(os.environ if env is None else env)
    return Settings(
        timeout=values.timeout,
        generate_allure_report=values.generate_allure_report,
        generate_history_report=values.generate_history_report,
        history_report_keep_limit=values.history_report_keep_limit,
        base_url=values.base_url,
        api_key=values.api_key,
        environment_name=values.environment_name,
    )


settings = load_settings()
USE_CHINA_ENVIRONMENT = settings.environment_name == "china"
