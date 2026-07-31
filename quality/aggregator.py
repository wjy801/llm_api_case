from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable
import hashlib
import json

from pydantic import ValidationError

from quality.classifier import (
    CLASSIFIER_RULE_VERSION,
    FINGERPRINT_VERSION,
    FailureEvidence,
    classify_failure,
    unknown_failure,
)
from quality.junit import JUnitCaseEvidence, parse_junit_file
from quality.models import (
    SCHEMA_VERSION,
    CasePhase,
    CaseResult,
    CaseStatus,
    FailureRecord,
    IntegrityIssue,
    IntegrityStatus,
    IssueSeverity,
    RequestMetric,
)
from quality.storage import ensure_quality_dirs, write_json_atomic, write_jsonl_atomic


MANIFEST_VERSION = "quality.merge.v1"
MERGE_VERSION = "p0-merge.v1"


@dataclass(frozen=True)
class QualityMergeRequest:
    run_id: str
    output_dir: Path
    expected_execution_ids: tuple[str, ...] = ()
    expected_case_count: int | None = None
    junit_files: tuple[Path, ...] = ()
    run_start_time: datetime | None = None


@dataclass(frozen=True)
class QualityMergeResult:
    run_id: str
    output_dir: Path
    manifest_path: Path
    integrity_status: IntegrityStatus
    case_results: int
    request_metrics: int
    failure_occurrences: int
    integrity_issues: tuple[IntegrityIssue, ...]


@dataclass
class _SourceStats:
    path: Path
    kind: str
    sha256: str = ""
    physical_non_empty_lines: int = 0
    current_run_records: int = 0
    foreign_run_records: int = 0
    invalid_json: int = 0
    invalid_schema: int = 0
    exact_duplicates: int = 0
    conflict_duplicates: int = 0

    def as_manifest(self, root: Path) -> dict[str, Any]:
        return {
            "path": _relative_path(self.path, root),
            "type": self.kind,
            "sha256": self.sha256,
            "physical_non_empty_lines": self.physical_non_empty_lines,
            "current_run_records": self.current_run_records,
            "foreign_run_records": self.foreign_run_records,
            "invalid_json": self.invalid_json,
            "invalid_schema": self.invalid_schema,
            "exact_duplicates": self.exact_duplicates,
            "conflict_duplicates": self.conflict_duplicates,
        }


@dataclass
class _MergeState:
    request: QualityMergeRequest
    cases: dict[tuple[str, str], CaseResult] = field(default_factory=dict)
    requests: dict[str, RequestMetric] = field(default_factory=dict)
    integrity: dict[tuple[str, str, str | None, str], IntegrityIssue] = field(default_factory=dict)
    failures: dict[tuple[str, str, str], FailureRecord] = field(default_factory=dict)
    issues: list[IntegrityIssue] = field(default_factory=list)
    source_stats: list[_SourceStats] = field(default_factory=list)
    junit_evidence: dict[str, JUnitCaseEvidence] = field(default_factory=dict)
    junit_files: list[dict[str, Any]] = field(default_factory=list)

    def issue(
        self,
        *,
        severity: IssueSeverity,
        source: str,
        code: str,
        message: str,
        related_id: str | None = None,
    ) -> IntegrityIssue:
        issue = IntegrityIssue(
            run_id=self.request.run_id,
            severity=severity,
            source=source,
            code=code,
            message=_safe_message(message),
            related_id=related_id,
            created_at=datetime.now(UTC),
        )
        self.issues.append(issue)
        return issue


