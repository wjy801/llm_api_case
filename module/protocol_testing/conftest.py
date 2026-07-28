from __future__ import annotations

import pytest

from module.protocol_testing.model_csv_loader import get_protocol_model_csv_path, load_protocol_model_ids


PROTOCOL_MODEL_PARAMETERS = {
    "openai_model_id": ("openai", "your-text-model", "请在 --protocol-model-csv 指定支持 OpenAI 协议的 model_id CSV"),
    "response_model_id": ("openai", "your-response-model", "请在 --protocol-model-csv 指定支持 Responses 协议的 model_id CSV"),
    "anthropic_model_id": (
        "anthropic",
        "your-anthropic-model",
        "请在 --protocol-model-csv 指定支持 Anthropic Messages 协议的 model_id CSV",
    ),
    "gemini_model_id": ("gemini", "your-gemini-model", "请在 --protocol-model-csv 指定支持 Gemini 协议的 model_id CSV"),
}


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    for parameter_name, (protocol, fallback_model_id, skip_reason_template) in PROTOCOL_MODEL_PARAMETERS.items():
        if parameter_name not in metafunc.fixturenames:
            continue

        csv_path = get_protocol_model_csv_path(metafunc.config)
        if csv_path is None:
            _parametrize_skipped_model(metafunc, parameter_name, fallback_model_id, skip_reason_template)
            return

        model_ids = load_protocol_model_ids(metafunc.config, protocol)
        if not model_ids:
            _parametrize_skipped_model(
                metafunc,
                parameter_name,
                fallback_model_id,
                f"请在 {csv_path} 中配置支持该协议的 model_id",
            )
            return

        metafunc.parametrize(parameter_name, model_ids, ids=model_ids)
        return


def _parametrize_skipped_model(
    metafunc: pytest.Metafunc,
    parameter_name: str,
    fallback_model_id: str,
    skip_reason: str,
) -> None:
    metafunc.parametrize(
        parameter_name,
        [
            pytest.param(
                fallback_model_id,
                marks=pytest.mark.skip(reason=skip_reason),
                id=f"unconfigured-{parameter_name.replace('_', '-')}",
            )
        ],
    )
