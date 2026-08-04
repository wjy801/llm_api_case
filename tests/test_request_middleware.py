from __future__ import annotations

from typing import Any

import requests

from common.request_context import RequestContext
from common.request_middleware import (
    LoggingMiddleware,
    MediaResourceMiddleware,
    RedactionMiddleware,
)
from util import REDACTED_VALUE


class TestRedactionMiddleware:
    def test_redacts_copy_without_mutating_original_kwargs(self):
        original_kwargs = {
            "headers": {
                "Authorization": "Bearer header-secret",
                "Cookie": "session=cookie-secret",
                "X-Trace-Id": "trace-1",
            },
            "params": {
                "api_key": "query-secret",
                "model": "wan2.7",
            },
            "json": {
                "input": {
                    "token": "body-secret",
                    "prompt": "hello",
                },
                "messages": [
                    {
                        "role": "user",
                        "password": "nested-secret",
                    }
                ],
            },
            "timeout": 3,
        }
        context = RequestContext(
            method="POST",
            path="/v1/chat/completions",
            url="https://example.com/v1/chat/completions",
            kwargs=original_kwargs,
        )

        RedactionMiddleware().before_request(context)

        redacted_kwargs = context.attributes[RedactionMiddleware.REDACTED_KWARGS_ATTR]
        assert original_kwargs["headers"]["Authorization"] == "Bearer header-secret"
        assert original_kwargs["params"]["api_key"] == "query-secret"
        assert original_kwargs["json"]["input"]["token"] == "body-secret"
        assert redacted_kwargs["headers"]["Authorization"] == REDACTED_VALUE
        assert redacted_kwargs["headers"]["Cookie"] == REDACTED_VALUE
        assert redacted_kwargs["headers"]["X-Trace-Id"] == "trace-1"
        assert redacted_kwargs["params"]["api_key"] == REDACTED_VALUE
        assert redacted_kwargs["json"]["input"]["token"] == REDACTED_VALUE
        assert redacted_kwargs["json"]["messages"][0]["password"] == REDACTED_VALUE


class TestLoggingMiddleware:
    def test_attach_success_uses_logger_from_context(self, monkeypatch):
        created_loggers: list[DummyLogger] = []

        def create_logger(*args: Any, **kwargs: Any) -> DummyLogger:
            logger = DummyLogger(*args, **kwargs)
            created_loggers.append(logger)
            return logger

        monkeypatch.setattr("common.request_middleware.ApiCallLogger", create_logger)
        context = RequestContext(
            method="GET",
            path="/v1/tasks/task-1",
            url="https://example.com/v1/tasks/task-1",
            kwargs={},
        )
        response = requests.Response()

        middleware = LoggingMiddleware()
        middleware.before_request(context)
        middleware.after_response(context, response)

        assert created_loggers[0].success_responses == [response]

    def test_attach_log_false_skips_success_and_failure_attach(self, monkeypatch):
        created_loggers: list[DummyLogger] = []

        def create_logger(*args: Any, **kwargs: Any) -> DummyLogger:
            logger = DummyLogger(*args, **kwargs)
            created_loggers.append(logger)
            return logger

        monkeypatch.setattr("common.request_middleware.ApiCallLogger", create_logger)
        context = RequestContext(
            method="GET",
            path="/v1/stream",
            url="https://example.com/v1/stream",
            kwargs={},
            attach_log=False,
        )
        response = requests.Response()
        error = requests.Timeout("timeout")

        middleware = LoggingMiddleware()
        middleware.before_request(context)
        middleware.after_response(context, response)
        middleware.on_exception(context, error)

        assert created_loggers[0].success_responses == []
        assert created_loggers[0].failure_errors == []


class TestMediaResourceMiddleware:
    def test_post_triggers_media_download_and_get_does_not(self, monkeypatch):
        payload = {"input": {"media": {"type": "image", "url": "https://example.com/a.png"}}}
        captured_payloads: list[dict[str, Any]] = []

        monkeypatch.setattr(
            "common.request_middleware.start_media_downloads",
            lambda value, **_kwargs: captured_payloads.append(value),
        )

        middleware = MediaResourceMiddleware()
        middleware.before_request(
            RequestContext(
                method="POST",
                path="/v1/media/generations",
                url="https://example.com/v1/media/generations",
                kwargs={"json": payload},
            )
        )
        middleware.before_request(
            RequestContext(
                method="GET",
                path="/v1/media/tasks/task-1",
                url="https://example.com/v1/media/tasks/task-1",
                kwargs={},
            )
        )

        assert captured_payloads == [payload]


class DummyLogger:
    def __init__(self, *args: Any, **kwargs: Any):
        self.args = args
        self.kwargs = kwargs
        self.success_responses: list[requests.Response] = []
        self.failure_errors: list[BaseException] = []

    def attach_success(self, response: requests.Response) -> None:
        self.success_responses.append(response)

    def attach_failure(self, error: BaseException) -> None:
        self.failure_errors.append(error)
