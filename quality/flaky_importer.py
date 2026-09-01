from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
from typing import Any, Sequence, TypeVar

from pydantic import BaseModel, ValidationError

from util.artifact_io import (
    ArtifactFormatError,
    ArtifactJsonLineError,
    exact_field_mismatches,
    file_sha256,
    read_json_object as read_artifact_json_object,
    read_jsonl_values,
)

from quality.aggregator import MANIFEST_VERSION
from quality.flaky_models import (
    CaseObservationCandidate,
    EpochResetRequest,
    EpochResetResult,
    FlakyDatabaseCheck,
    FlakyEvaluationResult,
    FlakyEvaluationStatus,
    FlakyGovernanceRecord,
    FlakyHistoryEntry,
    FlakyImportIssue,
    FlakyImportRequest,
    FlakyImportResult,
    FlakyImportStatus,
    FlakyManualActionRequest,
    FlakyQuarantineRequest,
    FlakyRunMetadata,
    FlakyStateRecord,
    GovernanceStatus,
    ObservationOutcome,
)
from quality.flaky_identity import (
    build_epoch_scope_key,
    build_flaky_key,
    normalize_execution_profile,
    normalize_flaky_environment,
    normalize_stored_execution_profile,
)
from quality.flaky_store import FlakyStore, FlakyStoreError
from quality.identifiers import normalize_nodeid
from quality.models import (
    SCHEMA_VERSION,
    CasePhase,
    CaseResult,
    CaseStatus,
    FailureRecord,
    IntegrityIssue,
    IntegrityStatus,
    IssueSeverity,
    RunRecord,
    RunStatus,
)
from quality.redaction import redact_quality_value
from quality.storage import write_json_atomic


_SAFE_WARN_CODES = frozenset(
    {
        "classification_failed",
        "junit_file_missing",
        "junit_file_stale",
        "junit_parse_failed",
    }
)
_SHARD_PARSE_WARN_CODES = frozenset(
    {"invalid_jsonl_line", "invalid_jsonl_schema", "invalid_quality_schema"}
)


class FlakyImportError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class FoldResult:
    candidates: tuple[CaseObservationCandidate, ...]
    excluded_reasons: dict[str, int]
    issues: tuple[FlakyImportIssue, ...]


@dataclass(frozen=True)
class PreparedFlakyImport:
    metadata: FlakyRunMetadata
    candidates: tuple[CaseObservationCandidate, ...]
    excluded_reasons: dict[str, int]
    profile_distribution: dict[str, int]
    source_hashes: dict[str, str]
    report_artifact_ref: str
    issues: tuple[FlakyImportIssue, ...]


def build_observation_id(run_id: str, flaky_key: str) -> str:
    payload = {
        "run_id": _required_text(run_id, "run_id"),
        "flaky_key": _required_text(flaky_key, "flaky_key"),
    }
    return f"observation-v1-{_full_hash(payload)}"


def fold_case_observations(
    case_results: Sequence[CaseResult],
    failures: Sequence[FailureRecord],
    *,
    environment: str,
    fingerprint_version: str,
) -> FoldResult:
    normalized_environment = normalize_flaky_environment(environment)
    failure_lookup: dict[tuple[str, str, str, CasePhase], list[FailureRecord]] = defaultdict(list)
    for failure in failures:
        failure_lookup[
            (failure.failure_id, failure.invocation_id, failure.case_id, failure.phase)
        ].append(failure)

    grouped: dict[tuple[str, str], list[CaseResult]] = defaultdict(list)
    for case in case_results:
        grouped[(case.run_id, case.invocation_id)].append(case)

    candidates: list[CaseObservationCandidate] = []
    excluded = Counter[str]()
    issues: list[FlakyImportIssue] = []
    for (_run_id, invocation_id), phases in sorted(grouped.items()):
        try:
            candidate = _fold_invocation(
                phases,
                failure_lookup,
                environment=normalized_environment,
                fingerprint_version=fingerprint_version,
            )
        except FlakyImportError as error:
            excluded[error.code] += 1
            issues.append(
                FlakyImportIssue(
                    severity=IssueSeverity.WARN,
                    code=error.code,
                    summary=_safe_summary(str(error)),
                    related_id=invocation_id,
                )
            )
            continue
        candidates.append(candidate)
    return FoldResult(
        candidates=tuple(candidates),
        excluded_reasons=dict(sorted(excluded.items())),
        issues=tuple(issues),
    )


