from __future__ import annotations

from typing import Any

import requests

from common import BaseAssertions


class ResponsesAssertions(BaseAssertions):
    def assert_response_success(
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

        assert self._has_response_content(body), (
            f"OpenAI Responses 响应应包含输出内容。请求模型：{request_model!r}，响应内容：{response.text}"
        )
        return response

    def _has_response_content(self, body: dict[str, Any]) -> bool:
        output_text = body.get("output_text")
        if isinstance(output_text, str) and output_text.strip():
            return True

        output = body.get("output")
        if isinstance(output, list) and output:
            return True

        choices = body.get("choices")
        if isinstance(choices, list) and choices:
            return True

        return False
