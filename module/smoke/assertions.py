from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

import requests

from common import BaseAssertions, allure_step


class SmokeAssertions(BaseAssertions):
    BILLING_AMOUNT_TOLERANCE = Decimal("0.01")

    @allure_step("Assert response text does not contain forbidden values")
    def assert_response_text_not_contains(
        self,
        response: requests.Response,
        forbidden_values: list[str] | tuple[str, ...],
    ) -> requests.Response:
        response_text = response.text
        found_values = [value for value in forbidden_values if value in response_text]
        assert not found_values, (
            f"Response text contains forbidden values: {found_values!r}. "
            f"Response body: {response_text}"
        )
        return response

    def assert_non_negative_total_balance(self, response: requests.Response) -> requests.Response:
        total_balance = self.get_total_balance_yuan(response)
        print(f"data.total_balance_yuan: {total_balance}")
        assert total_balance >= Decimal("0"), (
            f"data.total_balance_yuan should be non-negative, actual: {total_balance!r}. "
            f"Response body: {response.text}"
        )
        return response

    def assert_call_billing_deduction_matches(
        self,
        before_balance_response: requests.Response,
        usage_records_response: requests.Response,
        after_balance_response: requests.Response,
    ) -> requests.Response:
        before_balance = self.get_total_balance_yuan(before_balance_response)
        usage_quota = self.get_usage_quota_yuan(usage_records_response)
        after_balance = self.get_total_balance_yuan(after_balance_response)
        actual_deduction = before_balance - after_balance
        deduction_delta = self._billing_amount_delta(actual_deduction, usage_quota)

        print(f"before data.total_balance_yuan: {before_balance}")
        print(f"usage data.quota_yuan: {usage_quota}")
        print(f"after data.total_balance_yuan: {after_balance}")
        print(f"actual deduction: {actual_deduction}")
        print(f"deduction delta: {deduction_delta}")

        assert deduction_delta <= self.BILLING_AMOUNT_TOLERANCE, (
            "Call billing deduction mismatch: "
            f"before balance {before_balance} - after balance {after_balance} = {actual_deduction}, "
            f"but usage data.quota_yuan = {usage_quota}. "
            f"Allowed delta is +/-{self.BILLING_AMOUNT_TOLERANCE}, actual delta is {deduction_delta}. "
            f"Before balance response: {before_balance_response.text}; "
            f"Usage records response: {usage_records_response.text}; "
            f"After balance response: {after_balance_response.text}"
        )
        return usage_records_response

    def assert_total_billing_deduction_matches_usage_quota_sum(
        self,
        before_balance_response: requests.Response,
        usage_records_responses: list[requests.Response],
        after_balance_response: requests.Response,
    ) -> requests.Response:
        before_balance = self.get_total_balance_yuan(before_balance_response)
        usage_quota_sum = sum(
            (self.get_usage_quota_yuan(response) for response in usage_records_responses),
            Decimal("0"),
        )
        after_balance = self.get_total_balance_yuan(after_balance_response)
        actual_deduction = before_balance - after_balance
        deduction_delta = self._billing_amount_delta(actual_deduction, usage_quota_sum)

        print(f"before data.total_balance_yuan: {before_balance}")
        print(f"usage data.quota_yuan sum: {usage_quota_sum}")
        print(f"after data.total_balance_yuan: {after_balance}")
        print(f"actual deduction: {actual_deduction}")
        print(f"deduction delta: {deduction_delta}")

        assert deduction_delta <= self.BILLING_AMOUNT_TOLERANCE, (
            "Concurrent call billing deduction mismatch: "
            f"before balance {before_balance} - after balance {after_balance} = {actual_deduction}, "
            f"but usage data.quota_yuan sum = {usage_quota_sum}. "
            f"Allowed delta is +/-{self.BILLING_AMOUNT_TOLERANCE}, actual delta is {deduction_delta}. "
            f"Before balance response: {before_balance_response.text}; "
            f"Usage records responses: {[response.text for response in usage_records_responses]}; "
            f"After balance response: {after_balance_response.text}"
        )
        return after_balance_response

    def assert_total_balance_unchanged(
        self,
        before_balance_response: requests.Response,
        after_balance_response: requests.Response,
    ) -> requests.Response:
        before_balance = self.get_total_balance_yuan(before_balance_response)
        after_balance = self.get_total_balance_yuan(after_balance_response)

        print(f"before data.total_balance_yuan: {before_balance}")
        print(f"after data.total_balance_yuan: {after_balance}")

        assert before_balance == after_balance, (
            "Account balance changed after failed model call: "
            f"before balance {before_balance}, after balance {after_balance}. "
            f"Before balance response: {before_balance_response.text}; "
            f"After balance response: {after_balance_response.text}"
        )
        return after_balance_response

    def get_total_balance_yuan(self, response: requests.Response) -> Decimal:
        response_body = response.json()
        return self._to_decimal(response_body["data"]["total_balance_yuan"], "data.total_balance_yuan")

    def get_usage_quota_yuan(self, response: requests.Response) -> Decimal:
        response_body = response.json()
        return self._to_decimal(response_body["data"]["quota_yuan"], "data.quota_yuan")

    def print_glm5_actual_stream_cost(self, response: requests.Response) -> requests.Response:
        quota_yuan = self.get_usage_quota_yuan(response)
        print(f"GLM-5 usage data.quota_yuan: {quota_yuan}")
        assert quota_yuan >= Decimal("0"), f"data.quota_yuan should be non-negative. Response body: {response.text}"
        return response

    @staticmethod
    def _to_decimal(value: Any, field_path: str) -> Decimal:
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError) as exc:
            raise AssertionError(f"{field_path} is not a valid decimal value: {value!r}") from exc

    @staticmethod
    def _billing_amount_delta(actual_amount: Decimal, expected_amount: Decimal) -> Decimal:
        return abs(actual_amount - expected_amount)
