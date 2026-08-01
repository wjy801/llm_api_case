from __future__ import annotations

import json

import requests

from common.base_request import BaseRequest
from common.streaming import iter_sse_lines
from quality.storage import read_jsonl


class DummyConfig:
    base_url = "https://example.com"
    api_key = "secret"
    timeout = 5


def _stream_response(lines: list[bytes]) -> requests.Response:
    response = requests.Response()
    response.status_code = 200
    response.url = "https://example.com/v1/chat/completions"
    response._content = b""
    response.headers["Content-Type"] = "text/event-stream"
    response.iter_lines = lambda decode_unicode=False: iter(lines)  # type: ignore[method-assign]
    return response


def _start_stream(semantic_runtime, lines: list[bytes]) -> requests.Response:
    client = BaseRequest(config=DummyConfig())
    response = _stream_response(lines)
    client.session.request = lambda method, url, **kwargs: response  # type: ignore[method-assign]
    return client.post(
        "/v1/chat/completions",
        json={"model": "model-a", "stream": True},
        stream=True,
        _attach_log=False,
        _quality_operation_name="chat_completion_stream",
        _quality_traffic_role="workload",
    )


def test_complete_sse_records_timing_content_and_explicit_usage(semantic_runtime):
    usage = json.dumps({"usage": {"prompt_tokens": 2, "completion_tokens": 3}}).encode()
    content = json.dumps({"choices": [{"delta": {"content": "hello"}}]}).encode()
    response = _start_stream(
        semantic_runtime,
        [b"data: " + content, b"data: " + usage, b"data: [DONE]"],
    )

    lines = list(iter_sse_lines(response))

    assert lines[-1] == "data: [DONE]"
    operation = read_jsonl(semantic_runtime.semantic.paths.operations)[0]
    assert operation["outcome"] == "success"
    assert operation["stream_outcome"] == "complete"
    assert operation["timing"]["first_data_ms"] is not None
    assert operation["timing"]["first_content_ms"] is not None
    assert operation["usage"]["input_tokens"] == 2
    assert operation["usage"]["output_tokens"] == 3
    assert operation["usage"]["completeness"] == "complete"


def test_closed_sse_iterator_is_interrupted_without_changing_iterator_output(semantic_runtime):
    content = json.dumps({"choices": [{"delta": {"content": "hello"}}]}).encode()
    response = _start_stream(semantic_runtime, [b"data: " + content, b"data: [DONE]"])
    iterator = iter_sse_lines(response)

    assert next(iterator).startswith("data:")
    iterator.close()

    operation = read_jsonl(semantic_runtime.semantic.paths.operations)[0]
    assert operation["outcome"] == "interrupted"
    assert operation["stream_outcome"] == "interrupted"


def test_unconsumed_sse_is_finalized_at_case_teardown_boundary(semantic_runtime):
    _start_stream(semantic_runtime, [b"data: [DONE]"])

    semantic_runtime.semantic.finalize_pending(semantic_runtime.case_context.invocation_id)

    operation = read_jsonl(semantic_runtime.semantic.paths.operations)[0]
    issues = read_jsonl(semantic_runtime.semantic.paths.integrity)
    assert operation["outcome"] == "incomplete"
    assert operation["stream_outcome"] == "not_consumed"
    assert any(issue["code"] == "stream_not_finalized" for issue in issues)


def test_non_success_sse_headers_finish_as_failed_without_consuming_body(semantic_runtime):
    client = BaseRequest(config=DummyConfig())
    response = _stream_response([])
    response.status_code = 429
    client.session.request = lambda method, url, **kwargs: response  # type: ignore[method-assign]

    result = client.post(
        "/v1/chat/completions",
        stream=True,
        _attach_log=False,
    )

    assert result.status_code == 429
    operation = read_jsonl(semantic_runtime.semantic.paths.operations)[0]
    assert operation["outcome"] == "failed"
    assert operation["stream_outcome"] == "error"
