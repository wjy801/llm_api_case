from __future__ import annotations

from common import RetryPolicy


CONCURRENT_CHAT_429_RETRY_POLICY = RetryPolicy(
    max_attempts=3,
    retry_statuses=frozenset({429}),
    retry_exceptions=(),
    base_delay=2,
    max_delay=10,
    jitter=True,
    max_elapsed=30,
    allow_post=True,
)

SMOKE_GET_RETRY_POLICY = RetryPolicy(
    max_attempts=3,
    base_delay=0.5,
    max_delay=2,
    max_elapsed=10,
)
