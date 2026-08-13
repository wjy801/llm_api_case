from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from typing import Any

import pytest
import requests

from common import TestContext as ExportedTestContext
from common.test_context import (
    ContextCleanupError,
    ContextExtractionError,
    ContextVariableError,
    ContextVariableNotFound,
    ContextVariableTypeError,
    TestContext,
)


def make_response(
    body: Any,
    *,
    status_code: int = 200,
    headers: dict[str, str] | None = None,
    cookies: dict[str, str] | None = None,
    content_type: str = "application/json",
) -> requests.Response:
    response = requests.Response()
    response.status_code = status_code
    response.headers["Content-Type"] = content_type
    if headers:
        response.headers.update(headers)
    if cookies:
        for name, value in cookies.items():
            response.cookies.set(name, value)

    if isinstance(body, bytes):
        response._content = body
    elif isinstance(body, str):
        response._content = body.encode("utf-8")
    else:
        response._content = json.dumps(body, ensure_ascii=False).encode("utf-8")
    return response


def test_set_get_require_has_delete_clear_and_snapshot():
    context = TestContext(name="case-001")

    assert context.set("task_id", "task-001") == "task-001"
    assert context.has("task_id") is True
    assert context.get("task_id") == "task-001"
    assert context.require("task_id", expected_type=str) == "task-001"
    assert context.snapshot() == {"task_id": "task-001"}
    assert context.delete("task_id") == "task-001"
    assert context.has("task_id") is False

    context.set("request_id", "request-001")
    context.clear()

    assert context.snapshot() == {}


def test_invalid_variable_name_is_rejected():
    with pytest.raises(ContextVariableError, match="Context variable name"):
        TestContext().set("bad name", "value")


def test_missing_variable_raises_clear_error():
    with pytest.raises(ContextVariableNotFound, match="missing"):
        TestContext().require("missing")


def test_get_returns_default_without_storing_it():
    context = TestContext()

    assert context.get("optional", default="fallback") == "fallback"
    assert context.has("optional") is False


def test_type_mismatch_raises_clear_error_and_redacts_value():
    context = TestContext()
    context.set("api_key", "type-secret")

    with pytest.raises(ContextVariableTypeError) as exc_info:
        context.require("api_key", expected_type=int)

    message = str(exc_info.value)
    assert "Context variable 'api_key' type mismatch" in message
    assert "<redacted>" in message
    assert "type-secret" not in message


def test_extract_json_path_success_returns_and_stores_value():
    context = TestContext()
    response = make_response({"task_id": "task-001"})

    value = context.extract("task_id", response, json_path="$.task_id", expected_type=str)

    assert value == "task-001"
    assert context.get("task_id") == "task-001"


def test_extract_json_path_multiple_returns_all_values():
    context = TestContext()
    response = make_response({"data": [{"id": "a"}, {"id": "b"}]})

    value = context.extract("ids", response, json_path="$.data[*].id", multiple=True)

    assert value == ["a", "b"]
    assert context.get("ids") == ["a", "b"]


def test_extract_json_path_missing_required_reports_variable_path_and_redacted_body():
    context = TestContext()
    response = make_response({"api_key": "missing-secret"})

    with pytest.raises(ContextExtractionError) as exc_info:
        context.extract("task_id", response, json_path="$.task_id")

    message = str(exc_info.value)
    assert "task_id" in message
    assert "json_path='$.task_id'" in message
    assert "status_code=200" in message
    assert "<redacted>" in message
    assert "missing-secret" not in message


def test_extract_invalid_json_reports_redacted_body():
    context = TestContext()
    response = make_response("api_key=invalid-secret", content_type="text/plain")

    with pytest.raises(ContextExtractionError) as exc_info:
        context.extract("task_id", response, json_path="$.task_id")

    message = str(exc_info.value)
    assert "Response body is not valid JSON" in message
    assert "api_key=%3Credacted%3E" in message or "api_key=<redacted>" in message
    assert "invalid-secret" not in message


def test_extract_malformed_json_fails_closed_without_leaking_quoted_sensitive_value():
    context = TestContext()
    response = make_response(
        '{"api_key":"lesson-secret"',
        content_type="application/json",
    )

    with pytest.raises(ContextExtractionError) as exc_info:
        context.extract("task_id", response, json_path="$.task_id")

    message = str(exc_info.value)
    assert "Response body is not valid JSON" in message
    assert "<redacted>" in message
    assert "lesson-secret" not in message


def test_extract_header_is_case_insensitive_and_strips_value():
    context = TestContext()
    response = make_response({}, headers={"X-OneAPI-Request-ID": " request-001 "})

    value = context.extract("request_id", response, header="x-oneapi-request-id")

    assert value == "request-001"
    assert context.get("request_id") == "request-001"


def test_extract_missing_header_error_lists_header_names_without_values():
    context = TestContext()
    response = make_response(
        {},
        headers={
            "X-Trace-ID": "trace-secret",
            "Authorization": "Bearer auth-secret",
        },
    )

    with pytest.raises(ContextExtractionError) as exc_info:
        context.extract("request_id", response, header="x-oneapi-request-id")

    message = str(exc_info.value)
    assert "headers=[" in message
    assert "Authorization" in message
    assert "X-Trace-ID" in message
    assert "trace-secret" not in message
    assert "auth-secret" not in message


def test_extract_cookie_success():
    context = TestContext()
    response = make_response({}, cookies={"session_id": " session-001 "})

    value = context.extract("session_id", response, cookie="session_id")

    assert value == "session-001"
    assert context.get("session_id") == "session-001"


