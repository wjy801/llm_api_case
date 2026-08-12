from __future__ import annotations

from module.protocol_testing.model_csv_loader import (
    PROTOCOL_TESTING_ROOT,
    get_protocol_model_csv_path,
    load_model_ids_from_csv,
    load_protocol_model_ids,
    resolve_protocol_testing_path,
)


class FakeConfig:
    def __init__(self, **options: str | None):
        self.options = options

    def getoption(self, option_name: str, default=None):
        return self.options.get(option_name, default)


class TestProtocolModelCsvLoader:
    def test_load_model_ids_from_header_csv(self, tmp_path):
        csv_path = tmp_path / "model_id.csv"
        csv_path.write_text("model_id,remark\nmodel-a,stable\nmodel-b,beta\n", encoding="utf-8")

        assert load_model_ids_from_csv(csv_path) == ["model-a", "model-b"]

    def test_load_model_ids_from_plain_csv(self, tmp_path):
        csv_path = tmp_path / "model_id.csv"
        csv_path.write_text("model-a\nmodel-b\nmodel-a\n", encoding="utf-8")

        assert load_model_ids_from_csv(csv_path) == ["model-a", "model-b"]

    def test_load_model_ids_supports_utf8_sig(self, tmp_path):
        csv_path = tmp_path / "model_id.csv"
        csv_path.write_text("\ufeffmodel_id\nmodel-a\n", encoding="utf-8")

        assert load_model_ids_from_csv(csv_path) == ["model-a"]

    def test_load_model_ids_returns_empty_for_header_only_csv(self, tmp_path):
        csv_path = tmp_path / "model_id.csv"
        csv_path.write_text("model_id\n", encoding="utf-8")

        assert load_model_ids_from_csv(csv_path) == []

    def test_load_model_ids_returns_empty_for_missing_file(self, tmp_path):
        assert load_model_ids_from_csv(tmp_path / "missing.csv") == []

    def test_load_model_ids_returns_empty_for_empty_file(self, tmp_path):
        csv_path = tmp_path / "model_id.csv"
        csv_path.write_text("", encoding="utf-8")

        assert load_model_ids_from_csv(csv_path) == []

    def test_resolve_protocol_testing_path_uses_protocol_testing_root(self):
        assert resolve_protocol_testing_path("text_model/openai.csv") == (
            PROTOCOL_TESTING_ROOT / "text_model/openai.csv"
        ).resolve()

    def test_get_protocol_model_csv_path_uses_common_option(self, tmp_path):
        common_csv_path = tmp_path / "common.csv"
        config = FakeConfig(protocol_model_csv=str(common_csv_path))

        assert get_protocol_model_csv_path(config) == common_csv_path

    def test_get_protocol_model_csv_path_returns_none_when_option_missing(self):
        config = FakeConfig(protocol_model_csv=None)

        assert get_protocol_model_csv_path(config) is None

    def test_load_protocol_model_ids_uses_cli_csv_path(self, tmp_path):
        csv_path = tmp_path / "models.csv"
        csv_path.write_text("model_id\nmodel-a\nmodel-b\n", encoding="utf-8")
        config = FakeConfig(protocol_model_csv=str(csv_path))

        assert load_protocol_model_ids(config, "openai") == ["model-a", "model-b"]

    def test_load_protocol_model_ids_returns_empty_when_option_missing(self):
        config = FakeConfig(protocol_model_csv=None)

        assert load_protocol_model_ids(config, "openai") == []
