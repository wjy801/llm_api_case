from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

from pydantic import ValidationError

from quality.models import IntegrityStatus, IssueSeverity, RequestMetric
from quality.redaction import redact_quality_value
from quality.semantic_models import (
    SEMANTIC_MANIFEST_VERSION,
    SEMANTIC_MERGE_VERSION,
    SEMANTIC_SCHEMA_VERSION,
    OperationKind,
    OperationRecord,
    PollingSessionRecord,
    RecordCompleteness,
    RequestGroupRecord,
    SemanticIntegrityIssue,
    StreamOutcome,
)
from quality.storage import write_json_atomic, write_jsonl_atomic


@dataclass(frozen=True)
class SemanticMergeRequest:
    run_id: str
    output_dir: Path


@dataclass(frozen=True)
class SemanticMergeResult:
    run_id: str
    output_dir: Path
    manifest_path: Path
    integrity_status: IntegrityStatus
    request_groups: int
    polling_sessions: int
    operations: int
    integrity_issues: tuple[SemanticIntegrityIssue, ...]


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
    request: SemanticMergeRequest
    request_groups: dict[str, RequestGroupRecord] = field(default_factory=dict)
    polling_sessions: dict[str, PollingSessionRecord] = field(default_factory=dict)
    operations: dict[str, OperationRecord] = field(default_factory=dict)
    collected_issues: dict[tuple[str, str, str | None, str], SemanticIntegrityIssue] = field(
        default_factory=dict
    )
    generated_issues: list[SemanticIntegrityIssue] = field(default_factory=list)
    source_stats: list[_SourceStats] = field(default_factory=list)
    p0_requests: dict[str, RequestMetric] = field(default_factory=dict)
    p0_manifest_path: Path | None = None
    p0_manifest_sha256: str | None = None
    p0_request_metrics_sha256: str | None = None

    def issue(
        self,
        *,
        severity: IssueSeverity,
        source: str,
        code: str,
        message: str,
        related_id: str | None = None,
    ) -> None:
        self.generated_issues.append(
            SemanticIntegrityIssue(
                run_id=self.request.run_id,
                severity=severity,
                source=source,
                code=code,
                message=_safe_message(message),
                related_id=related_id,
                created_at=datetime.now(UTC),
            )
        )


def merge_semantic_run(request: SemanticMergeRequest) -> SemanticMergeResult:
    output_dir = Path(request.output_dir)
    semantic_root = output_dir / "semantic"
    shards_dir = semantic_root / "shards"
    merged_dir = semantic_root / "merged"
    merged_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = merged_dir / "manifest.json"
    state = _MergeState(request=request)
    _write_manifest(state, manifest_path, status="merging", output_hashes={})
    try:
        _scan_semantic_shards(state, shards_dir)
        _load_p0_evidence(state, output_dir)
        _validate_relationships(state)
        all_issues = _sorted_issues((*state.collected_issues.values(), *state.generated_issues))
        output_paths = {
            "request-groups": merged_dir / "request-groups.jsonl",
            "polling-sessions": merged_dir / "polling-sessions.jsonl",
            "operations": merged_dir / "operations.jsonl",
            "integrity-issues": merged_dir / "integrity-issues.jsonl",
        }
        write_jsonl_atomic(
            output_paths["request-groups"],
            sorted(state.request_groups.values(), key=lambda item: item.request_group_id),
        )
        write_jsonl_atomic(
            output_paths["polling-sessions"],
            sorted(state.polling_sessions.values(), key=lambda item: item.polling_session_id),
        )
        write_jsonl_atomic(
            output_paths["operations"],
            sorted(state.operations.values(), key=lambda item: item.operation_id),
        )
        write_jsonl_atomic(output_paths["integrity-issues"], all_issues)
        output_hashes = {name: _file_sha256(path) for name, path in output_paths.items()}
        _write_manifest(state, manifest_path, status="complete", output_hashes=output_hashes)
        return _result(state, manifest_path)
    except Exception as error:
        state.issue(
            severity=IssueSeverity.ERROR,
            source="semantic_aggregator",
            code="semantic_merge_failed",
            message=f"{type(error).__name__}: {error}",
            related_id=request.run_id,
        )
        try:
            _write_manifest(state, manifest_path, status="failed", output_hashes={})
        except Exception:
            pass
        return _result(state, manifest_path, forced=IntegrityStatus.FAILED)


