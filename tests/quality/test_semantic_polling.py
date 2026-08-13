from __future__ import annotations

import json

import requests

from common.base_request import BaseRequest
from common.polling import PollingPolicy
from quality.storage import read_jsonl


class DummyConfig:
    base_url = "https://example.com"
    api_key = "secret"
    timeout = 5


def _response(body, status: int = 200) -> requests.Response:
    response = requests.Response()
    response.status_code = status
    response.url = "https://example.com/v1/media/tasks/task-001"
    response._content = json.dumps(body).encode("utf-8")
    response.headers["Content-Type"] = "application/json"
    return response


def test_polling_session_separates_poll_count_from_request_groups(semantic_runtime, monkeypatch):
    client = BaseRequest(config=DummyConfig())
    responses = [_response({"status": "queued"}), _response({"status": "succeeded"})]
    client.session.request = lambda method, url, **kwargs: responses.pop(0)  # type: ignore[method-assign]
    monkeypatch.setattr("common.base_request.time.sleep", lambda _seconds: None)

    result = client.poll_get(
        "/v1/media/tasks/task-001",
        poll_interval=0.01,
        poll_timeout=1,
        polling_policy=PollingPolicy(),
    )

    assert result.json()["status"] == "succeeded"
    session = read_jsonl(semantic_runtime.semantic.paths.polling_sessions)[0]
    groups = read_jsonl(semantic_runtime.semantic.paths.request_groups)
    operation = read_jsonl(semantic_runtime.semantic.paths.operations)[0]
    assert session["poll_count"] == 2
    assert len(session["request_group_ids"]) == len(groups) == 2
    assert session["observed_state_sequence"] == ["pending", "success"]
    assert operation["operation_kind"] == "polling"
    assert operation["timing"]["polling_total_ms"] is not None
    assert all(group["url_template"] == "/v1/media/tasks/{id}" for group in groups)
