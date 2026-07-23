from __future__ import annotations

import pytest

from module.protocol_testing import ProtocolRequest, ProtocolTask
from module.protocol_testing.anthropic.assertions import AnthropicMessagesAssertions
from module.protocol_testing.anthropic.model_cases import (
    MODEL_ID_CSV_PATH,
    load_anthropic_message_model_ids,
)
from module.protocol_testing.payloads import build_anthropic_messages_payload


def anthropic_message_model_params() -> list[pytest.ParameterSet]:
    model_ids = load_anthropic_message_model_ids()
    if not model_ids:
        return [
            pytest.param(
                "your-anthropic-model",
                marks=pytest.mark.skip(reason=f"请先在 {MODEL_ID_CSV_PATH} 中配置支持 Anthropic Messages 协议的 model_id"),
                id="unconfigured-anthropic-model",
            )
        ]

    return [pytest.param(model_id, id=model_id) for model_id in model_ids]


class TestAnthropicMessagesProtocol:
    def setup_method(self):
        self.protocol_request = ProtocolRequest()
        self.protocol_assertions = AnthropicMessagesAssertions()
        self.protocol_task = ProtocolTask()

    def teardown_method(self):
        self.protocol_request.close()

    @pytest.mark.parametrize("model_id", anthropic_message_model_params())
    def test_text_model_anthropic_messages(self, model_id: str):
        payload = build_anthropic_messages_payload(model_id)

        response = self.protocol_task.create_message(
            self.protocol_request,
            payload,
        )

        self.protocol_assertions.assert_message_success(
            response,
            request_model=model_id,
        )
