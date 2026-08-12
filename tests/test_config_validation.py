from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest
from pydantic import BaseModel

from config import (
    DEFAULT_DOTENV_PATH,
    DOTENV_PATH_ENV,
    Settings,
    load_settings,
    resolve_dotenv_path,
)
from util.config_validation import (
    ConfigValidationError,
    is_enabled,
    parse_bool,
    redact_config_summary,
    validate_settings_values,
)


class TestLoadSettings:
    def test_dotenv_path_defaults_to_local_env(self):
        assert resolve_dotenv_path({}) == DEFAULT_DOTENV_PATH

    def test_dotenv_path_uses_explicit_pipeline_file(self):
        assert (
            resolve_dotenv_path({DOTENV_PATH_ENV: "  .env.pipeline  "})
            == ".env.pipeline"
        )

    def test_pipeline_dotenv_is_loaded_in_clean_process(self, tmp_path):
        pipeline_env = tmp_path / ".env.pipeline"
        pipeline_env.write_text(
            "\n".join(
                (
                    "USE_CHINA_ENVIRONMENT=TRUE",
                    "CHINA_TEST_ENVIRONMENT_BASE_URL=https://pipeline.example.com",
                    "CHINA_API_KEY=synthetic-pipeline-key",
                    "B_ACCOUNT_API_KEY=synthetic-b-account-key",
                    "API_TIMEOUT=30",
                )
            )
            + "\n",
            encoding="utf-8",
        )
        environment = os.environ.copy()
        for name in (
            "USE_CHINA_ENVIRONMENT",
            "CHINA_TEST_ENVIRONMENT_BASE_URL",
            "CHINA_API_KEY",
            "B_ACCOUNT_API_KEY",
            "API_TIMEOUT",
        ):
            environment.pop(name, None)
        environment[DOTENV_PATH_ENV] = ".env.pipeline"
        project_root = Path(__file__).resolve().parents[1]
        existing_pythonpath = environment.get("PYTHONPATH")
        environment["PYTHONPATH"] = os.pathsep.join(
            part
            for part in (str(project_root), existing_pythonpath)
            if part
        )

        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import os; "
                    "from config import settings; "
                    "assert settings.environment_name == 'china'; "
                    "assert settings.base_url == 'https://pipeline.example.com'; "
                    "assert settings.api_key == 'synthetic-pipeline-key'; "
                    "assert settings.timeout == 30; "
                    "assert os.environ['B_ACCOUNT_API_KEY'] == "
                    "'synthetic-b-account-key'"
                ),
            ],
            cwd=tmp_path,
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

        assert completed.returncode == 0, completed.stderr

    def test_loads_china_settings_and_strips_base_url_slash(self):
        settings = load_settings(
            {
                "USE_CHINA_ENVIRONMENT": "TRUE",
                "CHINA_TEST_ENVIRONMENT_BASE_URL": "https://pre.example.com/",
                "CHINA_API_KEY": "china-secret",
                "API_TIMEOUT": "10.5",
                "GENERATE_ALLURE_REPORT": "false",
                "GENERATE_HISTORY_REPORT": "true",
                "HISTORY_REPORT_KEEP_LIMIT": "7",
            }
        )

        assert settings.environment_name == "china"
        assert settings.base_url == "https://pre.example.com"
        assert settings.api_key == "china-secret"
        assert settings.timeout == 10.5
        assert settings.generate_allure_report is False
        assert settings.generate_history_report is True
        assert settings.history_report_keep_limit == 7

    def test_loads_overseas_settings_by_default(self):
        settings = load_settings(
            {
                "OVERSEAS_TEST_BASE_URL": "https://pre.example.org",
                "OVERSEAS_API_KEY": "overseas-secret",
            }
        )

        assert settings.environment_name == "overseas"
        assert settings.base_url == "https://pre.example.org"
        assert settings.api_key == "overseas-secret"
        assert settings.timeout == 600.0
        assert settings.generate_allure_report is True
        assert settings.generate_history_report is False
        assert settings.history_report_keep_limit == 30

    def test_settings_is_frozen_pydantic_model(self):
        settings = load_settings(
            {
                "OVERSEAS_TEST_BASE_URL": "https://pre.example.org",
                "OVERSEAS_API_KEY": "overseas-secret",
            }
        )

        assert isinstance(settings, BaseModel)
        assert isinstance(settings, Settings)
        with pytest.raises(ValueError, match="frozen"):
            settings.timeout = 1  # type: ignore[misc]

    def test_missing_china_required_configs_report_variable_names(self):
        with pytest.raises(ConfigValidationError) as exc_info:
            load_settings({"USE_CHINA_ENVIRONMENT": "TRUE"})

        error_text = str(exc_info.value)
        assert error_text.count("Configuration validation failed:") == 1
        assert "Configuration validation failed:" in error_text
        assert "Missing required config CHINA_TEST_ENVIRONMENT_BASE_URL." in error_text
        assert "Missing required config CHINA_API_KEY." in error_text

    def test_missing_overseas_required_configs_report_variable_names(self):
        with pytest.raises(ConfigValidationError) as exc_info:
            load_settings({"USE_CHINA_ENVIRONMENT": "FALSE"})

        error_text = str(exc_info.value)
        assert error_text.count("Configuration validation failed:") == 1
        assert "Missing required config OVERSEAS_TEST_BASE_URL." in error_text
        assert "Missing required config OVERSEAS_API_KEY." in error_text

    def test_invalid_url_is_rejected(self):
        with pytest.raises(ConfigValidationError) as exc_info:
            load_settings(
                {
                    "USE_CHINA_ENVIRONMENT": "TRUE",
                    "CHINA_TEST_ENVIRONMENT_BASE_URL": "pre.example.com",
                    "CHINA_API_KEY": "china-secret",
                }
            )

        assert "Invalid config CHINA_TEST_ENVIRONMENT_BASE_URL='pre.example.com'. Expected http(s) URL." in str(
            exc_info.value
        )

    def test_invalid_timeout_is_rejected(self):
        with pytest.raises(ConfigValidationError) as exc_info:
            load_settings(
                {
                    "OVERSEAS_TEST_BASE_URL": "https://pre.example.org",
                    "OVERSEAS_API_KEY": "overseas-secret",
                    "API_TIMEOUT": "abc",
                }
            )

        assert "Invalid config API_TIMEOUT='abc'. Expected positive number." in str(exc_info.value)

    def test_zero_timeout_is_rejected(self):
        with pytest.raises(ConfigValidationError) as exc_info:
            load_settings(
                {
                    "OVERSEAS_TEST_BASE_URL": "https://pre.example.org",
                    "OVERSEAS_API_KEY": "overseas-secret",
                    "API_TIMEOUT": "0",
                }
            )

        assert "Invalid config API_TIMEOUT='0'. Expected positive number." in str(exc_info.value)

    def test_invalid_history_keep_limit_is_rejected(self):
        with pytest.raises(ConfigValidationError) as exc_info:
            load_settings(
                {
                    "OVERSEAS_TEST_BASE_URL": "https://pre.example.org",
                    "OVERSEAS_API_KEY": "overseas-secret",
                    "HISTORY_REPORT_KEEP_LIMIT": "0",
                }
            )

        assert "Invalid config HISTORY_REPORT_KEEP_LIMIT='0'. Expected integer >= 1." in str(exc_info.value)

    def test_invalid_environment_bool_is_rejected(self):
        with pytest.raises(ConfigValidationError) as exc_info:
            load_settings(
                {
                    "USE_CHINA_ENVIRONMENT": "yes",
                    "OVERSEAS_TEST_BASE_URL": "https://pre.example.org",
                    "OVERSEAS_API_KEY": "overseas-secret",
                }
            )

        assert "Invalid config USE_CHINA_ENVIRONMENT='yes'. Expected TRUE or FALSE." in str(exc_info.value)

    def test_b_account_and_zero_account_are_not_global_required_configs(self):
        settings = load_settings(
            {
                "OVERSEAS_TEST_BASE_URL": "https://pre.example.org",
                "OVERSEAS_API_KEY": "overseas-secret",
            }
        )

        assert not hasattr(settings, "b_account_api_key")
        assert not hasattr(settings, "zero_balance_api_key")


