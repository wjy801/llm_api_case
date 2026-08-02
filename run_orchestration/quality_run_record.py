from __future__ import annotations

from datetime import datetime
import os

from quality.config import QualityRuntimeConfig
from quality.models import IntegrityStatus, RunRecord, RunStatus
from quality.storage import write_json_atomic


def write_initial_run_record(
    quality_config: QualityRuntimeConfig,
    start_time: datetime,
) -> None:
    if not quality_config.enabled or not quality_config.run_id:
        return
    try:
        write_json_atomic(
            quality_config.output_dir / "run.json",
            build_run_record(
                quality_config,
                start_time=start_time,
                end_time=None,
                status=RunStatus.PARTIAL,
                integrity_status=IntegrityStatus.DEGRADED,
                integrity_issues=(),
            ),
        )
    except Exception as error:
        print(
            "Quality run initialization failed: "
            f"{type(error).__name__}: {error}"
        )


def write_final_run_record(
    quality_config: QualityRuntimeConfig,
    *,
    start_time: datetime,
    end_time: datetime,
    status: RunStatus,
    integrity_status: IntegrityStatus,
    integrity_issues,
) -> None:
    try:
        write_json_atomic(
            quality_config.output_dir / "run.json",
            build_run_record(
                quality_config,
                start_time=start_time,
                end_time=end_time,
                status=status,
                integrity_status=integrity_status,
                integrity_issues=integrity_issues,
            ),
        )
    except Exception as error:
        print(
            "Quality run finalization failed open: "
            f"{type(error).__name__}: {error}"
        )


def build_run_record(
    quality_config: QualityRuntimeConfig,
    *,
    start_time: datetime,
    end_time: datetime | None,
    status: RunStatus,
    integrity_status: IntegrityStatus,
    integrity_issues,
) -> RunRecord:
    return RunRecord(
        run_id=str(quality_config.run_id),
        job_name=os.environ.get("JOB_NAME") or None,
        build_number=os.environ.get("BUILD_NUMBER") or None,
        branch=(
            os.environ.get("BRANCH_NAME")
            or os.environ.get("GIT_BRANCH")
            or None
        ),
        commit_sha=os.environ.get("GIT_COMMIT") or None,
        trigger=(
            "jenkins"
            if os.environ.get("JOB_NAME")
            and os.environ.get("BUILD_NUMBER")
            else "local"
        ),
        environment=quality_environment_name(),
        start_time=start_time,
        end_time=end_time,
        status=status,
        integrity_status=integrity_status,
        integrity_issues=tuple(integrity_issues),
    )


def quality_environment_name() -> str:
    value = os.environ.get("USE_CHINA_ENVIRONMENT")
    if value is None:
        return "unknown"
    return "china" if value.strip().upper() == "TRUE" else "overseas"
