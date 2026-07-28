from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
import requests

from common.base_request import BaseRequest
from common.polling import PollingFailedError, PollingPolicy, PollingTimeoutError, PollingUnknownStateError
from common.request_context import RequestContext
from common.request_middleware import LoggingMiddleware
from common.retry import RetryPolicy
from tests.mock_helpers import (
    FakeApiCallLogger,
    SequenceTransport,
    SleepRecorder,
    create_fake_logger,
    make_response,
    polling_responses,
    timeout_error,
)


@dataclass(frozen=True)
class DummyConfig:
    base_url: str = "https://example.com"
    api_key: str = "config-secret"
    timeout: float = 3


def test_default_request_does_not_retry():
    client = BaseRequest(config=DummyConfig(), middlewares=[])
    calls = 0

    def fake_request(method: str, url: str, **kwargs: Any) -> requests.Response:
        nonlocal calls
        calls += 1
        return make_response(url, status_code=503)

    client.session.request = fake_request  # type: ignore[method-assign]

    response = client.get("/v1/models")

    assert response.status_code == 503
    assert calls == 1


def test_poll_get_requires_polling_policy():
    client = BaseRequest(config=DummyConfig(), middlewares=[])

    with pytest.raises(TypeError, match="polling_policy"):
        client.poll_get("/v1/media/tasks/task-001")


def test_get_retries_503_then_returns_success(monkeypatch):
    sleep = SleepRecorder()
    monkeypatch.setattr("common.base_request.time.sleep", sleep)
    client = BaseRequest(config=DummyConfig(), middlewares=[])
    transport = SequenceTransport(
        [
            make_response("https://example.com/v1/models", status_code=503),
            make_response("https://example.com/v1/models", status_code=200),
        ]
    )

    client.session.request = transport  # type: ignore[method-assign]

    response = client.get(
        "/v1/models",
        retry_policy=RetryPolicy(max_attempts=3, base_delay=0.2, jitter=False),
    )

    assert response.status_code == 200
    assert sleep.calls == [0.2]
    assert [call.method for call in transport.calls] == ["GET", "GET"]


def test_get_retries_429_respecting_retry_after(monkeypatch):
    sleep_calls: list[float] = []
    monkeypatch.setattr("common.base_request.time.sleep", sleep_calls.append)
    client = BaseRequest(config=DummyConfig(), middlewares=[])
    responses = [
        make_response("https://example.com/v1/models", status_code=429, headers={"Retry-After": "2"}),
        make_response("https://example.com/v1/models", status_code=200),
    ]

    client.session.request = lambda method, url, **kwargs: responses.pop(0)  # type: ignore[method-assign]

    response = client.get(
        "/v1/models",
        retry_policy=RetryPolicy(max_attempts=2, base_delay=0.1, jitter=False),
    )

    assert response.status_code == 200
    assert sleep_calls == [2]


def test_timeout_retries_then_returns_success(monkeypatch):
    sleep = SleepRecorder()
    monkeypatch.setattr("common.base_request.time.sleep", sleep)
    client = BaseRequest(config=DummyConfig(), middlewares=[])
    transport = SequenceTransport(
        [
            timeout_error("temporary timeout"),
            make_response("https://example.com/v1/models", status_code=200),
        ]
    )

    client.session.request = transport  # type: ignore[method-assign]

    response = client.get(
        "/v1/models",
        retry_policy=RetryPolicy(max_attempts=2, base_delay=0.1, jitter=False),
    )

    assert response.status_code == 200
    assert sleep.calls == [0.1]
    assert len(transport.calls) == 2


def test_max_attempts_returns_last_retryable_response(monkeypatch):
    monkeypatch.setattr("common.base_request.time.sleep", lambda seconds: None)
    client = BaseRequest(config=DummyConfig(), middlewares=[])
    responses = [
        make_response("https://example.com/v1/models", status_code=503),
        make_response("https://example.com/v1/models", status_code=503),
    ]

    client.session.request = lambda method, url, **kwargs: responses.pop(0)  # type: ignore[method-assign]

    response = client.get(
        "/v1/models",
        retry_policy=RetryPolicy(max_attempts=2, base_delay=0.1, jitter=False),
    )

    assert response.status_code == 503


def test_max_elapsed_stops_retry_without_sleep(monkeypatch):
    sleep_calls: list[float] = []
    monkeypatch.setattr("common.base_request.time.sleep", sleep_calls.append)
    client = BaseRequest(config=DummyConfig(), middlewares=[])
    client.session.request = lambda method, url, **kwargs: make_response(url, status_code=503)  # type: ignore[method-assign]

    response = client.get(
        "/v1/models",
        retry_policy=RetryPolicy(max_attempts=3, base_delay=1, max_elapsed=0.001, jitter=False),
    )

    assert response.status_code == 503
    assert sleep_calls == []


