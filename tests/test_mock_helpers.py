from __future__ import annotations

import pytest
import requests

from tests.mock_helpers import (
    FakeApiCallLogger,
    FakeStreamResponse,
    SequenceTransport,
    SleepRecorder,
    connect_timeout,
    connection_error,
    create_fake_logger,
    make_response,
    polling_responses,
    read_timeout,
    timeout_error,
)


def test_make_response_builds_json_response_with_prepared_request():
    response = make_response(
        "https://example.com/v1/models",
        method="POST",
        status_code=201,
        headers={"x-oneapi-request-id": "request-001"},
        json_body={"id": "model-001"},
    )

    assert response.status_code == 201
    assert response.json() == {"id": "model-001"}
    assert response.headers["Content-Type"] == "application/json"
    assert response.headers["x-oneapi-request-id"] == "request-001"
    assert response.request.method == "POST"
    assert response.request.url == "https://example.com/v1/models"


def test_make_response_builds_text_response():
    response = make_response(
        "https://example.com/v1/models",
        text_body="not-json",
        content_type="text/plain; charset=utf-8",
    )

    assert response.text == "not-json"
    assert response.headers["Content-Type"] == "text/plain; charset=utf-8"
    with pytest.raises(ValueError):
        response.json()


def test_sequence_transport_returns_responses_and_records_calls():
    first = make_response("https://example.com/v1/models", status_code=503)
    second = make_response("https://example.com/v1/models", status_code=200)
    transport = SequenceTransport([first, second])

    assert transport("GET", "https://example.com/v1/models", timeout=1) is first
    assert transport("GET", "https://example.com/v1/models", headers={"X-Test": "1"}) is second

    assert transport.remaining == 0
    assert [call.method for call in transport.calls] == ["GET", "GET"]
    assert transport.calls[0].kwargs == {"timeout": 1}
    assert transport.calls[1].kwargs == {"headers": {"X-Test": "1"}}


def test_sequence_transport_raises_configured_exception():
    error = requests.Timeout("temporary timeout")
    transport = SequenceTransport([error])

    with pytest.raises(requests.Timeout) as exc_info:
        transport("GET", "https://example.com/v1/models")

    assert exc_info.value is error
    assert transport.remaining == 0


def test_sequence_transport_raises_when_results_are_exhausted():
    transport = SequenceTransport([])

    with pytest.raises(AssertionError, match="no response left"):
        transport("GET", "https://example.com/v1/models")


def test_exception_factories_return_requests_exceptions():
    assert isinstance(connection_error(), requests.ConnectionError)
    assert isinstance(connect_timeout(), requests.ConnectTimeout)
    assert isinstance(read_timeout(), requests.ReadTimeout)
    assert isinstance(timeout_error(), requests.Timeout)


def test_sleep_recorder_records_calls_and_advances_clock():
    clock = {"value": 0.0}

    def advance(seconds: float) -> None:
        clock["value"] += seconds

    sleep = SleepRecorder(advance_clock=advance)

    sleep(0.1)
    sleep(0.2)

    assert sleep.calls == [0.1, 0.2]
    assert clock["value"] == pytest.approx(0.3)


def test_fake_api_call_logger_records_all_attachments():
    logger = FakeApiCallLogger("GET", "https://example.com/v1/models")
    response = make_response("https://example.com/v1/models")
    error = requests.Timeout("timeout")

    logger.attach_success(response)
    logger.attach_failure(error)
    logger.attach_retry_records(["retry"])
    logger.attach_polling_transitions("queued -> succeeded")

    assert logger.success_responses == [response]
    assert logger.failure_errors == [error]
    assert logger.retry_records == [["retry"]]
    assert logger.polling_transitions == ["queued -> succeeded"]


def test_create_fake_logger_appends_to_list():
    created_loggers: list[FakeApiCallLogger] = []

    logger = create_fake_logger(created_loggers, "GET", "https://example.com/v1/models")

    assert created_loggers == [logger]
    assert logger.args == ("GET", "https://example.com/v1/models")


def test_polling_responses_builds_status_sequence():
    responses = polling_responses(
        "https://example.com/v1/media/tasks/task-001",
        ["queued", "running", "succeeded"],
        result={"urls": ["https://example.com/image.png"]},
    )

    assert [response.json()["status"] for response in responses] == ["queued", "running", "succeeded"]
    assert responses[-1].json()["result"] == {"urls": ["https://example.com/image.png"]}


def test_polling_responses_adds_error_for_failure_states():
    responses = polling_responses(
        "https://example.com/v1/media/tasks/task-001",
        ["queued", "failed"],
        error={"message": "failed"},
    )

    assert responses[-1].json() == {"status": "failed", "error": {"message": "failed"}}


def test_polling_responses_rejects_mismatched_status_codes():
    with pytest.raises(ValueError, match="status_code sequence length"):
        polling_responses(
            "https://example.com/v1/media/tasks/task-001",
            ["queued", "running"],
            status_code=[200],
        )


def test_fake_stream_response_iterates_bytes_and_closes():
    response = FakeStreamResponse(
        lines=[
            b'data: {"id":"chunk-1"}',
            "data: [DONE]",
        ],
        headers={"x-oneapi-request-id": "request-001"},
    )

    assert list(response.iter_lines(decode_unicode=False)) == [
        b'data: {"id":"chunk-1"}',
        b"data: [DONE]",
    ]
    assert list(response.iter_lines(decode_unicode=True)) == [
        'data: {"id":"chunk-1"}',
        "data: [DONE]",
    ]
    assert "chunk-1" in response.text
    response.close()
    assert response.closed is True


def test_fake_stream_response_can_raise_mid_stream():
    error = requests.exceptions.ChunkedEncodingError("stream interrupted")
    response = FakeStreamResponse(
        lines=[b'data: {"id":"chunk-1"}', b"data: [DONE]"],
        error_after=1,
        error=error,
    )

    iterator = response.iter_lines()

    assert next(iterator) == b'data: {"id":"chunk-1"}'
    with pytest.raises(requests.exceptions.ChunkedEncodingError) as exc_info:
        next(iterator)

    assert exc_info.value is error
