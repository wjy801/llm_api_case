from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any, Iterator, Mapping

import requests

from quality.models import IssueSeverity, Protocol, RequestMetric
from quality.runtime_context import get_case_context
from quality.redaction import redact_quality_value, sanitize_identifier_part
from quality.semantic_collector import get_semantic_collector
from quality.semantic_models import (
    OperationKind,
    OperationOutcome,
    PollingOutcome,
    StreamOutcome,
    TrafficRole,
)


OPERATION_ID_ATTR = "quality_semantic_operation_id"
REQUEST_GROUP_ID_ATTR = "quality_semantic_request_group_id"
POLLING_SESSION_ID_ATTR = "quality_semantic_polling_session_id"
_RESPONSE_OPERATION_ATTR = "_quality_semantic_operation_id"


@dataclass(frozen=True)
class SemanticOperationContext:
    operation_id: str
    case_id: str
    invocation_id: str
    kind: OperationKind
    name: str
    role: TrafficRole
    model_id: str | None


@dataclass(frozen=True)
class OperationHandle:
    context: SemanticOperationContext | None
    token: Token[SemanticOperationContext | None] | None
    owned: bool


@dataclass(frozen=True)
class PollingSessionHandle:
    polling_session_id: str | None
    token: Token[str | None] | None


_OPERATION_CONTEXT: ContextVar[SemanticOperationContext | None] = ContextVar(
    "quality_semantic_operation_context",
    default=None,
)
_POLLING_SESSION_CONTEXT: ContextVar[str | None] = ContextVar(
    "quality_semantic_polling_session_context",
    default=None,
)


def get_operation_context() -> SemanticOperationContext | None:
    return _OPERATION_CONTEXT.get()


def begin_operation(
    kind: OperationKind | str,
    *,
    name: str,
    role: TrafficRole | str = TrafficRole.UNKNOWN,
    model_id: str | None = None,
) -> OperationHandle:
    active = get_operation_context()
    if active is not None:
        return OperationHandle(context=active, token=None, owned=False)
    collector = get_semantic_collector()
    case_context = get_case_context()
    if collector is None or case_context is None:
        if collector is not None and case_context is None:
            collector.capture_integrity(
                source="semantic_context",
                code="missing_case_context",
                message="semantic operation skipped because case context is missing",
            )
        return OperationHandle(context=None, token=None, owned=False)
    try:
        normalized_kind = OperationKind(kind)
        normalized_role = TrafficRole(role)
        operation_id = collector.start_operation(
            case_id=case_context.case_id,
            invocation_id=case_context.invocation_id,
            kind=normalized_kind,
            name=name,
            role=normalized_role,
            model_id=model_id,
        )
        context = SemanticOperationContext(
            operation_id=operation_id,
            case_id=case_context.case_id,
            invocation_id=case_context.invocation_id,
            kind=normalized_kind,
            name=name,
            role=normalized_role,
            model_id=model_id,
        )
        token = _OPERATION_CONTEXT.set(context)
        return OperationHandle(context=context, token=token, owned=True)
    except Exception as error:
        collector.capture_integrity(
            source="semantic_context",
            code="operation_start_failed",
            message=f"{type(error).__name__}: {error}",
            severity=IssueSeverity.ERROR,
        )
        return OperationHandle(context=None, token=None, owned=False)


def finish_operation(handle: OperationHandle, outcome: OperationOutcome | str) -> None:
    if not handle.owned or handle.context is None:
        return
    collector = get_semantic_collector()
    try:
        if collector is not None:
            collector.finish_operation(handle.context.operation_id, OperationOutcome(outcome))
    except Exception as error:
        _capture("operation_finish_failed", error, handle.context.operation_id)
    finally:
        _reset_operation(handle)


def detach_operation(handle: OperationHandle) -> None:
    if handle.owned:
        _reset_operation(handle)


@contextmanager
def operation_scope(
    kind: OperationKind | str,
    *,
    name: str,
    role: TrafficRole | str = TrafficRole.UNKNOWN,
    model_id: str | None = None,
) -> Iterator[SemanticOperationContext | None]:
    handle = begin_operation(kind, name=name, role=role, model_id=model_id)
    try:
        yield handle.context
    except BaseException as error:
        finish_operation(handle, _outcome_for_error(error))
        raise
    else:
        finish_operation(handle, OperationOutcome.SUCCESS)


def start_request_group(
    *,
    method: str,
    path: str,
    protocol: Protocol | str,
    configured_max_attempts: int,
) -> str | None:
    operation = get_operation_context()
    collector = get_semantic_collector()
    if operation is None or collector is None:
        return None
    try:
        return collector.start_request_group(
            operation_id=operation.operation_id,
            polling_session_id=_POLLING_SESSION_CONTEXT.get(),
            method=method,
            path=path,
            protocol=Protocol(protocol),
            role=operation.role,
            configured_max_attempts=configured_max_attempts,
        )
    except Exception as error:
        _capture("request_group_start_failed", error, operation.operation_id)
        return None


def bind_request_context(context: Any, request_group_id: str | None) -> None:
    operation = get_operation_context()
    if operation is None or request_group_id is None:
        return
    context.attributes[OPERATION_ID_ATTR] = operation.operation_id
    context.attributes[REQUEST_GROUP_ID_ATTR] = request_group_id
    polling_session_id = _POLLING_SESSION_CONTEXT.get()
    if polling_session_id is not None:
        context.attributes[POLLING_SESSION_ID_ATTR] = polling_session_id


