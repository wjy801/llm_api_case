from datetime import UTC, datetime

import pytest

pytestmark = pytest.mark.usefixtures("legacy_flaky_runtime")

from quality.flaky_importer import (
    FlakyImportError,
    fold_case_observations,
    import_flaky_history,
    prepare_flaky_import,
)
from quality.flaky_models import FlakyImportRequest, FlakyImportStatus, ObservationOutcome
from quality.models import (
    CasePhase,
    CaseStatus,
    IntegrityIssue,
    IntegrityStatus,
    IssueSeverity,
    RunStatus,
)
from quality.storage import write_json_atomic


def _request(artifacts, database_path, **updates):
    values = {
        "run_id": artifacts.run.run_id,
        "quality_output_dir": artifacts.output_dir,
        "database_path": database_path,
    }
    values.update(updates)
    return FlakyImportRequest(**values)


def test_prepare_trusted_artifact_builds_one_pass_candidate(
    p0_artifact_factory,
    tmp_path,
):
    artifacts = p0_artifact_factory()

    prepared = prepare_flaky_import(_request(artifacts, tmp_path / "history.sqlite3"))

    assert prepared.metadata.environment == "overseas"
    assert prepared.metadata.eligible_count == 1
    assert prepared.metadata.excluded_count == 0
    assert prepared.profile_distribution == {"serial": 1}
    assert prepared.candidates[0].observation_outcome is ObservationOutcome.PASS
    assert prepared.candidates[0].decisive_phase is CasePhase.CALL
    assert prepared.report_artifact_ref.endswith(":<local-path>/quality-run-1")


def test_prepare_failure_candidate_keeps_unique_p0_fingerprint(
    p0_artifact_factory,
    tmp_path,
):
    artifacts = p0_artifact_factory(outcome="fail")

    prepared = prepare_flaky_import(_request(artifacts, tmp_path / "history.sqlite3"))

    candidate = prepared.candidates[0]
    assert candidate.observation_outcome is ObservationOutcome.FAIL
    assert candidate.failure_id == artifacts.failures[0].failure_id
    assert candidate.failure_category == "PRODUCT_DEFECT"


def test_source_digest_does_not_change_with_importer_version(
    p0_artifact_factory,
    tmp_path,
):
    artifacts = p0_artifact_factory()
    first = prepare_flaky_import(
        _request(artifacts, tmp_path / "history.sqlite3", importer_version="importer-a")
    )
    second = prepare_flaky_import(
        _request(artifacts, tmp_path / "history.sqlite3", importer_version="importer-b")
    )

    assert first.metadata.source_digest == second.metadata.source_digest


def test_tampered_case_results_are_rejected_before_database_creation(
    p0_artifact_factory,
    tmp_path,
):
    artifacts = p0_artifact_factory()
    database = tmp_path / "history.sqlite3"
    with (artifacts.merged / "case-results.jsonl").open("a", encoding="utf-8") as handle:
        handle.write("{}\n")

    result = import_flaky_history(_request(artifacts, database))

    assert result.status is FlakyImportStatus.FAILED
    assert result.issues[0].code == "artifact_hash_mismatch"
    assert not database.exists()


@pytest.mark.parametrize(
    ("factory_kwargs", "error_code"),
    [
        ({"environment": "unknown"}, "environment_unsupported"),
        ({"run_status": RunStatus.INTERRUPTED}, "run_not_finished"),
        ({"run_status": RunStatus.PARTIAL}, "run_not_finished"),
    ],
)
def test_untrusted_run_is_rejected(
    p0_artifact_factory,
    tmp_path,
    factory_kwargs,
    error_code,
):
    artifacts = p0_artifact_factory(**factory_kwargs)

    result = import_flaky_history(_request(artifacts, tmp_path / "history.sqlite3"))

    assert result.status is FlakyImportStatus.FAILED
    assert result.issues[0].code == error_code


@pytest.mark.parametrize(
    ("field", "value", "error_code"),
    (
        ("run_id", "foreign-run", "run_id_mismatch"),
        ("manifest_version", "quality.manifest.unsupported", "manifest_version_unsupported"),
        ("schema_version", "quality.schema.unsupported", "p0_schema_unsupported"),
        ("status", "merging", "manifest_incomplete"),
    ),
)
def test_manifest_exact_field_validation_preserves_error_codes(
    p0_artifact_factory,
    tmp_path,
    field,
    value,
    error_code,
):
    artifacts = p0_artifact_factory()
    manifest = dict(artifacts.manifest)
    manifest[field] = value
    write_json_atomic(artifacts.merged / "manifest.json", manifest)

    result = import_flaky_history(_request(artifacts, tmp_path / "history.sqlite3"))

    assert result.status is FlakyImportStatus.FAILED
    assert result.issues[0].code == error_code


