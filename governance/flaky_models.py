from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class AttemptOutcome(StrEnum):
    PASSED = "passed"
    FAILED = "failed"


class FlakyStatus(StrEnum):
    PASSED = "passed"
    RETRY_PASSED = "retry_passed"
    RETRY_FAILED = "retry_failed"
    FAILED = "failed"


@dataclass(frozen=True)
class AttemptResult:
    index: int
    outcome: AttemptOutcome
    duration: float = 0.0
    failure_type: str | None = None
    failure_message: str | None = None


@dataclass(frozen=True)
class FlakyTestResult:
    nodeid: str
    status: FlakyStatus
    attempts: tuple[AttemptResult, ...] = field(default_factory=tuple)
    total_duration: float = 0.0

    @property
    def attempt_count(self) -> int:
        return len(self.attempts)
