from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest

from module.smoke import SmokeAssertions, SmokeRequest, SmokeTask


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
        before_balance_response = self.smoke_task.query_account_balance_for_billing(self.smoke_request)
        image_response = self.smoke_task.create_sync_image_generation_for_billing(self.smoke_request)
        usage_records_response = self.smoke_task.query_usage_records_by_model_response_for_billing(
            self.smoke_request,
            image_response,
        )
        after_balance_response = self.smoke_task.query_account_balance_for_billing(self.smoke_request)

        self.smoke_assertions.assert_call_billing_deduction_matches(
            before_balance_response,
            usage_records_response,
            after_balance_response,
        )

    def test_text_model_call_billing_deduction_matches_usage_quota(self):
        before_balance_response = self.smoke_task.query_account_balance_for_billing(self.smoke_request)
        chat_response = self.smoke_task.create_chat_completion_for_billing(self.smoke_request)
        usage_records_response = self.smoke_task.query_usage_records_by_model_response_for_billing(
            self.smoke_request,
            chat_response,
        )
        after_balance_response = self.smoke_task.query_account_balance_for_billing(self.smoke_request)

        self.smoke_assertions.assert_call_billing_deduction_matches(
            before_balance_response,
            usage_records_response,
            after_balance_response,
        )

    def test_concurrent_text_model_call_billing_deduction_matches_usage_quota_sum(self):
        before_balance_response = self.smoke_task.query_account_balance_for_billing(self.smoke_request)

        request_ids = self._create_concurrent_text_model_calls(CONCURRENT_TEXT_MODEL_CALL_COUNT)
        usage_records_responses = [
            self.smoke_task.query_usage_records_by_request_id_for_billing(
                self.smoke_request,
                request_id,
            )
            for request_id in request_ids
        ]
        after_balance_response = self.smoke_task.query_account_balance_for_billing(self.smoke_request)

        for index, usage_records_response in enumerate(usage_records_responses, start=1):
            usage_quota = self.smoke_assertions.get_usage_quota_yuan(usage_records_response)
            print(f"concurrent text call {index} request_id: {request_ids[index - 1]}")
            print(f"concurrent text call {index} data.quota_yuan: {usage_quota}")

        self.smoke_assertions.assert_total_billing_deduction_matches_usage_quota_sum(
            before_balance_response,
            usage_records_responses,
            after_balance_response,
        )
    @pytest.mark.skip("余额计算方式精度不足，自动化用例可信度不足")
    def test_stream_text_model_outputs_total_tokens(self):
        before_balance_response = self.smoke_task.query_account_balance_for_billing(self.smoke_request)
        response = self.smoke_task.create_stream_chat_completion(self.smoke_request)

        self.smoke_assertions.assert_status_code(response, 200)
        stream_result = self.smoke_task.interrupt_stream_chat_completion(
            response,
            max_duration_seconds=50,
            print_raw_lines=False,
        )
        usage_records_response = self.smoke_task.query_usage_records_by_request_id_for_billing(
            self.smoke_request,
            stream_result.request_id,
        )
        self.smoke_assertions.print_glm5_actual_stream_cost(usage_records_response)
        after_balance_response = self.smoke_task.query_account_balance_for_billing(self.smoke_request)

        self.smoke_assertions.assert_call_billing_deduction_matches(
            before_balance_response,
            usage_records_response,
            after_balance_response,
        )

    def test_failed_sync_image_model_call_does_not_deduct_balance(self):
        before_balance_response = self.smoke_task.query_account_balance_for_billing(self.smoke_request)
        failed_response = self.smoke_task.create_image_generation(
            self.smoke_request,
            self.smoke_task.build_sync_image_generation_payload(UNKNOWN_IMAGE_MODEL_ID),
        )
        after_balance_response = self.smoke_task.query_account_balance_for_billing(self.smoke_request)

        self.smoke_assertions.assert_status_code(failed_response, 404)
        self.smoke_assertions.assert_json_value(failed_response, "$.error.code", "model_not_found")
        self.smoke_assertions.assert_json_value(failed_response, "$.error.type", "invalid_request_error")
        self.smoke_assertions.assert_total_balance_unchanged(
            before_balance_response,
            after_balance_response,
        )

    @staticmethod
    def _create_concurrent_text_model_calls(call_count: int) -> list[str]:
        request_ids_by_index: dict[int, str] = {}

        with ThreadPoolExecutor(max_workers=call_count) as executor:
            future_to_index = {
                executor.submit(TestCallBillingCorrectness._create_text_model_call_for_billing): index
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
            chat_response = smoke_task.create_chat_completion_for_billing(smoke_request)
            smoke_assertions.assert_status_code(chat_response, 200)
            return chat_response.headers["x-oneapi-request-id"].strip()
        finally:
            smoke_request.close()
