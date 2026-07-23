from __future__ import annotations

from module.protocol_testing.payloads import build_text_responses_payload
from module.protocol_testing.v1_responses.model_cases import load_response_model_ids


class TestProtocolResponsesModelCases:
    def test_load_response_model_ids_from_header_csv(self, tmp_path):
        csv_path = tmp_path / "model_id.csv"
        csv_path.write_text("model_id,remark\nmodel-a,stable\nmodel-b,beta\n", encoding="utf-8")

        assert load_response_model_ids(csv_path) == ["model-a", "model-b"]

    def test_load_response_model_ids_from_plain_csv(self, tmp_path):
        csv_path = tmp_path / "model_id.csv"
        csv_path.write_text("model-a\nmodel-b\nmodel-a\n", encoding="utf-8")

        assert load_response_model_ids(csv_path) == ["model-a", "model-b"]

    def test_load_response_model_ids_returns_empty_for_header_only_csv(self, tmp_path):
        csv_path = tmp_path / "model_id.csv"
        csv_path.write_text("model_id\n", encoding="utf-8")

        assert load_response_model_ids(csv_path) == []

    def test_build_text_responses_payload_uses_model_id(self):
        assert build_text_responses_payload("model-a") == {
            "model": "model-a",
            "input": "hi",
            "stream": False,
        }
