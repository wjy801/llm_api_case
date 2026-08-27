from __future__ import annotations

import ast
from pathlib import Path

from common import RetryPolicy
from module.smoke import CONCURRENT_CHAT_RETRY_POLICY, SMOKE_GET_RETRY_POLICY


SMOKE_DIR = Path(__file__).resolve().parents[2] / "module" / "smoke"


def _function(tree: ast.Module, class_name: str, function_name: str) -> ast.FunctionDef:
    return next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == class_name
        for node in node.body
        if isinstance(node, ast.FunctionDef) and node.name == function_name
    )


def _calls(function: ast.FunctionDef, method_name: str) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == method_name
    ]


def _uses_smoke_get_retry(call: ast.Call) -> bool:
    return any(
        keyword.arg == "retry_policy"
        and isinstance(keyword.value, ast.Name)
        and keyword.value.id == "SMOKE_GET_RETRY_POLICY"
        for keyword in call.keywords
    )


def test_smoke_get_retry_policy_is_bounded_and_get_only() -> None:
    assert SMOKE_GET_RETRY_POLICY.max_attempts == 3
    assert SMOKE_GET_RETRY_POLICY.max_elapsed == 10
    assert SMOKE_GET_RETRY_POLICY.allowed_methods == frozenset({"GET", "HEAD"})
    assert SMOKE_GET_RETRY_POLICY.allow_post is False


def test_concurrent_chat_retry_policy_uses_defaults_and_allows_post() -> None:
    default_policy = RetryPolicy()
    configured_defaults = CONCURRENT_CHAT_RETRY_POLICY.model_dump(exclude={"allow_post"})
    framework_defaults = default_policy.model_dump(exclude={"allow_post"})

    assert 429 in default_policy.retry_statuses
    assert CONCURRENT_CHAT_RETRY_POLICY.max_attempts == 3
    assert CONCURRENT_CHAT_RETRY_POLICY.allow_post is True
    assert configured_defaults == framework_defaults


def test_billing_control_get_calls_use_retry() -> None:
    control_get_methods = {
        "query_account_balance_after_settlement_for_billing",
        "query_account_balance_for_billing",
        "query_usage_records_by_model_response_for_billing",
        "query_usage_records_by_request_id_for_billing",
        "query_usage_records_for_billing",
    }
    calls: list[tuple[Path, ast.Call]] = []

    for source_file in [SMOKE_DIR / "task.py", *SMOKE_DIR.glob("test_*.py")]:
        tree = ast.parse(source_file.read_text(encoding="utf-8"))
        calls.extend(
            (source_file, node)
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in control_get_methods
        )

    assert calls
    assert all(_uses_smoke_get_retry(call) for _, call in calls), [
        f"{source_file.name}:{call.lineno}"
        for source_file, call in calls
        if not _uses_smoke_get_retry(call)
    ]


def test_positive_async_status_queries_use_retry_but_negative_queries_do_not() -> None:
    tree = ast.parse(
        (SMOKE_DIR / "test_图片生成异步调用.py").read_text(encoding="utf-8")
    )
    positive_test = _function(
        tree,
        "TestAsyncImageGeneration",
        "test_f8_08_async_image_generation_task_status_query",
    )
    negative_tests = [
        _function(
            tree,
            "TestAsyncImageGeneration",
            "test_f8_19_query_nonexistent_async_task_id_returns_not_found",
        ),
        _function(
            tree,
            "TestAsyncImageGeneration",
            "test_f8_20_async_task_cross_account_isolation",
        ),
    ]

    positive_calls = _calls(positive_test, "get_media_generation_task")
    assert len(positive_calls) == 1
    assert _uses_smoke_get_retry(positive_calls[0])

    negative_calls = [
        call
        for test in negative_tests
        for call in _calls(test, "get_media_generation_task")
    ]
    assert len(negative_calls) == 2
    assert all(not _uses_smoke_get_retry(call) for call in negative_calls)


def test_async_polling_calls_use_retry() -> None:
    tree = ast.parse(
        (SMOKE_DIR / "test_图片生成异步调用.py").read_text(encoding="utf-8")
    )
    test_names_and_methods = [
        ("test_f8_09_async_image_generation_task_succeeds_with_result", "create_and_poll_media_generation"),
        ("test_f8_14_async_image_generation_result_image_url_is_accessible", "create_and_poll_media_generation"),
        ("_poll_task_until_finished", "poll_media_generation_result"),
    ]

    for function_name, method_name in test_names_and_methods:
        function = _function(tree, "TestAsyncImageGeneration", function_name)
        calls = _calls(function, method_name)
        assert len(calls) == 1
        assert _uses_smoke_get_retry(calls[0])


def test_only_concurrent_chat_post_receives_retry_policy() -> None:
    post_methods = {
        "create_async_image_generation",
        "create_chat_completion",
        "create_chat_completion_for_billing",
        "create_image_generation",
        "create_stream_chat_completion",
    }
    retry_calls: list[tuple[Path, ast.Call]] = []

    for test_file in SMOKE_DIR.glob("test_*.py"):
        tree = ast.parse(test_file.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr not in post_methods:
                continue
            retry_keywords = [
                keyword
                for keyword in node.keywords
                if keyword.arg == "retry_policy"
            ]
            if retry_keywords:
                retry_calls.append((test_file, node))
                assert len(retry_keywords) == 1
                assert isinstance(retry_keywords[0].value, ast.Name)
                assert retry_keywords[0].value.id == "CONCURRENT_CHAT_RETRY_POLICY"

    assert len(retry_calls) == 1
    retry_file, retry_call = retry_calls[0]
    assert retry_file.name == "test_call_billing_correctness.py"
    assert isinstance(retry_call.func, ast.Attribute)
    assert retry_call.func.attr == "create_chat_completion_for_billing"
