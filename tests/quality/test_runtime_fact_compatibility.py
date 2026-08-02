from __future__ import annotations

import json

import requests

from common.base_request import BaseRequest
from common.retry import RetryPolicy
from common.retry_executor import RetryExecutor
from quality.storage import read_jsonl


class DummyConfig:
    base_url = "https://example.com"
    api_key = "secret"
    timeout = 5


def _response(status: int, body=None) -> requests.Response:
    response = requests.Response()
    response.status_code = status
    response.url = "https://example.com/v1/items"
    response._content = json.dumps(body or {}).encode("utf-8")
    response.headers["Content-Type"] = "application/json"
    return response


def test_retry_fact_relationships_are_preserved_after_runtime_hook_indirection(
    semantic_runtime,
):
    client = BaseRequest(
        config=DummyConfig(),
        retry_executor=RetryExecutor(sleeper=lambda _seconds: None),
    )
    responses = [
        _response(503),
        _response(200, {"usage": {"prompt_tokens": 3, "completion_tokens": 5}}),
    ]
    client.session.request = lambda method, url, **kwargs: responses.pop(0)  # type: ignore[method-assign]

    result = client.get(
        "/v1/items",
        _attach_log=False,
        _quality_operation_name="items",
        _quality_traffic_role="workload",
        retry_policy=RetryPolicy(
            max_attempts=2,
            base_delay=0.01,
            jitter=False,
            max_elapsed=None,
        ),
    )

    metrics = read_jsonl(semantic_runtime.p0.paths.requests)
    groups = read_jsonl(semantic_runtime.semantic.paths.request_groups)
    operations = read_jsonl(semantic_runtime.semantic.paths.operations)

    assert result.status_code == 200
    assert [item["attempt_index"] for item in metrics] == [1, 2]
    assert len(groups) == len(operations) == 1
    assert groups[0]["attempt_event_ids"] == [item["request_event_id"] for item in metrics]
    assert groups[0]["retry_wait_ms"] == 10
    assert operations[0]["operation_name"] == "items"
    assert operations[0]["traffic_role"] == "workload"
    assert operations[0]["usage"]["input_tokens"] == 3
    assert operations[0]["usage"]["output_tokens"] == 5
