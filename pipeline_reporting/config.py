from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import os
from pathlib import Path

from dotenv import dotenv_values

from util.config_validation import parse_bool


GENERATE_PIPELINE_SUMMARY_ENV = "GENERATE_PIPELINE_SUMMARY"
QUALITY_ENABLE_ENV = "QUALITY_ENABLE"


@dataclass(frozen=True)
class PipelineReportConfig:
    enabled: bool = True
    quality_enabled: bool = False


def load_pipeline_report_config(
    environment: Mapping[str, str] | None = None,
    *,
    dotenv_path: str | Path = ".env",
) -> PipelineReportConfig:
    current_environment = environment if environment is not None else os.environ
    path = Path(dotenv_path)
    dotenv_environment = dotenv_values(path) if path.is_file() else {}
    raw_value = _configured_value(
        current_environment,
        dotenv_environment,
        GENERATE_PIPELINE_SUMMARY_ENV,
    )
    quality_value = _configured_value(
        current_environment,
        dotenv_environment,
        QUALITY_ENABLE_ENV,
    )
    enabled = parse_bool(
        GENERATE_PIPELINE_SUMMARY_ENV,
        raw_value,
        default=True,
    )
    if not enabled:
        return PipelineReportConfig(enabled=False, quality_enabled=False)
    return PipelineReportConfig(
        enabled=True,
        quality_enabled=parse_bool(
            QUALITY_ENABLE_ENV,
            quality_value,
            default=False,
        ),
    )


def _configured_value(
    environment: Mapping[str, str],
    dotenv_environment: Mapping[str, object],
    name: str,
) -> str | None:
    value = environment.get(name)
    if value is None:
        value = dotenv_environment.get(name)
    return str(value) if value is not None else None