def prepare_flaky_import(request: FlakyImportRequest) -> PreparedFlakyImport:
    output_dir = request.quality_output_dir.resolve()
    paths = {
        "run_record": output_dir / "run.json",
        "manifest": output_dir / "merged" / "manifest.json",
        "case_results": output_dir / "merged" / "case-results.jsonl",
        "failures": output_dir / "merged" / "failures.jsonl",
        "integrity_issues": output_dir / "merged" / "integrity-issues.jsonl",
    }
    for name, path in paths.items():
        if not path.is_file():
            raise FlakyImportError(
                "artifact_missing",
                f"required P0 artifact is missing: {name}",
            )

    source_hashes = {name: _file_sha256(path) for name, path in paths.items()}
    run_record = _read_model(paths["run_record"], RunRecord)
    manifest = _read_json_object(paths["manifest"])
    _validate_run_and_manifest(request.run_id, run_record, manifest)
    _validate_output_hashes(manifest, source_hashes)

    case_results = _read_jsonl_models(paths["case_results"], CaseResult, request.run_id)
    failures = _read_jsonl_models(paths["failures"], FailureRecord, request.run_id)
    integrity_issues = _read_jsonl_models(
        paths["integrity_issues"],
        IntegrityIssue,
        request.run_id,
    )
    _validate_run_integrity_issues(run_record, integrity_issues)
    _validate_integrity_issues(integrity_issues)

    environment = normalize_flaky_environment(run_record.environment)
    fingerprint_version = _manifest_text(manifest, "fingerprint_version")
    fold = fold_case_observations(
        case_results,
        failures,
        environment=environment,
        fingerprint_version=fingerprint_version,
    )
    source_digest = _source_digest(request.run_id, source_hashes)
    source_kind, artifact_ref, report_artifact_ref = _artifact_references(
        run_record,
        output_dir,
    )
    profile_distribution = dict(
        sorted(Counter(candidate.execution_profile for candidate in fold.candidates).items())
    )
    if run_record.end_time is None:
        raise FlakyImportError("run_not_finished", "finished run is missing end_time")
    metadata = FlakyRunMetadata(
        run_id=request.run_id,
        source_digest=source_digest,
        source_kind=source_kind,
        artifact_ref=artifact_ref,
        job_name=run_record.job_name,
        build_number=run_record.build_number,
        branch=run_record.branch,
        commit_sha=run_record.commit_sha,
        environment=environment,
        run_status=run_record.status.value,
        p0_integrity_status=run_record.integrity_status.value,
        run_start_time=run_record.start_time,
        run_end_time=run_record.end_time,
        p0_schema_version=_manifest_text(manifest, "schema_version"),
        p0_merge_version=_manifest_text(manifest, "merge_version"),
        fingerprint_version=fingerprint_version,
        run_record_sha256=source_hashes["run_record"],
        manifest_sha256=source_hashes["manifest"],
        case_results_sha256=source_hashes["case_results"],
        failures_sha256=source_hashes["failures"],
        integrity_issues_sha256=source_hashes["integrity_issues"],
        importer_version=request.importer_version,
        eligible_count=len(fold.candidates),
        excluded_count=sum(fold.excluded_reasons.values()),
    )
    source_issues = tuple(
        FlakyImportIssue(
            severity=issue.severity,
            code=issue.code,
            summary=_safe_summary(issue.message),
            related_id=issue.related_id,
        )
        for issue in integrity_issues
    )
    return PreparedFlakyImport(
        metadata=metadata,
        candidates=fold.candidates,
        excluded_reasons=fold.excluded_reasons,
        profile_distribution=profile_distribution,
        source_hashes=source_hashes,
        report_artifact_ref=report_artifact_ref,
        issues=(*source_issues, *fold.issues),
    )


