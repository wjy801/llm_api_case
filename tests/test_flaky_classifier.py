from __future__ import annotations

import pytest

from governance.flaky_classifier import classify_attempts
from governance.flaky_models import AttemptOutcome, AttemptResult, FlakyStatus, FlakyTestResult


pytestmark = pytest.mark.flaky_governance


class TestClassifyAttempts:
    @pytest.mark.parametrize(
        ("attempts", "expected"),
        [
            (["passed"], FlakyStatus.PASSED),
            (["failed"], FlakyStatus.FAILED),
            (["failed", "passed"], FlakyStatus.RETRY_PASSED),
            (["failed", "failed"], FlakyStatus.RETRY_FAILED),
            (["failed", "failed", "passed"], FlakyStatus.RETRY_PASSED),
            (["failed", "failed", "failed"], FlakyStatus.RETRY_FAILED),
        ],
    )
    def test_classifies_outcome_sequences(self, attempts: list[str], expected: FlakyStatus):
        assert classify_attempts(attempts) == expected

    def test_accepts_attempt_result_models(self):
        attempts = [
            AttemptResult(index=1, outcome=AttemptOutcome.FAILED),
            AttemptResult(index=2, outcome=AttemptOutcome.PASSED),
        ]

        assert classify_attempts(attempts) == FlakyStatus.RETRY_PASSED

    def test_accepts_attempt_outcome_enum_values(self):
        assert classify_attempts([AttemptOutcome.PASSED]) == FlakyStatus.PASSED

    def test_requires_at_least_one_attempt(self):
        with pytest.raises(ValueError, match="attempts must not be empty"):
            classify_attempts([])

    def test_rejects_unknown_outcome(self):
        with pytest.raises(ValueError, match="unsupported attempt outcome"):
            classify_attempts(["skipped"])


class TestFlakyTestResult:
    def test_attempt_count_uses_attempts_length(self):
        result = FlakyTestResult(
            nodeid="module/test_demo.py::TestDemo::test_case",
            status=FlakyStatus.RETRY_FAILED,
            attempts=(
                AttemptResult(index=1, outcome=AttemptOutcome.FAILED),
                AttemptResult(index=2, outcome=AttemptOutcome.FAILED),
            ),
        )

        assert result.attempt_count == 2
