from __future__ import annotations

from datetime import UTC, datetime
from email.utils import format_datetime

import pytest
from pydantic import BaseModel
import requests

from common.retry import (
    RetryPolicy,
    calculate_retry_delay,
    is_method_retry_allowed,
    parse_retry_after,
    should_retry_exception,
    should_retry_response,
)


def test_retry_policy_rejects_invalid_max_attempts():
    with pytest.raises(ValueError, match="max_attempts"):
        RetryPolicy(max_attempts=0)


def test_retry_policy_is_frozen_pydantic_model():
    policy = RetryPolicy()

    assert isinstance(policy, BaseModel)
    with pytest.raises(ValueError, match="frozen"):
        policy.max_attempts = 5  # type: ignore[misc]


def test_parse_retry_after_seconds():
    assert parse_retry_after("3") == 3


def test_parse_retry_after_http_date():
    now = datetime(2026, 7, 26, 0, 0, 0, tzinfo=UTC)
    retry_at = datetime(2026, 7, 26, 0, 0, 5, tzinfo=UTC)

    assert parse_retry_after(format_datetime(retry_at, usegmt=True), now=now) == 5


def test_parse_retry_after_past_http_date_returns_zero():
    now = datetime(2026, 7, 26, 0, 0, 5, tzinfo=UTC)
    retry_at = datetime(2026, 7, 26, 0, 0, 0, tzinfo=UTC)

    assert parse_retry_after(format_datetime(retry_at, usegmt=True), now=now) == 0


def test_retry_after_parse_failure_falls_back_to_backoff():
    policy = RetryPolicy(base_delay=0.5, jitter=False)
    response = make_response(429, headers={"Retry-After": "not-a-date"})

    assert calculate_retry_delay(policy, 2, response=response) == 1.0


def test_exponential_backoff_is_limited_by_max_delay():
    policy = RetryPolicy(base_delay=2, max_delay=5, jitter=False)

    assert calculate_retry_delay(policy, 4) == 5


def test_jitter_uses_injected_random_function():
    policy = RetryPolicy(base_delay=2, jitter=True)

    delay = calculate_retry_delay(policy, 1, random_uniform=lambda start, end: end / 2)

    assert delay == 1


def test_get_is_retry_allowed_when_policy_enabled():
    assert is_method_retry_allowed("GET", {}, RetryPolicy())


def test_post_without_idempotency_key_is_not_retry_allowed():
    assert not is_method_retry_allowed("POST", {"headers": {}}, RetryPolicy())


def test_post_with_idempotency_key_is_retry_allowed():
    assert is_method_retry_allowed(
        "POST",
        {"headers": {"Idempotency-Key": "request-001"}},
        RetryPolicy(),
    )


def test_post_with_allow_post_is_retry_allowed():
    assert is_method_retry_allowed("POST", {"headers": {}}, RetryPolicy(allow_post=True))


def test_non_retryable_statuses_are_not_retried():
    policy = RetryPolicy()

    assert not should_retry_response(make_response(400), policy)
    assert not should_retry_response(make_response(404), policy)


def test_retryable_statuses_are_retried():
    policy = RetryPolicy()

    assert should_retry_response(make_response(429), policy)
    assert should_retry_response(make_response(503), policy)


def test_timeout_is_retryable_but_ssl_error_is_not():
    policy = RetryPolicy()

    assert should_retry_exception(requests.Timeout("timeout"), policy)
    assert not should_retry_exception(requests.exceptions.SSLError("ssl"), policy)


def make_response(status_code: int, headers: dict[str, str] | None = None) -> requests.Response:
    response = requests.Response()
    response.status_code = status_code
    response.reason = "Reason"
    response.headers.update(headers or {})
    response._content = b"{}"
    return response
