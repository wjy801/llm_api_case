from __future__ import annotations

from collections.abc import Sequence

from governance.flaky_models import AttemptOutcome, AttemptResult, FlakyStatus


AttemptLike = AttemptOutcome | AttemptResult | str


def classify_attempts(attempts: Sequence[AttemptLike]) -> FlakyStatus:
    outcomes = [_normalize_outcome(attempt) for attempt in attempts]
    if not outcomes:
        raise ValueError("attempts must not be empty")

    if outcomes == [AttemptOutcome.PASSED]:
        return FlakyStatus.PASSED

    if len(outcomes) == 1:
        return FlakyStatus.FAILED

    if outcomes[-1] == AttemptOutcome.PASSED:
        return FlakyStatus.RETRY_PASSED

    return FlakyStatus.RETRY_FAILED


def _normalize_outcome(attempt: AttemptLike) -> AttemptOutcome:
    if isinstance(attempt, AttemptResult):
        return attempt.outcome

    try:
        return AttemptOutcome(attempt)
    except ValueError as error:
        raise ValueError(f"unsupported attempt outcome: {attempt!r}") from error