def import_flaky_history(request: FlakyImportRequest) -> FlakyImportResult:
    prepared: PreparedFlakyImport | None = None
    try:
        prepared = prepare_flaky_import(request)
        outcome = FlakyStore(request.database_path).import_run(
            prepared.metadata,
            prepared.candidates,
        )
        if not outcome.imported:
            status = FlakyImportStatus.NOOP
        elif not prepared.candidates:
            status = FlakyImportStatus.NO_DATA
        elif prepared.metadata.p0_integrity_status == IntegrityStatus.DEGRADED.value:
            status = FlakyImportStatus.DEGRADED
        else:
            status = FlakyImportStatus.IMPORTED
        result = FlakyImportResult(
            run_id=request.run_id,
            status=status,
            source_digest=prepared.metadata.source_digest,
            artifact_ref=prepared.report_artifact_ref,
            environment=prepared.metadata.environment,
            profile_distribution=prepared.profile_distribution,
            eligible_count=prepared.metadata.eligible_count,
            excluded_count=prepared.metadata.excluded_count,
            inserted_count=outcome.inserted_count,
            excluded_reasons=prepared.excluded_reasons,
            database_schema_version=outcome.initialization.schema_version,
            quick_check=outcome.initialization.quick_check,
            source_hashes=prepared.source_hashes,
            p0_integrity_status=prepared.metadata.p0_integrity_status,
            migration_applied=outcome.initialization.migration_applied,
            backup_created=outcome.initialization.backup_created,
            issues=prepared.issues,
        )
    except (FlakyImportError, FlakyStoreError, ValidationError, OSError, ValueError) as error:
        code = getattr(error, "code", "flaky_import_failed")
        status = (
            FlakyImportStatus.NO_DATA
            if code in {"db_busy", "database_not_found", "invalid_database_path"}
            else FlakyImportStatus.FAILED
        )
        result = FlakyImportResult(
            run_id=request.run_id,
            status=status,
            source_digest=prepared.metadata.source_digest if prepared else None,
            artifact_ref=prepared.report_artifact_ref if prepared else None,
            environment=prepared.metadata.environment if prepared else None,
            profile_distribution=prepared.profile_distribution if prepared else {},
            eligible_count=prepared.metadata.eligible_count if prepared else 0,
            excluded_count=prepared.metadata.excluded_count if prepared else 0,
            excluded_reasons=prepared.excluded_reasons if prepared else {},
            source_hashes=prepared.source_hashes if prepared else {},
            p0_integrity_status=(
                prepared.metadata.p0_integrity_status if prepared else None
            ),
            issues=(
                *(prepared.issues if prepared else ()),
                FlakyImportIssue(
                    severity=IssueSeverity.ERROR,
                    code=code,
                    summary=_safe_summary(str(error)),
                ),
            ),
        )
    return _write_import_report(request.quality_output_dir, result)


def write_flaky_no_data_report(
    *,
    run_id: str,
    quality_output_dir: str | Path,
    code: str,
    summary: str,
) -> FlakyImportResult:
    result = FlakyImportResult(
        run_id=run_id,
        status=FlakyImportStatus.NO_DATA,
        issues=(
            FlakyImportIssue(
                severity=IssueSeverity.WARN,
                code=code,
                summary=_safe_summary(summary),
            ),
        ),
    )
    return _write_import_report(Path(quality_output_dir), result)


def query_flaky_history(
    database_path: str | Path,
    *,
    case_id: str,
    param_hash: str | None = None,
    environment: str | None = None,
    execution_profile: str | None = None,
    state_epoch: int | None = None,
) -> tuple[FlakyHistoryEntry, ...]:
    if state_epoch is not None and state_epoch < 1:
        raise ValueError("state_epoch must be greater than or equal to 1")
    normalized_environment = (
        normalize_flaky_environment(environment) if environment is not None else None
    )
    normalized_profile = (
        normalize_stored_execution_profile(execution_profile)
        if execution_profile is not None
        else None
    )
    return FlakyStore(database_path).history(
        case_id=_required_text(case_id, "case_id"),
        param_hash=_optional_text(param_hash),
        environment=normalized_environment,
        execution_profile=normalized_profile,
        state_epoch=state_epoch,
    )


