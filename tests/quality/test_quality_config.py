from pathlib import Path

import pytest

from quality.config import (
    DEFAULT_QUALITY_HTTP_5XX_WARN_RATE,
    DEFAULT_QUALITY_MIN_REQUEST_SAMPLES,
    DEFAULT_QUALITY_OUTPUT_DIR,
    DEFAULT_QUALITY_SHADOW_GATE,
    DEFAULT_QUALITY_TIMEOUT_WARN_RATE,
    QualityReportConfig,
    QualityRuntimeConfig,
    load_quality_config,
    load_quality_report_config,
    parse_quality_enabled,
)


@pytest.mark.parametrize("value", ["1", "true", "TRUE", " yes ", "on"])
def test_parse_quality_enabled_accepts_true_values(value):
    assert parse_quality_enabled(value) is True


@pytest.mark.parametrize("value", [None, "", "0", "false", "NO", " off "])
def test_parse_quality_enabled_accepts_false_values(value):
    assert parse_quality_enabled(value) is False


def test_parse_quality_enabled_rejects_unknown_value():
    with pytest.raises(ValueError, match="QUALITY_ENABLE"):
        parse_quality_enabled("sometimes")


def test_load_quality_config_uses_defaults_without_side_effects(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    config = load_quality_config({})

    assert config == QualityRuntimeConfig(
        enabled=False,
        run_id=None,
        execution_id=None,
        output_dir=DEFAULT_QUALITY_OUTPUT_DIR,
    )
    assert not (tmp_path / DEFAULT_QUALITY_OUTPUT_DIR).exists()


def test_load_quality_config_trims_identity_and_reads_output_dir():
    config = load_quality_config(
        {
            "QUALITY_ENABLE": "1",
            "QUALITY_RUN_ID": " run-1 ",
            "QUALITY_EXECUTION_ID": " serial-pool ",
            "QUALITY_OUTPUT_DIR": "custom-quality",
        }
    )

    assert config.enabled is True
    assert config.run_id == "run-1"
    assert config.execution_id == "serial-pool"
    assert config.output_dir == Path("custom-quality")


def test_load_quality_config_treats_blank_identity_as_missing():
    config = load_quality_config(
        {
            "QUALITY_RUN_ID": "   ",
            "QUALITY_EXECUTION_ID": "\t",
        }
    )

    assert config.run_id is None
    assert config.execution_id is None


def test_load_quality_config_treats_blank_output_dir_as_default():
    config = load_quality_config({"QUALITY_OUTPUT_DIR": "   "})

    assert config.output_dir == DEFAULT_QUALITY_OUTPUT_DIR


def test_semantic_collection_requires_quality_and_defaults_off():
    disabled = load_quality_config({"QUALITY_SEMANTIC_ENABLE": "1"})
    enabled = load_quality_config(
        {"QUALITY_ENABLE": "1", "QUALITY_SEMANTIC_ENABLE": "1"}
    )

    assert disabled.semantic_enabled is False
    assert enabled.semantic_enabled is True


def test_invalid_semantic_setting_fails_open_with_warning():
    config = load_quality_config(
        {"QUALITY_ENABLE": "1", "QUALITY_SEMANTIC_ENABLE": "invalid"}
    )

    assert config.semantic_enabled is False
    assert "QUALITY_SEMANTIC_ENABLE" in str(config.semantic_warning)


def test_metrics_requires_quality_and_semantic_and_defaults_off():
    without_quality = load_quality_config(
        {"QUALITY_SEMANTIC_ENABLE": "1", "QUALITY_METRICS_ENABLE": "1"}
    )
    without_semantic = load_quality_config(
        {"QUALITY_ENABLE": "1", "QUALITY_METRICS_ENABLE": "1"}
    )
    enabled = load_quality_config(
        {
            "QUALITY_ENABLE": "1",
            "QUALITY_SEMANTIC_ENABLE": "1",
            "QUALITY_METRICS_ENABLE": "1",
        }
    )

    assert without_quality.metrics_enabled is False
    assert without_semantic.metrics_enabled is False
    assert enabled.metrics_enabled is True
    assert enabled.metrics_warning is None


def test_invalid_metrics_setting_fails_open_with_warning():
    config = load_quality_config(
        {
            "QUALITY_ENABLE": "1",
            "QUALITY_SEMANTIC_ENABLE": "1",
            "QUALITY_METRICS_ENABLE": "sometimes",
        }
    )

    assert config.metrics_enabled is False
    assert "QUALITY_METRICS_ENABLE" in str(config.metrics_warning)


def test_p1_report_requires_quality_and_defaults_off():
    disabled = load_quality_config({"QUALITY_P1_REPORT_ENABLE": "1"})
    enabled = load_quality_config(
        {"QUALITY_ENABLE": "1", "QUALITY_P1_REPORT_ENABLE": "1"}
    )

    assert disabled.p1_report_enabled is False
    assert "QUALITY_ENABLE=1" in str(disabled.p1_report_warning)
    assert enabled.p1_report_enabled is True
    assert enabled.p1_report_warning is None


def test_invalid_p1_report_setting_fails_open_with_warning():
    config = load_quality_config(
        {"QUALITY_ENABLE": "1", "QUALITY_P1_REPORT_ENABLE": "sometimes"}
    )

    assert config.p1_report_enabled is False
    assert "QUALITY_P1_REPORT_ENABLE" in str(config.p1_report_warning)


def test_flaky_history_requires_quality_and_defaults_off(tmp_path):
    disabled = load_quality_config(
        {
            "QUALITY_FLAKY_HISTORY_ENABLE": "1",
            "QUALITY_FLAKY_DB_PATH": str(tmp_path / "history.sqlite3"),
        }
    )
    enabled = load_quality_config(
        {
            "QUALITY_ENABLE": "1",
            "QUALITY_FLAKY_HISTORY_ENABLE": "1",
            "QUALITY_FLAKY_DB_PATH": str(tmp_path / "history.sqlite3"),
        }
    )

    assert disabled.flaky_history_enabled is False
    assert enabled.flaky_history_enabled is True
    assert enabled.flaky_history_warning is None
    assert enabled.flaky_database_path == tmp_path / "history.sqlite3"


def test_flaky_history_missing_or_relative_path_is_fail_open_warning():
    missing = load_quality_config(
        {"QUALITY_ENABLE": "1", "QUALITY_FLAKY_HISTORY_ENABLE": "1"}
    )
    relative = load_quality_config(
        {
            "QUALITY_ENABLE": "1",
            "QUALITY_FLAKY_HISTORY_ENABLE": "1",
            "QUALITY_FLAKY_DB_PATH": "reports/flaky.sqlite3",
        }
    )

    assert missing.flaky_history_enabled is True
    assert "required" in str(missing.flaky_history_warning)
    assert relative.flaky_history_enabled is True
    assert "absolute" in str(relative.flaky_history_warning)


def test_invalid_flaky_history_setting_disables_only_history():
    config = load_quality_config(
        {
            "QUALITY_ENABLE": "1",
            "QUALITY_FLAKY_HISTORY_ENABLE": "sometimes",
        }
    )

    assert config.enabled is True
    assert config.flaky_history_enabled is False
    assert "QUALITY_FLAKY_HISTORY_ENABLE" in str(config.flaky_history_warning)


def test_flaky_state_requires_quality_and_history_and_defaults_off(tmp_path):
    path = str(tmp_path / "history.sqlite3")
    missing_history = load_quality_config(
        {
            "QUALITY_ENABLE": "1",
            "QUALITY_FLAKY_STATE_ENABLE": "1",
            "QUALITY_FLAKY_DB_PATH": path,
        }
    )
    enabled = load_quality_config(
        {
            "QUALITY_ENABLE": "1",
            "QUALITY_FLAKY_HISTORY_ENABLE": "1",
            "QUALITY_FLAKY_STATE_ENABLE": "1",
            "QUALITY_FLAKY_DB_PATH": path,
        }
    )

    assert missing_history.flaky_state_enabled is False
    assert "requires" in str(missing_history.flaky_state_warning)
    assert enabled.flaky_state_enabled is True
    assert enabled.flaky_state_warning is None


def test_invalid_flaky_state_setting_does_not_disable_history(tmp_path):
    config = load_quality_config(
        {
            "QUALITY_ENABLE": "1",
            "QUALITY_FLAKY_HISTORY_ENABLE": "1",
            "QUALITY_FLAKY_STATE_ENABLE": "sometimes",
            "QUALITY_FLAKY_DB_PATH": str(tmp_path / "history.sqlite3"),
        }
    )

    assert config.flaky_history_enabled is True
    assert config.flaky_state_enabled is False
    assert "QUALITY_FLAKY_STATE_ENABLE" in str(config.flaky_state_warning)


def test_load_quality_report_config_uses_defaults():
    assert load_quality_report_config({}) == QualityReportConfig(
        shadow_gate=DEFAULT_QUALITY_SHADOW_GATE,
        min_request_samples=DEFAULT_QUALITY_MIN_REQUEST_SAMPLES,
        http_5xx_warn_rate=DEFAULT_QUALITY_HTTP_5XX_WARN_RATE,
        timeout_warn_rate=DEFAULT_QUALITY_TIMEOUT_WARN_RATE,
    )


def test_load_quality_report_config_reads_environment():
    config = load_quality_report_config(
        {
            "QUALITY_SHADOW_GATE": "0",
            "QUALITY_MIN_REQUEST_SAMPLES": "8",
            "QUALITY_HTTP_5XX_WARN_RATE": "0.1",
            "QUALITY_TIMEOUT_WARN_RATE": "0.2",
        }
    )

    assert config == QualityReportConfig(
        shadow_gate=False,
        min_request_samples=8,
        http_5xx_warn_rate=0.1,
        timeout_warn_rate=0.2,
    )


@pytest.mark.parametrize(
    "values",
    [
        {"QUALITY_MIN_REQUEST_SAMPLES": "-1"},
        {"QUALITY_HTTP_5XX_WARN_RATE": "1.1"},
        {"QUALITY_TIMEOUT_WARN_RATE": "invalid"},
        {"QUALITY_SHADOW_GATE": "invalid"},
    ],
)
def test_load_quality_report_config_rejects_invalid_values(values):
    with pytest.raises(ValueError):
        load_quality_report_config(values)
