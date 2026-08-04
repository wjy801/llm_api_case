from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from common.polling import (
    PollingFailedError,
    PollingTimeoutError,
    PollingUnknownStateError,
)
from common.runtime_hooks.lifecycle import (
    add_polling_sleep,
    begin_operation,
    begin_polling_session,
    bind_request_context,
    bind_stream_response,
    detach_operation,
    finish_operation,
    finish_polling_session,
    finish_request_group,
    model_id_from_kwargs,
    observe_polling_state,
    operation_outcome_for_error,
    start_request_group,
)
from common.runtime_hooks.models import (
    RuntimeOperationKind,
    RuntimeOperationLease,
    RuntimeOperationMetadata,
    RuntimeOperationOutcome,
    RuntimePollingLease,
    RuntimePollingOutcome,
    RuntimeRequestGroupLease,
    RuntimeTrafficRole,
)


@dataclass
class RuntimeOperationObservation:
    lease: RuntimeOperationLease
    _finished: bool = False

    def finish_response(self, response: Any, *, stream: bool = False) -> None:
        if self._finished:
            return
        self._finished = True
        successful = 200 <= int(response.status_code) < 300
        if self.lease.owned and stream and successful:
            bind_stream_response(response, self.lease)
            detach_operation(self.lease)
            return
        finish_operation(
            self.lease,
            RuntimeOperationOutcome.SUCCESS if successful else RuntimeOperationOutcome.FAILED,
        )

    def finish_error(self, error: BaseException) -> None:
        if self._finished:
            return
        self._finished = True
        finish_operation(self.lease, operation_outcome_for_error(error))

    def finish(self, outcome: RuntimeOperationOutcome) -> None:
        if self._finished:
            return
        self._finished = True
        finish_operation(self.lease, outcome)


@dataclass
class RuntimeRequestGroupObservation:
    lease: RuntimeRequestGroupLease
    retry_wait_seconds: float = 0.0
    _finished: bool = False

    def bind(self, context: Any) -> None:
        bind_request_context(context, self.lease)

    def add_retry_wait(self, seconds: float) -> None:
        self.retry_wait_seconds += max(0.0, float(seconds))

    def finish(self) -> None:
        if self._finished:
            return
        self._finished = True
        finish_request_group(
            self.lease,
            retry_wait_seconds=self.retry_wait_seconds,
        )


@dataclass
class RuntimePollingObservation:
    operation: RuntimeOperationObservation
    polling_lease: RuntimePollingLease
    _finished: bool = False

    def observe_state(self, state: str) -> None:
        observe_polling_state(self.polling_lease, state)

    def add_sleep(self, seconds: float) -> None:
        add_polling_sleep(self.polling_lease, seconds)

    def finish_success(self) -> None:
        self._finish(
            RuntimePollingOutcome.SUCCESS,
            RuntimeOperationOutcome.SUCCESS,
        )

    def finish_error(self, error: BaseException) -> None:
        if isinstance(error, PollingFailedError):
            polling_outcome = RuntimePollingOutcome.FAILURE
            operation_outcome = RuntimeOperationOutcome.FAILED
        elif isinstance(error, PollingUnknownStateError):
            polling_outcome = RuntimePollingOutcome.UNKNOWN
            operation_outcome = RuntimeOperationOutcome.UNKNOWN
        elif isinstance(error, PollingTimeoutError):
            polling_outcome = RuntimePollingOutcome.TIMEOUT
            operation_outcome = RuntimeOperationOutcome.TIMEOUT
        elif isinstance(error, (KeyboardInterrupt, SystemExit)):
            polling_outcome = RuntimePollingOutcome.INTERRUPTED
            operation_outcome = RuntimeOperationOutcome.INTERRUPTED
        else:
            polling_outcome = RuntimePollingOutcome.FAILURE
            operation_outcome = operation_outcome_for_error(error)
        self._finish(polling_outcome, operation_outcome)

    def _finish(
        self,
        polling_outcome: RuntimePollingOutcome,
        operation_outcome: RuntimeOperationOutcome,
    ) -> None:
        if self._finished:
            return
        self._finished = True
        finish_polling_session(self.polling_lease, polling_outcome)
        self.operation.finish(operation_outcome)


class RuntimeObserver:
    """Owns runtime observation lifecycles without owning HTTP control flow."""

    def normalize_metadata(
        self,
        kwargs: dict[str, Any],
        *,
        kind: RuntimeOperationKind | str,
        default_name: str,
    ) -> RuntimeOperationMetadata:
        explicit = kwargs.pop("runtime_metadata", None)
        legacy_name = str(kwargs.pop("_quality_operation_name", "")).strip()
        legacy_role = kwargs.pop(
            "_quality_traffic_role",
            RuntimeTrafficRole.UNKNOWN,
        )
        inferred_model_id = model_id_from_kwargs(kwargs)

        if explicit is None:
            return RuntimeOperationMetadata(
                kind=kind,
                name=legacy_name or default_name,
                role=legacy_role,
                model_id=inferred_model_id,
            )
        if not isinstance(explicit, RuntimeOperationMetadata):
            raise TypeError("runtime_metadata must be RuntimeOperationMetadata")
        if explicit.model_id is None and inferred_model_id is not None:
            return replace(explicit, model_id=inferred_model_id)
        return explicit

    def start_operation(
        self,
        metadata: RuntimeOperationMetadata,
    ) -> RuntimeOperationObservation:
        return RuntimeOperationObservation(
            begin_operation(
                metadata.kind,
                name=metadata.name,
                role=metadata.role,
                model_id=metadata.model_id,
            )
        )

    def start_request_group(
        self,
        *,
        method: str,
        path: str,
        protocol: str,
        configured_max_attempts: int,
    ) -> RuntimeRequestGroupObservation:
        return RuntimeRequestGroupObservation(
            start_request_group(
                method=method,
                path=path,
                protocol=protocol,
                configured_max_attempts=configured_max_attempts,
            )
        )

    def start_polling(
        self,
        metadata: RuntimeOperationMetadata,
    ) -> RuntimePollingObservation:
        return RuntimePollingObservation(
            operation=self.start_operation(metadata),
            polling_lease=begin_polling_session(),
        )


def runtime_metadata(
    kind: RuntimeOperationKind | str,
    *,
    name: str,
    role: RuntimeTrafficRole | str = RuntimeTrafficRole.UNKNOWN,
    model_id: str | None = None,
) -> RuntimeOperationMetadata:
    """Build neutral request metadata without importing an observation backend."""

    return RuntimeOperationMetadata(
        kind=kind,
        name=name,
        role=role,
        model_id=model_id,
    )
