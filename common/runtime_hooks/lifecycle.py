from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import replace
from typing import Any, TypeVar

import requests

from common.runtime_hooks.models import (
    RUNTIME_REQUEST_HOOKS_ATTR,
    RUNTIME_STREAM_LEASE_ATTR,
    RuntimeOperationKind,
    RuntimeOperationLease,
    RuntimeOperationMetadata,
    RuntimeOperationOutcome,
    RuntimeOperationStart,
    RuntimePollingLease,
    RuntimePollingOutcome,
    RuntimeRequestGroupLease,
    RuntimeStreamLease,
    RuntimeStreamOutcome,
    RuntimeTrafficRole,
)
from common.runtime_hooks.protocol import RuntimeHooks
from common.runtime_hooks.provider import get_runtime_hooks


T = TypeVar("T")

_ACTIVE_OPERATION: ContextVar[RuntimeOperationLease | None] = ContextVar(
    "common_runtime_operation",
    default=None,
)


def get_active_runtime_hooks() -> RuntimeHooks:
    active = _ACTIVE_OPERATION.get()
    return active.hooks if active is not None else get_runtime_hooks()


def model_id_from_kwargs(kwargs: Mapping[str, Any]) -> str | None:
    hooks = get_active_runtime_hooks()
    return _safe_result(hooks.model_id_from_kwargs, None, kwargs)


def begin_operation(
    kind: RuntimeOperationKind | str,
    *,
    name: str,
    role: RuntimeTrafficRole | str = RuntimeTrafficRole.UNKNOWN,
    model_id: str | None = None,
) -> RuntimeOperationLease:
    active = _ACTIVE_OPERATION.get()
    if active is not None:
        return RuntimeOperationLease(
            hooks=active.hooks,
            native_handle=active.native_handle,
            owned=False,
        )

    hooks = get_runtime_hooks()
    metadata = RuntimeOperationMetadata(
        kind=kind,
        name=name,
        role=role,
        model_id=model_id,
    )
    started = _safe_result(
        hooks.begin_operation,
        RuntimeOperationStart(),
        metadata,
    )
    lease = RuntimeOperationLease(
        hooks=hooks,
        native_handle=started.native_handle,
        owned=started.owned,
    )
    if not lease.owned:
        return lease
    token = _ACTIVE_OPERATION.set(lease)
    return replace(lease, context_token=token)


def finish_operation(
    lease: RuntimeOperationLease,
    outcome: RuntimeOperationOutcome,
) -> None:
    if not lease.owned:
        return
    try:
        _safe_call(lease.hooks.finish_operation, lease.native_handle, outcome)
    finally:
        _reset_operation(lease)


def detach_operation(lease: RuntimeOperationLease) -> None:
    if not lease.owned:
        return
    try:
        _safe_call(lease.hooks.detach_operation, lease.native_handle)
    finally:
        _reset_operation(lease)


@contextmanager
def operation_scope(
    kind: RuntimeOperationKind | str,
    *,
    name: str,
    role: RuntimeTrafficRole | str = RuntimeTrafficRole.UNKNOWN,
    model_id: str | None = None,
) -> Iterator[RuntimeOperationLease]:
    lease = begin_operation(kind, name=name, role=role, model_id=model_id)
    try:
        yield lease
    except BaseException as error:
        finish_operation(lease, operation_outcome_for_error(error))
        raise
    else:
        finish_operation(lease, RuntimeOperationOutcome.SUCCESS)


def operation_outcome_for_error(error: BaseException) -> RuntimeOperationOutcome:
    if isinstance(error, (KeyboardInterrupt, SystemExit)):
        return RuntimeOperationOutcome.INTERRUPTED
    if isinstance(error, (requests.Timeout, TimeoutError)):
        return RuntimeOperationOutcome.TIMEOUT
    return RuntimeOperationOutcome.FAILED


def start_request_group(
    *,
    method: str,
    path: str,
    protocol: str,
    configured_max_attempts: int,
) -> RuntimeRequestGroupLease:
    hooks = get_active_runtime_hooks()
    native_handle = _safe_result(
        hooks.start_request_group,
        None,
        method=method,
        path=path,
        protocol=protocol,
        configured_max_attempts=configured_max_attempts,
    )
    return RuntimeRequestGroupLease(hooks=hooks, native_handle=native_handle)


