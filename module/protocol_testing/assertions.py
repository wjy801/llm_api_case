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
    blocked_message_fragment = "当前使用协议"
    blocked_error_type = "invalid_request_error"

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
        self._assert_blocked_error(body, response, case_id)
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

    def _assert_blocked_error(
        self,
        body: dict[str, Any],
        response: requests.Response,
        case_id: str,
    ) -> None:
        assert "error" in body, (
            f"协议拦截 block 用例应包含 error 字段。"
            f"case_id={case_id!r}，响应内容：{response.text}"
        )

        error = body["error"]
        assert isinstance(error, dict), (
            f"协议拦截 block 用例的 error 字段应为对象。"
            f"case_id={case_id!r}，响应内容：{response.text}"
        )

        message = error.get("message")
        assert isinstance(message, str) and self.blocked_message_fragment in message, (
            f"协议拦截 block 用例的 error.message 应包含 "
            f"{self.blocked_message_fragment!r}。case_id={case_id!r}，响应内容：{response.text}"
        )

        error_type = error.get("type")
        assert error_type == self.blocked_error_type, (
            f"协议拦截 block 用例的 error.type 应为 "
            f"{self.blocked_error_type!r}，实际为 {error_type!r}。"
            f"case_id={case_id!r}，响应内容：{response.text}"
        )

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
