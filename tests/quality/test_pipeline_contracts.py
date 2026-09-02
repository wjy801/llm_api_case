from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from quality.models import IntegrityStatus, RunKind, RunRecord, RunStatus
from quality.pipeline_contracts import (
    PipelineActivityStatus,
    PipelineBuildIdentity,
    PipelineCurrent,
    PipelineFreshnessStatus,
    PipelineIssue,
    PipelineQualityStatus,
    PipelineResultStatus,
    PipelineRunSummary,
    PipelineRuns,
    PipelineStage,
    PipelineTestSummary,
    associate_normal_run,
)


NOW = datetime(2026, 9, 2, 1, 0, tzinfo=UTC)
COMMIT_SHA = "a" * 40


def _current(**changes) -> PipelineCurrent:
    values = {
        "activity_status": PipelineActivityStatus.RUNNING,
        "result_status": PipelineResultStatus.UNKNOWN,
        "quality_status": PipelineQualityStatus.PENDING,
        "freshness_status": PipelineFreshnessStatus.FRESH,
        "job_name": "folder/api-case-main",
        "build_number": 128,
        "branch": "dev3",
        "commit_sha": COMMIT_SHA,
        "trigger_kind": "TIMER",
        "started_at": NOW,
        "duration_ms": 42_000,
        "current_stage": "Real Smoke",
        "observed_at": NOW,
        "last_successful_poll_at": NOW,
    }
    values.update(changes)
    return PipelineCurrent(**values)


def _normal_run(**changes) -> RunRecord:
    values = {
        "schema_version": "quality.v2",
        "run_id": "main-128",
        "job_name": "folder/api-case-main",
        "build_number": "128",
        "branch": "origin/dev3",
        "commit_sha": COMMIT_SHA,
        "trigger": "jenkins",
        "environment": "china",
        "start_time": NOW,
        "end_time": NOW,
        "status": RunStatus.FINISHED,
        "integrity_status": IntegrityStatus.COMPLETE,
        "run_kind": RunKind.NORMAL,
        "policy_revision": "policy-v1",
        "controller_commit_sha": "b" * 40,
        "fact_schema_version": "quality.fact.v1",
        "plugin_version": "quality-plugin.v1",
    }
    values.update(changes)
    return RunRecord(**values)


def test_pipeline_status_contracts_are_exact_and_stable():
    assert tuple(item.value for item in PipelineActivityStatus) == (
        "QUEUED",
        "RUNNING",
        "IDLE",
        "UNKNOWN",
    )
    assert tuple(item.value for item in PipelineResultStatus) == (
        "SUCCESS",
        "FAILURE",
        "UNSTABLE",
        "ABORTED",
        "NOT_BUILT",
        "UNKNOWN",
    )
    assert tuple(item.value for item in PipelineQualityStatus) == (
        "PENDING",
        "READY",
        "NOT_RUN",
        "MISSING",
        "INVALID",
    )
    assert tuple(item.value for item in PipelineFreshnessStatus) == (
        "FRESH",
        "STALE",
        "UNAVAILABLE",
    )


def test_pipeline_current_v1_schema_is_frozen_and_forbids_unknown_fields():
    current = _current()

    payload = current.model_dump(mode="json", exclude_none=True)

    assert payload["schema_version"] == "quality.pipeline-current.v1"
    assert payload["activity_status"] == "RUNNING"
    assert payload["result_status"] == "UNKNOWN"
    assert payload["quality_status"] == "PENDING"
    assert payload["freshness_status"] == "FRESH"
    assert payload["job_name"] == "folder/api-case-main"
    assert payload["commit_sha"] == COMMIT_SHA
    with pytest.raises(ValidationError):
        current.build_number = 129
    with pytest.raises(ValidationError, match="Extra inputs"):
        PipelineCurrent(**{**current.model_dump(), "secret": "must-not-escape"})


def test_pipeline_current_rejects_mixed_activity_result_and_freshness_states():
    with pytest.raises(ValidationError, match="cannot have a final result"):
        _current(result_status=PipelineResultStatus.SUCCESS)
    with pytest.raises(ValidationError, match="require build_number and started_at"):
        _current(build_number=None)
    with pytest.raises(ValidationError, match="must have UNKNOWN activity"):
        _current(
            activity_status=PipelineActivityStatus.IDLE,
            result_status=PipelineResultStatus.SUCCESS,
            freshness_status=PipelineFreshnessStatus.UNAVAILABLE,
        )
    with pytest.raises(ValidationError, match="timezone"):
        _current(observed_at=datetime(2026, 9, 2, 1, 0))


