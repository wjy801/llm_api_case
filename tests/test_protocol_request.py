from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from module.protocol_testing.request import (
    ANTHROPIC_API_KEY_HEADER,
    ANTHROPIC_BETA_HEADER,
    ANTHROPIC_VERSION,
    ANTHROPIC_VERSION_HEADER,
    ProtocolRequest,
)


@dataclass(frozen=True)
class FakeSettings:
    api_key: str = "sk-test-key"


class FakeProtocolRequest(ProtocolRequest):
    def __init__(self):
        self.post_calls: list[dict[str, Any]] = []
        self.response = object()
        self.config = FakeSettings()
        self.session_headers: dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": "Bearer test-key",
        }
        self.session = type(
            "FakeSession",
            (),
            {"headers": self.session_headers},
        )()

    def post(self, path: str, **kwargs: Any) -> object:
        self.post_calls.append(
            {
                "path": path,
                "kwargs": kwargs,
                "session_headers": dict(self.session.headers),
            }
        )
        return self.response

    def update_headers(self, headers: dict[str, str]) -> None:
        self.session.headers.update(headers)


class TestProtocolRequest:
    def test_create_message_passes_anthropic_protocol_headers(self):
        request = FakeProtocolRequest()
        payload = {"model": "kimi-k3"}

        response = request.create_message(payload)

        assert response is request.response
        assert request.post_calls == [
            {
                "path": "/v1/messages",
                "kwargs": {
                    "json": payload,
                    "headers": {
                        "Content-Type": "application/json",
                        ANTHROPIC_API_KEY_HEADER: "sk-test-key",
                        ANTHROPIC_VERSION_HEADER: ANTHROPIC_VERSION,
                    },
                    "_inherit_session_headers": False,
                },
                "session_headers": {
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "Authorization": "Bearer test-key",
                },
            }
        ]
        assert request.session.headers == {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": "Bearer test-key",
        }

    def test_create_message_can_pass_optional_anthropic_beta_header(self):
        request = FakeProtocolRequest()

        request.create_message(
            {"model": "kimi-k3"},
            anthropic_beta="tools-2024-04-04",
        )

        assert request.post_calls == [
            {
                "path": "/v1/messages",
                "kwargs": {
                    "json": {"model": "kimi-k3"},
                    "headers": {
                        "Content-Type": "application/json",
                        ANTHROPIC_API_KEY_HEADER: "sk-test-key",
                        ANTHROPIC_VERSION_HEADER: ANTHROPIC_VERSION,
                        ANTHROPIC_BETA_HEADER: "tools-2024-04-04",
                    },
                    "_inherit_session_headers": False,
                },
                "session_headers": {
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "Authorization": "Bearer test-key",
                },
            }
        ]

    def test_create_message_uses_fixed_required_anthropic_headers(self):
        request = FakeProtocolRequest()

        request.create_message(
            {"model": "kimi-k3"},
            headers={
                "Content-Type": "text/plain",
                ANTHROPIC_API_KEY_HEADER: "other-key",
                ANTHROPIC_VERSION_HEADER: "2024-01-01",
                "X-Case-Id": "anthropic-kimi",
            },
        )

        assert request.post_calls == [
            {
                "path": "/v1/messages",
                "kwargs": {
                    "json": {"model": "kimi-k3"},
                    "headers": {
                        "Content-Type": "application/json",
                        ANTHROPIC_API_KEY_HEADER: "sk-test-key",
                        ANTHROPIC_VERSION_HEADER: ANTHROPIC_VERSION,
                        "X-Case-Id": "anthropic-kimi",
                    },
                    "_inherit_session_headers": False,
                },
                "session_headers": {
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "Authorization": "Bearer test-key",
                },
            }
        ]

    def test_create_message_restores_session_headers_after_failure(self):
        class FailingProtocolRequest(FakeProtocolRequest):
            def post(self, path: str, **kwargs: Any) -> object:
                self.post_calls.append(
                    {
                        "path": path,
                        "kwargs": kwargs,
                        "session_headers": dict(self.session.headers),
                    }
                )
                raise RuntimeError("request failed")

        request = FailingProtocolRequest()

        try:
            request.create_message({"model": "kimi-k3"})
        except RuntimeError:
            pass

        assert request.session.headers == {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": "Bearer test-key",
        }

    def test_openai_protocol_paths_do_not_pass_anthropic_headers(self):
        request = FakeProtocolRequest()

        request.create_chat_completion({"model": "kimi-k3"})
        request.create_response({"model": "kimi-k3"})

        assert request.post_calls == [
            {
                "path": "/v1/chat/completions",
                "kwargs": {"json": {"model": "kimi-k3"}},
                "session_headers": {
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "Authorization": "Bearer test-key",
                },
            },
            {
                "path": "/v1/responses",
                "kwargs": {"json": {"model": "kimi-k3"}},
                "session_headers": {
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "Authorization": "Bearer test-key",
                },
            },
        ]

    def test_openai_protocol_path_passes_explicit_headers(self):
        request = FakeProtocolRequest()

        request.create_chat_completion(
            {"model": "kimi-k3"},
            headers={ANTHROPIC_VERSION_HEADER: ANTHROPIC_VERSION},
        )

        assert request.post_calls == [
            {
                "path": "/v1/chat/completions",
                "kwargs": {
                    "json": {"model": "kimi-k3"},
                    "headers": {
                        ANTHROPIC_VERSION_HEADER: ANTHROPIC_VERSION,
                    },
                },
                "session_headers": {
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "Authorization": "Bearer test-key",
                },
            }
        ]

    def test_image_edit_uses_multipart_without_json_content_type(self):
        request = FakeProtocolRequest()
        image = b"png-content"

        response = request.create_image_edit(
            {"model": "gpt-image-2", "prompt": "edit image"},
            image,
            headers={"X-Case-Id": "image-edit"},
        )

        assert response is request.response
        assert request.post_calls == [
            {
                "path": "/v1/images/edits",
                "kwargs": {
                    "data": {"model": "gpt-image-2", "prompt": "edit image"},
                    "files": {
                        "image": (
                            "protocol-interception.png",
                            image,
                            "image/png",
                        )
                    },
                    "headers": {
                        "Accept": "application/json",
                        "Authorization": "Bearer test-key",
                        "X-Case-Id": "image-edit",
                    },
                    "_inherit_session_headers": False,
                },
                "session_headers": {
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "Authorization": "Bearer test-key",
                },
            }
        ]
