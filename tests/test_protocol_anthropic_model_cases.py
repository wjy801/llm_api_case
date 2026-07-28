from __future__ import annotations

from module.protocol_testing.anthropic.model_cases import load_anthropic_message_model_ids
from module.protocol_testing.payloads import (
    build_text_anthropic_messages_payload,
    build_text_gemini_generate_content_payload,
)


class TestProtocolAnthropicModelCases:
    def test_load_anthropic_message_model_ids_from_header_csv(self, tmp_path):
        csv_path = tmp_path / "anthropic.csv"
        csv_path.write_text("model_id,remark\nmodel-a,stable\nmodel-b,beta\n", encoding="utf-8")

        assert load_anthropic_message_model_ids(csv_path) == ["model-a", "model-b"]

    def test_load_anthropic_message_model_ids_from_plain_csv(self, tmp_path):
        csv_path = tmp_path / "anthropic.csv"
        csv_path.write_text("model-a\nmodel-b\nmodel-a\n", encoding="utf-8")

        assert load_anthropic_message_model_ids(csv_path) == ["model-a", "model-b"]

    def test_load_anthropic_message_model_ids_returns_empty_for_header_only_csv(self, tmp_path):
        csv_path = tmp_path / "anthropic.csv"
        csv_path.write_text("model_id\n", encoding="utf-8")

        assert load_anthropic_message_model_ids(csv_path) == []

    def test_build_text_anthropic_messages_payload_uses_model_id(self):
        assert build_text_anthropic_messages_payload("model-a") == {
            "model": "model-a",
            "max_tokens": 500,
            "system": "你是一个乐于助人的AI",
            "temperature": 0.7,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "hi",
                        }
                    ],
                }
            ],
        }

    def test_build_text_gemini_generate_content_payload_uses_gemini_body_schema(self):
        assert build_text_gemini_generate_content_payload("gemini-model-a") == {
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {
                            "text": "hi",
                        }
                    ],
                }
            ],
            "generationConfig": {
                "temperature": 0.7,
            },
        }
