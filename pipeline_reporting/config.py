from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import os
from pathlib import Path

from dotenv import dotenv_values

from util.config_validation import parse_bool


GENERATE_PIPELINE_SUMMARY_ENV = "GENERATE_PIPELINE_SUMMARY"


@dataclass(frozen=True)
class PipelineReportConfig:
    enabled: bool = True


def load_pipeline_report_config(
    environment: Mapping[str, str] | None = None,
    *,
    dotenv_path: str | Path = ".env",
) -> PipelineReportConfig:
    current_environment = environment if environment is not None else os.environ
    raw_value = current_environment.get(GENERATE_PIPELINE_SUMMARY_ENV)
    if raw_value is None:
        path = Path(dotenv_path)
        if path.is_file():
            dotenv_value = dotenv_values(path).get(GENERATE_PIPELINE_SUMMARY_ENV)
            raw_value = str(dotenv_value) if dotenv_value is not None else None
    return PipelineReportConfig(
        enabled=parse_bool(
            GENERATE_PIPELINE_SUMMARY_ENV,
            raw_value,
            default=True,
        )
    )
