from __future__ import annotations

import pytest

from module.protocol_testing.text_model.protocol_interception_cases import load_protocol_interception_cases


class TestProtocolInterceptionCases:
    def test_load_protocol_interception_cases_from_header_csv(self, tmp_path):
        csv_path = tmp_path / "protocol_interception.csv"
        csv_path.write_text(
            "case_id,protocol_path,body_protocol,model_id,expected\n"
            "openai_kimi_allow,openai_chat_completions,openai,kimi-k3,allow\n"
            "anthropic_qwen_block,anthropic_messages,anthropic,qwen3.5-flash,block\n",
            encoding="utf-8",
        )

        cases = load_protocol_interception_cases(csv_path)

        assert [case.case_id for case in cases] == ["openai_kimi_allow", "anthropic_qwen_block"]
        assert cases[0].protocol_path == "openai_chat_completions"
        assert cases[0].body_protocol == "openai"
        assert cases[0].model_id == "kimi-k3"
        assert cases[0].expected == "allow"

    def test_load_protocol_interception_cases_returns_empty_for_missing_file(self, tmp_path):
        assert load_protocol_interception_cases(tmp_path / "missing.csv") == []

    def test_load_protocol_interception_cases_rejects_missing_required_column(self, tmp_path):
        csv_path = tmp_path / "protocol_interception.csv"
        csv_path.write_text("case_id,protocol_path,body_protocol,model_id\ncase-1,openai_chat_completions,openai,kimi-k3\n", encoding="utf-8")

        with pytest.raises(ValueError, match="缺少字段"):
            load_protocol_interception_cases(csv_path)

    def test_load_protocol_interception_cases_rejects_unknown_expected_value(self, tmp_path):
        csv_path = tmp_path / "protocol_interception.csv"
        csv_path.write_text(
            "case_id,protocol_path,body_protocol,model_id,expected\n"
            "case-1,openai_chat_completions,openai,kimi-k3,maybe\n",
            encoding="utf-8",
        )

        with pytest.raises(ValueError, match="expected 不支持"):
            load_protocol_interception_cases(csv_path)

    def test_default_protocol_interception_matrix_contains_expected_cases(self):
        cases = load_protocol_interception_cases()

        assert len(cases) == 14
        assert cases[0].case_id == "openai_qwen_allow"
        assert cases[-1].case_id == "anthropic_gemini_block"