def test_manifest_exact_field_validation_preserves_first_error_priority(
    p0_artifact_factory,
    tmp_path,
):
    artifacts = p0_artifact_factory()
    manifest = dict(artifacts.manifest)
    manifest.update(
        {
            "run_id": "foreign-run",
            "manifest_version": "quality.manifest.unsupported",
            "schema_version": "quality.schema.unsupported",
            "status": "merging",
        }
    )
    write_json_atomic(artifacts.merged / "manifest.json", manifest)

    result = import_flaky_history(_request(artifacts, tmp_path / "history.sqlite3"))

    assert result.issues[0].code == "run_id_mismatch"


def test_degraded_run_with_safe_warning_is_importable(
    p0_artifact_factory,
    tmp_path,
):
    issue = IntegrityIssue(
        run_id="run-1",
        severity=IssueSeverity.WARN,
        source="classifier",
        code="classification_failed",
        message="fallback fingerprint was used",
        created_at=datetime(2026, 8, 1, tzinfo=UTC),
    )
    artifacts = p0_artifact_factory(
        integrity_status=IntegrityStatus.DEGRADED,
        integrity_issues=(issue,),
    )

    result = import_flaky_history(_request(artifacts, tmp_path / "history.sqlite3"))

    assert result.status is FlakyImportStatus.DEGRADED
    assert result.inserted_count == 1


def test_degraded_request_shard_warning_does_not_block_case_history(
    p0_artifact_factory,
    tmp_path,
):
    issue = IntegrityIssue(
        run_id="run-1",
        severity=IssueSeverity.WARN,
        source="aggregator",
        code="invalid_quality_schema",
        message="requests shard contains one invalid record",
        related_id="requests-serial-pool-master.jsonl",
        created_at=datetime(2026, 8, 1, tzinfo=UTC),
    )
    artifacts = p0_artifact_factory(
        integrity_status=IntegrityStatus.DEGRADED,
        integrity_issues=(issue,),
    )

    result = import_flaky_history(_request(artifacts, tmp_path / "history.sqlite3"))

    assert result.status is FlakyImportStatus.DEGRADED
    assert result.inserted_count == 1


def test_degraded_run_with_case_status_warning_is_rejected(
    p0_artifact_factory,
    tmp_path,
):
    issue = IntegrityIssue(
        run_id="run-1",
        severity=IssueSeverity.WARN,
        source="junit",
        code="junit_status_mismatch",
        message="status differs",
        created_at=datetime(2026, 8, 1, tzinfo=UTC),
    )
    artifacts = p0_artifact_factory(
        integrity_status=IntegrityStatus.DEGRADED,
        integrity_issues=(issue,),
    )

    result = import_flaky_history(_request(artifacts, tmp_path / "history.sqlite3"))

    assert result.status is FlakyImportStatus.FAILED
    assert result.issues[0].code == "blocking_integrity_warning"


def test_phase_fold_excludes_incomplete_lifecycle(p0_artifact_factory):
    artifacts = p0_artifact_factory()
    cases = [case for case in artifacts.cases if case.phase is not CasePhase.TEARDOWN]

    folded = fold_case_observations(
        cases,
        artifacts.failures,
        environment="overseas",
        fingerprint_version="failure-fingerprint.v1",
    )

    assert folded.candidates == ()
    assert folded.excluded_reasons == {"incomplete_phase": 1}


def test_phase_fold_excludes_skip_xfail_and_xpass(p0_artifact_factory):
    for status in (CaseStatus.SKIPPED, CaseStatus.XFAILED, CaseStatus.XPASSED):
        artifacts = p0_artifact_factory(run_id=f"run-{status.value}")
        cases = [
            case.model_copy(
                update={"raw_status": status, "final_status": status}
            )
            if case.phase is CasePhase.CALL
            else case
            for case in artifacts.cases
        ]

        folded = fold_case_observations(
            cases,
            (),
            environment="overseas",
            fingerprint_version="failure-fingerprint.v1",
        )

        assert folded.candidates == ()
        assert folded.excluded_reasons == {"expected_outcome_excluded": 1}


def test_phase_fold_excludes_identity_conflict(p0_artifact_factory):
    artifacts = p0_artifact_factory()
    cases = [
        case.model_copy(update={"worker_id": "gw0"})
        if case.phase is CasePhase.CALL
        else case
        for case in artifacts.cases
    ]

    folded = fold_case_observations(
        cases,
        (),
        environment="overseas",
        fingerprint_version="failure-fingerprint.v1",
    )

    assert folded.excluded_reasons == {"identity_conflict": 1}


