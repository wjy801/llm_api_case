from __future__ import annotations

from dataclasses import dataclass
from threading import Barrier, Thread
from typing import Any

import pytest
import requests

from common.base_request import BaseRequest
from common.request_context import RequestContext
from common.request_middleware import LoggingMiddleware


@dataclass(frozen=True)
class DummyConfig:
    base_url: str = "https://example.com"
    api_key: str = "config-secret"
    timeout: float = 3


class TestBaseRequestMiddlewarePipeline:
    def test_runs_middlewares_in_registration_order(self):
        events: list[str] = []
        contexts: list[RequestContext] = []
        client = BaseRequest(
            config=DummyConfig(),
            middlewares=[
                RecordingMiddleware("one", events, contexts),
                RecordingMiddleware("two", events, contexts),
            ],
        )

        def fake_request(method: str, url: str, **kwargs: Any) -> requests.Response:
            events.append("send")
            assert method == "GET"
            assert url == "https://example.com/v1/tasks/task-1"
            assert kwargs["timeout"] == 3
            assert kwargs["headers"]["Authorization"] == "Bearer config-secret"
            assert kwargs["headers"]["X-Test"] == "1"
            return make_response(url=url)

        client.session.request = fake_request  # type: ignore[method-assign]

        response = client.get("/v1/tasks/task-1", headers={"X-Test": "1"})

        assert response.status_code == 200
        assert events == [
            "one.before",
            "two.before",
            "send",
            "one.after",
            "two.after",
        ]
        assert contexts[0] is contexts[1]

    def test_runs_exception_middlewares_then_reraises_original_error(self):
        events: list[str] = []
        client = BaseRequest(
            config=DummyConfig(),
            middlewares=[
                RecordingMiddleware("one", events),
                RecordingMiddleware("two", events),
            ],
        )
        request_error = requests.Timeout("network timeout")

        def fake_request(method: str, url: str, **kwargs: Any) -> requests.Response:
            events.append("send")
            raise request_error

        client.session.request = fake_request  # type: ignore[method-assign]

        with pytest.raises(requests.Timeout) as exc_info:
            client.get("/v1/tasks/task-1")

        assert exc_info.value is request_error
        assert events == [
            "one.before",
            "two.before",
            "send",
            "one.exception:Timeout",
            "two.exception:Timeout",
        ]

    def test_exception_middleware_error_does_not_hide_original_request_error(self):
        client = BaseRequest(config=DummyConfig(), middlewares=[BrokenExceptionMiddleware()])
        request_error = requests.Timeout("network timeout")

        def fake_request(method: str, url: str, **kwargs: Any) -> requests.Response:
            raise request_error

        client.session.request = fake_request  # type: ignore[method-assign]

        with pytest.raises(requests.Timeout) as exc_info:
            client.get("/v1/tasks/task-1")

        assert exc_info.value is request_error
        assert exc_info.value.__notes__ == [
            "Request middleware BrokenExceptionMiddleware failed in on_exception"
        ]

    def test_wraps_middleware_error_with_source(self):
        client = BaseRequest(config=DummyConfig(), middlewares=[BrokenBeforeMiddleware()])

        with pytest.raises(RuntimeError, match="Request middleware BrokenBeforeMiddleware failed in before_request"):
            client.get("/v1/tasks/task-1")

    def test_creates_independent_context_for_each_request(self):
        contexts: list[RequestContext] = []
        client = BaseRequest(
            config=DummyConfig(),
            middlewares=[CaptureContextMiddleware(contexts)],
        )
        client.session.request = lambda method, url, **kwargs: make_response(url=url)  # type: ignore[method-assign]

        client.get("/v1/tasks/task-1")
        client.get("/v1/tasks/task-2")

        assert contexts[0] is not contexts[1]
        assert contexts[0].attributes is not contexts[1].attributes
        assert contexts[0].attributes["request_index"] == 1
        assert contexts[1].attributes["request_index"] == 2

    def test_request_context_deep_copies_nested_kwargs(self):
        payload = {"input": {"messages": [{"content": [{"text": "original"}]}]}}
        client = BaseRequest(
            config=DummyConfig(),
            middlewares=[MutateNestedJsonMiddleware()],
        )
        client.session.request = lambda method, url, **kwargs: make_response(url=url)  # type: ignore[method-assign]

        client.post("/v1/media/generations", json=payload)

        assert payload["input"]["messages"][0]["content"][0]["text"] == "original"

    def test_request_context_is_isolated_under_threads(self):
        barrier = Barrier(2)
        observed_texts: list[str] = []
        client = BaseRequest(
            config=DummyConfig(),
            middlewares=[ConcurrentMutationMiddleware(barrier, observed_texts)],
        )
        client.session.request = lambda method, url, **kwargs: make_response(url=url)  # type: ignore[method-assign]
        payloads = [
            {"input": {"messages": [{"content": [{"text": "payload-1"}]}]}},
            {"input": {"messages": [{"content": [{"text": "payload-2"}]}]}},
        ]

        threads = [
            Thread(target=client.post, args=("/v1/media/generations",), kwargs={"json": payload})
            for payload in payloads
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert payloads[0]["input"]["messages"][0]["content"][0]["text"] == "payload-1"
        assert payloads[1]["input"]["messages"][0]["content"][0]["text"] == "payload-2"
        assert sorted(observed_texts) == ["mutated-1", "mutated-2"]

    def test_explicit_empty_middlewares_disables_default_middlewares(self):
        client = BaseRequest(config=DummyConfig(), middlewares=[])

        assert client.middlewares == []


class TestBaseRequestLoggingCompatibility:
    def test_attach_log_false_still_sends_request_without_auto_attach(self, monkeypatch):
        created_loggers: list[DummyLogger] = []

        def create_logger(*args: Any, **kwargs: Any) -> DummyLogger:
            logger = DummyLogger(*args, **kwargs)
            created_loggers.append(logger)
            return logger

        monkeypatch.setattr("common.request_middleware.ApiCallLogger", create_logger)
        client = BaseRequest(config=DummyConfig(), middlewares=[LoggingMiddleware()])
        sent_requests: list[tuple[str, str]] = []

        def fake_request(method: str, url: str, **kwargs: Any) -> requests.Response:
            sent_requests.append((method, url))
            return make_response(url=url)

        client.session.request = fake_request  # type: ignore[method-assign]

        response = client.get("/v1/chat/completions", _attach_log=False)

        assert response.status_code == 200
        assert sent_requests == [("GET", "https://example.com/v1/chat/completions")]
        assert len(created_loggers) == 1
        assert created_loggers[0].success_responses == []

    def test_poll_get_attaches_only_final_poll_response(self, monkeypatch):
        created_loggers: list[DummyLogger] = []

        def create_logger(*args: Any, **kwargs: Any) -> DummyLogger:
            logger = DummyLogger(*args, **kwargs)
            created_loggers.append(logger)
            return logger

        monkeypatch.setattr("common.request_middleware.ApiCallLogger", create_logger)
        client = BaseRequest(config=DummyConfig(), middlewares=[LoggingMiddleware()])
        client.session.request = lambda method, url, **kwargs: make_response(  # type: ignore[method-assign]
            url=url,
            json_text='{"done": true}',
        )

        response = client.poll_get(
            "/v1/media/tasks/task-1",
            poll_interval=0.01,
            poll_timeout=1,
            success_json_path="$.done",
            failure_json_path=None,
        )

        assert response.json() == {"done": True}
        assert len(created_loggers) == 1
        assert created_loggers[0].success_responses == [response]

    def test_poll_get_success_with_empty_middlewares_does_not_require_logger(self):
        client = BaseRequest(config=DummyConfig(), middlewares=[])
        client.session.request = lambda method, url, **kwargs: make_response(  # type: ignore[method-assign]
            url=url,
            json_text='{"done": true}',
        )

        response = client.poll_get(
            "/v1/media/tasks/task-1",
            poll_interval=0.01,
            poll_timeout=1,
            success_json_path="$.done",
            failure_json_path=None,
        )

        assert response.json() == {"done": True}

    def test_poll_get_request_exception_attaches_failure_log_then_reraises(self, monkeypatch):
        created_loggers: list[DummyLogger] = []

        def create_logger(*args: Any, **kwargs: Any) -> DummyLogger:
            logger = DummyLogger(*args, **kwargs)
            created_loggers.append(logger)
            return logger

        monkeypatch.setattr("common.request_middleware.ApiCallLogger", create_logger)
        client = BaseRequest(config=DummyConfig(), middlewares=[LoggingMiddleware()])
        request_error = requests.Timeout("poll timeout")
        client.session.request = lambda method, url, **kwargs: (_ for _ in ()).throw(request_error)  # type: ignore[method-assign]

        with pytest.raises(requests.Timeout) as exc_info:
            client.poll_get(
                "/v1/media/tasks/task-1",
                poll_interval=0.01,
                poll_timeout=1,
                success_json_path="$.done",
                failure_json_path=None,
            )

        assert exc_info.value is request_error
        assert len(created_loggers) == 1
        assert created_loggers[0].failure_errors == [request_error]

    def test_poll_get_failure_status_attaches_final_response_once(self, monkeypatch):
        created_loggers: list[DummyLogger] = []

        def create_logger(*args: Any, **kwargs: Any) -> DummyLogger:
            logger = DummyLogger(*args, **kwargs)
            created_loggers.append(logger)
            return logger

        monkeypatch.setattr("common.request_middleware.ApiCallLogger", create_logger)
        client = BaseRequest(config=DummyConfig(), middlewares=[LoggingMiddleware()])
        client.session.request = lambda method, url, **kwargs: make_response(  # type: ignore[method-assign]
            url=url,
            json_text='{"error": {"message": "failed"}}',
        )

        with pytest.raises(AssertionError, match="poll_get failed"):
            client.poll_get(
                "/v1/media/tasks/task-1",
                poll_interval=0.01,
                poll_timeout=1,
                success_json_path="$.done",
                failure_json_path="$.error",
            )

        assert len(created_loggers) == 1
        assert len(created_loggers[0].success_responses) == 1

    def test_poll_get_timeout_attaches_last_response_once(self, monkeypatch):
        created_loggers: list[DummyLogger] = []

        def create_logger(*args: Any, **kwargs: Any) -> DummyLogger:
            logger = DummyLogger(*args, **kwargs)
            created_loggers.append(logger)
            return logger

        monkeypatch.setattr("common.request_middleware.ApiCallLogger", create_logger)
        client = BaseRequest(config=DummyConfig(), middlewares=[LoggingMiddleware()])
        client.session.request = lambda method, url, **kwargs: make_response(  # type: ignore[method-assign]
            url=url,
            json_text='{"done": null}',
        )

        with pytest.raises(TimeoutError, match="poll_get timed out"):
            client.poll_get(
                "/v1/media/tasks/task-1",
                poll_interval=0.01,
                poll_timeout=0.01,
                success_json_path="$.done",
                failure_json_path=None,
            )

        assert created_loggers
        assert sum(len(logger.success_responses) for logger in created_loggers) == 1


class RecordingMiddleware:
    def __init__(
        self,
        name: str,
        events: list[str],
        contexts: list[RequestContext] | None = None,
    ):
        self.name = name
        self.events = events
        self.contexts = contexts

    def before_request(self, context: RequestContext) -> None:
        self.events.append(f"{self.name}.before")
        if self.contexts is not None:
            self.contexts.append(context)

    def after_response(self, context: RequestContext, response: requests.Response) -> None:
        self.events.append(f"{self.name}.after")

    def on_exception(self, context: RequestContext, error: BaseException) -> None:
        self.events.append(f"{self.name}.exception:{type(error).__name__}")


class BrokenBeforeMiddleware:
    def before_request(self, context: RequestContext) -> None:
        raise ValueError("broken")

    def after_response(self, context: RequestContext, response: requests.Response) -> None:
        return None

    def on_exception(self, context: RequestContext, error: BaseException) -> None:
        return None


class BrokenExceptionMiddleware:
    def before_request(self, context: RequestContext) -> None:
        return None

    def after_response(self, context: RequestContext, response: requests.Response) -> None:
        return None

    def on_exception(self, context: RequestContext, error: BaseException) -> None:
        raise ValueError("broken exception hook")


class CaptureContextMiddleware:
    def __init__(self, contexts: list[RequestContext]):
        self.contexts = contexts

    def before_request(self, context: RequestContext) -> None:
        self.contexts.append(context)
        context.attributes["request_index"] = len(self.contexts)

    def after_response(self, context: RequestContext, response: requests.Response) -> None:
        return None

    def on_exception(self, context: RequestContext, error: BaseException) -> None:
        return None


class MutateNestedJsonMiddleware:
    def before_request(self, context: RequestContext) -> None:
        context.kwargs["json"]["input"]["messages"][0]["content"][0]["text"] = "mutated"

    def after_response(self, context: RequestContext, response: requests.Response) -> None:
        return None

    def on_exception(self, context: RequestContext, error: BaseException) -> None:
        return None


class ConcurrentMutationMiddleware:
    def __init__(self, barrier: Barrier, observed_texts: list[str]):
        self.barrier = barrier
        self.observed_texts = observed_texts

    def before_request(self, context: RequestContext) -> None:
        self.barrier.wait(timeout=3)
        current_text = context.kwargs["json"]["input"]["messages"][0]["content"][0]["text"]
        suffix = current_text.rsplit("-", 1)[-1]
        context.kwargs["json"]["input"]["messages"][0]["content"][0]["text"] = f"mutated-{suffix}"
        self.observed_texts.append(context.kwargs["json"]["input"]["messages"][0]["content"][0]["text"])

    def after_response(self, context: RequestContext, response: requests.Response) -> None:
        return None

    def on_exception(self, context: RequestContext, error: BaseException) -> None:
        return None


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


def make_response(
    *,
    url: str,
    method: str = "GET",
    json_text: str = '{"ok": true}',
) -> requests.Response:
    response = requests.Response()
    response.status_code = 200
    response.reason = "OK"
    response._content = json_text.encode("utf-8")
    response.headers["Content-Type"] = "application/json"
    response.request = requests.Request(method, url).prepare()
    return response
