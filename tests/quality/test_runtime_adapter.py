from __future__ import annotations

import json

import requests

from common.request_context import RequestContext
from common.runtime_hooks import (
    RuntimeOperationKind,
    RuntimeOperationMetadata,
    RuntimeOperationOutcome,
    RuntimeTrafficRole,
)
from quality.runtime_adapter import QualityRuntimeHooks
from quality.storage import read_jsonl


def _response(body=None, status: int = 200) -> requests.Response:
    response = requests.Response()
    response.status_code = status
    response.url = "https://example.com/v1/items"
    response._content = json.dumps(body or {}).encode("utf-8")
    response.headers["Content-Type"] = "application/json"
    return response


def _context() -> RequestContext:
    return RequestContext(
        method="GET",
        path="/v1/items",
        url="https://example.com/v1/items",
        kwargs={},
        protocol="http",
    )


def test_adapter_links_p0_metric_to_existing_semantic_group(semantic_runtime):
    hooks = QualityRuntimeHooks()
    operation = hooks.begin_operation(
        RuntimeOperationMetadata(
            kind=RuntimeOperationKind.HTTP,
            name="items",
            role=RuntimeTrafficRole.WORKLOAD,
            model_id="model-a",
        )
    )
    group = hooks.start_request_group(
        method="GET",
        path="/v1/items",
        protocol="http",
        configured_max_attempts=1,
    )
    context = _context()
    hooks.bind_request_context(context, group)
    hooks.request_started(context)
    hooks.request_succeeded(context, _response({"usage": {"prompt_tokens": 2}}))
    hooks.finish_request_group(group)
    hooks.finish_operation(operation.native_handle, RuntimeOperationOutcome.SUCCESS)

    metrics = read_jsonl(semantic_runtime.p0.paths.requests)
    groups = read_jsonl(semantic_runtime.semantic.paths.request_groups)
    operations = read_jsonl(semantic_runtime.semantic.paths.operations)

    assert len(metrics) == len(groups) == len(operations) == 1
    assert groups[0]["attempt_event_ids"] == [metrics[0]["request_event_id"]]
    assert operations[0]["request_group_ids"] == [groups[0]["request_group_id"]]
    assert operations[0]["usage"]["input_tokens"] == 2


def test_adapter_request_failure_is_fail_open_and_records_integrity(
    semantic_runtime,
    monkeypatch,
):
    hooks = QualityRuntimeHooks()
    context = _context()
    hooks.request_started(context)
    monkeypatch.setattr(
        "quality.request_metrics.record_response",
        lambda context, response: (_ for _ in ()).throw(RuntimeError("token=secret")),
    )

    hooks.request_succeeded(context, _response())

    issues = read_jsonl(semantic_runtime.p0.paths.integrity)
    assert issues[-1]["code"] == "request_capture_failed"
    assert "secret" not in issues[-1]["message"]