def _scan_semantic_shards(state: _MergeState, shards_dir: Path) -> None:
    if not shards_dir.exists():
        state.issue(
            severity=IssueSeverity.ERROR,
            source="semantic_aggregator",
            code="semantic_shards_missing",
            message=f"semantic shards directory does not exist: {shards_dir}",
        )
        return
    specs = (
        ("request_groups", "request-groups-*.jsonl", RequestGroupRecord),
        ("polling_sessions", "polling-sessions-*.jsonl", PollingSessionRecord),
        ("operations", "operations-*.jsonl", OperationRecord),
        ("integrity", "integrity-*.jsonl", SemanticIntegrityIssue),
    )
    found = False
    for kind, pattern, model in specs:
        for path in sorted(shards_dir.glob(pattern), key=lambda item: item.as_posix()):
            found = True
            state.source_stats.append(_scan_shard(state, path, kind, model))
    if not found:
        state.issue(
            severity=IssueSeverity.WARN,
            source="semantic_aggregator",
            code="semantic_shards_empty",
            message="no semantic shard files found",
            related_id=state.request.run_id,
        )


def _scan_shard(
    state: _MergeState,
    path: Path,
    kind: str,
    model: type[RequestGroupRecord]
    | type[PollingSessionRecord]
    | type[OperationRecord]
    | type[SemanticIntegrityIssue],
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
                    source="semantic_aggregator",
                    code="invalid_semantic_jsonl",
                    message=f"{path.name}:{line_number}: {type(error).__name__}: {error}",
                    related_id=path.name,
                )
                continue
            if not isinstance(payload, dict):
                stats.invalid_schema += 1
                state.issue(
                    severity=IssueSeverity.WARN,
                    source="semantic_aggregator",
                    code="invalid_semantic_schema",
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
                    source="semantic_aggregator",
                    code="invalid_semantic_schema",
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
    record: RequestGroupRecord | PollingSessionRecord | OperationRecord | SemanticIntegrityIssue,
) -> None:
    if isinstance(record, RequestGroupRecord):
        key: Any = record.request_group_id
        target: dict[Any, Any] = state.request_groups
        conflict_code = "request_group_conflict"
    elif isinstance(record, PollingSessionRecord):
        key = record.polling_session_id
        target = state.polling_sessions
        conflict_code = "polling_session_conflict"
    elif isinstance(record, OperationRecord):
        key = record.operation_id
        target = state.operations
        conflict_code = "operation_conflict"
    else:
        key = (record.source, record.code, record.related_id, record.message)
        target = state.collected_issues
        conflict_code = "semantic_integrity_conflict"
    existing = target.get(key)
    if existing is None:
        target[key] = record
        return
    if _canonical(existing) == _canonical(record):
        stats.exact_duplicates += 1
        return
    stats.conflict_duplicates += 1
    state.issue(
        severity=IssueSeverity.ERROR,
        source="semantic_aggregator",
        code=conflict_code,
        message=f"conflicting duplicate semantic record for key {key!r}",
        related_id=str(key),
    )


