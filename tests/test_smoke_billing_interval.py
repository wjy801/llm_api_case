from __future__ import annotations

import pytest

from module.smoke import SmokeAssertions
from tests.mock_helpers import make_response


@pytest.mark.parametrize("after_balance", ["98.76", "98.78"])
def test_billing_deduction_accepts_both_tolerance_boundaries(after_balance):
    assertions = SmokeAssertions()

    assertions.assert_call_billing_deduction_matches(
        _balance_response("100.00"),
        _usage_response("1.23"),
        _balance_response(after_balance),
    )


@pytest.mark.parametrize("after_balance", ["98.75", "98.79"])
def test_billing_deduction_rejects_both_sides_outside_tolerance(after_balance):
    assertions = SmokeAssertions()

    with pytest.raises(AssertionError, match="Allowed delta is \\+/-0.01"):
        assertions.assert_call_billing_deduction_matches(
            _balance_response("100.00"),
            _usage_response("1.23"),
            _balance_response(after_balance),
        )


def test_concurrent_billing_sum_uses_same_tolerance():
    assertions = SmokeAssertions()

    assertions.assert_total_billing_deduction_matches_usage_quota_sum(
        _balance_response("100.00"),
        [_usage_response("1.20"), _usage_response("2.30")],
        _balance_response("96.49"),
    )


def _balance_response(total_balance_yuan: str):
    return make_response(
        "https://example.com/v1/account/balance",
        json_body={"data": {"total_balance_yuan": total_balance_yuan}},
    )


def _usage_response(quota_yuan: str):
    return make_response(
        "https://example.com/v1/account/usage-records",
        json_body={"data": {"quota_yuan": quota_yuan}},
    )
