from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from typing import Any

from common.runtime_hooks import (
    RuntimeOperationMetadata,
    RuntimeOperationOutcome,
    RuntimeOperationStart,
    RuntimePollingOutcome,
    RuntimeStreamOutcome,
)
from quality import request_metrics, semantic_context
from quality.collector import get_collector
from quality.semantic_context import OperationHandle, PollingSessionHandle
from quality.semantic_models import OperationOutcome, PollingOutcome, StreamOutcome


class QualityRuntimeHooks:
    """Map neutral common runtime hooks to the existing P0/P1 collectors."""

    def model_id_from_kwargs(self, kwargs: Mapping[str, Any]) -> str | None:
        return semantic_context.model_id_from_kwargs(kwargs)

    def begin_operation(self, metadata: RuntimeOperationMetadata) -> RuntimeOperationStart:
        handle = semantic_context.begin_operation(
            _enum_value(metadata.kind),
            name=metadata.name,
            role=_enum_value(metadata.role),
            model_id=metadata.model_id,
        )
        return RuntimeOperationStart(native_handle=handle, owned=handle.owned)

    def finish_operation(
        self,
        native_handle: object | None,
        outcome: RuntimeOperationOutcome,
    ) -> None:
        if isinstance(native_handle, OperationHandle):
            semantic_context.finish_operation(
                native_handle,
                OperationOutcome(outcome.value),
            )

    def detach_operation(self, native_handle: object | None) -> None:
        if isinstance(native_handle, OperationHandle):
            semantic_context.detach_operation(native_handle)

    def start_request_group(
        self,
        *,
        method: str,
        path: str,
        protocol: str,
        configured_max_attempts: int,
    ) -> object | None:
        return semantic_context.start_request_group(
            method=method,
            path=path,
            protocol=protocol,
            configured_max_attempts=configured_max_attempts,
        )

    def bind_request_context(self, context: Any, native_handle: object | None) -> None:
        semantic_context.bind_request_context(
            context,
            native_handle if isinstance(native_handle, str) else None,
        )

    def finish_request_group(
        self,
        native_handle: object | None,
        *,
        retry_wait_seconds: float = 0.0,
    ) -> None:
        semantic_context.finish_request_group(
            native_handle if isinstance(native_handle, str) else None,
            retry_wait_seconds=retry_wait_seconds,
        )

    def request_started(self, context: Any) -> None:
        self._capture_request_call(context, request_metrics.start_request_capture, context)

    def request_succeeded(self, context: Any, response: Any) -> None:
        self._capture_request_call(context, request_metrics.record_response, context, response)

    def request_failed(self, context: Any, error: BaseException) -> None:
        self._capture_request_call(context, request_metrics.record_exception, context, error)

    def begin_polling_session(self) -> object | None:
        return semantic_context.begin_polling_session()

    def observe_polling_state(self, native_handle: object | None, state: str) -> None:
        if isinstance(native_handle, PollingSessionHandle):
            semantic_context.observe_polling_state(native_handle, state)

    def add_polling_sleep(self, native_handle: object | None, seconds: float) -> None:
        if isinstance(native_handle, PollingSessionHandle):
            semantic_context.add_polling_sleep(native_handle, seconds)

    def finish_polling_session(
        self,
        native_handle: object | None,
        outcome: RuntimePollingOutcome,
    ) -> None:
        if isinstance(native_handle, PollingSessionHandle):
            semantic_context.finish_polling_session(
                native_handle,
                PollingOutcome(outcome.value),
            )

    def bind_stream(self, response: Any, operation_handle: object | None) -> object | None:
        if not isinstance(operation_handle, OperationHandle):
            return None
        semantic_context.bind_stream_response(response, operation_handle)
        return semantic_context.stream_operation_id(response)

    def observe_stream_line(self, native_handle: object | None, line: str) -> None:
        semantic_context.observe_stream_line(
            native_handle if isinstance(native_handle, str) else None,
            line,
        )

    def finish_stream(
        self,
        native_handle: object | None,
        outcome: RuntimeStreamOutcome,
    ) -> None:
        semantic_context.finish_stream(
            native_handle if isinstance(native_handle, str) else None,
            StreamOutcome(outcome.value),
        )

    @staticmethod
    def _capture_request_call(context: Any, function: Any, *args: Any) -> None:
        try:
            function(*args)
        except Exception as error:
            collector = get_collector()
            if collector is None:
                return
            collector.capture_integrity(
                source="request_metrics",
                code="request_capture_failed",
                message=f"{type(error).__name__}: {error}",
                related_id=context.attributes.get(request_metrics.REQUEST_EVENT_ID_ATTR),
            )


def _enum_value(value: object) -> object:
    return value.value if isinstance(value, Enum) else value
