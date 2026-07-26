from __future__ import annotations

import json

import pytest
import requests

from common.polling import (
    PollingFailedError,
    PollingPolicy,
    PollingState,
    PollingTimeoutError,
    PollingTransition,
    PollingUnknownStateError,
    evaluate_polling_response,
    format_polling_transitions,
)


def test_polling_policy_rejects_invalid_json_path():
    with pytest.raises(ValueError, match="status_json_path"):
        PollingPolicy(status_json_path="status")


def test_evaluate_pending_status():
    evaluation = evaluate_polling_response(make_response({"status": "queued"}), PollingPolicy())

    assert evaluation.state is PollingState.PENDING
    assert evaluation.raw_status == "queued"


def test_evaluate_success_status():
    evaluation = evaluate_polling_response(make_response({"status": "succeeded"}), PollingPolicy())

    assert evaluation.state is PollingState.SUCCESS
    assert evaluation.raw_status == "succeeded"


def test_evaluate_failure_status():
    evaluation = evaluate_polling_response(make_response({"status": "failed"}), PollingPolicy())

    assert evaluation.state is PollingState.FAILURE
    assert evaluation.raw_status == "failed"


def test_unknown_state_defaults_to_unknown():
    evaluation = evaluate_polling_response(make_response({"status": "paused"}), PollingPolicy())

    assert evaluation.state is PollingState.UNKNOWN
    assert evaluation.raw_status == "paused"


def test_unknown_state_can_be_treated_as_pending():
    evaluation = evaluate_polling_response(
        make_response({"status": "paused"}),
        PollingPolicy(unknown="pending"),
    )

    assert evaluation.state is PollingState.PENDING
    assert evaluation.raw_status == "paused"


def test_result_json_path_takes_success_priority_after_error_check():
    evaluation = evaluate_polling_response(
        make_response({"status": "running", "result": {"urls": ["https://example.com/image.png"]}}),
        PollingPolicy(result_json_path="$.result.urls"),
    )

    assert evaluation.state is PollingState.SUCCESS
    assert evaluation.result_value == ["https://example.com/image.png"]


def test_error_json_path_takes_failure_priority():
    evaluation = evaluate_polling_response(
        make_response({"status": "succeeded", "error": {"message": "failed"}}),
        PollingPolicy(result_json_path="$.result.urls", error_json_path="$.error"),
    )

    assert evaluation.state is PollingState.FAILURE
    assert evaluation.error_value == {"message": "failed"}


def test_invalid_json_raises_assertion_error_with_redacted_body():
    response = make_raw_response("api_key=secret")

    with pytest.raises(AssertionError) as exc_info:
        evaluate_polling_response(response, PollingPolicy())

    message = str(exc_info.value)
    assert "not valid JSON" in message
    assert "api_key=%3Credacted%3E" in message
    assert "secret" not in message


def test_polling_exceptions_expose_context():
    response = make_response({"status": "failed"})
    transitions = [
        PollingTransition(1, 0.0, PollingState.PENDING, "queued", 200),
        PollingTransition(2, 1.0, PollingState.FAILURE, "failed", 200),
    ]

    error = PollingFailedError(
        path="/v1/media/tasks/task-001",
        last_status="failed",
        last_response=response,
        transitions=transitions,
        error_value={"message": "failed"},
    )

    assert error.path == "/v1/media/tasks/task-001"
    assert error.last_status == "failed"
    assert error.last_response is response
    assert error.transitions == transitions
    assert "queued -> failed" in str(error)


def test_unknown_state_error_exposes_context():
    response = make_response({"status": "paused"})
    transitions = [PollingTransition(1, 0.0, PollingState.UNKNOWN, "paused", 200)]

    error = PollingUnknownStateError(
        path="/v1/media/tasks/task-001",
        last_status="paused",
        last_response=response,
        transitions=transitions,
    )

    assert error.last_response is response
    assert "unknown state" in str(error)


def test_timeout_error_exposes_last_response_and_transitions():
    response = make_response({"status": "running"})
    transitions = [PollingTransition(1, 0.0, PollingState.PENDING, "running", 200)]

    error = PollingTimeoutError(
        path="/v1/media/tasks/task-001",
        timeout=3,
        last_status="running",
        last_response=response,
        transitions=transitions,
    )

    assert error.timeout == 3
    assert error.last_response is response
    assert "running" in str(error)


def test_format_polling_transitions():
    text = format_polling_transitions(
        [
            PollingTransition(1, 0.0, PollingState.PENDING, "queued", 200),
            PollingTransition(2, 1.0, PollingState.SUCCESS, "succeeded", 200),
        ]
    )

    assert "1. 0.000s 'queued' -> pending HTTP 200" in text
    assert "2. 1.000s 'succeeded' -> success HTTP 200" in text


def make_response(body: object) -> requests.Response:
    response = requests.Response()
    response.status_code = 200
    response.reason = "OK"
    response.headers["Content-Type"] = "application/json"
    response._content = json.dumps(body).encode("utf-8")
    return response


def make_raw_response(body: str) -> requests.Response:
    response = requests.Response()
    response.status_code = 200
    response.reason = "OK"
    response.headers["Content-Type"] = "text/plain"
    response._content = body.encode("utf-8")
    return response
