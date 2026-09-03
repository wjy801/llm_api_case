from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from quality.flaky_v3 import (
    AdmissionStatus,
    AttemptEvidence,
    AttemptStatus,
    ComparabilityFacts,
    DEFAULT_GOVERNANCE_POLICY,
    DetectionObservation,
    NormalCaseAdmissionFacts,
    NormalRunAdmissionFacts,
    ProbeAdmissionFacts,
    ProbeClassification,
    ProbeEffectStatus,
    ProbeOutcome,
    classify_probe_evidence,
    comparability_fingerprint,
    evaluate_normal_case_admission,
    evaluate_normal_run_admission,
    recalculate_attempt,
    replay_detection_cohort,
)
from quality.models import IntegrityStatus, RunKind, RunRecord, RunStatus


def test_quality_v1_reader_maps_to_legacy_unknown_without_guessing():
    run = RunRecord.model_validate(
        {
            "schema_version": "quality.v1",
            "run_id": "legacy",
            "trigger": "jenkins",
            "environment": "overseas",
            "start_time": "2026-09-01T00:00:00Z",
            "end_time": "2026-09-01T00:01:00Z",
            "status": "finished",
            "integrity_status": "complete",
        }
    )

    assert run.run_kind is RunKind.LEGACY_UNKNOWN
    assert run.attempt_id is None
    assert run.policy_revision is None


def test_quality_v2_rejects_unknown_kind_missing_probe_fields_and_invalid_sha():
    values = _run_values()
    with pytest.raises(ValidationError):
        RunRecord(**{**values, "run_kind": "UNKNOWN"})
    with pytest.raises(ValidationError, match="FLAKY_PROBE fields are required"):
        RunRecord(**{**values, "run_kind": RunKind.FLAKY_PROBE})
    with pytest.raises(ValidationError, match="lowercase hexadecimal SHA"):
        RunRecord(**{**values, "run_kind": RunKind.NORMAL, "commit_sha": "A" * 40})


def test_stage0_policy_revision_and_admission_priority_are_frozen():
    assert DEFAULT_GOVERNANCE_POLICY.revision == (
        "sha256:54e21bd00acf350a26a9a9e13a2f748f65278c71337453a3c8806632c7e51569"
    )
    result = evaluate_normal_run_admission(
        NormalRunAdmissionFacts(
            run_kind=RunKind.LEGACY_UNKNOWN,
            source_job_allowed=False,
            branch_allowed=False,
            environment_allowed=True,
            execution_profile_allowed=True,
            run_finished=True,
            versions_compatible=True,
            artifacts_trusted=True,
            integrity_eligible=True,
            comparability_valid=False,
        ),
        policy_revision=DEFAULT_GOVERNANCE_POLICY.revision,
    )
    assert result.status is AdmissionStatus.INELIGIBLE
    assert result.reason_codes == (
        "normal_run_kind_mismatch",
        "normal_source_job_not_allowed",
        "normal_branch_not_allowed",
        "normal_comparability_missing",
    )
    assert result.primary_reason_code == "normal_run_kind_mismatch"

    case = evaluate_normal_case_admission(
        NormalCaseAdmissionFacts(
            lifecycle_valid=False,
            collection_failure=True,
            infrastructure_failure=True,
            outcome="fail",
        ),
        policy_revision=DEFAULT_GOVERNANCE_POLICY.revision,
    )
    assert case.reason_codes == (
        "case_lifecycle_invalid",
        "case_collection_failure",
        "case_infrastructure_failure",
    )


def test_comparability_fingerprint_and_replay_are_order_independent():
    facts = ComparabilityFacts(
        configuration_revision="config-v1",
        environment="overseas",
        execution_profile="serial",
        sut_revision="sut-v1",
        test_definition_digest=f"sha256:{'d' * 64}",
    )
    fingerprint = comparability_fingerprint(facts)
    assert fingerprint.startswith("flaky-comparability-v1-")
    times = datetime(2026, 9, 1, tzinfo=UTC)
    observations = (
        DetectionObservation("o1", "r1", times, "pass"),
        DetectionObservation("o2", "r2", times + timedelta(minutes=1), "fail", "a"),
        DetectionObservation("o3", "r3", times + timedelta(minutes=2), "fail", "a"),
        DetectionObservation("o4", "r4", times + timedelta(minutes=3), "pass"),
    )
    forward = replay_detection_cohort(
        observations,
        flaky_key="flaky-key",
        detection_generation=1,
        fingerprint=fingerprint,
    )
    reverse = replay_detection_cohort(
        tuple(reversed(observations)),
        flaky_key="flaky-key",
        detection_generation=1,
        fingerprint=fingerprint,
    )
    assert forward.state.value == "CONFIRMED"
    assert forward == reverse


@pytest.mark.parametrize(
    ("facts", "classification", "reason", "consumes"),
    [
        (
            ProbeAdmissionFacts(
                attempt_active=False,
                plan_matches=True,
                evidence_trusted=True,
                outcome=ProbeOutcome.PASS,
            ),
            ProbeClassification.NON_COUNTING,
            "probe_attempt_inactive",
            False,
        ),
        (
            ProbeAdmissionFacts(
                attempt_active=True,
                plan_matches=True,
                evidence_trusted=False,
                outcome=ProbeOutcome.PASS,
            ),
            ProbeClassification.NON_COUNTING,
            "probe_evidence_untrusted",
            True,
        ),
        (
            ProbeAdmissionFacts(
                attempt_active=True,
                plan_matches=True,
                evidence_trusted=True,
                outcome=ProbeOutcome.FAIL,
                trusted_failure=True,
            ),
            ProbeClassification.TRUSTED_FAIL,
            "probe_trusted_fail",
            False,
        ),
    ],
)
def test_probe_classification_is_deterministic(facts, classification, reason, consumes):
    result = classify_probe_evidence(facts)
    assert result.classification is classification
    assert result.reason_code == reason
    assert result.consumes_non_counting_quota is consumes


def test_attempt_recalculation_uses_full_sorted_evidence():
    start = datetime(2026, 9, 1, tzinfo=UTC)
    evidence = tuple(
        AttemptEvidence(
            run_id=f"r{round_no}",
            round_no=round_no,
            trusted_started_at=start + timedelta(minutes=31 * round_no),
            classification=ProbeClassification.COUNT_PASS,
            consumes_non_counting_quota=False,
            effect_status=ProbeEffectStatus.APPLIED,
        )
        for round_no in range(1, 6)
    )
    result = recalculate_attempt(
        tuple(reversed(evidence)),
        now=start + timedelta(hours=3),
        expires_at=start + timedelta(hours=72),
        required_consecutive_passes=5,
        max_non_counting_runs=3,
    )
    assert result.status is AttemptStatus.READY_TO_CLOSE
    assert result.counted_passes == 5


def _run_values() -> dict[str, object]:
    start = datetime(2026, 9, 1, tzinfo=UTC)
    return {
        "run_id": "run-v2",
        "job_name": "quality-job",
        "build_number": "1",
        "branch": "dev3",
        "commit_sha": "a" * 40,
        "trigger": "jenkins",
        "environment": "overseas",
        "start_time": start,
        "end_time": start + timedelta(minutes=1),
        "status": RunStatus.FINISHED,
        "integrity_status": IntegrityStatus.COMPLETE,
        "policy_revision": DEFAULT_GOVERNANCE_POLICY.revision,
        "controller_commit_sha": "b" * 40,
        "fact_schema_version": "quality.fact.v1",
        "plugin_version": "quality-plugin.v1",
    }
