from __future__ import annotations

import os

import pytest

from module.smoke import SmokeAssertions, SmokeRequest, SmokeTask


UNKNOWN_TEXT_MODEL_ID = "GLM-5-unknown"
ZERO_BALANCE_API_KEY = os.getenv("OVERSEAS_ZERO_BALANCE_API_KEY", "").strip()
ZERO_BALANCE_CONTROL_KEY = os.getenv("OVERSEAS_ZERO_BALANCE_CONTROL_KEY", "").strip()


class TestResponseBodyValidation:
    def setup_method(self):
        self.smoke_request = SmokeRequest()
        self.smoke_assertions = SmokeAssertions()
        self.smoke_task = SmokeTask()

    def teardown_method(self):
        self.smoke_request.close()

    def test_sync_image_generation_response_body(self):
        response = self.smoke_task.create_image_generation(
            self.smoke_request,
            self.smoke_task.build_sync_image_generation_payload(),
        )

        self.smoke_assertions.assert_status_code(response, 200)
        self.smoke_assertions.assert_json_path_exists(response, "$.created")
        self.smoke_assertions.assert_json_path_exists(response, "$.data[0].url")

    def test_chat_completions_response_body(self):
        response = self.smoke_task.create_chat_completion(
            self.smoke_request,
            self.smoke_task.build_chat_completions_payload(),
        )

        self.smoke_assertions.assert_status_code(response, 200)
        self.smoke_assertions.assert_json_value(response, "$.model", "glm-5")
        self.smoke_assertions.assert_json_path_exists(response, "$.id")
        self.smoke_assertions.assert_json_value(response, "$.object", "chat.completion")
        self.smoke_assertions.assert_json_path_exists(response, "$.choices[0].message")
        self.smoke_assertions.assert_json_path_exists(response, "$.usage.prompt_tokens")
        self.smoke_assertions.assert_json_path_exists(response, "$.usage.total_tokens")
        self.smoke_assertions.assert_json_path_exists(response, "$.usage.completion_tokens")

    def test_stream_chat_completions_chunk_fields(self):
        response = self.smoke_task.create_small_stream_chat_completion(self.smoke_request)

        self.smoke_assertions.assert_status_code(response, 200)
        stream_chunks = self.smoke_task.collect_stream_chat_completion_chunks(response)
        chunks = stream_chunks.chunks

        for chunk in chunks:
            assert isinstance(chunk, dict), f"Stream chunk should be JSON object, actual: {chunk!r}"
            assert "id" in chunk, f"Stream chunk missing id: {chunk!r}"
            assert "object" in chunk, f"Stream chunk missing object: {chunk!r}"
            assert "created" in chunk, f"Stream chunk missing created: {chunk!r}"
            assert "model" in chunk, f"Stream chunk missing model: {chunk!r}"
            assert "choices" in chunk, f"Stream chunk missing choices: {chunk!r}"

        first_chunk = chunks[0]
        assert first_chunk["choices"][0]["delta"]["role"] == "assistant", (
            f"First stream chunk should contain assistant role, actual: {first_chunk!r}"
        )

        last_chunk = chunks[-1]
        assert "usage" in last_chunk, f"Last stream JSON chunk should contain usage: {last_chunk!r}"
        assert isinstance(last_chunk["usage"], dict), f"Last stream chunk usage should be object: {last_chunk!r}"
        assert "prompt_tokens" in last_chunk["usage"], f"Last stream chunk usage missing prompt_tokens: {last_chunk!r}"
        assert "completion_tokens" in last_chunk["usage"], f"Last stream chunk usage missing completion_tokens: {last_chunk!r}"
        assert "total_tokens" in last_chunk["usage"], f"Last stream chunk usage missing total_tokens: {last_chunk!r}"
        assert stream_chunks.raw_data_lines[-1] == "data: [DONE]"

    def test_wrong_text_model_response_body_contains_error_object(self):
        response = self.smoke_task.create_chat_completion(
            self.smoke_request,
            self.smoke_task.build_chat_completions_payload(UNKNOWN_TEXT_MODEL_ID),
        )

        assert response.status_code != 200, f"Expected non-200 status code, actual: {response.status_code}."
        self.smoke_assertions.assert_json_path_exists(response, "$.error")
        self.smoke_assertions.assert_json_path_exists(response, "$.error.message")
        self.smoke_assertions.assert_json_path_exists(response, "$.error.type")
        self.smoke_assertions.assert_json_path_exists(response, "$.error.code")

    # @pytest.mark.xfail(reason="账户为0，响应体信息不精确")
    def test_zero_balance_account_call_response_body_contains_error_object(self):
        if not ZERO_BALANCE_API_KEY.strip() or not ZERO_BALANCE_CONTROL_KEY.strip():
            pytest.skip("Please configure ZERO_BALANCE_API_KEY and ZERO_BALANCE_CONTROL_KEY in this test first.")

        zero_balance_request = SmokeRequest()
        try:
            zero_balance_request.set_header("Authorization", f"Bearer {ZERO_BALANCE_API_KEY}")
            response = self.smoke_task.create_chat_completion(
                zero_balance_request,
                self.smoke_task.build_chat_completions_payload(),
            )
        finally:
            zero_balance_request.close()

        assert response.status_code != 200, f"Expected non-200 status code, actual: {response.status_code}."
        self.smoke_assertions.assert_json_path_exists(response, "$.error")
        self.smoke_assertions.assert_json_path_exists(response, "$.error.message")
        self.smoke_assertions.assert_json_path_exists(response, "$.error.type")
        self.smoke_assertions.assert_json_path_exists(response, "$.error.code")