def _load_p0_evidence(state: _MergeState, output_dir: Path) -> None:
    manifest_path = output_dir / "merged" / "manifest.json"
    requests_path = output_dir / "merged" / "request-metrics.jsonl"
    state.p0_manifest_path = manifest_path
    if not manifest_path.exists():
        state.issue(
            severity=IssueSeverity.ERROR,
            source="semantic_aggregator",
            code="p0_manifest_missing",
            message=f"P0 manifest does not exist: {manifest_path}",
        )
        return
    state.p0_manifest_sha256 = _file_sha256(manifest_path)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        state.issue(
            severity=IssueSeverity.ERROR,
            source="semantic_aggregator",
            code="p0_manifest_invalid",
            message=f"{type(error).__name__}: {error}",
        )
        return
    if manifest.get("run_id") != state.request.run_id or manifest.get("status") != "complete":
        state.issue(
            severity=IssueSeverity.ERROR,
            source="semantic_aggregator",
            code="p0_manifest_untrusted",
            message="P0 manifest run_id or commit status is not trustworthy",
            related_id=state.request.run_id,
        )
        return
    if not requests_path.exists():
        state.issue(
            severity=IssueSeverity.ERROR,
            source="semantic_aggregator",
            code="p0_request_metrics_missing",
            message=f"P0 request metrics do not exist: {requests_path}",
        )
        return
    actual_hash = _file_sha256(requests_path)
    state.p0_request_metrics_sha256 = actual_hash
    expected_hash = (manifest.get("output_hashes") or {}).get("request-metrics")
    if expected_hash != actual_hash:
        state.issue(
            severity=IssueSeverity.ERROR,
            source="semantic_aggregator",
            code="p0_request_metrics_hash_mismatch",
            message="P0 request metrics hash differs from manifest",
            related_id=requests_path.name,
        )
        return
    with requests_path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                metric = RequestMetric.model_validate_json(line)
            except (ValueError, ValidationError) as error:
                state.issue(
                    severity=IssueSeverity.ERROR,
                    source="semantic_aggregator",
                    code="p0_request_metric_invalid",
                    message=f"request-metrics.jsonl:{line_number}: {error}",
                )
                continue
            if metric.run_id != state.request.run_id:
                continue
            existing = state.p0_requests.get(metric.request_event_id)
            if existing is not None and _canonical(existing) != _canonical(metric):
                state.issue(
                    severity=IssueSeverity.ERROR,
                    source="semantic_aggregator",
                    code="p0_request_metric_conflict",
                    message="conflicting P0 request metric id",
                    related_id=metric.request_event_id,
                )
                continue
            state.p0_requests[metric.request_event_id] = metric


