from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

import requests

from common import BaseAssertions, allure_step


class SmokeAssertions(BaseAssertions):
    SUCCESSFUL_USAGE_STATUSES = frozenset({"success", "succeeded", "completed"})

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

    def assert_successful_usage_record(
        self,
        usage_records_response: requests.Response,
        *,
        expected_request_id: str,
    ) -> requests.Response:
        self.assert_status_code(usage_records_response, 200)
        data = self._get_usage_record_data(usage_records_response)
        self._assert_usage_request_id(data, expected_request_id, usage_records_response)

        status = str(data.get("status", "")).strip().lower()
        assert status in self.SUCCESSFUL_USAGE_STATUSES, (
            "Successful model call should have a successful usage record: "
            f"expected one of {sorted(self.SUCCESSFUL_USAGE_STATUSES)!r}, actual {status!r}. "
            f"Response body: {usage_records_response.text}"
        )

        usage_quota = self.get_usage_quota_yuan(usage_records_response)
        assert usage_quota > Decimal("0"), (
            "Successful model call should produce a positive request-scoped charge: "
            f"actual data.quota_yuan={usage_quota}. "
            f"Response body: {usage_records_response.text}"
        )

        print(f"usage request_id: {expected_request_id}")
        print(f"usage data.quota_yuan: {usage_quota}")
        return usage_records_response

    def assert_usage_record_not_charged(
        self,
        usage_records_response: requests.Response,
        *,
        expected_request_id: str,
    ) -> requests.Response:
        self.assert_status_code(usage_records_response, 200)
        data = self._get_usage_record_data(usage_records_response)
        self._assert_usage_request_id(data, expected_request_id, usage_records_response)

        usage_quota = self.get_usage_quota_yuan(usage_records_response)
        assert usage_quota == Decimal("0"), (
            "Failed model call should not produce a request-scoped charge: "
            f"actual data.quota_yuan={usage_quota}. "
            f"Response body: {usage_records_response.text}"
        )

        print(f"usage request_id: {expected_request_id}")
        print(f"usage data.quota_yuan: {usage_quota}")
        return usage_records_response

    def get_total_balance_yuan(self, response: requests.Response) -> Decimal:
        response_body = response.json()
        return self._to_decimal(response_body["data"]["total_balance_yuan"], "data.total_balance_yuan")

    def get_usage_quota_yuan(self, response: requests.Response) -> Decimal:
        data = self._get_usage_record_data(response)
        return self._to_decimal(data["quota_yuan"], "data.quota_yuan")

    def print_glm5_actual_stream_cost(self, response: requests.Response) -> requests.Response:
        quota_yuan = self.get_usage_quota_yuan(response)
        print(f"GLM-5 usage data.quota_yuan: {quota_yuan}")
        assert quota_yuan >= Decimal("0"), f"data.quota_yuan should be non-negative. Response body: {response.text}"
        return response

    @staticmethod
    def _get_usage_record_data(response: requests.Response) -> dict[str, Any]:
        response_body = response.json()
        data = response_body.get("data") if isinstance(response_body, dict) else None
        assert isinstance(data, dict), (
            "Usage response data should be an object. "
            f"Response body: {response.text}"
        )
        return data

    @staticmethod
    def _assert_usage_request_id(
        data: dict[str, Any],
        expected_request_id: str,
        response: requests.Response,
    ) -> None:
        actual_request_id = str(data.get("request_id", "")).strip()
        assert actual_request_id == expected_request_id, (
            "Usage record request_id mismatch: "
            f"expected {expected_request_id!r}, actual {actual_request_id!r}. "
            f"Response body: {response.text}"
        )

    @staticmethod
    def _to_decimal(value: Any, field_path: str) -> Decimal:
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError) as exc:
            raise AssertionError(f"{field_path} is not a valid decimal value: {value!r}") from exc
