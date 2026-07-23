from __future__ import annotations

import pytest

from module.protocol_testing import ProtocolRequest, ProtocolTask
from module.protocol_testing.payloads import build_text_responses_payload
from module.protocol_testing.v1_responses.assertions import ResponsesAssertions
from module.protocol_testing.v1_responses.model_cases import MODEL_ID_CSV_PATH, load_response_model_ids


def response_model_params() -> list[pytest.ParameterSet]:
    model_ids = load_response_model_ids()
    if not model_ids:
        return [
            pytest.param(
                "your-response-model",
                marks=pytest.mark.skip(reason=f"请先在 {MODEL_ID_CSV_PATH} 中配置支持 Responses 协议的 model_id"),
                id="unconfigured-response-model",
            )
        ]

    return [pytest.param(model_id, id=model_id) for model_id in model_ids]


class TestOpenAIResponsesProtocol:
    def setup_method(self):
        self.protocol_request = ProtocolRequest()
        self.protocol_assertions = ResponsesAssertions()
        self.protocol_task = ProtocolTask()

    def teardown_method(self):
        self.protocol_request.close()

    @pytest.mark.parametrize("model_id", response_model_params())
    def test_text_model_openai_responses(self, model_id: str):
        payload = build_text_responses_payload(model_id)

        response = self.protocol_task.create_response(
            self.protocol_request,
            payload,
        )

        self.protocol_assertions.assert_response_success(
            response,
            request_model=model_id,
        )
