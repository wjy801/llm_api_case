from __future__ import annotations

from decimal import Decimal
from typing import Any

from jsonpath_ng.ext import parse
import pytest
import requests

from module.smoke import SmokeAssertions, SmokeRequest, SmokeTask


IMAGE_URL_TIMEOUT_SECONDS = 30
pytestmark = pytest.mark.serial


class TestSyncImageGeneration:
    def setup_method(self):
        self.smoke_request = SmokeRequest()
        self.smoke_assertions = SmokeAssertions()
        self.smoke_task = SmokeTask()

    def teardown_method(self):
        self.smoke_request.close()

    def test_f8_01_sync_image_generation_returns_success(self):
        response = self.smoke_task.create_image_generation(
            self.smoke_request,
            self.smoke_task.build_sync_image_generation_payload(),
        )

        self.smoke_assertions.assert_status_code(response, 200)
        self._assert_image_generation_output_exists(response)

    @pytest.mark.xfail("海外环境图片模型响应体返回格式不准确")
    def test_f8_02_sync_image_generation_response_body_integrity(self):
        response = self.smoke_task.create_image_generation(
            self.smoke_request,
            self.smoke_task.build_sync_image_generation_payload(),
        )

        self.smoke_assertions.assert_status_code(response, 200)
        self.smoke_assertions.assert_json_path_exists(response, "$.created")
        self.smoke_assertions.assert_json_path_exists(response, "$.data")
        self._assert_optional_json_path_exists(response, "$.id")
        self._assert_optional_json_path_exists(response, "$.model")
        self._assert_optional_json_path_exists(response, "$.usage")
        self._assert_image_generation_output_exists(response)

    def test_f8_03_sync_image_generation_billing_deduction_matches_usage_quota(self):
        before_balance_response = self.smoke_task.query_account_balance_for_billing(self.smoke_request)
        image_response = self.smoke_task.create_sync_image_generation_for_billing(self.smoke_request)
        usage_records_response = self.smoke_task.query_usage_records_by_model_response_for_billing(
            self.smoke_request,
            image_response,
        )
        after_balance_response = self.smoke_task.query_account_balance_after_settlement_for_billing(
            self.smoke_request,
        )

        self.smoke_assertions.assert_call_billing_deduction_matches(
            before_balance_response,
            usage_records_response,
            after_balance_response,
        )

    def test_f8_04_sync_image_generation_failed_call_does_not_deduct_balance(self):
        before_balance_response = self.smoke_task.query_account_balance_for_billing(self.smoke_request)
        payload = self._build_invalid_prompt_payload()
        failed_response = self.smoke_task.create_image_generation(self.smoke_request, payload)
        after_balance_response = self.smoke_task.query_account_balance_after_settlement_for_billing(
            self.smoke_request,
        )

        assert 400 <= failed_response.status_code < 500, (
            f"Expected 4xx status code, actual: {failed_response.status_code}. "
            f"Response body: {failed_response.text}"
        )
        self.smoke_assertions.assert_json_path_exists(failed_response, "$.error")
        self.smoke_assertions.assert_json_path_exists(failed_response, "$.error.message")
        self.smoke_assertions.assert_total_balance_unchanged(
            before_balance_response,
            after_balance_response,
        )

        request_id = failed_response.headers.get("x-oneapi-request-id", "").strip()
        if request_id:
            usage_records_response = self.smoke_task.query_usage_records_by_request_id_for_billing(
                self.smoke_request,
                request_id,
            )
            usage_quota = self.smoke_assertions.get_usage_quota_yuan(usage_records_response)
            assert usage_quota == Decimal("0"), (
                f"Failed sync image generation should not charge, actual data.quota_yuan: {usage_quota}. "
                f"Usage response body: {usage_records_response.text}"
            )

    @pytest.mark.skip(reason="F8-05 暂无稳定服务端 504/超时触发方式，按确认结果先占位。")
    def test_f8_05_sync_image_generation_timeout_response_body(self):
        pass

    def test_f8_06_sync_image_generation_returned_image_url_is_accessible(self):
        response = self.smoke_task.create_image_generation(
            self.smoke_request,
            self.smoke_task.build_sync_image_generation_payload(),
        )

        self.smoke_assertions.assert_status_code(response, 200)
        image_url = self._extract_image_url(response)
        image_response = requests.get(image_url, timeout=IMAGE_URL_TIMEOUT_SECONDS)

        assert image_response.status_code == 200, (
            f"Image URL should be accessible, actual status code: {image_response.status_code}. "
            f"Image URL: {image_url}"
        )
        content_type = image_response.headers.get("Content-Type", "")
        assert content_type.startswith("image/"), (
            f"Image URL should return image content, actual Content-Type: {content_type!r}. "
            f"Image URL: {image_url}"
        )
        assert image_response.content, f"Image URL returned empty content. Image URL: {image_url}"

    def _build_invalid_prompt_payload(self) -> dict[str, Any]:
        payload = self.smoke_task.build_sync_image_generation_payload()
        payload["prompt"] = ""
        return payload

    def _assert_image_generation_output_exists(self, response: requests.Response) -> None:
        body = self._get_json_body(response)
        image_url = self._extract_first_json_path_value(body, "$.data[0].url")
        b64_json = self._extract_first_json_path_value(body, "$.data[0].b64_json")

        assert image_url or b64_json, (
            "Image generation response should contain image URL or base64 data. "
            f"Response body: {response.text}"
        )

    def _extract_image_url(self, response: requests.Response) -> str:
        body = self._get_json_body(response)
        image_url = self._extract_first_json_path_value(body, "$.data[0].url")
        assert image_url, f"Image generation response did not contain $.data[0].url. Response body: {response.text}"
        return str(image_url)

    @staticmethod
    def _assert_optional_json_path_exists(response: requests.Response, json_path: str) -> None:
        body = TestSyncImageGeneration._get_json_body(response)
        if TestSyncImageGeneration._extract_first_json_path_value(body, json_path) is None:
            print(f"optional response field missing: {json_path}")

    @staticmethod
    def _get_json_body(response: requests.Response) -> dict[str, Any]:
        try:
            body = response.json()
        except ValueError as exc:
            raise AssertionError(f"Response body is not valid JSON. Response body: {response.text}") from exc
        assert isinstance(body, dict), f"Response body should be JSON object, actual: {body!r}"
        return body

    @staticmethod
    def _extract_first_json_path_value(body: dict[str, Any], json_path: str) -> Any:
        matches = [match.value for match in parse(json_path).find(body)]
        if not matches:
            return None
        return matches[0]
