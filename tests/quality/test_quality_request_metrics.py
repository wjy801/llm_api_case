from __future__ import annotations

import json

import pytest
import requests

from common.base_request import BaseRequest
from common.polling import PollingPolicy
from common.request_context import RequestContext
from common.request_middleware import QualityMetricsMiddleware
from common.runtime_hooks import bind_runtime_hooks, reset_runtime_hooks
from common.retry import RetryPolicy
from quality.collector import QualityCollector, configure_collector, reset_collector
from quality.request_metrics import record_exception, record_response, start_request_capture
from quality.runtime_context import (
    QualityCaseContext,
    QualityRunContext,
    clear_case_context,
    clear_run_context,
    reset_case_context,
    reset_run_context,
    set_case_context,
    set_run_context,
)
from quality.runtime_adapter import QualityRuntimeHooks
from quality.storage import read_jsonl


class DummyConfig:
    base_url = "https://example.com"
    api_key = "secret"
    timeout = 5


@pytest.fixture
def quality_runtime(tmp_path):
    run_context = QualityRunContext(
        run_id="run-request",
        execution_id="serial-pool",
        worker_id="master",
        output_dir=tmp_path / "quality",
    )
    collector = configure_collector(run_context)
    run_token = set_run_context(run_context)
    case_token = set_case_context(
        QualityCaseContext(
            case_id="module/test_api.py::test_request",
            invocation_id="inv-request",
            nodeid="module/test_api.py::test_request",
            param_hash="hash",
        )
    )
    hooks_token = bind_runtime_hooks(QualityRuntimeHooks())
    try:
        yield collector
    finally:
        reset_runtime_hooks(hooks_token)
        reset_case_context(case_token)
        reset_run_context(run_token)
        reset_collector()
        clear_case_context()
        clear_run_context()


def _response(status=200, body=None, headers=None, url="https://example.com/v1/items/12345"):
    response = requests.Response()
    response.status_code = status
    response.url = url
    response.headers.update(headers or {})
    if body is not None:
        response._content = json.dumps(body).encode("utf-8")
        response.headers.setdefault("Content-Type", "application/json")
    else:
        response._content = b""
    return response


def _context(*, protocol="http", retry_policy=None, polling_policy=None, method="GET"):
    return RequestContext(
        method=method,
        path="/v1/items/12345?token=secret",
        url="https://example.com/v1/items/12345?token=secret",
        kwargs={},
        protocol=protocol,
        retry_policy=retry_policy,
        polling_policy=polling_policy,
    )


def test_http_success_extracts_safe_identity_usage_and_request_id(quality_runtime):
    context = _context()
    start_request_capture(context)

    record_response(
        context,
        _response(
            body={"request_id": "body-id", "usage": {"prompt_tokens": 0, "completion_tokens": 7}},
            headers={"X-OneAPI-Request-Id": "header-id"},
        ),
    )

    metric = read_jsonl(quality_runtime.paths.requests)[0]
    assert metric["business_status"] == "success"
    assert metric["server_request_id"] == "header-id"
    assert metric["url_template"] == "/v1/items/{id}"
    assert metric["interface_id"] == "GET /v1/items/{id} http"
    assert metric["usage"] == {
        "input_tokens": 0,
        "media_count": None,
        "output_tokens": 7,
    }
    assert "secret" not in json.dumps(metric)


def test_http_failure_and_timeout_exception_are_recorded(quality_runtime):
    failed_context = _context()
    start_request_capture(failed_context)
    record_response(failed_context, _response(status=503))

    timeout_context = _context(retry_policy=RetryPolicy(base_delay=0, jitter=False))
    timeout_context.attributes["attempt_index"] = 2
    start_request_capture(timeout_context)
    record_exception(timeout_context, requests.Timeout("token=secret"))

    failed, timeout = read_jsonl(quality_runtime.paths.requests)
    assert failed["business_status"] == "failed"
    assert failed["status_code"] == 503
    assert timeout["business_status"] == "failed"
    assert timeout["timeout"] is True
    assert timeout["retryable"] is True
    assert timeout["attempt_index"] == 2
    assert timeout["error_type"] == "Timeout"


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ({"data": [{"url": "https://example.com/a.png"}]}, 1),
        ({"result": {"urls": ["https://example.com/a.png", "https://example.com/b.png"]}}, 2),
    ],
)
def test_http_media_count_uses_only_explicit_response_results(body, expected, quality_runtime):
    context = _context(method="POST")
    start_request_capture(context)

    record_response(context, _response(body=body))

    metric = read_jsonl(quality_runtime.paths.requests)[0]
    assert metric["usage"]["media_count"] == expected