def test_extract_missing_cookie_error_does_not_leak_cookie_values():
    context = TestContext()
    response = make_response({}, headers={"Set-Cookie": "secret_cookie=cookie-secret"})
    response.cookies.set("secret_cookie", "cookie-secret")

    with pytest.raises(ContextExtractionError) as exc_info:
        context.extract("session_id", response, cookie="session_id")

    message = str(exc_info.value)
    assert "cookie='session_id'" in message
    assert "cookie-secret" not in message


def test_extract_regex_from_response_text():
    context = TestContext()
    response = make_response("image=https://example.test/result.png", content_type="text/plain")

    value = context.extract("image_url", response, regex=r"https://[^\s]+")

    assert value == "https://example.test/result.png"


def test_extract_regex_group_from_source_text():
    context = TestContext()

    value = context.extract(
        "task_id",
        regex=r"task=(?P<task_id>task-\d+)",
        group="task_id",
        source_text="task=task-123",
    )

    assert value == "task-123"


def test_extract_requires_exactly_one_source():
    with pytest.raises(ContextExtractionError, match="exactly one source"):
        TestContext().extract("value", make_response({}), json_path="$.id", header="x-id")


def test_extract_first_uses_first_non_empty_source():
    context = TestContext()
    response = make_response({"id": "task-001", "request_id": "request-001"})

    value = context.extract_first(
        "task_id",
        response,
        sources=[
            {"json_path": "$.task_id"},
            {"json_path": "$.id"},
            {"json_path": "$.request_id"},
        ],
        expected_type=str,
    )

    assert value == "task-001"
    assert context.get("task_id") == "task-001"


def test_extract_first_failure_reports_sources_and_redacts_body():
    context = TestContext()
    response = make_response({"api_key": "first-secret"})

    with pytest.raises(ContextExtractionError) as exc_info:
        context.extract_first(
            "task_id",
            response,
            sources=[{"json_path": "$.task_id"}, {"header": "x-task-id"}],
        )

    message = str(exc_info.value)
    assert "Failed to extract required context variable 'task_id' from any source" in message
    assert "json_path='$.task_id'" in message
    assert "header='x-task-id'" in message
    assert "first-secret" not in message


def test_default_required_false_transform_and_expected_type():
    context = TestContext()
    response = make_response({})

    value = context.extract(
        "count",
        response,
        json_path="$.count",
        required=False,
        default="2",
        transform=int,
        expected_type=int,
    )

    assert value == 2
    assert context.get("count", expected_type=int) == 2


def test_required_false_without_default_does_not_store_value():
    context = TestContext()
    response = make_response({})

    value = context.extract("optional", response, json_path="$.optional", required=False)

    assert value is None
    assert context.has("optional") is False


def test_json_null_requires_explicit_allow_none():
    context = TestContext()
    response = make_response({"value": None})

    with pytest.raises(ContextExtractionError, match="None is not allowed"):
        context.extract("value", response, json_path="$.value")


def test_json_null_can_be_stored_when_allow_none_is_true():
    context = TestContext()
    response = make_response({"value": None})

    value = context.extract("value", response, json_path="$.value", allow_none=True)

    assert value is None
    assert context.has("value") is True
    assert context.get("value") is None


def test_transform_failure_raises_context_extraction_error():
    context = TestContext()
    response = make_response({"count": "abc"})

    with pytest.raises(ContextExtractionError) as exc_info:
        context.extract("count", response, json_path="$.count", transform=int)

    assert "Failed to transform context variable 'count'" in str(exc_info.value)


def test_cleanup_callbacks_run_in_lifo_order_and_cleanup_is_idempotent():
    context = TestContext()
    calls: list[str] = []

    context.add_cleanup(calls.append, "first")
    context.add_cleanup(calls.append, "second")
    context.cleanup()
    context.cleanup()

    assert calls == ["second", "first"]


def test_cleanup_continues_after_failure_and_aggregates_errors():
    context = TestContext()
    calls: list[str] = []

    def fail() -> None:
        calls.append("fail")
        raise RuntimeError("api_key=cleanup-secret")

    context.add_cleanup(calls.append, "first")
    context.add_cleanup(fail)
    context.add_cleanup(calls.append, "last")

    with pytest.raises(ContextCleanupError) as exc_info:
        context.cleanup()

    assert calls == ["last", "fail", "first"]
    assert len(exc_info.value.errors) == 1
    message = str(exc_info.value)
    assert "<redacted>" in message or "%3Credacted%3E" in message
    assert "cleanup-secret" not in message


def test_multiple_context_instances_are_isolated():
    first = TestContext()
    second = TestContext()

    first.set("request_id", "request-001")
    second.set("request_id", "request-002")

    assert first.get("request_id") == "request-001"
    assert second.get("request_id") == "request-002"


def test_threaded_context_instances_are_isolated():
    def worker(index: int) -> str:
        context = TestContext()
        context.set("request_id", f"request-{index}")
        return context.require("request_id", expected_type=str)

    with ThreadPoolExecutor(max_workers=8) as executor:
        values = list(executor.map(worker, range(20)))

    assert values == [f"request-{index}" for index in range(20)]


def test_module_level_test_context_is_exported():
    assert ExportedTestContext is TestContext


def test_module_test_context_fixture_runs_cleanup():
    from module.conftest import test_context as test_context_fixture

    calls: list[str] = []
    fixture_generator = test_context_fixture.__wrapped__()
    context = next(fixture_generator)
    context.add_cleanup(calls.append, "cleaned")

    with pytest.raises(StopIteration):
        next(fixture_generator)

    assert calls == ["cleaned"]


def test_context_can_cleanup_files(tmp_path):
    target = tmp_path / "resource.txt"
    target.write_text("temporary", encoding="utf-8")
    context = TestContext()
    context.add_cleanup(target.unlink)

    context.cleanup()

    assert not target.exists()
