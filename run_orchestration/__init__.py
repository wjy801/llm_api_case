"""Public API for test execution orchestration."""

from .cli import main
from .paths import DEFAULT_ALLURE_RESULTS_DIR, PROJECT_ROOT
from .runner import run


__all__ = (
    "DEFAULT_ALLURE_RESULTS_DIR",
    "PROJECT_ROOT",
    "main",
    "run",
)
