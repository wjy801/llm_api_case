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


class ProtocolInterceptionAssertions(BaseAssertions):
    forbidden_error_text_values = ["traceback", "stack trace", "exception", "sql", "internal server error"]

    def assert_protocol_interception_allowed(
        self,
        response: requests.Response,
        *,
        case_id: str,
    ) -> requests.Response:
        self.assert_status_code(response, 200)
        body = self._json_body(response, case_id)
        assert "error" not in body, f"协议拦截 allow 用例不应返回 error。case_id={case_id!r}，响应内容：{response.text}"
        return response

    def assert_protocol_interception_blocked(
        self,
        response: requests.Response,
        *,
        case_id: str,
    ) -> requests.Response:
        assert response.status_code != 200, (
            f"协议拦截 block 用例应返回非 200。case_id={case_id!r}，响应内容：{response.text}"
        )
        body = self._json_body(response, case_id)
        assert self._has_error_message(body), (
            f"协议拦截 block 用例应返回错误对象或错误信息。case_id={case_id!r}，响应内容：{response.text}"
        )
        self._assert_response_text_not_contains_internal_information(response, case_id)
        return response

    @staticmethod
    def _json_body(response: requests.Response, case_id: str) -> dict[str, Any]:
        try:
            body = response.json()
        except ValueError as exc:
            raise AssertionError(f"响应内容不是合法 JSON。case_id={case_id!r}，响应内容：{response.text}") from exc

        assert isinstance(body, dict), f"响应 JSON 顶层应为对象。case_id={case_id!r}，响应内容：{response.text}"
        return body

    @staticmethod
    def _has_error_message(body: dict[str, Any]) -> bool:
        error = body.get("error")
        if error is not None:
            return True

        for field_name in ("message", "error_message", "code", "type"):
            value = body.get(field_name)
            if isinstance(value, str) and value.strip():
                return True
        return False

    def _assert_response_text_not_contains_internal_information(
        self,
        response: requests.Response,
        case_id: str,
    ) -> None:
        response_text = response.text.lower()
        found_values = [value for value in self.forbidden_error_text_values if value in response_text]
        assert not found_values, (
            f"协议拦截错误响应不应泄露内部信息: {found_values!r}。case_id={case_id!r}，响应内容：{response.text}"
        )
