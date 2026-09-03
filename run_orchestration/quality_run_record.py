from __future__ import annotations

from datetime import datetime
import os

from quality.config import QualityRuntimeConfig
from quality.flaky_identity import runtime_flaky_environment
from quality.flaky_v3 import DEFAULT_GOVERNANCE_POLICY
from quality.models import IntegrityStatus, RunKind, RunRecord, RunStatus
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
    contract = quality_run_contract_fields()
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
        **contract,
    )


def quality_run_contract_fields() -> dict[str, object]:
    run_kind = RunKind(os.environ.get("QUALITY_RUN_KIND", RunKind.NORMAL.value))
    round_text = os.environ.get("QUALITY_PROBE_ROUND_NO")
    return {
        "run_kind": run_kind,
        "policy_revision": (
            os.environ.get("QUALITY_POLICY_REVISION")
            or DEFAULT_GOVERNANCE_POLICY.revision
        ),
        "controller_commit_sha": (
            os.environ.get("QUALITY_CONTROLLER_COMMIT_SHA")
            or os.environ.get("GIT_COMMIT")
            or None
        ),
        "attempt_id": os.environ.get("QUALITY_PROBE_ATTEMPT_ID") or None,
        "trigger_id": os.environ.get("QUALITY_PROBE_TRIGGER_ID") or None,
        "plan_digest": os.environ.get("QUALITY_PROBE_PLAN_DIGEST") or None,
        "round_no": int(round_text) if round_text else None,
        "target_commit_sha": os.environ.get("QUALITY_PROBE_TARGET_COMMIT_SHA") or None,
        "jenkins_job_name": (
            os.environ.get("QUALITY_PROBE_JENKINS_JOB_NAME") or None
        ),
        "jenkins_build_number": (
            os.environ.get("QUALITY_PROBE_JENKINS_BUILD_NUMBER") or None
        ),
        "fact_schema_version": "quality.fact.v1",
        "plugin_version": "quality-plugin.v1",
    }


def quality_environment_name() -> str:
    return runtime_flaky_environment()
