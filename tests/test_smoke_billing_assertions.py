from __future__ import annotations

import pytest

from module.smoke import SmokeAssertions
from tests.mock_helpers import make_response


def test_call_billing_deduction_allows_actual_deduction_inside_tolerance():
    assertions = SmokeAssertions()

    before_balance_response = _balance_response("100.00")
    usage_records_response = _usage_response("1.23")
    after_balance_response = _balance_response("98.775")

    assert (
        assertions.assert_call_billing_deduction_matches(
            before_balance_response,
            usage_records_response,
            after_balance_response,
        )
        is usage_records_response
    )


def test_call_billing_deduction_allows_exact_actual_deduction():
    assertions = SmokeAssertions()

    before_balance_response = _balance_response("100.00")
    usage_records_response = _usage_response("1.23")
    after_balance_response = _balance_response("98.77")

    assertions.assert_call_billing_deduction_matches(
        before_balance_response,
        usage_records_response,
        after_balance_response,
    )


def test_call_billing_deduction_allows_actual_deduction_at_tolerance_boundary():
    assertions = SmokeAssertions()

    before_balance_response = _balance_response("100.00")
    usage_records_response = _usage_response("1.23")
    after_balance_response = _balance_response("98.76")

    assertions.assert_call_billing_deduction_matches(
        before_balance_response,
        usage_records_response,
        after_balance_response,
    )


def test_call_billing_deduction_rejects_actual_deduction_outside_tolerance():
    assertions = SmokeAssertions()

    before_balance_response = _balance_response("100.00")
    usage_records_response = _usage_response("1.23")
    after_balance_response = _balance_response("98.75")

    with pytest.raises(AssertionError, match="Allowed delta is \\+/-0.01"):
        assertions.assert_call_billing_deduction_matches(
            before_balance_response,
            usage_records_response,
            after_balance_response,
        )


def test_total_billing_deduction_allows_usage_quota_sum_inside_tolerance():
    assertions = SmokeAssertions()

    before_balance_response = _balance_response("100.00")
    usage_records_responses = [_usage_response("1.20"), _usage_response("2.30")]
    after_balance_response = _balance_response("96.49")

    assert (
        assertions.assert_total_billing_deduction_matches_usage_quota_sum(
            before_balance_response,
            usage_records_responses,
            after_balance_response,
        )
        is after_balance_response
    )


def test_total_billing_deduction_rejects_usage_quota_sum_outside_tolerance():
    assertions = SmokeAssertions()

    before_balance_response = _balance_response("100.00")
    usage_records_responses = [_usage_response("1.20"), _usage_response("2.30")]
    after_balance_response = _balance_response("96.48")

    with pytest.raises(AssertionError, match="Allowed delta is \\+/-0.01"):
        assertions.assert_total_billing_deduction_matches_usage_quota_sum(
            before_balance_response,
            usage_records_responses,
            after_balance_response,
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
