from __future__ import annotations

import os
from pathlib import Path

import pytest


# Framework tests must be collectable in a clean checkout without a developer
# .env file. These values are process-local, synthetic, and never used for real
# interface execution.
os.environ.update(
    {
        "USE_CHINA_ENVIRONMENT": "FALSE",
        "OVERSEAS_TEST_BASE_URL": "https://offline.invalid",
        "OVERSEAS_API_KEY": "offline-test-key",
        "OVERSEAS_CONTROL_API_KEY": "offline-control-key",
        "RUN_REAL_ENV_TESTS": "FALSE",
        "GENERATE_ALLURE_REPORT": "FALSE",
        "GENERATE_HISTORY_REPORT": "FALSE",
    }
)


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config: pytest.Config) -> None:
    """Do not mix implicit framework-test Allure data with business results."""
    if _has_explicit_allure_results_arg(config.invocation_params.args):
        return
    config.option.allure_report_dir = None


def _has_explicit_allure_results_arg(args: tuple[str, ...]) -> bool:
    return any(
        argument == "--alluredir" or argument.startswith("--alluredir=")
        for argument in args
    )


@pytest.fixture(autouse=True)
def isolate_framework_runner_artifacts(monkeypatch, tmp_path: Path) -> None:
    """Redirect production-default Runner artifacts used by framework tests."""
    from run_orchestration import artifacts, pytest_execution

    monkeypatch.setattr(
        pytest_execution,
        "DEFAULT_ALLURE_RESULTS_DIR",
        tmp_path / "allure-results",
    )
    write_execution_result = artifacts.write_execution_result_atomic
    monkeypatch.setattr(
        artifacts,
        "write_execution_result_atomic",
        lambda payload: write_execution_result(
            payload,
            tmp_path / "execution-result.json",
        ),
    )
