from __future__ import annotations

from module.protocol_testing import ProtocolRequest, ProtocolTask
from module.protocol_testing.anthropic.assertions import AnthropicMessagesAssertions
from module.protocol_testing.payloads import build_text_anthropic_messages_payload


class TestAnthropicMessagesProtocol:
    def setup_method(self):
        self.protocol_request = ProtocolRequest()
        self.protocol_assertions = AnthropicMessagesAssertions()
        self.protocol_task = ProtocolTask()

    def teardown_method(self):
        self.protocol_request.close()

    def test_text_model_anthropic_messages(self, anthropic_model_id: str):
        payload = build_text_anthropic_messages_payload(anthropic_model_id)

        response = self.protocol_task.create_message(
            self.protocol_request,
            payload,
        )

        self.protocol_assertions.assert_message_success(
            response,
            request_model=anthropic_model_id,
        )
