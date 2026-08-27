from __future__ import annotations

import struct

from module.protocol_testing.image_model.protocol_interception_cases import (
    ProtocolInterceptionCase as ImageProtocolInterceptionCase,
)
from module.protocol_testing.image_model.test_protocol_interception import (
    build_protocol_interception_payload as build_image_protocol_interception_payload,
)
from module.protocol_testing.payloads import build_protocol_interception_image_png
from module.protocol_testing.text_model.protocol_interception_cases import ProtocolInterceptionCase
from module.protocol_testing.text_model.test_protocol_interception import build_protocol_interception_payload
from module.protocol_testing.video_model.protocol_interception_cases import (
    ProtocolInterceptionCase as VideoProtocolInterceptionCase,
)
from module.protocol_testing.video_model.test_protocol_interception import (
    build_protocol_interception_payload as build_video_protocol_interception_payload,
)


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

    def test_image_edit_fixture_is_a_256_pixel_rgb_png(self):
        image = build_protocol_interception_image_png()

        assert image.startswith(b"\x89PNG\r\n\x1a\n")
        assert image[12:16] == b"IHDR"
        assert struct.unpack(">II", image[16:24]) == (256, 256)
        assert image.endswith(b"IEND\xaeB`\x82")

    def test_image_edit_case_uses_endpoint_valid_form_fields(self):
        payload = build_image_protocol_interception_payload(
            ImageProtocolInterceptionCase(
                case_id="image-edit",
                protocol_path="images_edits",
                header_protocol="openai",
                body_protocol="image_media",
                model_id="gpt-image-2",
                expected="block",
            )
        )

        assert payload == {
            "model": "gpt-image-2",
            "prompt": "在图片中添加陡峭的山脉",
        }

    def test_video_model_on_image_edit_uses_endpoint_valid_form_fields(self):
        payload = build_video_protocol_interception_payload(
            VideoProtocolInterceptionCase(
                case_id="video-on-image-edit",
                protocol_path="images_edits",
                header_protocol="openai",
                body_protocol="video_media",
                model_id="seedance-2-0-oversea",
                expected="block",
            )
        )

        assert payload == {
            "model": "seedance-2-0-oversea",
            "prompt": "在图片中添加陡峭的山脉",
        }

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
