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
    ) -> requests.Response:
        return protocol_request.create_chat_completion(payload)

    @allure_step("OpenAI POST /v1/responses")
    def create_response(
        self,
        protocol_request: ProtocolRequest,
        payload: dict[str, Any],
    ) -> requests.Response:
        return protocol_request.create_response(payload)

    @allure_step("Anthropic POST /v1/messages")
    def create_message(
        self,
        protocol_request: ProtocolRequest,
        payload: dict[str, Any],
    ) -> requests.Response:
        return protocol_request.create_message(payload)
