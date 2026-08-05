from __future__ import annotations

from module.protocol_testing.text_model.protocol_interception_cases import ProtocolInterceptionCase
from module.protocol_testing.text_model.test_protocol_interception import build_protocol_interception_payload


class TestProtocolInterceptionPayloads:
    def test_openai_kimi_case_does_not_send_temperature(self):
        payload = build_protocol_interception_payload(
            ProtocolInterceptionCase(
                case_id="openai_kimi_allow",
                protocol_path="openai_chat_completions",
                body_protocol="openai",
                model_id="kimi-k3",
                expected="allow",
            )
        )

        assert payload["model"] == "kimi-k3"
        assert "temperature" not in payload

    def test_openai_non_kimi_case_keeps_temperature(self):
        payload = build_protocol_interception_payload(
            ProtocolInterceptionCase(
                case_id="openai_qwen_allow",
                protocol_path="openai_chat_completions",
                body_protocol="openai",
                model_id="qwen3.5-flash",
                expected="allow",
            )
        )

        assert payload["temperature"] == 0.7

    def test_anthropic_kimi_case_does_not_send_temperature(self):
        payload = build_protocol_interception_payload(
            ProtocolInterceptionCase(
                case_id="anthropic_kimi_allow",
                protocol_path="anthropic_messages",
                body_protocol="anthropic",
                model_id="kimi-k3",
                expected="allow",
            )
        )

        assert "temperature" not in payload