def reset_flaky_epoch(
    database_path: str | Path,
    request: EpochResetRequest,
) -> EpochResetResult:
    normalized = EpochResetRequest(
        case_id=request.case_id,
        environment=normalize_flaky_environment(request.environment),
        execution_profile=normalize_stored_execution_profile(request.execution_profile),
        actor=_safe_audit_text(request.actor, 128),
        reason=_safe_audit_text(request.reason, 500),
    )
    epoch_scope_key = build_epoch_scope_key(
        normalized.case_id,
        normalized.environment,
        normalized.execution_profile,
    )
    return FlakyStore(database_path).reset_epoch(
        normalized,
        epoch_scope_key=epoch_scope_key,
    )


def check_flaky_database(database_path: str | Path) -> FlakyDatabaseCheck:
    return FlakyStore(database_path).check_database()


def evaluate_flaky_state(
    database_path: str | Path,
    *,
    run_id: str,
    quality_output_dir: str | Path,
) -> FlakyEvaluationResult:
    try:
        result = FlakyStore(database_path).evaluate_run(run_id)
    except (FlakyStoreError, ValidationError, OSError, ValueError) as error:
        code = getattr(error, "code", "flaky_state_evaluation_failed")
        result = FlakyEvaluationResult(
            run_id=_required_text(run_id, "run_id"),
            status=(
                FlakyEvaluationStatus.NO_DATA
                if code in {"db_busy", "database_not_found", "invalid_database_path"}
                else FlakyEvaluationStatus.FAILED
            ),
            evaluated_at=datetime.now(UTC),
            stale_count=1,
            issues=(
                FlakyImportIssue(
                    severity=IssueSeverity.ERROR,
                    code=code,
                    summary=_safe_summary(str(error)),
                ),
            ),
        )
    return _write_evaluation_report(quality_output_dir, result)


def write_flaky_state_no_data_report(
    *,
    run_id: str,
    quality_output_dir: str | Path,
    code: str,
    summary: str,
) -> FlakyEvaluationResult:
    result = FlakyEvaluationResult(
        run_id=_required_text(run_id, "run_id"),
        status=FlakyEvaluationStatus.NO_DATA,
        evaluated_at=datetime.now(UTC),
        issues=(
            FlakyImportIssue(
                severity=IssueSeverity.WARN,
                code=_required_text(code, "code"),
                summary=_safe_summary(summary),
            ),
        ),
    )
    return _write_evaluation_report(quality_output_dir, result)


def query_flaky_states(
    database_path: str | Path,
    *,
    case_id: str,
    param_hash: str | None = None,
    environment: str | None = None,
    execution_profile: str | None = None,
    state_epoch: int | None = None,
) -> tuple[FlakyStateRecord, ...]:
    if state_epoch is not None and state_epoch < 1:
        raise ValueError("state_epoch must be greater than or equal to 1")
    return FlakyStore(database_path).states(
        case_id=_required_text(case_id, "case_id"),
        param_hash=_optional_text(param_hash),
        environment=(
            normalize_flaky_environment(environment)
            if environment is not None
            else None
        ),
        execution_profile=(
            normalize_stored_execution_profile(execution_profile)
            if execution_profile is not None
            else None
        ),
        state_epoch=state_epoch,
    )


def confirm_flaky_state(
    database_path: str | Path,
    request: FlakyManualActionRequest,
) -> FlakyStateRecord:
    return FlakyStore(database_path).confirm_flaky(_safe_manual_request(request))


def mark_flaky_not_flaky(
    database_path: str | Path,
    request: FlakyManualActionRequest,
) -> FlakyStateRecord:
    return FlakyStore(database_path).mark_not_flaky(_safe_manual_request(request))


def quarantine_flaky_state(
    database_path: str | Path,
    request: FlakyQuarantineRequest,
) -> FlakyGovernanceRecord:
    safe = FlakyQuarantineRequest(
        flaky_key=request.flaky_key,
        actor=_safe_audit_text(request.actor, 128),
        reason=_safe_audit_text(request.reason, 500),
        owner=_safe_audit_text(request.owner, 128),
        expires_at=request.expires_at,
    )
    return FlakyStore(database_path).quarantine(safe)


def start_flaky_recovery(
    database_path: str | Path,
    request: FlakyManualActionRequest,
) -> FlakyGovernanceRecord:
    return FlakyStore(database_path).start_recovery(_safe_manual_request(request))


def cancel_flaky_quarantine(
    database_path: str | Path,
    request: FlakyManualActionRequest,
) -> FlakyStateRecord:
    return FlakyStore(database_path).cancel_quarantine(_safe_manual_request(request))