def test_post_without_idempotency_is_not_marked_retryable(quality_runtime):
    context = _context(method="POST", retry_policy=RetryPolicy(base_delay=0, jitter=False))
    start_request_capture(context)

    record_response(context, _response(status=503))

    metric = read_jsonl(quality_runtime.paths.requests)[0]
    assert metric["retryable"] is False


@pytest.mark.parametrize(
    ("status", "expected"),
    [("queued", "unknown"), ("succeeded", "success"), ("failed", "failed"), ("other", "failed")],
)
def test_polling_uses_existing_policy(status, expected, quality_runtime):
    context = _context(protocol="polling", polling_policy=PollingPolicy())
    start_request_capture(context)

    record_response(context, _response(body={"status": status}))

    metric = read_jsonl(quality_runtime.paths.requests)[0]
    assert metric["protocol"] == "polling"
    assert metric["business_status"] == expected


def test_sse_does_not_read_response_body(quality_runtime, monkeypatch):
    context = _context(protocol="sse")
    response = _response(headers={"X-Request-Id": "stream-id"})
    accessed = []
    monkeypatch.setattr(
        response,
        "json",
        lambda: accessed.append("json") or (_ for _ in ()).throw(AssertionError("body read")),
    )
    start_request_capture(context)

    record_response(context, response)

    metric = read_jsonl(quality_runtime.paths.requests)[0]
    assert accessed == []
    assert metric["protocol"] == "sse"
    assert metric["business_status"] == "unknown"
    assert metric["usage"] == {
        "input_tokens": None,
        "media_count": None,
        "output_tokens": None,
    }


def test_missing_case_context_writes_integrity_only(quality_runtime):
    clear_case_context()
    context = _context()
    start_request_capture(context)

    record_response(context, _response())

    assert read_jsonl(quality_runtime.paths.requests) == []
    issues = read_jsonl(quality_runtime.paths.integrity)
    assert issues[0]["code"] == "missing_case_context"


def test_middleware_capture_failure_is_fail_open(quality_runtime, monkeypatch):
    context = _context()
    response = _response()
    monkeypatch.setattr(
        "quality.request_metrics.record_response",
        lambda context, response: (_ for _ in ()).throw(RuntimeError("token=secret")),
    )

    QualityMetricsMiddleware().after_response(context, response)

    issues = read_jsonl(quality_runtime.paths.integrity)
    assert issues[0]["code"] == "request_capture_failed"
    assert "secret" not in issues[0]["message"]


def test_base_request_retry_records_each_real_attempt(quality_runtime):
    client = BaseRequest(config=DummyConfig())
    responses = [_response(status=503), _response(status=200)]
    client.session.request = lambda method, url, **kwargs: responses.pop(0)  # type: ignore[method-assign]
    policy = RetryPolicy(max_attempts=2, base_delay=0, jitter=False, max_elapsed=None)

    response = client.get("/v1/items/12345", retry_policy=policy, _attach_log=False)
    client.close()

    assert response.status_code == 200
    metrics = read_jsonl(quality_runtime.paths.requests)
    assert [metric["attempt_index"] for metric in metrics] == [1, 2]
    assert [metric["business_status"] for metric in metrics] == ["failed", "success"]
    assert len({metric["request_event_id"] for metric in metrics}) == 2


def test_base_request_passes_polling_protocol_and_policy(quality_runtime, monkeypatch):
    client = BaseRequest(config=DummyConfig())
    responses = [
        _response(body={"status": "queued"}),
        _response(body={"status": "succeeded"}),
    ]
    client.session.request = lambda method, url, **kwargs: responses.pop(0)  # type: ignore[method-assign]
    monkeypatch.setattr("common.base_request.time.sleep", lambda seconds: None)

    response = client.poll_get(
        "/v1/items/12345",
        poll_interval=0.01,
        poll_timeout=1,
        polling_policy=PollingPolicy(),
    )
    client.close()

    assert response.status_code == 200
    metrics = read_jsonl(quality_runtime.paths.requests)
    assert [metric["protocol"] for metric in metrics] == ["polling", "polling"]
    assert [metric["business_status"] for metric in metrics] == ["unknown", "success"]


def test_base_request_marks_stream_as_sse(quality_runtime):
    client = BaseRequest(config=DummyConfig())
    client.session.request = lambda method, url, **kwargs: _response()  # type: ignore[method-assign]

    client.post("/v1/stream", stream=True, _attach_log=False)
    client.close()

    metric = read_jsonl(quality_runtime.paths.requests)[0]
    assert metric["protocol"] == "sse"
    assert metric["business_status"] == "unknown"
