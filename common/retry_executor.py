from __future__ import annotations

from collections.abc import Callable, Mapping
import time
from typing import Any

import requests

from common.request_context import RequestContext
from common.retry import (
    RetryAttemptRecord,
    RetryPolicy,
    calculate_retry_delay,
    is_method_retry_allowed,
    retry_reason_for_exception,
    retry_reason_for_response,
    should_retry_exception,
    should_retry_response,
)


class RetryDeadlineExceeded(TimeoutError):
    def __init__(
        self,
        *,
        last_response: requests.Response | None = None,
    ) -> None:
        super().__init__("retry deadline exhausted")
        self.last_response = last_response


class RetryExecutor:
    """Execute a single-send callable under a RetryPolicy.

    The executor owns retry orchestration only. Request context construction,
    middleware execution, HTTP transport, and log attachment stay outside.
    """

    def __init__(
        self,
        *,
        sleeper: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        self.sleeper = sleeper
        self.monotonic = monotonic

    def execute(
        self,
        *,
        method: str,
        request_kwargs: Mapping[str, Any],
        policy: RetryPolicy,
        context_factory: Callable[[int], RequestContext],
        send_once: Callable[[RequestContext], requests.Response],
        attach_records: Callable[[RequestContext, list[RetryAttemptRecord]], None],
        context_recorder: list[RequestContext] | None = None,
        on_wait: Callable[[float], None] | None = None,
        deadline: float | None = None,
    ) -> requests.Response:
        retry_records: list[RetryAttemptRecord] = []

        if not is_method_retry_allowed(method, request_kwargs, policy):
            self.require_remaining(deadline)
            context = context_factory(1)
            self._prepare_context(context, policy, 1, retry_records)
            self._record_context(context_recorder, context)
            return send_once(context)

        started_at = self.monotonic()
        last_response: requests.Response | None = None

        for attempt_index in range(1, policy.max_attempts + 1):
            self.require_remaining(deadline, last_response=last_response)
            context = context_factory(attempt_index)
            self._prepare_context(context, policy, attempt_index, retry_records)
            self._record_context(context_recorder, context)

            try:
                response = send_once(context)
            except Exception as error:
                if attempt_index >= policy.max_attempts or not should_retry_exception(error, policy):
                    attach_records(context, retry_records)
                    raise

                wait_seconds = calculate_retry_delay(policy, attempt_index)
                retry_records.append(
                    RetryAttemptRecord(
                        attempt_index=attempt_index,
                        max_attempts=policy.max_attempts,
                        reason=retry_reason_for_exception(error),
                        wait_seconds=wait_seconds,
                        exception_type=type(error).__name__,
                        exception_message=str(error),
                    )
                )
                attach_records(context, retry_records)
                if not self._can_retry_within_elapsed(policy, started_at, wait_seconds):
                    raise
                if not self._can_wait_within_deadline(deadline, wait_seconds):
                    raise RetryDeadlineExceeded(last_response=last_response) from error
                self.sleeper(wait_seconds)
                self._notify_wait(on_wait, wait_seconds)
                continue

            last_response = response
            if attempt_index >= policy.max_attempts or not should_retry_response(response, policy):
                attach_records(context, retry_records)
                return response

            wait_seconds = calculate_retry_delay(policy, attempt_index, response=response)
            retry_records.append(
                RetryAttemptRecord(
                    attempt_index=attempt_index,
                    max_attempts=policy.max_attempts,
                    reason=retry_reason_for_response(response),
                    wait_seconds=wait_seconds,
                    response_status_code=response.status_code,
                )
            )
            attach_records(context, retry_records)
            if not self._can_retry_within_elapsed(policy, started_at, wait_seconds):
                return response
            if not self._can_wait_within_deadline(deadline, wait_seconds):
                raise RetryDeadlineExceeded(last_response=response)
            self.sleeper(wait_seconds)
            self._notify_wait(on_wait, wait_seconds)

        if last_response is not None:
            return last_response
        raise RuntimeError("retry loop ended without response or exception")

    @staticmethod
    def _prepare_context(
        context: RequestContext,
        policy: RetryPolicy,
        attempt_index: int,
        retry_records: list[RetryAttemptRecord],
    ) -> None:
        context.attributes["attempt_index"] = attempt_index
        context.attributes["max_attempts"] = policy.max_attempts
        context.attributes["retry_records"] = retry_records

    @staticmethod
    def _record_context(
        context_recorder: list[RequestContext] | None,
        context: RequestContext,
    ) -> None:
        if context_recorder is not None:
            context_recorder[:] = [context]

    def _can_retry_within_elapsed(
        self,
        policy: RetryPolicy,
        started_at: float,
        wait_seconds: float,
    ) -> bool:
        if policy.max_elapsed is None:
            return True
        return (self.monotonic() - started_at + wait_seconds) <= policy.max_elapsed

    def remaining(self, deadline: float | None) -> float | None:
        if deadline is None:
            return None
        return deadline - self.monotonic()

    def require_remaining(
        self,
        deadline: float | None,
        *,
        last_response: requests.Response | None = None,
    ) -> float | None:
        remaining = self.remaining(deadline)
        if remaining is not None and remaining <= 0:
            raise RetryDeadlineExceeded(last_response=last_response)
        return remaining

    def clamp_timeout(
        self,
        timeout: Any,
        deadline: float | None,
    ) -> Any:
        remaining = self.require_remaining(deadline)
        if remaining is None:
            return timeout
        if isinstance(timeout, tuple):
            return tuple(
                remaining if value is None else min(float(value), remaining)
                for value in timeout
            )
        if timeout is None:
            return remaining
        return min(float(timeout), remaining)

    def _can_wait_within_deadline(
        self,
        deadline: float | None,
        wait_seconds: float,
    ) -> bool:
        remaining = self.remaining(deadline)
        return remaining is None or wait_seconds < remaining

    @staticmethod
    def _notify_wait(on_wait: Callable[[float], None] | None, wait_seconds: float) -> None:
        if on_wait is not None:
            try:
                on_wait(wait_seconds)
            except Exception:
                return