def list_flaky_governance(
    database_path: str | Path,
    *,
    status: GovernanceStatus | None = None,
    overdue: bool = False,
) -> tuple[FlakyGovernanceRecord, ...]:
    return FlakyStore(database_path).governance(status=status, overdue=overdue)


def rebuild_flaky_states(
    database_path: str | Path,
    *,
    apply: bool,
) -> dict[str, object]:
    return FlakyStore(database_path).rebuild_states(apply=apply)


def _safe_manual_request(
    request: FlakyManualActionRequest,
) -> FlakyManualActionRequest:
    return FlakyManualActionRequest(
        flaky_key=request.flaky_key,
        actor=_safe_audit_text(request.actor, 128),
        reason=_safe_audit_text(request.reason, 500),
    )


def _fold_invocation(
    phases: Sequence[CaseResult],
    failure_lookup: dict[tuple[str, str, str, CasePhase], list[FailureRecord]],
    *,
    environment: str,
    fingerprint_version: str,
) -> CaseObservationCandidate:
    if not phases:
        raise FlakyImportError("empty_invocation", "invocation has no CaseResult phases")
    first = phases[0]
    try:
        stable_nodeids = {normalize_nodeid(phase.nodeid).stable_nodeid for phase in phases}
    except ValueError as error:
        raise FlakyImportError("identity_conflict", str(error)) from error
    identity_sets = {
        "run_id": {phase.run_id for phase in phases},
        "case_id": {phase.case_id for phase in phases},
        "param_hash": {phase.param_hash for phase in phases},
        "execution_id": {phase.execution_id for phase in phases},
        "worker_id": {phase.worker_id for phase in phases},
    }
    if any(len(values) != 1 for values in identity_sets.values()):
        raise FlakyImportError(
            "identity_conflict",
            "CaseResult phases contain inconsistent identity fields",
        )
    if stable_nodeids != {first.case_id}:
        raise FlakyImportError(
            "identity_conflict",
            "CaseResult nodeid stable definition does not match case_id",
        )
    try:
        profile = normalize_execution_profile(first.execution_id, first.worker_id)
    except ValueError as error:
        raise FlakyImportError("execution_profile_unsupported", str(error)) from error

    by_phase: dict[CasePhase, CaseResult] = {}
    for phase in phases:
        if phase.phase in by_phase:
            raise FlakyImportError(
                "duplicate_phase",
                f"invocation contains duplicate {phase.phase.value} phase",
            )
        by_phase[phase.phase] = phase
    phase_set = set(by_phase)
    normal = {CasePhase.SETUP, CasePhase.CALL, CasePhase.TEARDOWN}
    early_setup = {CasePhase.SETUP, CasePhase.TEARDOWN}
    if phase_set == normal:
        pass
    elif phase_set == early_setup and by_phase[CasePhase.SETUP].final_status in {
        CaseStatus.ERROR,
        CaseStatus.SKIPPED,
    }:
        pass
    elif CasePhase.COLLECTION in phase_set:
        raise FlakyImportError(
            "collection_phase",
            "collection-only failures are not Case observations",
        )
    else:
        raise FlakyImportError(
            "incomplete_phase",
            "invocation does not match an accepted Case lifecycle",
        )

    error_phases = [phase for phase in phases if phase.final_status is CaseStatus.ERROR]
    failed_phases = [phase for phase in phases if phase.final_status is CaseStatus.FAILED]
    if error_phases or failed_phases:
        relevant = error_phases or failed_phases
        failure_ids = {
            phase.failure_id
            for phase in (*error_phases, *failed_phases)
            if phase.failure_id is not None
        }
        if not failure_ids:
            raise FlakyImportError(
                "missing_failure_fingerprint",
                "failed invocation has no P0 failure_id",
            )
        if len(failure_ids) != 1:
            raise FlakyImportError(
                "multiple_failure_fingerprints",
                "failed invocation has multiple P0 failure fingerprints",
            )
        failure_id = next(iter(failure_ids))
        decisive_options = [phase for phase in relevant if phase.failure_id == failure_id]
        if not decisive_options:
            raise FlakyImportError(
                "missing_failure_fingerprint",
                "decisive failure/error phase has no P0 failure_id",
            )
        decisive = min(decisive_options, key=lambda item: _phase_order(item.phase))
        matching_failures = failure_lookup.get(
            (failure_id, first.invocation_id, first.case_id, decisive.phase),
            [],
        )
        if len(matching_failures) != 1:
            raise FlakyImportError(
                "missing_failure_fingerprint",
                "failure_id has no unique matching P0 FailureRecord occurrence",
            )
        failure = matching_failures[0]
        outcome = ObservationOutcome.FAIL
        final_status = CaseStatus.ERROR if error_phases else CaseStatus.FAILED
        failure_category = failure.category.value
    else:
        call = by_phase.get(CasePhase.CALL)
        if call is not None and call.final_status is CaseStatus.PASSED:
            if call.raw_status is not CaseStatus.PASSED:
                raise FlakyImportError(
                    "unsupported_status",
                    "passed call has a non-passed raw status",
                )
            decisive = call
            outcome = ObservationOutcome.PASS
            final_status = CaseStatus.PASSED
            failure_id = None
            failure_category = None
        elif any(
            phase.final_status
            in {CaseStatus.SKIPPED, CaseStatus.XFAILED, CaseStatus.XPASSED}
            for phase in phases
        ):
            raise FlakyImportError(
                "expected_outcome_excluded",
                "skipped/xfail/xpass invocation is excluded from Flaky history",
            )
        else:
            raise FlakyImportError(
                "unsupported_status",
                "invocation status cannot be folded to pass/fail",
            )

    return CaseObservationCandidate(
        run_id=first.run_id,
        invocation_id=first.invocation_id,
        case_id=first.case_id,
        param_hash=first.param_hash,
        environment=environment,
        execution_profile=profile,
        decisive_phase=decisive.phase,
        raw_status=decisive.raw_status,
        final_status=final_status,
        observation_outcome=outcome,
        failure_id=failure_id,
        failure_category=failure_category,
        observed_at=decisive.end_time,
        fingerprint_version=fingerprint_version,
    )


