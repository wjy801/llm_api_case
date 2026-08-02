from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from common.runtime_hooks.models import (
    RuntimeOperationMetadata,
    RuntimeOperationOutcome,
    RuntimeOperationStart,
    RuntimePollingOutcome,
    RuntimeStreamOutcome,
)


class NoopRuntimeHooks:
    def model_id_from_kwargs(self, kwargs: Mapping[str, Any]) -> str | None:
        return None

    def begin_operation(self, metadata: RuntimeOperationMetadata) -> RuntimeOperationStart:
        return RuntimeOperationStart()

    def finish_operation(
        self,
        native_handle: object | None,
        outcome: RuntimeOperationOutcome,
    ) -> None:
        return None

    def detach_operation(self, native_handle: object | None) -> None:
        return None

    def start_request_group(
        self,
        *,
        method: str,
        path: str,
        protocol: str,
        configured_max_attempts: int,
    ) -> object | None:
        return None

    def bind_request_context(self, context: Any, native_handle: object | None) -> None:
        return None

    def finish_request_group(
        self,
        native_handle: object | None,
        *,
        retry_wait_seconds: float = 0.0,
    ) -> None:
        return None

    def request_started(self, context: Any) -> None:
        return None

    def request_succeeded(self, context: Any, response: Any) -> None:
        return None

    def request_failed(self, context: Any, error: BaseException) -> None:
        return None

    def begin_polling_session(self) -> object | None:
        return None

    def observe_polling_state(self, native_handle: object | None, state: str) -> None:
        return None

    def add_polling_sleep(self, native_handle: object | None, seconds: float) -> None:
        return None

    def finish_polling_session(
        self,
        native_handle: object | None,
        outcome: RuntimePollingOutcome,
    ) -> None:
        return None

    def bind_stream(self, response: Any, operation_handle: object | None) -> object | None:
        return None

    def observe_stream_line(self, native_handle: object | None, line: str) -> None:
        return None

    def finish_stream(
        self,
        native_handle: object | None,
        outcome: RuntimeStreamOutcome,
    ) -> None:
        return None
