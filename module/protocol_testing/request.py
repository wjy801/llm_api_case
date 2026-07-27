from __future__ import annotations

from typing import Any

import requests

from common import BaseRequest


ANTHROPIC_VERSION_HEADER = "anthropic-version"
ANTHROPIC_API_KEY_HEADER = "x-api-key"
ANTHROPIC_BETA_HEADER = "anthropic-beta"
ANTHROPIC_VERSION = "2023-06-01"


class ProtocolRequest(BaseRequest):
    chat_completions_path = "/v1/chat/completions"
    responses_path = "/v1/responses"
    messages_path = "/v1/messages"
    image_generations_path = "/v1/images/generations"
    image_edits_path = "/v1/images/edits"
    media_generations_path = "/v1/media/generations"

    def create_chat_completion(
        self,
        payload: dict[str, Any],
        *,
        headers: dict[str, str] | None = None,
    ) -> requests.Response:
        return self.post(
            self.chat_completions_path,
            json=payload,
            **self._build_optional_headers_kwargs(headers),
        )

    def create_response(
        self,
        payload: dict[str, Any],
        *,
        headers: dict[str, str] | None = None,
    ) -> requests.Response:
        return self.post(
            self.responses_path,
            json=payload,
            **self._build_optional_headers_kwargs(headers),
        )

    def create_media_generation(
        self,
        payload: dict[str, Any],
        *,
        headers: dict[str, str] | None = None,
    ) -> requests.Response:
        return self.post(
            self.media_generations_path,
            json=payload,
            **self._build_optional_headers_kwargs(headers),
        )

    def create_image_generation(
        self,
        payload: dict[str, Any],
        *,
        headers: dict[str, str] | None = None,
    ) -> requests.Response:
        return self.post(
            self.image_generations_path,
            json=payload,
            **self._build_optional_headers_kwargs(headers),
        )

    def create_image_edit(
        self,
        payload: dict[str, Any],
        image: bytes,
        *,
        image_filename: str = "protocol-interception.png",
        headers: dict[str, str] | None = None,
    ) -> requests.Response:
        original_headers = dict(self.session.headers)
        multipart_headers = dict(original_headers)
        multipart_headers.pop("Content-Type", None)
        if headers:
            multipart_headers.update(headers)
            multipart_headers.pop("Content-Type", None)

        self.session.headers.clear()
        self.update_headers(multipart_headers)
        try:
            return self.post(
                self.image_edits_path,
                data=payload,
                files={"image": (image_filename, image, "image/png")},
            )
        finally:
            self.session.headers.clear()
            self.update_headers(original_headers)

    def create_message(
        self,
        payload: dict[str, Any],
        *,
        headers: dict[str, str] | None = None,
        anthropic_beta: str | None = None,
    ) -> requests.Response:
        return self._post_with_anthropic_headers(
            self.messages_path,
            payload,
            headers=headers,
            anthropic_beta=anthropic_beta,
        )

    def _post_with_anthropic_headers(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        headers: dict[str, str] | None = None,
        anthropic_beta: str | None = None,
    ) -> requests.Response:
        original_headers = dict(self.session.headers)
        self.session.headers.clear()
        self.update_headers(
            self._build_anthropic_headers(
                headers=headers,
                anthropic_beta=anthropic_beta,
            )
        )
        try:
            return self.post(path, json=payload)
        finally:
            self.session.headers.clear()
            self.update_headers(original_headers)

    def _build_anthropic_headers(
        self,
        *,
        headers: dict[str, str] | None = None,
        anthropic_beta: str | None = None,
    ) -> dict[str, str]:
        anthropic_headers = {
            "Content-Type": "application/json",
            ANTHROPIC_API_KEY_HEADER: self.config.api_key,
            ANTHROPIC_VERSION_HEADER: ANTHROPIC_VERSION,
        }
        if anthropic_beta:
            anthropic_headers[ANTHROPIC_BETA_HEADER] = anthropic_beta
        if headers:
            anthropic_headers.update(headers)
            anthropic_headers["Content-Type"] = "application/json"
            anthropic_headers[ANTHROPIC_API_KEY_HEADER] = self.config.api_key
            anthropic_headers[ANTHROPIC_VERSION_HEADER] = ANTHROPIC_VERSION
        return anthropic_headers

    @staticmethod
    def _build_optional_headers_kwargs(headers: dict[str, str] | None) -> dict[str, dict[str, str]]:
        if not headers:
            return {}
        return {"headers": headers}
