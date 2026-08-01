from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json

import requests

from common.base_request import BaseRequest
from common.context_executor import submit_with_context
from common.retry import RetryPolicy
from common.retry_executor import RetryExecutor
from quality.storage import read_jsonl


class DummyConfig:
    base_url = "https://example.com"
    api_key = "secret"
    timeout = 5


def _response(status: int = 200, body=None) -> requests.Response:
    response = requests.Response()
    response.status_code = status
    response.url = "https://example.com/v1/items"
    response._content = json.dumps(body or {}).encode("utf-8")
    response.headers["Content-Type"] = "application/json"
    return response


def test_http_request_writes_one_operation_group_and_event(semantic_runtime):
    client = BaseRequest(config=DummyConfig())
    captured_kwargs = []
    client.session.request = lambda method, url, **kwargs: (
        captured_kwargs.append(kwargs) or _response(body={"usage": {"prompt_tokens": 2}})
    )  # type: ignore[method-assign]

    response = client.get(
        "/v1/items",
        _attach_log=False,
        _quality_operation_name="items",
        _quality_traffic_role="workload",
    )

    assert response.status_code == 200
    assert all(not key.startswith("_quality_") for key in captured_kwargs[0])
    groups = read_jsonl(semantic_runtime.semantic.paths.request_groups)
    operations = read_jsonl(semantic_runtime.semantic.paths.operations)
    assert len(groups) == len(operations) == 1
    assert groups[0]["attempt_count"] == 1
    assert groups[0]["attempt_event_ids"] == [operations[0]["usage"]["source_request_event_ids"][0]]
    assert operations[0]["operation_name"] == "items"
    assert operations[0]["usage"]["input_tokens"] == 2


def test_retry_attempts_share_group_and_only_executed_wait_is_counted(semantic_runtime):
    executor = RetryExecutor(sleeper=lambda _seconds: None)
    client = BaseRequest(config=DummyConfig(), retry_executor=executor)
    responses = [_response(503), _response(200, {"usage": {"completion_tokens": 3}})]
    client.session.request = lambda method, url, **kwargs: responses.pop(0)  # type: ignore[method-assign]

    client.get(
        "/v1/items",
        retry_policy=RetryPolicy(
            max_attempts=2,
            base_delay=0.01,
            jitter=False,
            max_elapsed=None,
        ),
        _attach_log=False,
    )

    group = read_jsonl(semantic_runtime.semantic.paths.request_groups)[0]
    operation = read_jsonl(semantic_runtime.semantic.paths.operations)[0]
    assert group["attempt_count"] == 2
    assert group["retry_wait_ms"] == 10
    assert group["first_status_code"] == 503
    assert group["final_status_code"] == 200
    assert operation["outcome"] == "success"
    assert operation["usage"]["completeness"] == "partial"


def test_max_elapsed_blocked_retry_does_not_count_unexecuted_wait(semantic_runtime):
    executor = RetryExecutor(sleeper=lambda _seconds: None)
    client = BaseRequest(config=DummyConfig(), retry_executor=executor)
    client.session.request = lambda method, url, **kwargs: _response(503)  # type: ignore[method-assign]

    response = client.get(
        "/v1/items",
        retry_policy=RetryPolicy(
            max_attempts=2,
            base_delay=1,
            jitter=False,
            max_elapsed=0.000000001,
        ),
        _attach_log=False,
    )

    assert response.status_code == 503
    group = read_jsonl(semantic_runtime.semantic.paths.request_groups)[0]
    assert group["attempt_count"] == 1
    assert group["retry_wait_ms"] == 0


def test_same_case_concurrent_requests_get_distinct_operation_and_group_ids(semantic_runtime):
    def call_once(index: int) -> int:
        client = BaseRequest(config=DummyConfig())
        client.session.request = lambda method, url, **kwargs: _response(200, {"usage": {"prompt_tokens": index}})  # type: ignore[method-assign]
        try:
            return client.get("/v1/items", _attach_log=False).status_code
        finally:
            client.close()

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [submit_with_context(executor, call_once, index) for index in range(1, 5)]
        assert [future.result() for future in futures] == [200, 200, 200, 200]

    groups = read_jsonl(semantic_runtime.semantic.paths.request_groups)
    operations = read_jsonl(semantic_runtime.semantic.paths.operations)
    assert len(groups) == len(operations) == 4
    assert len({item["request_group_id"] for item in groups}) == 4
    assert len({item["operation_id"] for item in operations}) == 4


def test_semantic_observer_failure_does_not_change_http_result_or_p0_metric(
    semantic_runtime,
    monkeypatch,
):
    client = BaseRequest(config=DummyConfig())
    client.session.request = lambda method, url, **kwargs: _response(200)  # type: ignore[method-assign]
    monkeypatch.setattr(
        semantic_runtime.semantic,
        "observe_request_metric",
        lambda group_id, metric: (_ for _ in ()).throw(OSError("semantic unavailable")),
    )

    response = client.get("/v1/items", _attach_log=False)

    assert response.status_code == 200
    p0_metrics = read_jsonl(semantic_runtime.p0.paths.requests)
    issues = read_jsonl(semantic_runtime.semantic.paths.integrity)
    assert len(p0_metrics) == 1
    assert any(issue["code"] == "request_metric_observe_failed" for issue in issues)