@pytest.mark.parametrize(
    "model",
    [
        lambda: PipelineIssue(code="   ", source="jenkins", message="message"),
        lambda: PipelineIssue(code="code", source="jenkins", message="   "),
        lambda: PipelineStage(name="   ", status="RUNNING"),
        lambda: PipelineStage(name="stage", status="   "),
    ],
)
def test_pipeline_contract_text_fields_reject_whitespace(model):
    with pytest.raises(ValidationError, match="must not be blank"):
        model()


def test_pipeline_test_summary_does_not_treat_missing_counts_as_zero():
    assert PipelineTestSummary(
        total=6,
        passed=3,
        failed=1,
        errors=1,
        skipped=1,
    ).total == 6
    with pytest.raises(ValidationError, match="add up"):
        PipelineTestSummary(total=6, passed=3, failed=1, errors=0, skipped=1)


def test_pipeline_runs_contract_limits_and_orders_history():
    newest = PipelineRunSummary(
        build_number=9,
        activity_status="IDLE",
        result_status="SUCCESS",
        quality_status="READY",
        freshness_status="FRESH",
        branch="dev3",
        commit_sha=COMMIT_SHA,
        started_at=NOW,
    )
    older = newest.model_copy(update={"build_number": 8})

    result = PipelineRuns(
        job_name="folder/api-case-main",
        limit=2,
        items=(newest, older),
        observed_at=NOW,
    )

    assert result.schema_version == "quality.pipeline-runs.v1"
    with pytest.raises(ValidationError, match="descending"):
        PipelineRuns(
            job_name="folder/api-case-main",
            limit=2,
            items=(older, newest),
            observed_at=NOW,
        )
    with pytest.raises(ValidationError):
        PipelineRuns(
            job_name="folder/api-case-main",
            limit=51,
            items=(),
            observed_at=NOW,
        )


def test_pipeline_run_summary_uses_the_same_status_consistency_rules():
    values = {
        "build_number": 9,
        "activity_status": "RUNNING",
        "result_status": "UNKNOWN",
        "quality_status": "PENDING",
        "freshness_status": "FRESH",
        "started_at": NOW,
    }
    assert PipelineRunSummary(**values).activity_status is PipelineActivityStatus.RUNNING
    with pytest.raises(ValidationError, match="cannot have a final result"):
        PipelineRunSummary(**{**values, "result_status": "SUCCESS"})
    with pytest.raises(ValidationError, match="must have UNKNOWN activity"):
        PipelineRunSummary(
            **{
                **values,
                "activity_status": "IDLE",
                "result_status": "SUCCESS",
                "freshness_status": "UNAVAILABLE",
            }
        )
    with pytest.raises(ValidationError, match="require started_at"):
        PipelineRunSummary(**{**values, "started_at": None})


def test_normal_run_association_requires_all_four_identity_fields():
    build = PipelineBuildIdentity(
        job_name="folder/api-case-main",
        build_number=128,
        branch="refs/heads/dev3",
        commit_sha=COMMIT_SHA.upper(),
    )

    result = associate_normal_run(build, _normal_run())

    assert result.matched is True
    assert result.issue_codes == ()
    assert build.branch == "dev3"
    assert build.commit_sha == COMMIT_SHA


def test_normal_run_association_reports_each_conflicting_identity():
    build = PipelineBuildIdentity(
        job_name="folder/api-case-main",
        build_number=128,
        branch="dev3",
        commit_sha=COMMIT_SHA,
    )
    conflicting = _normal_run(
        job_name="other-job",
        build_number="129",
        branch="feature/other",
        commit_sha="c" * 40,
    )

    result = associate_normal_run(build, conflicting)

    assert result.matched is False
    assert result.issue_codes == (
        "job_name_mismatch",
        "build_number_mismatch",
        "branch_mismatch",
        "commit_mismatch",
    )


def test_association_rejects_probe_and_incomplete_normal_runs():
    build = PipelineBuildIdentity(
        job_name="folder/api-case-main",
        build_number=128,
        branch="dev3",
        commit_sha=COMMIT_SHA,
    )
    probe = _normal_run().model_copy(update={"run_kind": RunKind.FLAKY_PROBE})
    incomplete = _normal_run().model_copy(update={"commit_sha": None})

    assert associate_normal_run(build, probe).issue_codes == (
        "run_kind_not_normal",
    )
    assert associate_normal_run(build, incomplete).issue_codes == (
        "normal_run_identity_incomplete",
    )


@pytest.mark.parametrize("build_number", [0, -1, "01", "1.0", True])
def test_build_identity_requires_a_positive_canonical_build_number(build_number):
    with pytest.raises(ValidationError, match="positive canonical integer"):
        PipelineBuildIdentity(
            job_name="api-case-main",
            build_number=build_number,
            branch="dev3",
            commit_sha=COMMIT_SHA,
        )
