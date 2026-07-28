from __future__ import annotations

from module.protocol_testing.payloads import build_text_v1_chat_completions_payload
from module.protocol_testing.v1_chat_completions.model_cases import load_chat_completion_model_ids


class TestProtocolModelCases:
    def test_load_chat_completion_model_ids_from_header_csv(self, tmp_path):
        csv_path = tmp_path / "model_id.csv"
        csv_path.write_text("model_id,remark\nmodel-a,stable\nmodel-b,beta\n", encoding="utf-8")

        assert load_chat_completion_model_ids(csv_path) == ["model-a", "model-b"]

    def test_load_chat_completion_model_ids_from_plain_csv(self, tmp_path):
        csv_path = tmp_path / "model_id.csv"
        csv_path.write_text("model-a\nmodel-b\nmodel-a\n", encoding="utf-8")

        assert load_chat_completion_model_ids(csv_path) == ["model-a", "model-b"]

    def test_load_chat_completion_model_ids_supports_utf8_sig(self, tmp_path):
        csv_path = tmp_path / "model_id.csv"
        csv_path.write_text("\ufeffmodel_id\nmodel-a\n", encoding="utf-8")

        assert load_chat_completion_model_ids(csv_path) == ["model-a"]

    def test_load_chat_completion_model_ids_returns_empty_for_missing_file(self, tmp_path):
        csv_path = tmp_path / "missing.csv"

        assert load_chat_completion_model_ids(csv_path) == []

    def test_load_chat_completion_model_ids_returns_empty_for_empty_file(self, tmp_path):
        csv_path = tmp_path / "model_id.csv"
        csv_path.write_text("", encoding="utf-8")

        assert load_chat_completion_model_ids(csv_path) == []

    def test_build_text_v1_chat_completions_payload_uses_model_id(self):
        assert build_text_v1_chat_completions_payload("model-a") == {
            "model": "model-a",
            "messages": [
                {
                    "role": "system",
                    "content": "你是墨行AI助手，请简洁回答。",
                },
                {
                    "role": "user",
                    "content": "我们在做企业知识库问答。",
                },
                {
                    "role": "assistant",
                    "content": "收到，请告诉我你希望接入的场景。",
                },
                {
                    "role": "user",
                    "content": "请给我一个最小接入建议。",
                },
            ],
            "temperature": 0.7,
            "stream": False,
            "user": "demo-user-001",
        }
