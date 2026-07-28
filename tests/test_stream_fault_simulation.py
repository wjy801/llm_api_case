from __future__ import annotations

import pytest
import requests

from module.smoke.task import SmokeTask
from tests.mock_helpers import FakeStreamResponse


def test_collect_stream_chat_completion_chunks_accepts_valid_stream():
    response = FakeStreamResponse(
        lines=[
            b'data: {"id":"chatcmpl-001","object":"chat.completion.chunk","choices":[]}',
            b'data: {"id":"chatcmpl-001","usage":{"total_tokens":2},"choices":[]}',
            b"data: [DONE]",
        ],
        headers={"x-oneapi-request-id": "request-001"},
    )

    result = SmokeTask().collect_stream_chat_completion_chunks(response)  # type: ignore[arg-type]

    assert result.raw_data_lines[-1] == "data: [DONE]"
    assert [chunk["id"] for chunk in result.chunks] == ["chatcmpl-001", "chatcmpl-001"]
    assert response.closed is True


def test_collect_stream_chat_completion_chunks_rejects_invalid_json_chunk():
    response = FakeStreamResponse(
        lines=[
            b"data: not-json",
            b"data: [DONE]",
        ],
        headers={"x-oneapi-request-id": "request-001"},
    )

    with pytest.raises(AssertionError, match="Stream data chunk is not valid JSON"):
        SmokeTask().collect_stream_chat_completion_chunks(response)  # type: ignore[arg-type]

    assert response.closed is True


def test_collect_stream_chat_completion_chunks_rejects_non_data_line():
    response = FakeStreamResponse(
        lines=[
            b"event: message",
            b"data: [DONE]",
        ],
        headers={"x-oneapi-request-id": "request-001"},
    )

    with pytest.raises(AssertionError, match="Stream chunk should start with 'data:'"):
        SmokeTask().collect_stream_chat_completion_chunks(response)  # type: ignore[arg-type]

    assert response.closed is True


def test_collect_stream_chat_completion_chunks_rejects_missing_done_line():
    response = FakeStreamResponse(
        lines=[
            b'data: {"id":"chatcmpl-001","choices":[]}',
        ],
        headers={"x-oneapi-request-id": "request-001"},
    )

    with pytest.raises(AssertionError, match="Stream response should end with 'data: \\[DONE\\]'"):
        SmokeTask().collect_stream_chat_completion_chunks(response)  # type: ignore[arg-type]

    assert response.closed is True


def test_collect_stream_chat_completion_chunks_reraises_mid_stream_error_and_closes_response():
    error = requests.exceptions.ChunkedEncodingError("stream interrupted")
    response = FakeStreamResponse(
        lines=[
            b'data: {"id":"chatcmpl-001","choices":[]}',
            b"data: [DONE]",
        ],
        headers={"x-oneapi-request-id": "request-001"},
        error_after=1,
        error=error,
    )

    with pytest.raises(requests.exceptions.ChunkedEncodingError) as exc_info:
        SmokeTask().collect_stream_chat_completion_chunks(response)  # type: ignore[arg-type]

    assert exc_info.value is error
    assert response.closed is True


def test_interrupt_stream_chat_completion_reads_request_id_and_closes_response(monkeypatch):
    times = iter([0.0, 0.0, 20.0])
    monkeypatch.setattr("module.smoke.task.time.monotonic", lambda: next(times))
    response = FakeStreamResponse(
        lines=[
            b'data: {"id":"chatcmpl-001","choices":[]}',
            b'data: {"id":"chatcmpl-001","choices":[]}',
            b"data: [DONE]",
        ],
        headers={"x-oneapi-request-id": "request-001"},
    )

    result = SmokeTask().interrupt_stream_chat_completion(  # type: ignore[arg-type]
        response,
        max_duration_seconds=15,
        print_raw_lines=False,
    )

    assert result.request_id == "request-001"
    assert response.closed is True
