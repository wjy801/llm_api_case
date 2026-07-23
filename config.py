from __future__ import annotations

from dataclasses import dataclass
import os

from dotenv import load_dotenv


load_dotenv()


def _is_true(value: str | None) -> bool:
    return value is not None and value.strip().upper() == "TRUE"


USE_CHINA_ENVIRONMENT = _is_true(os.getenv("USE_CHINA_ENVIRONMENT"))


@dataclass(frozen=True)
class Settings:
    timeout: float = float(os.getenv("API_TIMEOUT",600))
    generate_allure_report: bool = _is_true(os.getenv("GENERATE_ALLURE_REPORT", "TRUE"))
    generate_history_report: bool = _is_true(os.getenv("GENERATE_HISTORY_REPORT", "FALSE"))
    history_report_keep_limit: int = int(os.getenv("HISTORY_REPORT_KEEP_LIMIT", "30"))

    if USE_CHINA_ENVIRONMENT:
        base_url: str = os.getenv("CHINA_TEST_ENVIRONMENT_BASE_URL").rstrip("/")
        api_key: str = os.getenv("CHINA_API_KEY").strip()
    else:
        base_url: str = os.getenv("OVERSEAS_TEST_BASE_URL").rstrip("/")
        api_key: str = os.getenv("OVERSEAS_API_KEY").strip()


settings = Settings()