def _validate_relationships(state: _MergeState) -> None:
    event_owner: dict[str, str] = {}
    for group in state.request_groups.values():
        referenced_operation = state.operations.get(group.operation_id)
        if referenced_operation is None:
            _missing_reference(state, "group_operation_missing", group.operation_id)
        else:
            if group.request_group_id not in referenced_operation.request_group_ids:
                _identity_issue(
                    state,
                    "group_missing_from_operation_references",
                    group.request_group_id,
                )
            if group.invocation_id != referenced_operation.invocation_id:
                _identity_issue(state, "group_operation_invocation_mismatch", group.request_group_id)
        metrics: list[RequestMetric] = []
        for event_id in group.attempt_event_ids:
            owner = event_owner.setdefault(event_id, group.request_group_id)
            if owner != group.request_group_id:
                state.issue(
                    severity=IssueSeverity.ERROR,
                    source="semantic_integrity",
                    code="request_event_multiple_groups",
                    message="one P0 request event belongs to multiple request groups",
                    related_id=event_id,
                )
            metric = state.p0_requests.get(event_id)
            if metric is None:
                state.issue(
                    severity=IssueSeverity.ERROR,
                    source="semantic_integrity",
                    code="request_event_missing",
                    message="request group references missing P0 request event",
                    related_id=event_id,
                )
                continue
            metrics.append(metric)
            if metric.invocation_id != group.invocation_id:
                _identity_issue(state, "group_event_invocation_mismatch", group.request_group_id)
        indexes = [metric.attempt_index for metric in metrics]
        if indexes and indexes != list(range(1, len(metrics) + 1)):
            state.issue(
                severity=IssueSeverity.ERROR,
                source="semantic_integrity",
                code="attempt_index_sequence_invalid",
                message=f"request group attempt indexes are not continuous: {indexes}",
                related_id=group.request_group_id,
            )
        if group.final_request_event_id not in group.attempt_event_ids:
            state.issue(
                severity=IssueSeverity.ERROR,
                source="semantic_integrity",
                code="final_request_event_invalid",
                message="final request event does not belong to request group",
                related_id=group.request_group_id,
            )

    for session in state.polling_sessions.values():
        referenced_operation = state.operations.get(session.operation_id)
        if referenced_operation is None:
            _missing_reference(state, "polling_operation_missing", session.operation_id)
        else:
            if session.polling_session_id not in referenced_operation.polling_session_ids:
                _identity_issue(
                    state,
                    "polling_missing_from_operation_references",
                    session.polling_session_id,
                )
            if session.invocation_id != referenced_operation.invocation_id:
                _identity_issue(
                    state,
                    "polling_operation_invocation_mismatch",
                    session.polling_session_id,
                )
        for group_id in session.request_group_ids:
            group = state.request_groups.get(group_id)
            if group is None:
                _missing_reference(state, "polling_group_missing", group_id)
                continue
            if group.polling_session_id != session.polling_session_id:
                _identity_issue(state, "polling_session_group_mismatch", group_id)
            if group.operation_id != session.operation_id or group.invocation_id != session.invocation_id:
                _identity_issue(state, "polling_group_operation_mismatch", group_id)

    group_operation_owner: dict[str, str] = {}
    usage_owner: dict[str, str] = {}
    for operation in state.operations.values():
        operation_event_ids: set[str] = set()
        for group_id in operation.request_group_ids:
            owner = group_operation_owner.setdefault(group_id, operation.operation_id)
            if owner != operation.operation_id:
                _identity_issue(state, "request_group_multiple_operations", group_id)
            group = state.request_groups.get(group_id)
            if group is None:
                _missing_reference(state, "operation_group_missing", group_id)
                continue
            operation_event_ids.update(group.attempt_event_ids)
            if group.operation_id != operation.operation_id or group.invocation_id != operation.invocation_id:
                _identity_issue(state, "operation_group_identity_mismatch", group_id)
        for session_id in operation.polling_session_ids:
            session = state.polling_sessions.get(session_id)
            if session is None:
                _missing_reference(state, "operation_polling_session_missing", session_id)
                continue
            if session.operation_id != operation.operation_id or session.invocation_id != operation.invocation_id:
                _identity_issue(state, "operation_polling_identity_mismatch", session_id)
        if operation.operation_kind is OperationKind.ASYNC_TASK:
            if not operation.request_group_ids or not operation.polling_session_ids:
                state.issue(
                    severity=IssueSeverity.ERROR,
                    source="semantic_integrity",
                    code="async_operation_incomplete",
                    message="async operation requires a create group and polling session",
                    related_id=operation.operation_id,
                )
        if operation.operation_kind is OperationKind.SSE:
            if operation.stream_outcome is StreamOutcome.COMPLETE and (
                "stream_outcome:complete" not in operation.evidence_refs
            ):
                state.issue(
                    severity=IssueSeverity.ERROR,
                    source="semantic_integrity",
                    code="sse_completion_evidence_missing",
                    message="complete SSE operation has no completion evidence",
                    related_id=operation.operation_id,
                )
        if operation.completeness is RecordCompleteness.INCOMPLETE:
            state.issue(
                severity=IssueSeverity.WARN,
                source="semantic_integrity",
                code="operation_incomplete",
                message="operation is explicitly incomplete",
                related_id=operation.operation_id,
            )
        for event_id in operation.usage.source_request_event_ids:
            if event_id not in operation_event_ids:
                _identity_issue(state, "usage_event_outside_operation", event_id)
            owner = usage_owner.setdefault(event_id, operation.operation_id)
            if owner != operation.operation_id:
                _identity_issue(state, "usage_event_multiple_operations", event_id)


