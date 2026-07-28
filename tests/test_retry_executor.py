from __future__ import annotations

from typing import Any

import pytest
import requests

from common.request_context import RequestContext
from common.retry import RetryAttemptRecord, RetryPolicy
from common.retry_executor import RetryExecutor
from tests.mock_helpers import make_response, timeout_error


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class RetryExecutorHarness:
    def __init__(
        self,
        results: list[requests.Response | BaseException],
        *,
        method: str = "GET",
        request_kwargs: dict[str, Any] | None = None,
        policy: RetryPolicy | None = None,
    ):
        self.results = list(results)
        self.method = method
        self.request_kwargs = request_kwargs or {"headers": {}}
        self.policy = policy or RetryPolicy(base_delay=0.1, jitter=False)
        self.clock = FakeClock()
        self.executor = RetryExecutor(sleeper=self.clock.sleep, monotonic=self.clock.monotonic)
        self.contexts: list[RequestContext] = []
        self.attached_records: list[tuple[RequestContext, list[RetryAttemptRecord]]] = []

    def context_factory(self, attempt_index: int) -> RequestContext:
        context = RequestContext(
            method=self.method,
            path="/v1/models",
            url="https://example.com/v1/models",
            kwargs={"headers": dict(self.request_kwargs.get("headers") or {})},
        )
        context.attributes["factory_attempt_index"] = attempt_index
        self.contexts.append(context)
        return context

    def send_once(self, context: RequestContext) -> requests.Response:
        if not self.results:
            raise AssertionError("no result left")
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result

    def attach_records(self, context: RequestContext, records: list[RetryAttemptRecord]) -> None:
        self.attached_records.append((context, list(records)))

    def execute(self, context_recorder: list[RequestContext] | None = None) -> requests.Response:
        return self.executor.execute(
            method=self.method,
            request_kwargs=self.request_kwargs,
            policy=self.policy,
            context_factory=self.context_factory,
            send_once=self.send_once,
            attach_records=self.attach_records,
            context_recorder=context_recorder,
        )


def test_get_retries_retryable_response_and_returns_success():
    harness = RetryExecutorHarness(
        [
            make_response("https://example.com/v1/models", status_code=503),
            make_response("https://example.com/v1/models", status_code=200),
        ]
    )

    response = harness.execute()

    assert response.status_code == 200
    assert harness.clock.sleeps == [0.1]
    assert [context.attributes["attempt_index"] for context in harness.contexts] == [1, 2]
    assert harness.attached_records[-1][1][0].reason == "HTTP 503"


def test_get_retries_timeout_and_returns_success():
    harness = RetryExecutorHarness(
        [
            timeout_error("temporary timeout"),
            make_response("https://example.com/v1/models", status_code=200),
        ]
    )

    response = harness.execute()

    assert response.status_code == 200
    assert harness.clock.sleeps == [0.1]
    assert harness.attached_records[-1][1][0].exception_type == "Timeout"


def test_get_final_timeout_raises_original_exception():
    error = timeout_error("still timeout")
    harness = RetryExecutorHarness(
        [error],
        policy=RetryPolicy(max_attempts=1, base_delay=0.1, jitter=False),
    )

    with pytest.raises(requests.Timeout) as exc_info:
        harness.execute()

    assert exc_info.value is error
    assert harness.attached_records[-1][1] == []


def test_post_without_idempotency_key_runs_once():
    harness = RetryExecutorHarness(
        [
            make_response("https://example.com/v1/models", status_code=503),
            make_response("https://example.com/v1/models", status_code=200),
        ],
        method="POST",
        request_kwargs={"headers": {}},
    )

    response = harness.execute()

    assert response.status_code == 503
    assert len(harness.contexts) == 1
    assert harness.clock.sleeps == []


def test_post_with_idempotency_key_retries():
    harness = RetryExecutorHarness(
        [
            make_response("https://example.com/v1/models", status_code=503),
            make_response("https://example.com/v1/models", status_code=200),
        ],
        method="POST",
        request_kwargs={"headers": {"Idempotency-Key": "request-001"}},
    )

    response = harness.execute()

    assert response.status_code == 200
    assert len(harness.contexts) == 2
    assert harness.clock.sleeps == [0.1]


def test_post_with_allow_post_retries():
    harness = RetryExecutorHarness(
        [
            make_response("https://example.com/v1/models", status_code=503),
            make_response("https://example.com/v1/models", status_code=200),
        ],
        method="POST",
        request_kwargs={"headers": {}},
        policy=RetryPolicy(max_attempts=2, base_delay=0.1, jitter=False, allow_post=True),
    )

    response = harness.execute()

    assert response.status_code == 200
    assert len(harness.contexts) == 2


def test_max_attempts_returns_last_retryable_response():
    harness = RetryExecutorHarness(
        [
            make_response("https://example.com/v1/models", status_code=503),
            make_response("https://example.com/v1/models", status_code=503),
        ],
        policy=RetryPolicy(max_attempts=2, base_delay=0.1, jitter=False),
    )

    response = harness.execute()

    assert response.status_code == 503
    assert len(harness.contexts) == 2
    assert harness.clock.sleeps == [0.1]


def test_response_path_max_elapsed_returns_current_response_without_sleep():
    harness = RetryExecutorHarness(
        [make_response("https://example.com/v1/models", status_code=503)],
        policy=RetryPolicy(max_attempts=3, base_delay=1.0, max_elapsed=0.5, jitter=False),
    )

    response = harness.execute()

    assert response.status_code == 503
    assert harness.clock.sleeps == []


def test_exception_path_max_elapsed_raises_original_exception_without_sleep():
    error = timeout_error("temporary timeout")
    harness = RetryExecutorHarness(
        [error],
        policy=RetryPolicy(max_attempts=3, base_delay=1.0, max_elapsed=0.5, jitter=False),
    )

    with pytest.raises(requests.Timeout) as exc_info:
        harness.execute()

    assert exc_info.value is error
    assert harness.clock.sleeps == []
    assert harness.attached_records[-1][1][0].reason == "Timeout: temporary timeout"


def test_context_recorder_points_to_latest_context():
    harness = RetryExecutorHarness(
        [
            make_response("https://example.com/v1/models", status_code=503),
            make_response("https://example.com/v1/models", status_code=200),
        ]
    )
    recorder: list[RequestContext] = []

    response = harness.execute(context_recorder=recorder)

    assert response.status_code == 200
    assert recorder == [harness.contexts[-1]]
    assert harness.contexts[0] is not harness.contexts[1]
