from __future__ import annotations

from common import RetryPolicy


CONCURRENT_CHAT_RETRY_POLICY = RetryPolicy(allow_post=True)

SMOKE_GET_RETRY_POLICY = RetryPolicy(
    max_attempts=3,
    base_delay=0.5,
    max_delay=2,
    max_elapsed=10,
)
