from __future__ import annotations

import pytest

from module.protocol_testing import ProtocolRequest, ProtocolTask
from module.protocol_testing.payloads import build_text_chat_completions_payload
from module.protocol_testing.v1_chat_completions.assertions import ChatCompletionsAssertions
from module.protocol_testing.v1_chat_completions.model_cases import (
    MODEL_ID_CSV_PATH,
    load_chat_completion_model_ids,
)


def text_model_params() -> list[pytest.ParameterSet]:
    model_ids = load_chat_completion_model_ids()
    if not model_ids:
        return [
            pytest.param(
                "your-text-model",
                marks=pytest.mark.skip(reason=f"请先在 {MODEL_ID_CSV_PATH} 中配置支持对话模型的 model_id"),
                id="unconfigured-text-model",
            )
        ]

    return [pytest.param(model_id, id=model_id) for model_id in model_ids]


class TestOpenAIChatCompletionsProtocol:
    def setup_method(self):
        self.protocol_request = ProtocolRequest()
        self.protocol_assertions = ChatCompletionsAssertions()
        self.protocol_task = ProtocolTask()

    def teardown_method(self):
        self.protocol_request.close()

    @pytest.mark.parametrize("model_id", text_model_params())
    def test_text_model_openai_chat_completions(self, model_id: str):
        payload = build_text_chat_completions_payload(model_id)

        response = self.protocol_task.create_chat_completion(
            self.protocol_request,
            payload,
        )

        self.protocol_assertions.assert_chat_completion_success(
            response,
            request_model=model_id,
        )
