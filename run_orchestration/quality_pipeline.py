from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from quality.config import QualityRuntimeConfig
from quality.models import RunStatus

from . import (
    quality_flaky_stage,
    quality_metrics_stage,
    quality_observation_stage,
    quality_p0_stage,
    quality_run_record,
    quality_semantic_stage,
)


def finalize_quality_run(
    quality_config: QualityRuntimeConfig,
    *,
    start_time: datetime,
    expected_execution_ids: tuple[str, ...],
    expected_case_count: int,
    junit_files: tuple[Path | None, ...],
    status: RunStatus,
) -> None:
    if not quality_config.enabled or not quality_config.run_id:
        return
    merge_result = quality_p0_stage.merge_p0(
        quality_config,
        start_time=start_time,
        expected_execution_ids=expected_execution_ids,
        expected_case_count=expected_case_count,
        junit_files=junit_files,
    )
    if merge_result is None:
        return

    quality_run_record.write_final_run_record(
        quality_config,
        start_time=start_time,
        end_time=datetime.now(UTC),
        status=status,
        integrity_status=merge_result.integrity_status,
        integrity_issues=merge_result.integrity_issues,
    )
    quality_p0_stage.generate_p0_report(quality_config)
    quality_semantic_stage.run_semantic_stage(quality_config)
    quality_metrics_stage.run_metrics_stage(quality_config)
    flaky_import_result = quality_flaky_stage.run_flaky_history_stage(
        quality_config, status=status
    )
    quality_flaky_stage.run_flaky_state_stage(
        quality_config, flaky_import_result
    )
    quality_observation_stage.run_observation_stage(quality_config)
