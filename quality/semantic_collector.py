from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import sys
from threading import RLock
import time
from typing import Any

from quality.identifiers import (
    build_interface_id,
    build_url_template,
    new_operation_id,
    new_polling_session_id,
    new_request_group_id,
)
from quality.models import BusinessStatus, IssueSeverity, Protocol, RequestMetric
from quality.runtime_context import QualityRunContext
from quality.redaction import redact_quality_value
from quality.semantic_models import (
    AttemptTransportOutcome,
    OperationKind,
    OperationOutcome,
    OperationRecord,
    OperationTiming,
    OperationUsage,
    PollingOutcome,
    PollingSessionRecord,
    RecordCompleteness,
    RequestGroupRecord,
    SemanticIntegrityIssue,
    StreamOutcome,
    TimingCompleteness,
    TrafficRole,
    UsageCompleteness,
)
from quality.storage import append_jsonl


WarningSink = Callable[[str], None]
MAX_OBSERVED_STATES = 64
_SEMANTIC_ROOT = "semantic"


@dataclass(frozen=True)
class SemanticShardPaths:
    request_groups: Path
    polling_sessions: Path
    operations: Path
    integrity: Path


@dataclass
class _PendingOperation:
    operation_id: str
    case_id: str
    invocation_id: str
    kind: OperationKind
    name: str
    role: TrafficRole
    model_id: str | None
    started_at: datetime
    started_perf: float
    request_group_ids: list[str] = field(default_factory=list)
    polling_session_ids: list[str] = field(default_factory=list)
    metrics: list[RequestMetric] = field(default_factory=list)
    response_headers_ms: float | None = None
    first_data_ms: float | None = None
    first_content_ms: float | None = None
    stream_usage: dict[str, int] = field(default_factory=dict)
    stream_source_event_id: str | None = None
    stream_outcome: StreamOutcome | None = None
    create_request_ms: float | None = None
    polling_total_ms: float | None = None
    polling_sleep_ms: float | None = None
    terminal_perf: float | None = None
    incomplete: bool = False


@dataclass
class _PendingGroup:
    request_group_id: str
    operation_id: str
    polling_session_id: str | None
    method: str
    path: str
    protocol: Protocol
    role: TrafficRole
    configured_max_attempts: int
    started_at: datetime
    started_perf: float
    metrics: list[RequestMetric] = field(default_factory=list)


@dataclass
class _PendingPollingSession:
    polling_session_id: str
    operation_id: str
    case_id: str
    invocation_id: str
    started_at: datetime
    started_perf: float
    request_group_ids: list[str] = field(default_factory=list)
    sleep_duration_ms: float = 0.0
    observed_states: list[str] = field(default_factory=list)
    first_observed_offsets_ms: dict[str, float] = field(default_factory=dict)


