from __future__ import annotations

from module.protocol_testing import ProtocolRequest, ProtocolTask
from module.protocol_testing.assertions import ResponsesAssertions
from module.protocol_testing.payloads import build_text_v1_responses_payload


class TestOpenAIResponsesProtocol:
    def setup_method(self):
        self.protocol_request = ProtocolRequest()
        self.protocol_assertions = ResponsesAssertions()
        self.protocol_task = ProtocolTask()

    def teardown_method(self):
        self.protocol_request.close()

    def test_text_model_openai_responses(self, response_model_id: str):
        payload = build_text_v1_responses_payload(response_model_id)

        response = self.protocol_task.create_response(
            self.protocol_request,
            payload,
        )

        self.protocol_assertions.assert_response_success(
            response,
            request_model=response_model_id,
        )