def observe_request_metric(context: Any, metric: RequestMetric) -> None:
    group_id = context.attributes.get(REQUEST_GROUP_ID_ATTR)
    collector = get_semantic_collector()
    if collector is None or not isinstance(group_id, str):
        return
    try:
        collector.observe_request_metric(group_id, metric)
    except Exception as error:
        _capture("request_metric_observe_failed", error, group_id)


def finish_request_group(
    request_group_id: str | None,
    *,
    retry_wait_seconds: float = 0.0,
) -> None:
    collector = get_semantic_collector()
    if collector is None:
        return
    try:
        collector.finish_request_group(
            request_group_id,
            retry_wait_seconds=retry_wait_seconds,
        )
    except Exception as error:
        _capture("request_group_finish_failed", error, request_group_id)


def begin_polling_session() -> PollingSessionHandle:
    operation = get_operation_context()
    collector = get_semantic_collector()
    if operation is None or collector is None:
        return PollingSessionHandle(None, None)
    try:
        session_id = collector.start_polling_session(
            operation_id=operation.operation_id,
            case_id=operation.case_id,
            invocation_id=operation.invocation_id,
        )
        if session_id is None:
            return PollingSessionHandle(None, None)
        return PollingSessionHandle(session_id, _POLLING_SESSION_CONTEXT.set(session_id))
    except Exception as error:
        _capture("polling_session_start_failed", error, operation.operation_id)
        return PollingSessionHandle(None, None)


def observe_polling_state(handle: PollingSessionHandle, state: str) -> None:
    collector = get_semantic_collector()
    if collector is None:
        return
    try:
        collector.observe_polling_state(handle.polling_session_id, state)
    except Exception as error:
        _capture("polling_state_observe_failed", error, handle.polling_session_id)


def add_polling_sleep(handle: PollingSessionHandle, seconds: float) -> None:
    collector = get_semantic_collector()
    if collector is None:
        return
    try:
        collector.add_polling_sleep(handle.polling_session_id, seconds)
    except Exception as error:
        _capture("polling_sleep_observe_failed", error, handle.polling_session_id)


def finish_polling_session(handle: PollingSessionHandle, outcome: PollingOutcome | str) -> None:
    collector = get_semantic_collector()
    try:
        if collector is not None:
            collector.finish_polling_session(handle.polling_session_id, PollingOutcome(outcome))
    except Exception as error:
        _capture("polling_session_finish_failed", error, handle.polling_session_id)
    finally:
        if handle.token is not None:
            try:
                _POLLING_SESSION_CONTEXT.reset(handle.token)
            except Exception:
                _POLLING_SESSION_CONTEXT.set(None)


def bind_stream_response(response: requests.Response, handle: OperationHandle) -> None:
    if handle.context is None:
        return
    setattr(response, _RESPONSE_OPERATION_ATTR, handle.context.operation_id)


def stream_operation_id(response: requests.Response) -> str | None:
    value = getattr(response, _RESPONSE_OPERATION_ATTR, None)
    return value if isinstance(value, str) and value else None


def observe_stream_line(operation_id: str | None, line: str) -> None:
    collector = get_semantic_collector()
    if collector is None or operation_id is None:
        return
    try:
        collector.observe_stream_line(operation_id, line)
    except Exception as error:
        _capture("stream_line_observe_failed", error, operation_id)


def finish_stream(operation_id: str | None, outcome: StreamOutcome | str) -> None:
    collector = get_semantic_collector()
    if collector is None or operation_id is None:
        return
    try:
        collector.finish_stream(operation_id, StreamOutcome(outcome))
    except Exception as error:
        _capture("stream_finish_failed", error, operation_id)


def operation_kind_for_request(*, stream: bool) -> OperationKind:
    return OperationKind.SSE if stream else OperationKind.HTTP


def model_id_from_kwargs(kwargs: Mapping[str, Any]) -> str | None:
    payload = kwargs.get("json")
    if not isinstance(payload, Mapping):
        return None
    value = payload.get("model")
    if not isinstance(value, str):
        return None
    redacted = redact_quality_value(value)
    text = str(redacted).strip()
    return sanitize_identifier_part(text)[:128] if text else None


def _reset_operation(handle: OperationHandle) -> None:
    if handle.token is None:
        return
    try:
        _OPERATION_CONTEXT.reset(handle.token)
    except Exception:
        _OPERATION_CONTEXT.set(None)


def _outcome_for_error(error: BaseException) -> OperationOutcome:
    if isinstance(error, (KeyboardInterrupt, SystemExit)):
        return OperationOutcome.INTERRUPTED
    if isinstance(error, (requests.Timeout, TimeoutError)):
        return OperationOutcome.TIMEOUT
    return OperationOutcome.FAILED


def _capture(code: str, error: BaseException, related_id: object | None) -> None:
    collector = get_semantic_collector()
    if collector is None:
        return
    collector.capture_integrity(
        source="semantic_context",
        code=code,
        message=f"{type(error).__name__}: {error}",
        related_id=str(related_id) if related_id else None,
        severity=IssueSeverity.ERROR,
    )