def test_post_without_idempotency_key_does_not_retry(monkeypatch):
    sleep_calls: list[float] = []
    monkeypatch.setattr("common.base_request.time.sleep", sleep_calls.append)
    client = BaseRequest(config=DummyConfig(), middlewares=[])
    calls = 0

    def fake_request(method: str, url: str, **kwargs: Any) -> requests.Response:
        nonlocal calls
        calls += 1
        return make_response(url, status_code=503)

    client.session.request = fake_request  # type: ignore[method-assign]

    response = client.post(
        "/v1/media/generations",
        json={"model": "wan2.7-image"},
        retry_policy=RetryPolicy(max_attempts=3, base_delay=0.1, jitter=False),
    )

    assert response.status_code == 503
    assert calls == 1
    assert sleep_calls == []


def test_post_with_idempotency_key_retries(monkeypatch):
    sleep_calls: list[float] = []
    monkeypatch.setattr("common.base_request.time.sleep", sleep_calls.append)
    client = BaseRequest(config=DummyConfig(), middlewares=[])
    responses = [
        make_response("https://example.com/v1/media/generations", status_code=503),
        make_response("https://example.com/v1/media/generations", status_code=200),
    ]

    client.session.request = lambda method, url, **kwargs: responses.pop(0)  # type: ignore[method-assign]

    response = client.post(
        "/v1/media/generations",
        json={"model": "wan2.7-image"},
        headers={"Idempotency-Key": "request-001"},
        retry_policy=RetryPolicy(max_attempts=3, base_delay=0.1, jitter=False),
    )

    assert response.status_code == 200
    assert sleep_calls == [0.1]


def test_retry_uses_independent_context_for_each_attempt(monkeypatch):
    monkeypatch.setattr("common.base_request.time.sleep", lambda seconds: None)
    contexts: list[RequestContext] = []
    client = BaseRequest(config=DummyConfig(), middlewares=[MutatePayloadMiddleware(contexts)])
    payload = {"input": {"text": "original"}}
    responses = [
        make_response("https://example.com/v1/models", status_code=503),
        make_response("https://example.com/v1/models", status_code=200),
    ]
    observed_payloads: list[str] = []

    def fake_request(method: str, url: str, **kwargs: Any) -> requests.Response:
        observed_payloads.append(kwargs["json"]["input"]["text"])
        return responses.pop(0)

    client.session.request = fake_request  # type: ignore[method-assign]

    response = client.get(
        "/v1/models",
        json=payload,
        retry_policy=RetryPolicy(max_attempts=2, base_delay=0.1, jitter=False),
    )

    assert response.status_code == 200
    assert payload["input"]["text"] == "original"
    assert observed_payloads == ["mutated-1", "mutated-2"]
    assert contexts[0] is not contexts[1]


def test_retry_records_can_be_observed_by_logger(monkeypatch):
    monkeypatch.setattr("common.base_request.time.sleep", lambda seconds: None)
    created_loggers: list[FakeApiCallLogger] = []

    monkeypatch.setattr(
        "common.request_middleware.ApiCallLogger",
        lambda *args, **kwargs: create_fake_logger(created_loggers, *args, **kwargs),
    )
    client = BaseRequest(config=DummyConfig(), middlewares=[LoggingMiddleware()])
    responses = [
        make_response("https://example.com/v1/models", status_code=503),
        make_response("https://example.com/v1/models", status_code=200),
    ]
    client.session.request = lambda method, url, **kwargs: responses.pop(0)  # type: ignore[method-assign]

    client.get("/v1/models", retry_policy=RetryPolicy(max_attempts=2, base_delay=0.1, jitter=False))

    assert any(logger.retry_records for logger in created_loggers)
    assert created_loggers[-1].retry_records[-1][0].reason == "HTTP 503"


def test_polling_policy_success_records_transitions(monkeypatch):
    sleep = SleepRecorder()
    monkeypatch.setattr("common.base_request.time.sleep", sleep)
    created_loggers: list[FakeApiCallLogger] = []
    monkeypatch.setattr(
        "common.request_middleware.ApiCallLogger",
        lambda *args, **kwargs: create_fake_logger(created_loggers, *args, **kwargs),
    )
    client = BaseRequest(config=DummyConfig(), middlewares=[LoggingMiddleware()])
    transport = SequenceTransport(
        polling_responses(
            "https://example.com/v1/media/tasks/task-001",
            ["queued", "running", "succeeded"],
        )
    )
    client.session.request = transport  # type: ignore[method-assign]

    response = client.poll_get(
        "/v1/media/tasks/task-001",
        poll_interval=0.1,
        poll_timeout=3,
        polling_policy=PollingPolicy(),
    )

    assert response.json() == {"status": "succeeded"}
    assert sleep.calls == [0.1, 0.1]
    assert len(transport.calls) == 3
    assert created_loggers[-1].success_responses == [response]
    assert "queued" in created_loggers[-1].polling_transitions[-1]
    assert "succeeded" in created_loggers[-1].polling_transitions[-1]