def _write_manifest(
    state: _MergeState,
    manifest_path: Path,
    *,
    status: str,
    output_hashes: dict[str, str],
) -> None:
    issues = _sorted_issues((*state.collected_issues.values(), *state.generated_issues))
    p0_manifest = state.p0_manifest_path
    payload = {
        "manifest_version": SEMANTIC_MANIFEST_VERSION,
        "schema_version": SEMANTIC_SCHEMA_VERSION,
        "merge_version": SEMANTIC_MERGE_VERSION,
        "run_id": state.request.run_id,
        "status": status,
        "integrity_status": _integrity_status(issues).value,
        "created_at": datetime.now(UTC).isoformat(),
        "p0_evidence": {
            "manifest_path": (
                _relative_path(p0_manifest, state.request.output_dir)
                if p0_manifest is not None
                else None
            ),
            "manifest_sha256": state.p0_manifest_sha256,
            "request_metrics_sha256": state.p0_request_metrics_sha256,
        },
        "source_shards": [
            item.as_manifest(state.request.output_dir)
            for item in sorted(state.source_stats, key=lambda value: value.path.as_posix())
        ],
        "output_counts": {
            "request_groups": len(state.request_groups),
            "polling_sessions": len(state.polling_sessions),
            "operations": len(state.operations),
            "integrity_issues": len(issues),
            "foreign_run_records": sum(item.foreign_run_records for item in state.source_stats),
            "conflict_duplicates": sum(item.conflict_duplicates for item in state.source_stats),
            "incomplete_operations": sum(
                item.completeness is RecordCompleteness.INCOMPLETE
                for item in state.operations.values()
            ),
        },
        "output_hashes": output_hashes,
    }
    write_json_atomic(manifest_path, payload)


def _result(
    state: _MergeState,
    manifest_path: Path,
    forced: IntegrityStatus | None = None,
) -> SemanticMergeResult:
    issues = _sorted_issues((*state.collected_issues.values(), *state.generated_issues))
    return SemanticMergeResult(
        run_id=state.request.run_id,
        output_dir=state.request.output_dir,
        manifest_path=manifest_path,
        integrity_status=forced or _integrity_status(issues),
        request_groups=len(state.request_groups),
        polling_sessions=len(state.polling_sessions),
        operations=len(state.operations),
        integrity_issues=tuple(issues),
    )


def _missing_reference(state: _MergeState, code: str, related_id: str) -> None:
    state.issue(
        severity=IssueSeverity.ERROR,
        source="semantic_integrity",
        code=code,
        message="semantic record references a missing record",
        related_id=related_id,
    )


def _identity_issue(state: _MergeState, code: str, related_id: str) -> None:
    state.issue(
        severity=IssueSeverity.ERROR,
        source="semantic_integrity",
        code=code,
        message="semantic relationship identity is inconsistent",
        related_id=related_id,
    )


def _integrity_status(issues: Iterable[SemanticIntegrityIssue]) -> IntegrityStatus:
    severities = {issue.severity for issue in issues}
    if IssueSeverity.ERROR in severities:
        return IntegrityStatus.FAILED
    if IssueSeverity.WARN in severities:
        return IntegrityStatus.DEGRADED
    return IntegrityStatus.COMPLETE


def _sorted_issues(values: Iterable[SemanticIntegrityIssue]) -> list[SemanticIntegrityIssue]:
    return sorted(
        values,
        key=lambda item: (
            item.severity.value,
            item.source,
            item.code,
            item.related_id or "",
            item.message,
        ),
    )


def _canonical(record: Any) -> str:
    payload = record.model_dump(mode="json") if hasattr(record, "model_dump") else record
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


def _safe_message(value: object) -> str:
    redacted = redact_quality_value(str(value), remove_url_query=True)
    text = str(redacted).replace("\r", " ").replace("\n", " ").strip()
    return (text or type(value).__name__)[:500]


def _validation_summary(error: ValidationError) -> str:
    details = error.errors()
    if not details:
        return str(error)
    first = details[0]
    location = ".".join(str(item) for item in first.get("loc", ())) or "<root>"
    return f"{location}: {first.get('msg', 'validation failed')}"