def _validate_run_and_manifest(
    requested_run_id: str,
    run_record: RunRecord,
    manifest: dict[str, Any],
) -> None:
    if run_record.run_id != requested_run_id:
        raise FlakyImportError("run_id_mismatch", "run.json run_id differs from request")
    if run_record.status is not RunStatus.FINISHED:
        raise FlakyImportError(
            "run_not_finished",
            f"run status {run_record.status.value!r} is not importable",
        )
    try:
        normalize_flaky_environment(run_record.environment)
    except ValueError as error:
        raise FlakyImportError("environment_unsupported", str(error)) from error

    mismatches = exact_field_mismatches(
        manifest,
        {
            "run_id": requested_run_id,
            "manifest_version": MANIFEST_VERSION,
            "schema_version": SCHEMA_VERSION,
            "status": "complete",
        },
    )
    field = mismatches[0] if mismatches else None
    if field == "run_id":
        raise FlakyImportError("run_id_mismatch", "manifest run_id differs from request")
    if field == "manifest_version":
        raise FlakyImportError(
            "manifest_version_unsupported",
            "manifest version is missing or unsupported",
        )
    if field == "schema_version":
        raise FlakyImportError(
            "p0_schema_unsupported",
            "P0 schema version is missing or unsupported",
        )
    if field == "status":
        raise FlakyImportError(
            "manifest_incomplete",
            "P0 manifest status must be complete",
        )
    _manifest_text(manifest, "merge_version")
    _manifest_text(manifest, "fingerprint_version")
    try:
        integrity = IntegrityStatus(_manifest_text(manifest, "integrity_status"))
    except ValueError as error:
        raise FlakyImportError(
            "integrity_status_invalid",
            "manifest integrity status is unsupported",
        ) from error
    if integrity is IntegrityStatus.FAILED:
        raise FlakyImportError("integrity_failed", "P0 manifest integrity failed")
    if run_record.integrity_status is not integrity:
        raise FlakyImportError(
            "integrity_status_mismatch",
            "run.json and manifest integrity statuses differ",
        )