class SemanticCollector:
    def __init__(
        self,
        run_context: QualityRunContext,
        *,
        warning_sink: WarningSink | None = None,
        monotonic: Callable[[], float] = time.perf_counter,
    ) -> None:
        self.run_context = run_context
        self._warning_sink = warning_sink or _default_warning_sink
        self._monotonic = monotonic
        self._lock = RLock()
        self._operations: dict[str, _PendingOperation] = {}
        self._groups: dict[str, _PendingGroup] = {}
        self._polling_sessions: dict[str, _PendingPollingSession] = {}

        shards = run_context.output_dir / _SEMANTIC_ROOT / "shards"
        shards.mkdir(parents=True, exist_ok=True)
        suffix = f"{run_context.execution_id}-{run_context.worker_id}.jsonl"
        self.paths = SemanticShardPaths(
            request_groups=shards / f"request-groups-{suffix}",
            polling_sessions=shards / f"polling-sessions-{suffix}",
            operations=shards / f"operations-{suffix}",
            integrity=shards / f"integrity-{suffix}",
        )
        for path in (
            self.paths.request_groups,
            self.paths.polling_sessions,
            self.paths.operations,
            self.paths.integrity,
        ):
            path.write_text("", encoding="utf-8")

    def start_operation(
        self,
        *,
        case_id: str,
        invocation_id: str,
        kind: OperationKind,
        name: str,
        role: TrafficRole,
        model_id: str | None,
    ) -> str:
        operation_id = new_operation_id()
        now = datetime.now(UTC)
        with self._lock:
            self._operations[operation_id] = _PendingOperation(
                operation_id=operation_id,
                case_id=case_id,
                invocation_id=invocation_id,
                kind=kind,
                name=_bounded_text(name, 128),
                role=role,
                model_id=_bounded_optional_text(model_id, 128),
                started_at=now,
                started_perf=self._monotonic(),
            )
        return operation_id

    def start_request_group(
        self,
        *,
        operation_id: str,
        polling_session_id: str | None,
        method: str,
        path: str,
        protocol: Protocol,
        role: TrafficRole,
        configured_max_attempts: int,
    ) -> str | None:
        group_id = new_request_group_id()
        with self._lock:
            if operation_id not in self._operations:
                self.capture_integrity(
                    source="semantic_collector",
                    code="operation_missing_for_group",
                    message="request group skipped because operation is not pending",
                    related_id=operation_id,
                )
                return None
            self._groups[group_id] = _PendingGroup(
                request_group_id=group_id,
                operation_id=operation_id,
                polling_session_id=polling_session_id,
                method=method,
                path=path,
                protocol=protocol,
                role=role,
                configured_max_attempts=max(int(configured_max_attempts), 1),
                started_at=datetime.now(UTC),
                started_perf=self._monotonic(),
            )
        return group_id

    def observe_request_metric(self, request_group_id: str, metric: RequestMetric) -> None:
        with self._lock:
            group = self._groups.get(request_group_id)
            if group is None:
                self.capture_integrity(
                    source="semantic_collector",
                    code="request_group_missing_for_metric",
                    message="request metric could not be associated with a pending request group",
                    related_id=request_group_id,
                )
                return
            if any(item.request_event_id == metric.request_event_id for item in group.metrics):
                return
            group.metrics.append(metric)

    def finish_request_group(
        self,
        request_group_id: str | None,
        *,
        retry_wait_seconds: float = 0.0,
    ) -> RequestGroupRecord | None:
        if request_group_id is None:
            return None
        with self._lock:
            group = self._groups.pop(request_group_id, None)
            if group is None:
                return None
            operation = self._operations.get(group.operation_id)
            if not group.metrics:
                if operation is not None:
                    operation.incomplete = True
                self.capture_integrity(
                    source="semantic_collector",
                    code="request_group_without_metrics",
                    message="request group finished without P0 request metrics",
                    related_id=request_group_id,
                    severity=IssueSeverity.ERROR,
                )
                return None

            metrics = list(group.metrics)
            attempt_ids = tuple(metric.request_event_id for metric in metrics)
            indexes = [metric.attempt_index for metric in metrics]
            complete = indexes == list(range(1, len(metrics) + 1))
            if not complete and operation is not None:
                operation.incomplete = True
            first = metrics[0]
            final = metrics[-1]
            ended_at = datetime.now(UTC)
            duration_ms = _elapsed_ms(group.started_perf, self._monotonic())
            record = RequestGroupRecord(
                **self._identity(group.operation_id),
                request_group_id=request_group_id,
                operation_id=group.operation_id,
                polling_session_id=group.polling_session_id,
                interface_id=build_interface_id(group.method, group.path, group.protocol),
                method=group.method,
                url_template=build_url_template(group.path),
                protocol=group.protocol,
                traffic_role=group.role,
                attempt_event_ids=attempt_ids,
                attempt_count=len(metrics),
                configured_max_attempts=group.configured_max_attempts,
                retry_wait_ms=max(float(retry_wait_seconds), 0.0) * 1000,
                started_at=group.started_at,
                ended_at=ended_at,
                total_duration_ms=duration_ms,
                first_transport_outcome=_transport_outcome(first),
                final_transport_outcome=_transport_outcome(final),
                first_status_code=first.status_code,
                final_status_code=final.status_code,
                final_request_event_id=final.request_event_id,
                completeness=(
                    RecordCompleteness.COMPLETE if complete else RecordCompleteness.INCOMPLETE
                ),
            )
            self._append(self.paths.request_groups, record, "request_group_write_failed", request_group_id)
            if operation is not None:
                operation.request_group_ids.append(request_group_id)
                operation.metrics.extend(metrics)
                operation.response_headers_ms = _elapsed_ms(operation.started_perf, self._monotonic())
                if operation.kind is OperationKind.ASYNC_TASK and group.protocol is not Protocol.POLLING:
                    if operation.create_request_ms is None:
                        operation.create_request_ms = duration_ms
            if group.polling_session_id is not None:
                session = self._polling_sessions.get(group.polling_session_id)
                if session is not None:
                    session.request_group_ids.append(request_group_id)
            return record

    def start_polling_session(
        self,
        *,
        operation_id: str,
        case_id: str,
        invocation_id: str,
    ) -> str | None:
        session_id = new_polling_session_id()
        with self._lock:
            if operation_id not in self._operations:
                return None
            self._polling_sessions[session_id] = _PendingPollingSession(
                polling_session_id=session_id,
                operation_id=operation_id,
                case_id=case_id,
                invocation_id=invocation_id,
                started_at=datetime.now(UTC),
                started_perf=self._monotonic(),
            )
        return session_id

    def observe_polling_state(self, polling_session_id: str | None, state: str) -> None:
        if polling_session_id is None:
            return
        normalized = _bounded_text(state, 64).lower()
        with self._lock:
            session = self._polling_sessions.get(polling_session_id)
            if session is None:
                return
            offset = _elapsed_ms(session.started_perf, self._monotonic())
            session.first_observed_offsets_ms.setdefault(normalized, offset)
            if len(session.observed_states) < MAX_OBSERVED_STATES:
                session.observed_states.append(normalized)

    def add_polling_sleep(self, polling_session_id: str | None, seconds: float) -> None:
        if polling_session_id is None:
            return
        with self._lock:
            session = self._polling_sessions.get(polling_session_id)
            if session is not None:
                session.sleep_duration_ms += max(float(seconds), 0.0) * 1000

    def finish_polling_session(
        self,
        polling_session_id: str | None,
        outcome: PollingOutcome,
    ) -> PollingSessionRecord | None:
        if polling_session_id is None:
            return None
        with self._lock:
            session = self._polling_sessions.pop(polling_session_id, None)
            if session is None:
                return None
            operation = self._operations.get(session.operation_id)
            complete = bool(session.request_group_ids)
            if not complete and operation is not None:
                operation.incomplete = True
            ended_at = datetime.now(UTC)
            duration_ms = _elapsed_ms(session.started_perf, self._monotonic())
            terminal = session.observed_states[-1] if session.observed_states else None
            record = PollingSessionRecord(
                **self._identity(session.operation_id),
                polling_session_id=polling_session_id,
                operation_id=session.operation_id,
                request_group_ids=tuple(session.request_group_ids),
                poll_count=len(session.request_group_ids),
                started_at=session.started_at,
                ended_at=ended_at,
                total_duration_ms=duration_ms,
                sleep_duration_ms=session.sleep_duration_ms,
                final_outcome=outcome,
                terminal_status=terminal,
                observed_state_sequence=tuple(session.observed_states),
                first_observed_offsets_ms=dict(session.first_observed_offsets_ms),
                completeness=(
                    RecordCompleteness.COMPLETE if complete else RecordCompleteness.INCOMPLETE
                ),
            )
            self._append(
                self.paths.polling_sessions,
                record,
                "polling_session_write_failed",
                polling_session_id,
            )
            if operation is not None:
                operation.polling_session_ids.append(polling_session_id)
                operation.polling_total_ms = duration_ms
                operation.polling_sleep_ms = session.sleep_duration_ms
                operation.terminal_perf = self._monotonic()
            return record

    def observe_stream_line(self, operation_id: str, line: str) -> None:
        with self._lock:
            operation = self._operations.get(operation_id)
            if operation is None or operation.kind is not OperationKind.SSE:
                return
            stripped = line.strip()
            if not stripped.startswith("data:"):
                return
            data = stripped.removeprefix("data:").strip()
            if not data:
                return
            now = self._monotonic()
            if operation.first_data_ms is None:
                operation.first_data_ms = _elapsed_ms(operation.started_perf, now)
            if data == "[DONE]":
                return
            try:
                payload = json.loads(data)
            except (TypeError, ValueError):
                return
            if not isinstance(payload, Mapping):
                return
            if operation.first_content_ms is None and _contains_stream_content(payload):
                operation.first_content_ms = _elapsed_ms(operation.started_perf, now)
            usage = payload.get("usage")
            if isinstance(usage, Mapping):
                _merge_stream_usage(operation.stream_usage, usage)

    def finish_stream(self, operation_id: str, outcome: StreamOutcome) -> None:
        with self._lock:
            operation = self._operations.get(operation_id)
            if operation is None:
                return
            operation.stream_outcome = outcome
            if operation.metrics:
                operation.stream_source_event_id = operation.metrics[-1].request_event_id
        mapped = {
            StreamOutcome.COMPLETE: OperationOutcome.SUCCESS,
            StreamOutcome.INTERRUPTED: OperationOutcome.INTERRUPTED,
            StreamOutcome.ERROR: OperationOutcome.FAILED,
            StreamOutcome.NOT_CONSUMED: OperationOutcome.INCOMPLETE,
        }[outcome]
        self.finish_operation(operation_id, mapped)

    def finish_operation(
        self,
        operation_id: str | None,
        outcome: OperationOutcome,
    ) -> OperationRecord | None:
        if operation_id is None:
            return None
        with self._lock:
            operation = self._operations.pop(operation_id, None)
            if operation is None:
                return None
            pending_groups = [
                group_id
                for group_id, group in self._groups.items()
                if group.operation_id == operation_id
            ]
            pending_sessions = [
                session_id
                for session_id, session in self._polling_sessions.items()
                if session.operation_id == operation_id
            ]
            if pending_groups or pending_sessions:
                operation.incomplete = True
            ended_perf = operation.terminal_perf or self._monotonic()
            total_duration_ms = _elapsed_ms(operation.started_perf, ended_perf)
            ended_at = operation.started_at + timedelta(milliseconds=total_duration_ms)
            if outcome is OperationOutcome.SUCCESS and operation.metrics:
                final_metric = operation.metrics[-1]
                if (
                    final_metric.status_code is None
                    or not 200 <= final_metric.status_code < 300
                    or final_metric.business_status is BusinessStatus.FAILED
                ):
                    outcome = OperationOutcome.FAILED
            usage = _build_operation_usage(operation)
            timing = _build_operation_timing(operation, total_duration_ms)
            complete = (
                not operation.incomplete
                and not pending_groups
                and not pending_sessions
                and bool(operation.request_group_ids)
            )
            if outcome is OperationOutcome.INCOMPLETE:
                complete = False
            if operation.kind is OperationKind.SSE and operation.stream_outcome is None:
                if outcome is OperationOutcome.INCOMPLETE:
                    operation.stream_outcome = StreamOutcome.NOT_CONSUMED
                elif outcome is OperationOutcome.INTERRUPTED:
                    operation.stream_outcome = StreamOutcome.INTERRUPTED
                else:
                    operation.stream_outcome = StreamOutcome.ERROR
                complete = False
            evidence = tuple(
                [*(f"request_group:{value}" for value in operation.request_group_ids)]
                + [*(f"polling_session:{value}" for value in operation.polling_session_ids)]
                + (
                    [f"stream_outcome:{operation.stream_outcome.value}"]
                    if operation.stream_outcome is not None
                    else []
                )
            )
            record = OperationRecord(
                **self._identity_from_operation(operation),
                operation_id=operation.operation_id,
                operation_kind=operation.kind,
                operation_name=operation.name,
                traffic_role=operation.role,
                model_id=operation.model_id,
                request_group_ids=tuple(operation.request_group_ids),
                polling_session_ids=tuple(operation.polling_session_ids),
                started_at=operation.started_at,
                ended_at=ended_at,
                outcome=outcome,
                stream_outcome=operation.stream_outcome,
                timing=timing,
                usage=usage,
                evidence_refs=evidence,
                completeness=(
                    RecordCompleteness.COMPLETE if complete else RecordCompleteness.INCOMPLETE
                ),
            )
            self._append(self.paths.operations, record, "operation_write_failed", operation_id)
            return record

    def finalize_pending(self, invocation_id: str | None = None) -> None:
        with self._lock:
            operation_ids = [
                operation_id
                for operation_id, operation in self._operations.items()
                if invocation_id is None or operation.invocation_id == invocation_id
            ]
        for operation_id in operation_ids:
            with self._lock:
                operation = self._operations.get(operation_id)
                is_stream = operation is not None and operation.kind is OperationKind.SSE
            self.capture_integrity(
                source="semantic_collector",
                code="stream_not_finalized" if is_stream else "operation_not_finalized",
                message=(
                    "SSE operation was not consumed or closed before case teardown"
                    if is_stream
                    else "operation was still pending at case teardown"
                ),
                related_id=operation_id,
                severity=IssueSeverity.WARN,
            )
            if is_stream:
                self.finish_stream(operation_id, StreamOutcome.NOT_CONSUMED)
            else:
                self.finish_operation(operation_id, OperationOutcome.INCOMPLETE)

    def capture_integrity(
        self,
        *,
        source: str,
        code: str,
        message: str,
        related_id: str | None = None,
        severity: IssueSeverity = IssueSeverity.WARN,
    ) -> bool:
        try:
            issue = SemanticIntegrityIssue(
                run_id=self.run_context.run_id,
                severity=severity,
                source=source,
                code=code,
                message=_safe_message(message),
                related_id=related_id,
                created_at=datetime.now(UTC),
            )
            with self._lock:
                append_jsonl(self.paths.integrity, issue)
            return True
        except Exception as error:
            self._warn(
                "quality semantic integrity write failed: "
                f"{type(error).__name__}: {_safe_message(error)}"
            )
            return False

    def _identity(self, operation_id: str) -> dict[str, Any]:
        operation = self._operations.get(operation_id)
        if operation is None:
            raise ValueError(f"operation is not pending: {operation_id}")
        return self._identity_from_operation(operation)

    def _identity_from_operation(self, operation: _PendingOperation) -> dict[str, Any]:
        return {
            "run_id": self.run_context.run_id,
            "execution_id": self.run_context.execution_id,
            "worker_id": self.run_context.worker_id,
            "case_id": operation.case_id,
            "invocation_id": operation.invocation_id,
            "created_at": datetime.now(UTC),
        }

    def _append(self, path: Path, record: Any, code: str, related_id: str) -> bool:
        try:
            append_jsonl(path, record)
            return True
        except Exception as error:
            self.capture_integrity(
                source="semantic_collector",
                code=code,
                message=f"{type(error).__name__}: {_safe_message(error)}",
                related_id=related_id,
                severity=IssueSeverity.ERROR,
            )
            return False

    def _warn(self, message: str) -> None:
        try:
            self._warning_sink(message)
        except Exception:
            return


