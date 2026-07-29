from __future__ import annotations

from typing import Any

import requests

from common import allure_step
from module.protocol_testing.request import ProtocolRequest


class ProtocolTask:
    @allure_step("OpenAI POST /v1/chat/completions")
    def create_chat_completion(
        self,
        protocol_request: ProtocolRequest,
        payload: dict[str, Any],
        *,
        headers: dict[str, str] | None = None,
    ) -> requests.Response:
        return protocol_request.create_chat_completion(payload, headers=headers)

    @allure_step("OpenAI POST /v1/responses")
    def create_response(
        self,
        protocol_request: ProtocolRequest,
        payload: dict[str, Any],
        *,
        headers: dict[str, str] | None = None,
    ) -> requests.Response:
        return protocol_request.create_response(payload, headers=headers)

    @allure_step("OpenAI POST /v1/media/generations")
    def create_media_generation(
        self,
        protocol_request: ProtocolRequest,
        payload: dict[str, Any],
        *,
        headers: dict[str, str] | None = None,
    ) -> requests.Response:
        return protocol_request.create_media_generation(payload, headers=headers)

    @allure_step("OpenAI POST /v1/images/generations")
    def create_image_generation(
        self,
        protocol_request: ProtocolRequest,
        payload: dict[str, Any],
        *,
        headers: dict[str, str] | None = None,
    ) -> requests.Response:
        return protocol_request.create_image_generation(payload, headers=headers)

    @allure_step("OpenAI POST /v1/images/edits")
    def create_image_edit(
        self,
        protocol_request: ProtocolRequest,
        payload: dict[str, Any],
        image: bytes,
        *,
        image_filename: str = "protocol-interception.png",
        headers: dict[str, str] | None = None,
    ) -> requests.Response:
        return protocol_request.create_image_edit(
            payload,
            image,
            image_filename=image_filename,
            headers=headers,
        )

    @allure_step("Anthropic POST /v1/messages")
    def create_message(
        self,
        protocol_request: ProtocolRequest,
        payload: dict[str, Any],
        *,
        headers: dict[str, str] | None = None,
        anthropic_beta: str | None = None,
    ) -> requests.Response:
        return protocol_request.create_message(
            payload,
            headers=headers,
            anthropic_beta=anthropic_beta,
        )