def merge_quality_run(request: QualityMergeRequest) -> QualityMergeResult:
    output_dir = Path(request.output_dir)
    layout = ensure_quality_dirs(output_dir)
    manifest_path = layout.merged / "manifest.json"
    state = _MergeState(request=request)
    _write_manifest(state, manifest_path, status="merging", output_hashes={})

    try:
        _scan_shards(state, layout.shards)
        _read_junit(state)
        _reconcile(state)
        _classify_failures(state)
        all_issues = _sorted_issues((*state.integrity.values(), *state.issues))
        integrity_status = _integrity_status(all_issues)
        output_paths = {
            "case-results": layout.merged / "case-results.jsonl",
            "request-metrics": layout.merged / "request-metrics.jsonl",
            "failures": layout.merged / "failures.jsonl",
            "integrity-issues": layout.merged / "integrity-issues.jsonl",
        }
        write_jsonl_atomic(output_paths["case-results"], _sorted_cases(state.cases.values()))
        write_jsonl_atomic(output_paths["request-metrics"], _sorted_requests(state.requests.values()))
        write_jsonl_atomic(output_paths["failures"], _sorted_failures(state.failures.values()))
        write_jsonl_atomic(output_paths["integrity-issues"], all_issues)
        output_hashes = {
            name: _file_sha256(path)
            for name, path in output_paths.items()
        }
        _write_manifest(state, manifest_path, status="complete", output_hashes=output_hashes)
        return QualityMergeResult(
            run_id=request.run_id,
            output_dir=output_dir,
            manifest_path=manifest_path,
            integrity_status=integrity_status,
            case_results=len(state.cases),
            request_metrics=len(state.requests),
            failure_occurrences=len(state.failures),
            integrity_issues=tuple(all_issues),
        )
    except Exception as error:
        state.issue(
            severity=IssueSeverity.ERROR,
            source="aggregator",
            code="merge_failed",
            message=f"{type(error).__name__}: {error}",
            related_id=request.run_id,
        )
        try:
            _write_manifest(state, manifest_path, status="failed", output_hashes={})
        except Exception:
            pass
        return QualityMergeResult(
            run_id=request.run_id,
            output_dir=output_dir,
            manifest_path=manifest_path,
            integrity_status=IntegrityStatus.FAILED,
            case_results=len(state.cases),
            request_metrics=len(state.requests),
            failure_occurrences=len(state.failures),
            integrity_issues=tuple(_sorted_issues((*state.integrity.values(), *state.issues))),
        )


def _scan_shards(state: _MergeState, shards_dir: Path) -> None:
    if not shards_dir.exists():
        state.issue(
            severity=IssueSeverity.ERROR,
            source="aggregator",
            code="shards_dir_missing",
            message=f"quality shards directory does not exist: {shards_dir}",
        )
        return

    shard_specs = (
        ("cases", "cases-*.jsonl", CaseResult),
        ("requests", "requests-*.jsonl", RequestMetric),
        ("integrity", "integrity-*.jsonl", IntegrityIssue),
    )
    for kind, pattern, model in shard_specs:
        for path in sorted(shards_dir.glob(pattern), key=lambda item: item.as_posix()):
            stats = _scan_shard(state, path, kind, model)
            state.source_stats.append(stats)

    for execution_id in state.request.expected_execution_ids:
        if not any(
            stats.kind == "cases" and f"cases-{execution_id}-" in stats.path.name
            for stats in state.source_stats
        ):
            state.issue(
                severity=IssueSeverity.ERROR,
                source="aggregator",
                code="missing_case_shard",
                message=f"no case shard found for expected execution {execution_id}",
                related_id=execution_id,
            )


def _scan_shard(
    state: _MergeState,
    path: Path,
    kind: str,
    model: type[CaseResult] | type[RequestMetric] | type[IntegrityIssue],
) -> _SourceStats:
    stats = _SourceStats(path=path, kind=kind, sha256=_file_sha256(path))
    with path.open("rb") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line:
                continue
            stats.physical_non_empty_lines += 1
            try:
                payload = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                stats.invalid_json += 1
                state.issue(
                    severity=IssueSeverity.WARN,
                    source="aggregator",
                    code="invalid_jsonl_line",
                    message=f"{path.name}:{line_number}: {type(error).__name__}: {error}",
                    related_id=path.name,
                )
                continue
            if not isinstance(payload, dict):
                stats.invalid_schema += 1
                state.issue(
                    severity=IssueSeverity.WARN,
                    source="aggregator",
                    code="invalid_jsonl_schema",
                    message=f"{path.name}:{line_number}: record is not an object",
                    related_id=path.name,
                )
                continue
            if payload.get("run_id") != state.request.run_id:
                stats.foreign_run_records += 1
                continue
            try:
                record = model.model_validate(payload)
            except ValidationError as error:
                stats.invalid_schema += 1
                state.issue(
                    severity=IssueSeverity.WARN,
                    source="aggregator",
                    code="invalid_quality_schema",
                    message=f"{path.name}:{line_number}: {_validation_summary(error)}",
                    related_id=path.name,
                )
                continue
            stats.current_run_records += 1
            _add_record(state, stats, record)
    return stats