def _validate_output_hashes(
    manifest: dict[str, Any],
    source_hashes: dict[str, str],
) -> None:
    output_hashes = manifest.get("output_hashes")
    if not isinstance(output_hashes, dict):
        raise FlakyImportError("manifest_hashes_missing", "manifest output_hashes is missing")
    mapping = {
        "case-results": "case_results",
        "failures": "failures",
        "integrity-issues": "integrity_issues",
    }
    for manifest_name, source_name in mapping.items():
        expected = output_hashes.get(manifest_name)
        if not isinstance(expected, str) or not expected.strip():
            raise FlakyImportError(
                "manifest_hashes_missing",
                f"manifest hash is missing for {manifest_name}",
            )
        if expected != source_hashes[source_name]:
            raise FlakyImportError(
                "artifact_hash_mismatch",
                f"P0 artifact hash mismatch for {manifest_name}",
            )


def _validate_integrity_issues(issues: Sequence[IntegrityIssue]) -> None:
    errors = [issue for issue in issues if issue.severity is IssueSeverity.ERROR]
    if errors:
        raise FlakyImportError(
            "blocking_integrity_error",
            f"P0 integrity contains blocking ERROR issue: {errors[0].code}",
        )
    unsafe_warns = [
        issue
        for issue in issues
        if issue.severity is IssueSeverity.WARN
        and not _is_safe_integrity_warning(issue)
    ]
    if unsafe_warns:
        raise FlakyImportError(
            "blocking_integrity_warning",
            f"P0 integrity warning affects Case trust: {unsafe_warns[0].code}",
        )


def _is_safe_integrity_warning(issue: IntegrityIssue) -> bool:
    if issue.code in _SAFE_WARN_CODES:
        return True
    if issue.code in _SHARD_PARSE_WARN_CODES:
        related = (issue.related_id or "").casefold()
        return related.startswith("requests-")
    return False


def _validate_run_integrity_issues(
    run_record: RunRecord,
    merged_issues: Sequence[IntegrityIssue],
) -> None:
    if any(issue.run_id != run_record.run_id for issue in run_record.integrity_issues):
        raise FlakyImportError(
            "integrity_issue_run_mismatch",
            "run.json contains an integrity issue for another run",
        )
    run_values = sorted(
        _canonical_json(issue.model_dump(mode="json"))
        for issue in run_record.integrity_issues
    )
    merged_values = sorted(
        _canonical_json(issue.model_dump(mode="json")) for issue in merged_issues
    )
    if run_values != merged_values:
        raise FlakyImportError(
            "integrity_issue_mismatch",
            "run.json and merged integrity issue records differ",
        )


def _artifact_references(run_record: RunRecord, output_dir: Path) -> tuple[str, str, str]:
    if run_record.job_name and run_record.build_number:
        reference = (
            f"jenkins:{run_record.job_name}#{run_record.build_number}:reports/quality"
        )
        return "jenkins", reference, reference
    actual = f"local:{run_record.run_id}:{output_dir.as_posix()}"
    redacted = f"local:{run_record.run_id}:<local-path>/{output_dir.name}"
    return "local", actual, redacted


def _source_digest(run_id: str, source_hashes: dict[str, str]) -> str:
    payload = {
        "run_id": run_id,
        "run_record_sha256": source_hashes["run_record"],
        "manifest_sha256": source_hashes["manifest"],
        "case_results_sha256": source_hashes["case_results"],
        "failures_sha256": source_hashes["failures"],
        "integrity_issues_sha256": source_hashes["integrity_issues"],
    }
    return _full_hash(payload)


TModel = TypeVar("TModel", bound=BaseModel)


def _read_model(path: Path, model: type[TModel]) -> TModel:
    payload = _read_json_object(path)
    try:
        return model.model_validate(payload)
    except ValidationError as error:
        raise FlakyImportError(
            "artifact_schema_invalid",
            f"{path.name} does not match {model.__name__}: {_validation_summary(error)}",
        ) from error


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        return read_artifact_json_object(path)
    except ArtifactFormatError as error:
        raise FlakyImportError(
            "artifact_schema_invalid",
            f"{path.name} must contain a JSON object",
        ) from error
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise FlakyImportError(
            "artifact_invalid_json",
            f"cannot read {path.name}: {type(error).__name__}",
        ) from error
