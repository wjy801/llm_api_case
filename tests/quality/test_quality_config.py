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
