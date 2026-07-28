from __future__ import annotations

from module.protocol_testing import ProtocolRequest, ProtocolTask
from module.protocol_testing.payloads import build_text_v1_chat_completions_payload
from module.protocol_testing.v1_chat_completions.assertions import ChatCompletionsAssertions


class TestOpenAIChatCompletionsProtocol:
    def setup_method(self):
        self.protocol_request = ProtocolRequest()
        self.protocol_assertions = ChatCompletionsAssertions()
        self.protocol_task = ProtocolTask()

    def teardown_method(self):
        self.protocol_request.close()

    def test_text_model_openai_chat_completions(self, openai_model_id: str):
        payload = build_text_v1_chat_completions_payload(openai_model_id)

        response = self.protocol_task.create_chat_completion(
            self.protocol_request,
            payload,
        )

        self.protocol_assertions.assert_chat_completion_success(
            response,
            request_model=openai_model_id,
        )
