from __future__ import annotations

from typing import Any

import requests

from common import BaseRequest


class ProtocolRequest(BaseRequest):
    chat_completions_path = "/v1/chat/completions"
    responses_path = "/v1/responses"
    messages_path = "/v1/messages"

    def create_chat_completion(self, payload: dict[str, Any]) -> requests.Response:
        return self.post(self.chat_completions_path, json=payload)

    def create_response(self, payload: dict[str, Any]) -> requests.Response:
        return self.post(self.responses_path, json=payload)

    def create_message(self, payload: dict[str, Any]) -> requests.Response:
        return self.post(self.messages_path, json=payload)
