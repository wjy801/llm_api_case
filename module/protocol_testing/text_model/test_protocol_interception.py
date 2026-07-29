from __future__ import annotations

from typing import Any

import pytest
import requests

from module.protocol_testing import ProtocolInterceptionAssertions, ProtocolRequest, ProtocolTask
from module.protocol_testing.payloads import (
    build_text_anthropic_messages_payload,
    build_text_v1_chat_completions_payload,
)
from module.protocol_testing.text_model.protocol_interception_cases import (
    ProtocolInterceptionCase,
    load_protocol_interception_cases,
)


def protocol_interception_case_params() -> list[pytest.ParameterSet]:
    cases = load_protocol_interception_cases()
    if not cases:
        return [
            pytest.param(
                ProtocolInterceptionCase(
                    case_id="unconfigured-protocol-interception-case",
                    protocol_path="openai_chat_completions",
                    body_protocol="openai",
                    model_id="your-model",
                    expected="block",
                ),
                marks=pytest.mark.skip(reason="请先配置 text_model/protocol_interception.csv"),
                id="unconfigured-protocol-interception-case",
            )
        ]
    return [pytest.param(case, id=case.case_id) for case in cases]


def build_protocol_interception_payload(case: ProtocolInterceptionCase) -> dict[str, Any]:
    if case.body_protocol == "openai":
        payload = build_text_v1_chat_completions_payload(case.model_id)
    elif case.body_protocol == "anthropic":
        payload = build_text_anthropic_messages_payload(case.model_id)
    else:
        raise ValueError(f"Unsupported body protocol: {case.body_protocol!r}")

    if case.model_id == "kimi-k3":
        payload.pop("temperature", None)
    return payload


class TestTextModelProtocolInterception:
    def setup_method(self):
        self.protocol_request = ProtocolRequest()
        self.protocol_assertions = ProtocolInterceptionAssertions()
        self.protocol_task = ProtocolTask()

    def teardown_method(self):
        self.protocol_request.close()

    @pytest.mark.parametrize("case", protocol_interception_case_params())
    def test_text_model_protocol_interception(self, case: ProtocolInterceptionCase):
        payload = build_protocol_interception_payload(case)
        response = self._create_by_protocol_path(case.protocol_path, payload)

        if case.expected == "allow":
            self.protocol_assertions.assert_protocol_interception_allowed(response, case_id=case.case_id)
            return

        self.protocol_assertions.assert_protocol_interception_blocked(response, case_id=case.case_id)

    def _create_by_protocol_path(
        self,
        protocol_path: str,
        payload: dict[str, Any],
    ) -> requests.Response:
        if protocol_path == "openai_chat_completions":
            return self.protocol_task.create_chat_completion(
                self.protocol_request,
                payload,
            )
        if protocol_path == "anthropic_messages":
            return self.protocol_task.create_message(
                self.protocol_request,
                payload,
            )
        raise ValueError(f"Unsupported protocol path: {protocol_path!r}")
