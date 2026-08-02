from __future__ import annotations

from datetime import datetime
from typing import Any

from quality.aggregator import QualityMergeRequest, merge_quality_run
from quality.config import (
    QualityReportConfig,
    QualityRuntimeConfig,
    load_quality_report_config,
)
from quality.report import QualityReportRequest, generate_quality_report


def merge_p0(
    quality_config: QualityRuntimeConfig,
    *,
    start_time: datetime,
    expected_execution_ids: tuple[str, ...],
    expected_case_count: int,
    junit_files: tuple,
) -> Any | None:
    try:
        return merge_quality_run(
            QualityMergeRequest(
                run_id=str(quality_config.run_id),
                output_dir=quality_config.output_dir,
                expected_execution_ids=expected_execution_ids,
                expected_case_count=expected_case_count,
                junit_files=tuple(
                    path for path in junit_files if path is not None
                ),
                run_start_time=start_time,
            )
        )
    except Exception as error:
        print(f"Quality merge failed open: {type(error).__name__}: {error}")
        return None


def generate_p0_report(quality_config: QualityRuntimeConfig) -> None:
    try:
        report_config = load_quality_report_config_fail_open()
        generate_quality_report(
            QualityReportRequest(
                run_id=str(quality_config.run_id),
                output_dir=quality_config.output_dir,
                shadow_gate=report_config.shadow_gate,
                min_request_samples=report_config.min_request_samples,
                http_5xx_warn_rate=report_config.http_5xx_warn_rate,
                timeout_warn_rate=report_config.timeout_warn_rate,
            )
        )
    except Exception as error:
        print(f"Quality report failed open: {type(error).__name__}: {error}")


def load_quality_report_config_fail_open() -> QualityReportConfig:
    try:
        return load_quality_report_config()
    except ValueError as error:
        print(f"Quality report configuration warning: {error}; using defaults")
        return QualityReportConfig()