def _add_record(
    state: _MergeState,
    stats: _SourceStats,
    record: CaseResult | RequestMetric | IntegrityIssue,
) -> None:
    if isinstance(record, CaseResult):
        key = (record.invocation_id, record.phase.value)
        target = state.cases
        conflict_code = "case_result_conflict"
    elif isinstance(record, RequestMetric):
        key = record.request_event_id
        target = state.requests
        conflict_code = "request_metric_conflict"
    else:
        key = (record.source, record.code, record.related_id, record.message)
        target = state.integrity
        conflict_code = "integrity_issue_conflict"

    existing = target.get(key)  # type: ignore[arg-type]
    if existing is None:
        target[key] = record  # type: ignore[index]
        return
    if _canonical_record(existing) == _canonical_record(record):
        stats.exact_duplicates += 1
        return
    stats.conflict_duplicates += 1
    state.issue(
        severity=IssueSeverity.ERROR,
        source="aggregator",
        code=conflict_code,
        message=f"conflicting duplicate record for key {key!r}",
        related_id=str(key),
    )


def _read_junit(state: _MergeState) -> None:
    for raw_path in state.request.junit_files:
        path = Path(raw_path)
        file_info: dict[str, Any] = {
            "path": _relative_path(path, state.request.output_dir),
            "exists": path.exists(),
            "cases": 0,
        }
        if not path.exists():
            state.issue(
                severity=IssueSeverity.WARN,
                source="junit",
                code="junit_file_missing",
                message=f"JUnit file does not exist: {path}",
                related_id=path.name,
            )
            state.junit_files.append(file_info)
            continue
        if state.request.run_start_time is not None:
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
            if mtime < state.request.run_start_time:
                state.issue(
                    severity=IssueSeverity.WARN,
                    source="junit",
                    code="junit_file_stale",
                    message=f"JUnit file is older than current run: {path}",
                    related_id=path.name,
                )
                state.junit_files.append(file_info)
                continue
        try:
            cases = parse_junit_file(path)
        except Exception as error:
            state.issue(
                severity=IssueSeverity.WARN,
                source="junit",
                code="junit_parse_failed",
                message=f"{type(error).__name__}: {error}",
                related_id=path.name,
            )
            state.junit_files.append(file_info)
            continue
        file_info["cases"] = len(cases)
        state.junit_files.append(file_info)
        for evidence in cases:
            if not evidence.invocation_id or not evidence.case_id:
                state.issue(
                    severity=IssueSeverity.WARN,
                    source="junit",
                    code="junit_missing_quality_identity",
                    message="JUnit testcase is missing quality identity properties",
                    related_id=f"{evidence.classname}.{evidence.name}",
                )
                continue
            existing = state.junit_evidence.get(evidence.invocation_id)
            if existing is not None and existing != evidence:
                state.issue(
                    severity=IssueSeverity.ERROR,
                    source="junit",
                    code="junit_identity_conflict",
                    message=f"multiple JUnit testcases use invocation_id {evidence.invocation_id}",
                    related_id=evidence.invocation_id,
                )
                continue
            state.junit_evidence[evidence.invocation_id] = evidence