class TestConfigValidationHelpers:
    def test_canonical_values_match_public_settings(self):
        environment = {
            "USE_CHINA_ENVIRONMENT": "TRUE",
            "CHINA_TEST_ENVIRONMENT_BASE_URL": "https://pre.example.com/",
            "CHINA_API_KEY": "china-secret",
            "API_TIMEOUT": "10.5",
            "GENERATE_ALLURE_REPORT": "false",
            "GENERATE_HISTORY_REPORT": "true",
            "HISTORY_REPORT_KEEP_LIMIT": "7",
        }

        values = validate_settings_values(environment)
        settings = load_settings(environment)

        assert values.timeout == settings.timeout
        assert values.generate_allure_report is settings.generate_allure_report
        assert values.generate_history_report is settings.generate_history_report
        assert values.history_report_keep_limit == settings.history_report_keep_limit
        assert values.base_url == settings.base_url
        assert values.api_key == settings.api_key
        assert values.environment_name == settings.environment_name

    def test_canonical_values_preserve_scalar_error_order(self):
        with pytest.raises(ConfigValidationError) as exc_info:
            validate_settings_values(
                {
                    "OVERSEAS_TEST_BASE_URL": "https://pre.example.org",
                    "OVERSEAS_API_KEY": "overseas-secret",
                    "API_TIMEOUT": "bad",
                    "GENERATE_ALLURE_REPORT": "nope",
                    "HISTORY_REPORT_KEEP_LIMIT": "0",
                }
            )

        assert str(exc_info.value) == (
            "Configuration validation failed:\n"
            "- Invalid config API_TIMEOUT='bad'. Expected positive number.\n"
            "- Invalid config GENERATE_ALLURE_REPORT='nope'. Expected TRUE or FALSE.\n"
            "- Invalid config HISTORY_REPORT_KEEP_LIMIT='0'. Expected integer >= 1."
        )

    def test_parse_bool_is_case_insensitive(self):
        assert parse_bool("FLAG", "TRUE") is True
        assert parse_bool("FLAG", "true") is True
        assert parse_bool("FLAG", "FALSE") is False
        assert parse_bool("FLAG", "false") is False

    def test_is_enabled_reads_true_flag(self):
        assert is_enabled("RUN_REAL_ENV_TESTS", {"RUN_REAL_ENV_TESTS": "TRUE"}) is True
        assert is_enabled("RUN_REAL_ENV_TESTS", {"RUN_REAL_ENV_TESTS": "FALSE"}) is False
        assert is_enabled("RUN_REAL_ENV_TESTS", {}) is False

    def test_redact_config_summary_hides_sensitive_values(self):
        summary = redact_config_summary(
            {
                "environment_name": "china",
                "base_url": "https://pre.example.com",
                "api_key": "api-secret",
                "authorization": "Bearer auth-secret",
                "timeout": 600.0,
            }
        )

        assert summary == {
            "environment_name": "china",
            "base_url": "https://pre.example.com",
            "api_key": "<redacted>",
            "authorization": "<redacted>",
            "timeout": 600.0,
        }