def bind_request_context(context: Any, lease: RuntimeRequestGroupLease) -> None:
    _safe_call(lease.hooks.bind_request_context, context, lease.native_handle)


def finish_request_group(
    lease: RuntimeRequestGroupLease,
    *,
    retry_wait_seconds: float = 0.0,
) -> None:
    _safe_call(
        lease.hooks.finish_request_group,
        lease.native_handle,
        retry_wait_seconds=retry_wait_seconds,
    )


def observe_request_started(context: Any) -> None:
    hooks = get_active_runtime_hooks()
    context.attributes[RUNTIME_REQUEST_HOOKS_ATTR] = hooks
    _safe_call(hooks.request_started, context)


def observe_request_succeeded(context: Any, response: Any) -> None:
    hooks = _request_hooks(context)
    _safe_call(hooks.request_succeeded, context, response)


def observe_request_failed(context: Any, error: BaseException) -> None:
    hooks = _request_hooks(context)
    _safe_call(hooks.request_failed, context, error)


def begin_polling_session() -> RuntimePollingLease:
    hooks = get_active_runtime_hooks()
    native_handle = _safe_result(hooks.begin_polling_session, None)
    return RuntimePollingLease(hooks=hooks, native_handle=native_handle)


def observe_polling_state(lease: RuntimePollingLease, state: str) -> None:
    _safe_call(lease.hooks.observe_polling_state, lease.native_handle, state)


def add_polling_sleep(lease: RuntimePollingLease, seconds: float) -> None:
    _safe_call(lease.hooks.add_polling_sleep, lease.native_handle, seconds)


def finish_polling_session(
    lease: RuntimePollingLease,
    outcome: RuntimePollingOutcome,
) -> None:
    _safe_call(lease.hooks.finish_polling_session, lease.native_handle, outcome)


def bind_stream_response(
    response: Any,
    operation: RuntimeOperationLease,
) -> RuntimeStreamLease:
    native_handle = _safe_result(
        operation.hooks.bind_stream,
        None,
        response,
        operation.native_handle,
    )
    lease = RuntimeStreamLease(hooks=operation.hooks, native_handle=native_handle)
    setattr(response, RUNTIME_STREAM_LEASE_ATTR, lease)
    return lease


def get_stream_lease(response: Any) -> RuntimeStreamLease | None:
    lease = getattr(response, RUNTIME_STREAM_LEASE_ATTR, None)
    return lease if isinstance(lease, RuntimeStreamLease) else None


def observe_stream_line(lease: RuntimeStreamLease | None, line: str) -> None:
    if lease is None:
        return
    _safe_call(lease.hooks.observe_stream_line, lease.native_handle, line)


def finish_stream(
    lease: RuntimeStreamLease | None,
    outcome: RuntimeStreamOutcome,
) -> None:
    if lease is None:
        return
    _safe_call(lease.hooks.finish_stream, lease.native_handle, outcome)


def _request_hooks(context: Any) -> RuntimeHooks:
    hooks = context.attributes.get(RUNTIME_REQUEST_HOOKS_ATTR)
    return hooks if hooks is not None else get_active_runtime_hooks()


def _reset_operation(lease: RuntimeOperationLease) -> None:
    if lease.context_token is None:
        return
    try:
        _ACTIVE_OPERATION.reset(lease.context_token)
    except (LookupError, RuntimeError, ValueError):
        active = _ACTIVE_OPERATION.get()
        if (
            active is not None
            and active.hooks is lease.hooks
            and active.native_handle == lease.native_handle
        ):
            _ACTIVE_OPERATION.set(None)


def _safe_call(function: Any, *args: Any, **kwargs: Any) -> None:
    try:
        function(*args, **kwargs)
    except Exception:
        return


def _safe_result(function: Any, default: T, *args: Any, **kwargs: Any) -> T:
    try:
        return function(*args, **kwargs)
    except Exception:
        return default
