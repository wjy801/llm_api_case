from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest

from common import submit_with_context
from module.smoke import (
    CONCURRENT_CHAT_RETRY_POLICY,
    SMOKE_GET_RETRY_POLICY,
    SmokeAssertions,
    SmokeRequest,
    SmokeTask,
)


UNKNOWN_IMAGE_MODEL_ID = "wan2.7-image111"
CONCURRENT_TEXT_MODEL_CALL_COUNT = 5
pytestmark = pytest.mark.serial


class TestCallBillingCorrectness:
    def setup_method(self):
        self.smoke_request = SmokeRequest()
        self.smoke_assertions = SmokeAssertions()
        self.smoke_task = SmokeTask()

    def teardown_method(self):
        self.smoke_request.close()

    def test_sync_image_model_call_billing_deduction_matches_usage_quota(self):
        image_response = self.smoke_task.create_sync_image_generation_for_billing(self.smoke_request)
        request_id = self.smoke_task.get_request_id_from_response(image_response)
        usage_records_response = self.smoke_task.query_usage_records_by_request_id_for_billing(
            self.smoke_request,
            request_id,
            retry_policy=SMOKE_GET_RETRY_POLICY,
        )

        self.smoke_assertions.assert_successful_usage_record(
            usage_records_response,
            expected_request_id=request_id,
        )

    def test_text_model_call_billing_deduction_matches_usage_quota(self):
        chat_response = self.smoke_task.create_chat_completion_for_billing(self.smoke_request)
        request_id = self.smoke_task.get_request_id_from_response(chat_response)
        usage_records_response = self.smoke_task.query_usage_records_by_request_id_for_billing(
            self.smoke_request,
            request_id,
            retry_policy=SMOKE_GET_RETRY_POLICY,
        )

        self.smoke_assertions.assert_successful_usage_record(
            usage_records_response,
            expected_request_id=request_id,
        )

    def test_concurrent_text_model_call_billing_deduction_matches_usage_quota_sum(self):
        request_ids = self._create_concurrent_text_model_calls(CONCURRENT_TEXT_MODEL_CALL_COUNT)
        usage_records_responses = [
            self.smoke_task.query_usage_records_by_request_id_for_billing(
                self.smoke_request,
                request_id,
                retry_policy=SMOKE_GET_RETRY_POLICY,
            )
            for request_id in request_ids
        ]

        for request_id, usage_records_response in zip(
            request_ids,
            usage_records_responses,
            strict=True,
        ):
            self.smoke_assertions.assert_successful_usage_record(
                usage_records_response,
                expected_request_id=request_id,
            )

    def test_failed_sync_image_model_call_does_not_deduct_balance(self):
        failed_response = self.smoke_task.create_image_generation(
            self.smoke_request,
            self.smoke_task.build_sync_image_generation_payload(UNKNOWN_IMAGE_MODEL_ID),
        )

        self.smoke_assertions.assert_status_code(failed_response, 404)
        self.smoke_assertions.assert_json_value(failed_response, "$.error.code", "model_not_found")
        self.smoke_assertions.assert_json_value(failed_response, "$.error.type", "invalid_request_error")
        request_id = self.smoke_task.get_request_id_from_response(failed_response)
        usage_records_response = self.smoke_task.query_usage_records_by_request_id_for_billing(
            self.smoke_request,
            request_id,
            retry_policy=SMOKE_GET_RETRY_POLICY,
        )
        self.smoke_assertions.assert_usage_record_not_charged(
            usage_records_response,
            expected_request_id=request_id,
        )

    @staticmethod
    def _create_concurrent_text_model_calls(call_count: int) -> list[str]:
        request_ids_by_index: dict[int, str] = {}

        with ThreadPoolExecutor(max_workers=call_count) as executor:
            future_to_index = {
                submit_with_context(
                    executor,
                    TestCallBillingCorrectness._create_text_model_call_for_billing,
                ): index
                for index in range(call_count)
            }
            for future in as_completed(future_to_index):
                index = future_to_index[future]
                request_ids_by_index[index] = future.result()

        return [request_ids_by_index[index] for index in range(call_count)]

    @staticmethod
    def _create_text_model_call_for_billing() -> str:
        smoke_request = SmokeRequest()
        smoke_task = SmokeTask()
        smoke_assertions = SmokeAssertions()
        try:
            chat_response = smoke_task.create_chat_completion_for_billing(
                smoke_request,
                retry_policy=CONCURRENT_CHAT_RETRY_POLICY,
            )
            smoke_assertions.assert_status_code(chat_response, 200)
            return chat_response.headers["x-oneapi-request-id"].strip()
        finally:
            smoke_request.close()
