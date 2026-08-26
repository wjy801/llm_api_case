from __future__ import annotations

from module.smoke.task import (
    ASYNC_IMAGE_GENERATION_MODEL_ID,
    KEY_CHAT_COMPLETIONS_MODEL_ID,
    KEY_CHAT_COMPLETIONS_RESPONSE_MODEL_ID,
    SYNC_IMAGE_GENERATION_MODEL_ID,
)


def test_smoke_models_match_current_protocol_catalog() -> None:
    assert KEY_CHAT_COMPLETIONS_MODEL_ID == "deepseek-v4-flash"
    assert KEY_CHAT_COMPLETIONS_RESPONSE_MODEL_ID == "DeepSeek-V4-Flash"
    assert SYNC_IMAGE_GENERATION_MODEL_ID == "gpt-image-2-gw"
    assert ASYNC_IMAGE_GENERATION_MODEL_ID == "gpt-image-2"


def test_sync_and_async_image_protocols_use_distinct_models() -> None:
    assert SYNC_IMAGE_GENERATION_MODEL_ID != ASYNC_IMAGE_GENERATION_MODEL_ID