_COLLECTOR_LOCK = RLock()
_COLLECTOR: SemanticCollector | None = None


def configure_semantic_collector(
    run_context: QualityRunContext,
    *,
    warning_sink: WarningSink | None = None,
) -> SemanticCollector:
    global _COLLECTOR
    with _COLLECTOR_LOCK:
        collector = SemanticCollector(run_context, warning_sink=warning_sink)
        _COLLECTOR = collector
        return collector


def get_semantic_collector(default: SemanticCollector | None = None) -> SemanticCollector | None:
    with _COLLECTOR_LOCK:
        return _COLLECTOR or default


def reset_semantic_collector() -> None:
    global _COLLECTOR
    with _COLLECTOR_LOCK:
        _COLLECTOR = None


def _build_operation_usage(operation: _PendingOperation) -> OperationUsage:
    if operation.role is TrafficRole.CONTROL or operation.kind is OperationKind.POLLING:
        return OperationUsage(completeness=UsageCompleteness.NOT_APPLICABLE)

    known: dict[str, int | float | None] = {
        "input_tokens": None,
        "output_tokens": None,
        "media_count": None,
        "media_duration_ms": None,
    }
    source_ids: list[str] = []
    missing_ids: list[str] = []
    metrics = list(operation.metrics)
    for metric in metrics:
        values = {
            "input_tokens": metric.usage.input_tokens,
            "output_tokens": metric.usage.output_tokens,
            "media_count": metric.usage.media_count,
        }
        if any(value is not None for value in values.values()):
            source_ids.append(metric.request_event_id)
            _sum_usage(known, values)
        elif operation.kind in {OperationKind.HTTP, OperationKind.SSE} and metric.protocol is not Protocol.POLLING:
            missing_ids.append(metric.request_event_id)

    if operation.stream_usage:
        _sum_usage(known, operation.stream_usage)
        if operation.stream_source_event_id:
            if operation.stream_source_event_id not in source_ids:
                source_ids.append(operation.stream_source_event_id)
            missing_ids = [
                value for value in missing_ids if value != operation.stream_source_event_id
            ]

    if operation.kind is OperationKind.ASYNC_TASK and not source_ids:
        final_event_id = metrics[-1].request_event_id if metrics else None
        if final_event_id is not None:
            missing_ids.append(final_event_id)

    source_ids = list(dict.fromkeys(source_ids))
    missing_ids = list(dict.fromkeys(missing_ids))
    has_known = any(value is not None for value in known.values())
    if has_known and missing_ids:
        completeness = UsageCompleteness.PARTIAL
    elif has_known:
        completeness = UsageCompleteness.COMPLETE
    else:
        completeness = UsageCompleteness.MISSING
    return OperationUsage(
        input_tokens=_as_int(known["input_tokens"]),
        output_tokens=_as_int(known["output_tokens"]),
        media_count=_as_int(known["media_count"]),
        media_duration_ms=_as_float(known["media_duration_ms"]),
        source_request_event_ids=tuple(source_ids),
        completeness=completeness,
        missing_request_event_ids=tuple(missing_ids),
    )