def _reconcile(state: _MergeState) -> None:
    invocation_ids = {case.invocation_id for case in state.cases.values()}
    if not state.cases:
        state.issue(
            severity=IssueSeverity.ERROR,
            source="aggregator",
            code="no_case_results",
            message="no CaseResult records found for current run",
            related_id=state.request.run_id,
        )
    if state.request.expected_case_count is not None and len(invocation_ids) != state.request.expected_case_count:
        state.issue(
            severity=IssueSeverity.ERROR,
            source="aggregator",
            code="expected_case_count_mismatch",
            message=(
                f"expected {state.request.expected_case_count} invocations, "
                f"merged {len(invocation_ids)}"
            ),
            related_id=state.request.run_id,
        )
    if state.request.junit_files:
        if len(state.junit_evidence) != len(invocation_ids):
            state.issue(
                severity=IssueSeverity.WARN,
                source="junit",
                code="junit_case_count_mismatch",
                message=f"JUnit identities={len(state.junit_evidence)}, merged invocations={len(invocation_ids)}",
                related_id=state.request.run_id,
            )
        for invocation_id, evidence in sorted(state.junit_evidence.items()):
            cases = [case for case in state.cases.values() if case.invocation_id == invocation_id]
            if not cases:
                state.issue(
                    severity=IssueSeverity.WARN,
                    source="junit",
                    code="junit_invocation_missing_case_result",
                    message=f"JUnit invocation has no CaseResult: {invocation_id}",
                    related_id=invocation_id,
                )
                continue
            expected_status = _fold_case_status(cases)
            if not _compatible_status(expected_status, evidence.status):
                state.issue(
                    severity=IssueSeverity.WARN,
                    source="junit",
                    code="junit_status_mismatch",
                    message=f"CaseResult status {expected_status.value} differs from JUnit {evidence.status.value}",
                    related_id=invocation_id,
                )


def _classify_failures(state: _MergeState) -> None:
    requests_by_invocation: dict[str, list[RequestMetric]] = {}
    for metric in state.requests.values():
        requests_by_invocation.setdefault(metric.invocation_id, []).append(metric)
    integrity_codes_by_related: dict[str, list[str]] = {}
    for issue in (*state.integrity.values(), *state.issues):
        if issue.related_id:
            integrity_codes_by_related.setdefault(issue.related_id, []).append(issue.code)

    updated_cases: dict[tuple[str, str], CaseResult] = {}
    for key, case in state.cases.items():
        if case.raw_status not in {CaseStatus.FAILED, CaseStatus.ERROR} and case.final_status not in {
            CaseStatus.FAILED,
            CaseStatus.ERROR,
        }:
            updated_cases[key] = case
            continue
        evidence = state.junit_evidence.get(case.invocation_id)
        failure_evidence = FailureEvidence(
            run_id=case.run_id,
            case_id=case.case_id,
            invocation_id=case.invocation_id,
            phase=case.phase,
            error_type=evidence.error_type if evidence is not None else None,
            message=evidence.message if evidence is not None else case.raw_status.value,
            assert_location=evidence.assert_location if evidence is not None else None,
            junit_status=evidence.status.value if evidence is not None else None,
            request_metrics=tuple(sorted(
                requests_by_invocation.get(case.invocation_id, []),
                key=lambda metric: (metric.attempt_index, metric.request_event_id),
            )),
            related_integrity_codes=tuple(integrity_codes_by_related.get(case.invocation_id, [])),
        )
        try:
            failure = classify_failure(failure_evidence)
        except Exception as error:
            state.issue(
                severity=IssueSeverity.WARN,
                source="classifier",
                code="classification_failed",
                message=f"{type(error).__name__}: {error}",
                related_id=case.invocation_id,
            )
            failure = unknown_failure(failure_evidence, "classification failed")
        failure_key = (failure.failure_id, failure.invocation_id, failure.phase.value)
        existing = state.failures.get(failure_key)
        if existing is None:
            state.failures[failure_key] = failure
        elif _canonical_record(existing) != _canonical_record(failure):
            state.issue(
                severity=IssueSeverity.ERROR,
                source="classifier",
                code="failure_record_conflict",
                message=f"conflicting FailureRecord for key {failure_key!r}",
                related_id=case.invocation_id,
            )
        updated_cases[key] = case.model_copy(update={"failure_id": failure.failure_id})
    state.cases = updated_cases


