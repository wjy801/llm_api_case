from __future__ import annotations

from typing import Any

import requests

from common import BaseAssertions


class AnthropicMessagesAssertions(BaseAssertions):
    def assert_message_success(
        self,
        response: requests.Response,
        *,
        request_model: str,
    ) -> requests.Response:
        self.assert_status_code(response, 200)

        try:
            body = response.json()
        except ValueError as exc:
            raise AssertionError(f"响应内容不是合法 JSON。响应内容：{response.text}") from exc

        assert isinstance(body, dict), f"响应 JSON 顶层应为对象。响应内容：{response.text}"

        response_model = body.get("model")
        assert response_model is None or isinstance(response_model, str), (
            f"响应 model 字段应为字符串。请求模型：{request_model!r}，响应内容：{response.text}"
        )

        content = body.get("content")
        assert self._has_message_content(content), (
            f"Anthropic Messages 响应应包含 content。请求模型：{request_model!r}，响应内容：{response.text}"
        )
        return response

    @staticmethod
    def _has_message_content(content: Any) -> bool:
        if isinstance(content, str):
            return bool(content.strip())

        if isinstance(content, list):
            return bool(content)

        return content is not None
