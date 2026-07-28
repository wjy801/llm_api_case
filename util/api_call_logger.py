from __future__ import annotations

from copy import deepcopy
import json
import time
from typing import Any

import allure
from allure_commons.types import AttachmentType
import requests

from util.curl_builder import build_curl
from util.redaction import (
    redact_headers,
    redact_sensitive_data,
    redact_text_body,
    redact_url,
    redact_urlencoded_text,
)


MAX_TEXT_LENGTH = 50000
API_REQUEST_STEP_NAME = "接口请求"
API_RESPONSE_STEP_NAME = "接口响应"
POLL_GET_REQUEST_STEP_NAME = "轮询结果请求"
POLL_GET_RESPONSE_STEP_NAME = "轮询结果响应"
REQUEST_CURL_ATTACHMENT_NAME = "请求 cURL"


class ApiCallLogger:
    def __init__(
        self,
        method: str,
        url: str,
        kwargs: dict[str, Any],
        step_name: str = API_REQUEST_STEP_NAME,
        response_step_name: str = API_RESPONSE_STEP_NAME,
    ):
        self.method = method.upper()
        self.url = url
        self.kwargs = deepcopy(kwargs)
        self.step_name = step_name
        self.response_step_name = response_step_name
        self.started_perf = time.perf_counter()

    def attach_success(self, response: requests.Response) -> None:
        self._attach_parts(
            self.step_name,
            self._request_parts(response.request),
            (REQUEST_CURL_ATTACHMENT_NAME, "请求行", "请求头", "请求体"),
        )
        self._attach_parts(self.response_step_name, self._response_parts(response), ("响应行", "响应头", "响应体"))

    def attach_failure(self, error: BaseException) -> None:
        self._attach_parts(
            self.step_name,
            self._request_parts(self._request_from_error(error)),
            (REQUEST_CURL_ATTACHMENT_NAME, "请求行", "请求头", "请求体"),
        )
        self._attach_parts(
            self.response_step_name,
            {
                "响应行": "<no response>",
                "响应头": "<empty>",
                "响应体": "\n".join(
                    [
                        f"异常类型: {type(error).__name__}",
                        f"异常内容: {self._format_error_text(error)}",
                    ]
                ),
            },
            ("响应行", "响应头", "响应体"),
        )

    def attach_retry_records(self, records: list[Any]) -> None:
        if not records:
            return

        lines: list[str] = []
        for record in records:
            lines.append(
                "\n".join(
                    [
                        f"Attempt: {getattr(record, 'attempt_index', '<unknown>')}/"
                        f"{getattr(record, 'max_attempts', '<unknown>')}",
                        f"Reason: {self._format_error_text_value(str(getattr(record, 'reason', '<unknown>')))}",
                        f"Wait seconds: {getattr(record, 'wait_seconds', '<unknown>')}",
                        f"Response status: {getattr(record, 'response_status_code', None)}",
                        f"Exception type: {getattr(record, 'exception_type', None)}",
                        f"Exception message: "
                        f"{self._format_error_text_value(str(getattr(record, 'exception_message', '') or ''))}",
                    ]
                )
            )

        with allure.step("接口重试记录"):
            allure.attach(
                self._truncate("\n\n".join(lines)),
                name="重试记录",
                attachment_type=AttachmentType.TEXT,
            )

    def attach_polling_transitions(self, transitions_text: str) -> None:
        with allure.step("轮询状态迁移"):
            allure.attach(
                self._truncate(redact_text_body(redact_urlencoded_text(transitions_text))),
                name="状态迁移",
                attachment_type=AttachmentType.TEXT,
            )

    def _request_parts(
        self,
        prepared_request: requests.PreparedRequest | None = None,
    ) -> dict[str, str]:
        if prepared_request is None:
            method = self.method
            url = redact_url(self.url) or self.url
            headers = self.kwargs.get("headers")
            body = self._fallback_request_body()
        else:
            method = prepared_request.method or self.method
            url = redact_url(prepared_request.url or self.url) or self.url
            headers = redact_headers(prepared_request.headers)
            body = self._format_body_value(prepared_request.body)

        return {
            REQUEST_CURL_ATTACHMENT_NAME: self._format_curl(prepared_request),
            "请求行": f"{method} {url} HTTP/1.1",
            "请求头": self._format_headers(headers),
            "请求体": body,
        }

    def _response_parts(self, response: requests.Response) -> dict[str, str]:
        return {
            "响应行": "\n".join(
                [
                    f"HTTP/1.1 {response.status_code} {response.reason}",
                    f"响应耗时(秒): {self._response_elapsed_seconds(response)}",
                    f"执行耗时(秒): {self._elapsed_seconds()}",
                ]
            ),
            "响应头": self._format_headers(redact_headers(response.headers)),
            "响应体": self._format_response_body(response),
        }

    def _fallback_request_body(self) -> str:
        if "json" in self.kwargs:
            return self._to_pretty_text(redact_sensitive_data(self.kwargs["json"]))
        if "data" in self.kwargs:
            return self._to_pretty_text(redact_sensitive_data(self.kwargs["data"]))
        return "<empty>"

    def _format_response_body(self, response: requests.Response) -> str:
        content_type = response.headers.get("Content-Type", "")
        return self._format_text_body(response.text, content_type)

    def _format_headers(self, headers: Any) -> str:
        if not headers:
            return "<empty>"

        header_items = dict(headers).items()
        return "\n".join(f"{key}: {value}" for key, value in header_items)

    def _format_body_value(self, body: Any) -> str:
        if body is None:
            return "<empty>"
        if isinstance(body, bytes):
            body = body.decode("utf-8", errors="replace")
        return self._format_text_body(redact_text_body(str(body)))

    def _format_curl(self, prepared_request: requests.PreparedRequest | None) -> str:
        if prepared_request is None:
            return "<unavailable>"

        try:
            return self._truncate(build_curl(prepared_request))
        except Exception as error:
            return f"<curl unavailable: {type(error).__name__}: {error}>"

    def _attach_parts(
        self,
        step_name: str,
        parts: dict[str, str],
        attachment_names: tuple[str, ...],
    ) -> None:
        with allure.step(step_name):
            for name in attachment_names:
                allure.attach(
                    parts.get(name) or "<empty>",
                    name=name,
                    attachment_type=AttachmentType.TEXT,
                )

    def _elapsed_seconds(self) -> float:
        return round(time.perf_counter() - self.started_perf, 3)

    def _format_error_text(self, error: BaseException) -> str:
        return self._format_error_text_value(str(error))

    def _format_error_text_value(self, value: str) -> str:
        return self._truncate(redact_text_body(redact_urlencoded_text(value)))

    @staticmethod
    def _response_elapsed_seconds(response: requests.Response) -> float | None:
        if response.elapsed is None:
            return None
        return round(response.elapsed.total_seconds(), 3)

    def _to_pretty_text(self, value: Any) -> str:
        if isinstance(value, (dict, list)):
            return self._truncate(json.dumps(value, ensure_ascii=False, indent=2))
        return self._truncate(str(value))

    def _format_text_body(self, body: str, content_type: str = "") -> str:
        if self._looks_like_json(content_type, body):
            try:
                return self._to_pretty_text(redact_sensitive_data(json.loads(body)))
            except ValueError:
                pass
        return self._truncate(body)

    @staticmethod
    def _looks_like_json(content_type: str, body: str) -> bool:
        stripped_body = body.lstrip()
        return (
            "json" in content_type.lower()
            or stripped_body.startswith("{")
            or stripped_body.startswith("[")
        )

    @staticmethod
    def _truncate(value: str) -> str:
        if len(value) > MAX_TEXT_LENGTH:
            return value[:MAX_TEXT_LENGTH] + "\n...<truncated>"
        return value

    @staticmethod
    def _request_from_error(error: BaseException) -> requests.PreparedRequest | None:
        request = getattr(error, "request", None)
        if isinstance(request, requests.PreparedRequest):
            return request
        return None
