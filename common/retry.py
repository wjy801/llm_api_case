from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
import random
from typing import Any

import requests


DEFAULT_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
DEFAULT_ALLOWED_METHODS = frozenset({"GET", "HEAD"})


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    retry_statuses: frozenset[int] = DEFAULT_RETRY_STATUSES
    retry_exceptions: tuple[type[BaseException], ...] = (
        requests.ConnectionError,
        requests.Timeout,
    )
    backoff: str = "exponential"
    base_delay: float = 0.5
    max_delay: float = 10.0
    jitter: bool = True
    respect_retry_after: bool = True
    max_elapsed: float | None = 30.0
    allowed_methods: frozenset[str] = DEFAULT_ALLOWED_METHODS
    allow_post: bool = False
    idempotency_header: str = "Idempotency-Key"

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be greater than or equal to 1")
        if self.base_delay < 0:
            raise ValueError("base_delay must be greater than or equal to 0")
        if self.max_delay < self.base_delay:
            raise ValueError("max_delay must be greater than or equal to base_delay")
        if self.max_elapsed is not None and self.max_elapsed <= 0:
            raise ValueError("max_elapsed must be greater than 0")
        if self.backoff not in {"fixed", "exponential"}:
            raise ValueError("backoff must be 'fixed' or 'exponential'")
        if not self.idempotency_header.strip():
            raise ValueError("idempotency_header must not be empty")


@dataclass(frozen=True)
class RetryAttemptRecord:
    attempt_index: int
    max_attempts: int
    reason: str
    wait_seconds: float
    response_status_code: int | None = None
    exception_type: str | None = None
    exception_message: str | None = None


def is_method_retry_allowed(method: str, kwargs: Mapping[str, Any], policy: RetryPolicy) -> bool:
    normalized_method = method.upper()
    if normalized_method in {name.upper() for name in policy.allowed_methods}:
        return True

    if normalized_method != "POST":
        return False

    if policy.allow_post:
        return True

    headers = kwargs.get("headers") or {}
    header_names = {str(name).lower() for name in dict(headers).keys()}
    return policy.idempotency_header.lower() in header_names


def should_retry_exception(error: BaseException, policy: RetryPolicy) -> bool:
    if isinstance(error, (requests.exceptions.SSLError, requests.exceptions.TooManyRedirects)):
        return False
    return isinstance(error, policy.retry_exceptions)


def should_retry_response(response: requests.Response, policy: RetryPolicy) -> bool:
    return response.status_code in policy.retry_statuses


def retry_reason_for_exception(error: BaseException) -> str:
    return f"{type(error).__name__}: {error}"


def retry_reason_for_response(response: requests.Response) -> str:
    return f"HTTP {response.status_code}"


def parse_retry_after(value: str | None, *, now: datetime | None = None) -> float | None:
    if value is None:
        return None

    stripped_value = value.strip()
    if not stripped_value:
        return None

    try:
        delay = float(stripped_value)
    except ValueError:
        delay = None
    if delay is not None:
        if delay < 0:
            return None
        return delay

    try:
        retry_datetime = parsedate_to_datetime(stripped_value)
    except (TypeError, ValueError, IndexError, OverflowError):
        return None

    if retry_datetime.tzinfo is None:
        retry_datetime = retry_datetime.replace(tzinfo=UTC)

    now_datetime = now or datetime.now(UTC)
    if now_datetime.tzinfo is None:
        now_datetime = now_datetime.replace(tzinfo=UTC)

    return max(0.0, (retry_datetime - now_datetime).total_seconds())


def calculate_retry_delay(
    policy: RetryPolicy,
    attempt_index: int,
    *,
    response: requests.Response | None = None,
    random_uniform: Callable[[float, float], float] = random.uniform,
    now: datetime | None = None,
) -> float:
    retry_after_delay = None
    if policy.respect_retry_after and response is not None:
        retry_after_delay = parse_retry_after(response.headers.get("Retry-After"), now=now)

    use_jitter = policy.jitter
    if retry_after_delay is not None:
        delay = retry_after_delay
        use_jitter = False
    elif policy.backoff == "fixed":
        delay = policy.base_delay
    else:
        delay = policy.base_delay * (2 ** max(0, attempt_index - 1))

    delay = min(policy.max_delay, delay)
    if use_jitter and delay > 0:
        delay = random_uniform(0, delay)
    return max(0.0, delay)