def _write_manifest(
    state: _MergeState,
    manifest_path: Path,
    *,
    status: str,
    output_hashes: dict[str, str],
) -> None:
    cases = state.cases.values()
    failures = state.failures.values()
    issues = _sorted_issues((*state.integrity.values(), *state.issues))
    manifest = {
        "manifest_version": MANIFEST_VERSION,
        "schema_version": SCHEMA_VERSION,
        "run_id": state.request.run_id,
        "status": status,
        "merge_version": MERGE_VERSION,
        "classifier_rule_version": CLASSIFIER_RULE_VERSION,
        "fingerprint_version": FINGERPRINT_VERSION,
        "created_at": datetime.now(UTC).isoformat(),
        "expected_execution_ids": list(state.request.expected_execution_ids),
        "expected_case_count": state.request.expected_case_count,
        "source_shards": [
            stats.as_manifest(state.request.output_dir)
            for stats in sorted(state.source_stats, key=lambda item: item.path.as_posix())
        ],
        "junit_files": state.junit_files,
        "output_counts": {
            "case_results": len(state.cases),
            "invocations": len({case.invocation_id for case in cases}),
            "request_metrics": len(state.requests),
            "failure_occurrences": len(state.failures),
            "failure_fingerprints": len({failure.failure_id for failure in failures}),
            "integrity_issues": len(issues),
        },
        "output_hashes": output_hashes,
        "integrity_status": _integrity_status(issues).value,
    }
    write_json_atomic(manifest_path, manifest)


def _fold_case_status(cases: Iterable[CaseResult]) -> CaseStatus:
    statuses = {case.final_status for case in cases}
    if CaseStatus.ERROR in statuses:
        return CaseStatus.ERROR
    if CaseStatus.FAILED in statuses:
        return CaseStatus.FAILED
    if statuses and statuses <= {CaseStatus.SKIPPED, CaseStatus.XFAILED}:
        return CaseStatus.SKIPPED
    return CaseStatus.PASSED


def _compatible_status(case_status: CaseStatus, junit_status: CaseStatus) -> bool:
    if case_status in {CaseStatus.FAILED, CaseStatus.ERROR}:
        return junit_status in {CaseStatus.FAILED, CaseStatus.ERROR}
    if case_status in {CaseStatus.SKIPPED, CaseStatus.XFAILED}:
        return junit_status is CaseStatus.SKIPPED
    return junit_status is CaseStatus.PASSED


def _integrity_status(issues: Iterable[IntegrityIssue]) -> IntegrityStatus:
    severities = {issue.severity for issue in issues}
    if IssueSeverity.ERROR in severities:
        return IntegrityStatus.FAILED
    if IssueSeverity.WARN in severities:
        return IntegrityStatus.DEGRADED
    return IntegrityStatus.COMPLETE


def _sorted_cases(values: Iterable[CaseResult]) -> list[CaseResult]:
    return sorted(values, key=lambda item: (item.invocation_id, _phase_order(item.phase), item.nodeid))


def _sorted_requests(values: Iterable[RequestMetric]) -> list[RequestMetric]:
    return sorted(values, key=lambda item: (item.invocation_id, item.attempt_index, item.request_event_id))


def _sorted_failures(values: Iterable[FailureRecord]) -> list[FailureRecord]:
    return sorted(values, key=lambda item: (item.failure_id, item.invocation_id, item.phase.value))


def _sorted_issues(values: Iterable[IntegrityIssue]) -> list[IntegrityIssue]:
    return sorted(values, key=lambda item: (item.severity.value, item.source, item.code, item.related_id or "", item.message))


def _phase_order(phase: CasePhase) -> int:
    return {
        CasePhase.COLLECTION: 0,
        CasePhase.SETUP: 1,
        CasePhase.CALL: 2,
        CasePhase.TEARDOWN: 3,
    }[phase]


def _canonical_record(record: Any) -> str:
    if hasattr(record, "model_dump"):
        payload = record.model_dump(mode="json")
    else:
        payload = record
    return json.dumps(payload, allow_nan=False, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _safe_message(message: str) -> str:
    text = str(message).replace("\r", " ").replace("\n", " ").strip()
    return text[:500] if len(text) > 500 else text


def _validation_summary(error: ValidationError) -> str:
    details = error.errors()
    if not details:
        return str(error)
    first = details[0]
    location = ".".join(str(item) for item in first.get("loc", ())) or "<root>"
    return f"{location}: {first.get('msg', 'validation failed')}"
