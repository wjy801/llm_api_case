from __future__ import annotations

from datetime import datetime
from pathlib import Path

from quality.aggregator import (
    QualityMergeRequest,
    QualityMergeResult,
    merge_quality_run,
)
from quality.config import QualityRuntimeConfig

from .quality_run_record import quality_run_contract_fields


def merge_quality_facts(
    quality_config: QualityRuntimeConfig,
    *,
    start_time: datetime,
    expected_execution_ids: tuple[str, ...],
    expected_case_count: int,
    junit_files: tuple[Path | None, ...],
) -> QualityMergeResult | None:
    try:
        contract = quality_run_contract_fields()
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
                **contract,
            )
        )
    except Exception as error:
        print(f"Quality merge failed open: {type(error).__name__}: {error}")
        return None