def test_polling_policy_failure_raises_with_context(monkeypatch):
    monkeypatch.setattr("common.base_request.time.sleep", lambda seconds: None)
    client = BaseRequest(config=DummyConfig(), middlewares=[])
    responses = [
        make_response("https://example.com/v1/media/tasks/task-001", json_text='{"status": "queued"}'),
        make_response("https://example.com/v1/media/tasks/task-001", json_text='{"status": "failed"}'),
    ]
    client.session.request = lambda method, url, **kwargs: responses.pop(0)  # type: ignore[method-assign]

    with pytest.raises(PollingFailedError) as exc_info:
        client.poll_get(
            "/v1/media/tasks/task-001",
            poll_interval=0.1,
            poll_timeout=3,
            polling_policy=PollingPolicy(),
        )

    assert exc_info.value.last_status == "failed"
    assert [transition.raw_status for transition in exc_info.value.transitions] == ["queued", "failed"]


def test_polling_policy_unknown_state_raises(monkeypatch):
    monkeypatch.setattr("common.base_request.time.sleep", lambda seconds: None)
    client = BaseRequest(config=DummyConfig(), middlewares=[])
    client.session.request = lambda method, url, **kwargs: make_response(  # type: ignore[method-assign]
        url,
        json_text='{"status": "paused"}',
    )

    with pytest.raises(PollingUnknownStateError) as exc_info:
        client.poll_get(
            "/v1/media/tasks/task-001",
            poll_interval=0.1,
            poll_timeout=3,
            polling_policy=PollingPolicy(),
        )

    assert exc_info.value.last_status == "paused"


def test_polling_policy_timeout_raises_with_last_response(monkeypatch):
    sleep_calls: list[float] = []
    monkeypatch.setattr("common.base_request.time.sleep", sleep_calls.append)
    times = iter([0.0, 0.0, 0.0, 0.2, 0.2])
    monkeypatch.setattr("common.base_request.time.monotonic", lambda: next(times))
    client = BaseRequest(config=DummyConfig(), middlewares=[])
    client.session.request = lambda method, url, **kwargs: make_response(  # type: ignore[method-assign]
        url,
        json_text='{"status": "running"}',
    )

    with pytest.raises(PollingTimeoutError) as exc_info:
        client.poll_get(
            "/v1/media/tasks/task-001",
            poll_interval=0.1,
            poll_timeout=0.1,
            polling_policy=PollingPolicy(),
        )

    assert exc_info.value.last_status == "running"
    assert exc_info.value.last_response is not None
    assert len(exc_info.value.transitions) == 1


def test_polling_request_uses_retry_policy(monkeypatch):
    sleep_calls: list[float] = []
    monkeypatch.setattr("common.base_request.time.sleep", sleep_calls.append)
    created_loggers: list[FakeApiCallLogger] = []
    monkeypatch.setattr(
        "common.request_middleware.ApiCallLogger",
        lambda *args, **kwargs: create_fake_logger(created_loggers, *args, **kwargs),
    )
    client = BaseRequest(config=DummyConfig(), middlewares=[LoggingMiddleware()])
    responses = [
        make_response("https://example.com/v1/media/tasks/task-001", status_code=503, json_text='{"status": "running"}'),
        make_response("https://example.com/v1/media/tasks/task-001", status_code=200, json_text='{"status": "succeeded"}'),
    ]
    client.session.request = lambda method, url, **kwargs: responses.pop(0)  # type: ignore[method-assign]

    response = client.poll_get(
        "/v1/media/tasks/task-001",
        poll_interval=0.1,
        poll_timeout=3,
        polling_policy=PollingPolicy(),
        retry_policy=RetryPolicy(max_attempts=2, base_delay=0.2, jitter=False),
    )

    assert response.status_code == 200
    assert sleep_calls == [0.2]
    assert created_loggers[-1].success_responses == [response]
    assert "succeeded" in created_loggers[-1].polling_transitions[-1]


class MutatePayloadMiddleware:
    def __init__(self, contexts: list[RequestContext]):
        self.contexts = contexts

    def before_request(self, context: RequestContext) -> None:
        self.contexts.append(context)
        context.kwargs["json"]["input"]["text"] = f"mutated-{len(self.contexts)}"

    def after_response(self, context: RequestContext, response: requests.Response) -> None:
        return None

    def on_exception(self, context: RequestContext, error: BaseException) -> None:
        return None
