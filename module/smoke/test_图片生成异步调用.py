from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from jsonpath_ng.ext import parse
import pytest
import requests

from module.smoke import SmokeAssertions, SmokeRequest, SmokeTask
from module.smoke.task import B_ACCOUNT_API_KEY, B_ACCOUNT_CONTROL_KEY


IMAGE_URL_TIMEOUT_SECONDS = 30
ASYNC_IMAGE_POLL_INTERVAL_SECONDS = 2
ASYNC_IMAGE_POLL_TIMEOUT_SECONDS = 600
ASYNC_IMAGE_TASK_STATUS_VALUES = {"queued", "pending", "processing", "running", "succeeded", "success", "failed"}
ASYNC_IMAGE_SUCCESS_STATUS_VALUES = {"succeeded", "success"}
FORBIDDEN_ERROR_RESPONSE_TEXT_VALUES = ["traceback", "stack trace", "exception", "sql", "internal server error"]


class TestAsyncImageGeneration:
    def setup_method(self):
        self.smoke_request = SmokeRequest()
        self.smoke_assertions = SmokeAssertions()
        self.smoke_task = SmokeTask()

    def teardown_method(self):
        self.smoke_request.close()

    def test_f8_07_async_image_generation_submit_returns_task_id(self):
        create_response = self.smoke_task.create_async_image_generation(self.smoke_request)

        assert create_response.status_code in (200, 202), (
            f"Expected 200 or 202, actual: {create_response.status_code}. "
            f"Response body: {create_response.text}"
        )
        task_id = self._extract_task_id(create_response)
        assert task_id, f"Async image generation response should contain task_id. Response body: {create_response.text}"
        self._assert_task_status_if_present(create_response)

    def test_f8_08_async_image_generation_task_status_query(self):
        create_response = self.smoke_task.create_async_image_generation(self.smoke_request)
        task_id = self._extract_task_id(create_response)

        task_response = self.smoke_task.get_media_generation_task(self.smoke_request, task_id)

        self.smoke_assertions.assert_status_code(task_response, 200)
        status = self._extract_status(task_response)
        assert status in ASYNC_IMAGE_TASK_STATUS_VALUES, (
            f"Unexpected async task status: {status!r}. Response body: {task_response.text}"
        )

    def test_f8_09_async_image_generation_task_succeeds_with_result(self):
        create_response = self.smoke_task.create_async_image_generation(self.smoke_request)
        task_id = self._extract_task_id(create_response)

        result_response = self._poll_task_until_finished(task_id)

        status = self._extract_status(result_response)
        assert status in ASYNC_IMAGE_SUCCESS_STATUS_VALUES, (
            f"Async image generation task should succeed, actual status: {status!r}. "
            f"Response body: {result_response.text}"
        )
        self._assert_async_image_output_exists(result_response)

    def test_f8_10_async_image_generation_billing_deduction_matches_usage_quota(self):
        before_balance_response = self.smoke_task.query_account_balance_for_billing(self.smoke_request)
        create_response = self.smoke_task.create_async_image_generation(self.smoke_request)
        task_id = self._extract_task_id(create_response)
        unfinished_balance_response = self.smoke_task.query_account_balance_for_billing(self.smoke_request)
        result_response = self._poll_task_until_finished(task_id)

        status = self._extract_status(result_response)
        assert status in ASYNC_IMAGE_SUCCESS_STATUS_VALUES, (
            f"Async image generation task should succeed before billing assertion, actual status: {status!r}. "
            f"Response body: {result_response.text}"
        )
        usage_records_response = self.smoke_task.query_usage_records_by_request_id_for_billing(
            self.smoke_request,
            self._extract_request_id(create_response),
        )
        after_balance_response = self.smoke_task.query_account_balance_after_settlement_for_billing(
            self.smoke_request,
        )

        self.smoke_assertions.assert_total_balance_unchanged(
            before_balance_response,
            unfinished_balance_response,
        )
        self.smoke_assertions.assert_call_billing_deduction_matches(
            before_balance_response,
            usage_records_response,
            after_balance_response,
        )

    @pytest.mark.skip(reason="F8-11 缺少稳定生成 failed 任务的异步触发参数，先占位。")
    def test_f8_11_async_image_generation_failed_task_does_not_deduct_balance(self):
        pass

    @pytest.mark.skip(reason="F8-12 依赖 F8-11 的稳定 failed 任务，先占位。")
    def test_f8_12_async_image_generation_failed_task_response_body_contains_error(self):
        pass

    @pytest.mark.skip(reason="F8-13 需要稳定长时间未完成或服务端 timeout 触发方式，先占位。")
    def test_f8_13_async_image_generation_timeout_task_response_body(self):
        pass

    def test_f8_14_async_image_generation_result_image_url_is_accessible(self):
        create_response = self.smoke_task.create_async_image_generation(self.smoke_request)
        task_id = self._extract_task_id(create_response)
        result_response = self._poll_task_until_finished(task_id)

        status = self._extract_status(result_response)
        assert status in ASYNC_IMAGE_SUCCESS_STATUS_VALUES, (
            f"Async image generation task should succeed, actual status: {status!r}. "
            f"Response body: {result_response.text}"
        )
        image_url = self._extract_image_url(result_response)
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

    def test_f8_15_async_image_generation_task_id_is_unique(self):
        task_ids = []

        for _ in range(3):
            create_response = self.smoke_task.create_async_image_generation(self.smoke_request)
            task_ids.append(self._extract_task_id(create_response))

        assert len(set(task_ids)) == 3, f"Async task IDs should be unique, actual: {task_ids!r}"

    def test_f8_16_async_image_generation_concurrent_submit_returns_independent_task_ids(self):
        task_ids = self._submit_concurrent_async_image_tasks(5)

        assert len(set(task_ids)) == 5, f"Concurrent async task IDs should be unique, actual: {task_ids!r}"

    def test_f8_19_query_nonexistent_async_task_id_returns_not_found(self):
        nonexistent_task_id = f"not-exist-{uuid.uuid4().hex}"

        response = self.smoke_task.get_media_generation_task(self.smoke_request, nonexistent_task_id)

        self.smoke_assertions.assert_status_code(response, 404)
        self._assert_error_response_body_exists(response)
        assert "task not found" in response.text, (
            f"Nonexistent async task response should contain '任务不存在'. Response body: {response.text}"
        )
        self._assert_response_text_not_contains_internal_information(response)

    def test_f8_20_async_task_cross_account_isolation(self):
        if not B_ACCOUNT_API_KEY.strip() or not B_ACCOUNT_CONTROL_KEY.strip():
            pytest.skip("Please configure B account API key and control key in test_response_body_validation.py first.")

        create_response = self.smoke_task.create_async_image_generation(self.smoke_request)
        task_id = self._extract_task_id(create_response)
        b_account_request = SmokeRequest()

        try:
            b_account_request.set_header("Authorization", f"Bearer {B_ACCOUNT_API_KEY}")
            response = self.smoke_task.get_media_generation_task(b_account_request, task_id)
        finally:
            b_account_request.close()

        assert response.status_code in (403, 404), (
            f"Expected 403 or 404 when querying another account's async task, "
            f"actual: {response.status_code}. Response body: {response.text}"
        )
        self._assert_error_response_body_exists(response)
        self._assert_response_text_not_contains_internal_information(response)

    def _poll_task_until_finished(self, task_id: str) -> requests.Response:
        return self.smoke_task.poll_media_generation_result(
            self.smoke_request,
            task_id,
            poll_interval=ASYNC_IMAGE_POLL_INTERVAL_SECONDS,
            poll_timeout=ASYNC_IMAGE_POLL_TIMEOUT_SECONDS,
        )

    @staticmethod
    def _submit_concurrent_async_image_tasks(task_count: int) -> list[str]:
        task_ids_by_index: dict[int, str] = {}

        with ThreadPoolExecutor(max_workers=task_count) as executor:
            future_to_index = {
                executor.submit(TestAsyncImageGeneration._submit_async_image_task): index
                for index in range(task_count)
            }
            for future in as_completed(future_to_index):
                index = future_to_index[future]
                task_ids_by_index[index] = future.result()

        return [task_ids_by_index[index] for index in range(task_count)]

    @staticmethod
    def _submit_async_image_task() -> str:
        smoke_request = SmokeRequest()
        smoke_task = SmokeTask()
        try:
            create_response = smoke_task.create_async_image_generation(smoke_request)
            return TestAsyncImageGeneration._extract_task_id(create_response)
        finally:
            smoke_request.close()

    @staticmethod
    def _extract_task_id(response: requests.Response) -> str:
        body = TestAsyncImageGeneration._get_json_body(response)
        task_id = (
            TestAsyncImageGeneration._extract_first_json_path_value(body, "$.task_id")
            or TestAsyncImageGeneration._extract_first_json_path_value(body, "$.id")
            or TestAsyncImageGeneration._extract_first_json_path_value(body, "$.request_id")
        )
        assert task_id, f"Response should contain task_id, id, or request_id. Response body: {response.text}"
        return str(task_id)

    @staticmethod
    def _extract_request_id(response: requests.Response) -> str:
        request_id = response.headers.get("x-oneapi-request-id", "").strip()
        if request_id:
            return request_id

        body = TestAsyncImageGeneration._get_json_body(response)
        body_request_id = (
            TestAsyncImageGeneration._extract_first_json_path_value(body, "$.request_id")
            or TestAsyncImageGeneration._extract_first_json_path_value(body, "$.id")
        )
        assert body_request_id, (
            "Async image generation response should contain request id in header or body. "
            f"Response headers: {dict(response.headers)}. Response body: {response.text}"
        )
        return str(body_request_id)

    @staticmethod
    def _extract_status(response: requests.Response) -> str:
        body = TestAsyncImageGeneration._get_json_body(response)
        status = TestAsyncImageGeneration._extract_first_json_path_value(body, "$.status")
        assert status, f"Async task response should contain status. Response body: {response.text}"
        return str(status)

    @staticmethod
    def _assert_task_status_if_present(response: requests.Response) -> None:
        body = TestAsyncImageGeneration._get_json_body(response)
        status = TestAsyncImageGeneration._extract_first_json_path_value(body, "$.status")
        if status is None:
            return
        assert str(status) in ASYNC_IMAGE_TASK_STATUS_VALUES, (
            f"Unexpected async task status: {status!r}. Response body: {response.text}"
        )

    @staticmethod
    def _assert_error_response_body_exists(response: requests.Response) -> None:
        body = TestAsyncImageGeneration._get_json_body(response)
        error = TestAsyncImageGeneration._extract_first_json_path_value(body, "$.error")
        message = (
            TestAsyncImageGeneration._extract_first_json_path_value(body, "$.error.message")
            or TestAsyncImageGeneration._extract_first_json_path_value(body, "$.message")
            or TestAsyncImageGeneration._extract_first_json_path_value(body, "$.error_message")
        )

        assert error is not None or message is not None, (
            f"Error response should contain error object or message. Response body: {response.text}"
        )

    @staticmethod
    def _assert_response_text_not_contains_internal_information(response: requests.Response) -> None:
        response_text = response.text.lower()
        found_values = [
            value for value in FORBIDDEN_ERROR_RESPONSE_TEXT_VALUES
            if value in response_text
        ]
        assert not found_values, (
            f"Error response should not leak internal information: {found_values!r}. "
            f"Response body: {response.text}"
        )

    @staticmethod
    def _assert_async_image_output_exists(response: requests.Response) -> None:
        body = TestAsyncImageGeneration._get_json_body(response)
        image_url = (
            TestAsyncImageGeneration._extract_first_json_path_value(body, "$.result.urls[0]")
            or TestAsyncImageGeneration._extract_first_json_path_value(body, "$.result.url")
            or TestAsyncImageGeneration._extract_first_json_path_value(body, "$.data[0].url")
        )
        b64_json = (
            TestAsyncImageGeneration._extract_first_json_path_value(body, "$.result.b64_json")
            or TestAsyncImageGeneration._extract_first_json_path_value(body, "$.data[0].b64_json")
        )

        assert image_url or b64_json, (
            "Async image generation result should contain image URL or base64 data. "
            f"Response body: {response.text}"
        )

    @staticmethod
    def _extract_image_url(response: requests.Response) -> str:
        body = TestAsyncImageGeneration._get_json_body(response)
        image_url = (
            TestAsyncImageGeneration._extract_first_json_path_value(body, "$.result.urls[0]")
            or TestAsyncImageGeneration._extract_first_json_path_value(body, "$.result.url")
            or TestAsyncImageGeneration._extract_first_json_path_value(body, "$.data[0].url")
        )
        assert image_url, f"Async image generation result did not contain image URL. Response body: {response.text}"
        return str(image_url)

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