def _build_operation_timing(
    operation: _PendingOperation,
    total_duration_ms: float,
) -> OperationTiming:
    if operation.kind is OperationKind.SSE:
        complete = operation.response_headers_ms is not None and operation.first_data_ms is not None
        timing_completeness = (
            TimingCompleteness.COMPLETE
            if complete and operation.stream_outcome is StreamOutcome.COMPLETE
            else TimingCompleteness.PARTIAL
        )
        return OperationTiming(
            total_duration_ms=total_duration_ms,
            response_headers_ms=operation.response_headers_ms,
            first_data_ms=operation.first_data_ms,
            first_content_ms=operation.first_content_ms,
            stream_duration_ms=total_duration_ms,
            timing_completeness=timing_completeness,
        )
    if operation.kind is OperationKind.ASYNC_TASK:
        complete = operation.create_request_ms is not None and operation.polling_total_ms is not None
        return OperationTiming(
            total_duration_ms=total_duration_ms,
            create_request_ms=operation.create_request_ms,
            polling_total_ms=operation.polling_total_ms,
            polling_sleep_ms=operation.polling_sleep_ms,
            timing_completeness=(
                TimingCompleteness.COMPLETE if complete else TimingCompleteness.PARTIAL
            ),
        )
    if operation.kind is OperationKind.POLLING:
        return OperationTiming(
            total_duration_ms=total_duration_ms,
            polling_total_ms=operation.polling_total_ms,
            polling_sleep_ms=operation.polling_sleep_ms,
            timing_completeness=(
                TimingCompleteness.COMPLETE
                if operation.polling_total_ms is not None
                else TimingCompleteness.MISSING
            ),
        )
    return OperationTiming(
        total_duration_ms=total_duration_ms,
        response_headers_ms=operation.response_headers_ms,
        timing_completeness=(
            TimingCompleteness.COMPLETE
            if operation.response_headers_ms is not None
            else TimingCompleteness.MISSING
        ),
    )