def test_failed_phase_without_failure_record_is_excluded(p0_artifact_factory):
    artifacts = p0_artifact_factory(outcome="fail")

    folded = fold_case_observations(
        artifacts.cases,
        (),
        environment="overseas",
        fingerprint_version="failure-fingerprint.v1",
    )

    assert folded.candidates == ()
    assert folded.excluded_reasons == {"missing_failure_fingerprint": 1}


def test_setup_error_plus_teardown_folds_to_fail(p0_artifact_factory):
    artifacts = p0_artifact_factory(outcome="fail")
    failure_id = artifacts.failures[0].failure_id
    cases = []
    for case in artifacts.cases:
        if case.phase is CasePhase.CALL:
            continue
        if case.phase is CasePhase.SETUP:
            case = case.model_copy(
                update={
                    "raw_status": CaseStatus.ERROR,
                    "final_status": CaseStatus.ERROR,
                    "failure_id": failure_id,
                }
            )
        cases.append(case)
    failure = artifacts.failures[0].model_copy(
        update={
            "phase": CasePhase.SETUP,
            "fingerprint_source": artifacts.failures[0].fingerprint_source.model_copy(
                update={"phase": CasePhase.SETUP}
            ),
        }
    )

    folded = fold_case_observations(
        cases,
        (failure,),
        environment="overseas",
        fingerprint_version="failure-fingerprint.v1",
    )

    assert folded.candidates[0].decisive_phase is CasePhase.SETUP
    assert folded.candidates[0].final_status is CaseStatus.ERROR


def test_teardown_error_has_priority_and_uses_teardown_fingerprint(
    p0_artifact_factory,
):
    artifacts = p0_artifact_factory(outcome="fail")
    failure_id = artifacts.failures[0].failure_id
    cases = []
    for case in artifacts.cases:
        if case.phase is CasePhase.CALL:
            case = case.model_copy(
                update={
                    "raw_status": CaseStatus.PASSED,
                    "final_status": CaseStatus.PASSED,
                    "failure_id": None,
                }
            )
        if case.phase is CasePhase.TEARDOWN:
            case = case.model_copy(
                update={
                    "raw_status": CaseStatus.ERROR,
                    "final_status": CaseStatus.ERROR,
                    "failure_id": failure_id,
                }
            )
        cases.append(case)
    failure = artifacts.failures[0].model_copy(
        update={
            "phase": CasePhase.TEARDOWN,
            "fingerprint_source": artifacts.failures[0].fingerprint_source.model_copy(
                update={"phase": CasePhase.TEARDOWN}
            ),
        }
    )

    folded = fold_case_observations(
        cases,
        (failure,),
        environment="overseas",
        fingerprint_version="failure-fingerprint.v1",
    )

    assert folded.candidates[0].decisive_phase is CasePhase.TEARDOWN
    assert folded.candidates[0].final_status is CaseStatus.ERROR


def test_multiple_failure_fingerprints_are_excluded(p0_artifact_factory):
    artifacts = p0_artifact_factory(outcome="fail")
    cases = [
        case.model_copy(
            update={
                "raw_status": CaseStatus.ERROR,
                "final_status": CaseStatus.ERROR,
                "failure_id": "fail-teardown-other",
            }
        )
        if case.phase is CasePhase.TEARDOWN
        else case
        for case in artifacts.cases
    ]

    folded = fold_case_observations(
        cases,
        artifacts.failures,
        environment="overseas",
        fingerprint_version="failure-fingerprint.v1",
    )

    assert folded.candidates == ()
    assert folded.excluded_reasons == {"multiple_failure_fingerprints": 1}


def test_run_and_merged_integrity_issue_mismatch_is_rejected(
    p0_artifact_factory,
    tmp_path,
):
    issue = IntegrityIssue(
        run_id="run-1",
        severity=IssueSeverity.WARN,
        source="classifier",
        code="classification_failed",
        message="fallback fingerprint was used",
        created_at=datetime(2026, 8, 1, tzinfo=UTC),
    )
    artifacts = p0_artifact_factory(
        integrity_status=IntegrityStatus.DEGRADED,
        integrity_issues=(issue,),
    )
    changed_run = artifacts.run.model_copy(update={"integrity_issues": ()})
    from quality.storage import write_json_atomic

    write_json_atomic(artifacts.output_dir / "run.json", changed_run)

    result = import_flaky_history(_request(artifacts, tmp_path / "history.sqlite3"))

    assert result.status is FlakyImportStatus.FAILED
    assert result.issues[0].code == "integrity_issue_mismatch"
