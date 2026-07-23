from __future__ import annotations

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
