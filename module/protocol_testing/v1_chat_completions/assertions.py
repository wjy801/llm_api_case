from __future__ import annotations

from typing import Any

import requests

from common import BaseAssertions


class ChatCompletionsAssertions(BaseAssertions):
    def assert_chat_completion_success(
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

        choices = body.get("choices")
        assert isinstance(choices, list) and choices, (
            f"OpenAI Chat Completions 响应应包含非空 choices。请求模型：{request_model!r}，响应内容：{response.text}"
        )

        first_choice = choices[0]
        assert isinstance(first_choice, dict), (
            f"choices[0] 应为对象。请求模型：{request_model!r}，响应内容：{response.text}"
        )

        message = first_choice.get("message")
        assert isinstance(message, dict), (
            f"choices[0].message 应为对象。请求模型：{request_model!r}，响应内容：{response.text}"
        )

        content = message.get("content")
        assert self._has_completion_content(content), (
            f"choices[0].message.content 应包含模型回复内容。请求模型：{request_model!r}，响应内容：{response.text}"
        )
        return response

    @staticmethod
    def _has_completion_content(content: Any) -> bool:
        if isinstance(content, str):
            return bool(content.strip())

        if isinstance(content, list):
            return bool(content)

        return content is not None