def _transport_outcome(metric: RequestMetric) -> AttemptTransportOutcome:
    if metric.timeout:
        return AttemptTransportOutcome.TIMEOUT
    if metric.status_code is None:
        return AttemptTransportOutcome.ERROR
    return AttemptTransportOutcome.RESPONSE


def _contains_stream_content(payload: Mapping[str, Any]) -> bool:
    choices = payload.get("choices")
    if not isinstance(choices, list):
        return False
    for choice in choices:
        if not isinstance(choice, Mapping):
            continue
        for container_name in ("delta", "message"):
            container = choice.get(container_name)
            if isinstance(container, Mapping):
                content = container.get("content")
                if isinstance(content, str) and content:
                    return True
    return False


def _merge_stream_usage(target: dict[str, int], usage: Mapping[str, Any]) -> None:
    aliases = {
        "input_tokens": ("input_tokens", "prompt_tokens"),
        "output_tokens": ("output_tokens", "completion_tokens"),
        "media_count": ("media_count",),
    }
    for target_name, source_names in aliases.items():
        for source_name in source_names:
            value = usage.get(source_name)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                target[target_name] = value
                break


def _sum_usage(target: dict[str, int | float | None], values: Mapping[str, Any]) -> None:
    for name in target:
        value = values.get(name)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            continue
        target[name] = (target[name] or 0) + value


def _as_int(value: int | float | None) -> int | None:
    return int(value) if value is not None else None


def _as_float(value: int | float | None) -> float | None:
    return float(value) if value is not None else None


def _elapsed_ms(started: float, ended: float) -> float:
    return max((ended - started) * 1000, 0.0)


def _bounded_text(value: object, limit: int) -> str:
    text = str(value).strip()
    if not text:
        raise ValueError("value must not be empty")
    return text[:limit]


def _bounded_optional_text(value: object | None, limit: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text[:limit] or None


def _safe_message(value: object) -> str:
    redacted = redact_quality_value(str(value), remove_url_query=True)
    text = str(redacted).replace("\r", " ").replace("\n", " ").strip()
    return (text or type(value).__name__)[:500]


def _default_warning_sink(message: str) -> None:
    print(message, file=sys.stderr)