def _read_jsonl_models(
    path: Path,
    model: type[TModel],
    run_id: str,
) -> tuple[TModel, ...]:
    records: list[TModel] = []
    try:
        for item in read_jsonl_values(path):
            try:
                record = model.model_validate(item.value)
            except ValidationError as error:
                raise FlakyImportError(
                    "artifact_schema_invalid",
                    f"{path.name}:{item.number} is invalid: {type(error).__name__}",
                ) from error
            if getattr(record, "run_id", None) != run_id:
                raise FlakyImportError(
                    "foreign_run_record",
                    f"{path.name}:{item.number} belongs to another run",
                )
            records.append(record)
    except ArtifactJsonLineError as error:
        raise FlakyImportError(
            "artifact_schema_invalid",
            f"{path.name}:{error.line_number} is invalid: {type(error.error).__name__}",
        ) from error
    except OSError as error:
        raise FlakyImportError(
            "artifact_read_failed",
            f"cannot read {path.name}: {type(error).__name__}",
        ) from error
    return tuple(records)


def _manifest_text(manifest: dict[str, Any], name: str) -> str:
    value = manifest.get(name)
    if not isinstance(value, str) or not value.strip():
        raise FlakyImportError(
            "manifest_field_missing",
            f"manifest field {name!r} is missing",
        )
    return value.strip()


def _write_import_report(
    output_dir: str | Path,
    result: FlakyImportResult,
) -> FlakyImportResult:
    try:
        write_json_atomic(Path(output_dir) / "flaky-import.json", result)
        return result
    except Exception as error:
        issue = FlakyImportIssue(
            severity=IssueSeverity.ERROR,
            code="import_report_write_failed",
            summary=_safe_summary(f"{type(error).__name__}: {error}"),
        )
        status = (
            FlakyImportStatus.DEGRADED
            if result.status
            in {
                FlakyImportStatus.IMPORTED,
                FlakyImportStatus.NOOP,
                FlakyImportStatus.DEGRADED,
            }
            else result.status
        )
        return result.model_copy(
            update={"status": status, "issues": (*result.issues, issue)}
        )


def _write_evaluation_report(
    output_dir: str | Path,
    result: FlakyEvaluationResult,
) -> FlakyEvaluationResult:
    try:
        write_json_atomic(Path(output_dir) / "flaky-evaluation.json", result)
        return result
    except Exception as error:
        issue = FlakyImportIssue(
            severity=IssueSeverity.ERROR,
            code="evaluation_report_write_failed",
            summary=_safe_summary(f"{type(error).__name__}: {error}"),
        )
        status = (
            FlakyEvaluationStatus.DEGRADED
            if result.status
            in {
                FlakyEvaluationStatus.EVALUATED,
                FlakyEvaluationStatus.NOOP,
                FlakyEvaluationStatus.DEGRADED,
            }
            else result.status
        )
        return result.model_copy(
            update={"status": status, "issues": (*result.issues, issue)}
        )


def _safe_summary(value: str) -> str:
    redacted = redact_quality_value(str(value), remove_url_query=True)
    text = str(redacted).replace("\r", " ").replace("\n", " ").strip()
    return text[:500] if len(text) > 500 else text


def _safe_audit_text(value: str, limit: int) -> str:
    text = _safe_summary(value)
    if not text:
        raise ValueError("audit text must not be empty")
    return text[:limit]


def _file_sha256(path: Path) -> str:
    return file_sha256(path)


def _full_hash(payload: dict[str, Any]) -> str:
    canonical = _canonical_json(payload)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _canonical_json(payload: Any) -> str:
    return json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _required_text(value: str, name: str) -> str:
    text = str(value).strip()
    if not text:
        raise ValueError(f"{name} must not be empty")
    return text


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    return _required_text(value, "value")


def _phase_order(phase: CasePhase) -> int:
    return {
        CasePhase.COLLECTION: 0,
        CasePhase.SETUP: 1,
        CasePhase.CALL: 2,
        CasePhase.TEARDOWN: 3,
    }[phase]


def _validation_summary(error: ValidationError) -> str:
    details = error.errors()
    if not details:
        return str(error)
    first = details[0]
    location = ".".join(str(item) for item in first.get("loc", ())) or "<root>"
    return f"{location}: {first.get('msg', 'validation failed')}"
