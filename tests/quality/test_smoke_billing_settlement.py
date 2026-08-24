from __future__ import annotations

import ast
from pathlib import Path

from common.base_task import BALANCE_SETTLEMENT_WAIT_SECONDS


BILLING_TEST_FILE = (
    Path(__file__).resolve().parents[2]
    / "module"
    / "smoke"
    / "test_call_billing_correctness.py"
)
ASYNC_IMAGE_TEST_FILE = (
    Path(__file__).resolve().parents[2]
    / "module"
    / "smoke"
    / "test_图片生成异步调用.py"
)
SYNC_IMAGE_TEST_FILE = (
    Path(__file__).resolve().parents[2]
    / "module"
    / "smoke"
    / "test_图片生成同步调用.py"
)


def test_billing_settlement_waits_five_seconds_by_default() -> None:
    assert BALANCE_SETTLEMENT_WAIT_SECONDS == 5


def test_all_post_call_balance_queries_wait_for_billing_settlement() -> None:
    tree = ast.parse(BILLING_TEST_FILE.read_text(encoding="utf-8"))
    post_call_balance_queries: list[str] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(
            isinstance(target, ast.Name) and target.id == "after_balance_response"
            for target in node.targets
        ):
            continue
        assert isinstance(node.value, ast.Call)
        assert isinstance(node.value.func, ast.Attribute)
        post_call_balance_queries.append(node.value.func.attr)

    assert post_call_balance_queries
    assert set(post_call_balance_queries) == {
        "query_account_balance_after_settlement_for_billing"
    }


def test_async_billing_does_not_assume_balance_unchanged_before_task_finishes() -> None:
    tree = ast.parse(ASYNC_IMAGE_TEST_FILE.read_text(encoding="utf-8"))
    billing_test = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "TestAsyncImageGeneration"
        for node in node.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "test_f8_10_async_image_generation_billing_deduction_matches_usage_quota"
    )
    called_methods = {
        node.func.attr
        for node in ast.walk(billing_test)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }

    assert "assert_total_balance_unchanged" not in called_methods
    assert "assert_call_billing_deduction_matches" in called_methods


def test_failed_sync_image_billing_uses_request_scoped_usage_with_retry() -> None:
    tree = ast.parse(SYNC_IMAGE_TEST_FILE.read_text(encoding="utf-8"))
    billing_test = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "TestSyncImageGeneration"
        for node in node.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "test_f8_04_sync_image_generation_failed_call_does_not_deduct_balance"
    )
    calls = [node for node in ast.walk(billing_test) if isinstance(node, ast.Call)]
    called_methods = {
        node.func.attr
        for node in calls
        if isinstance(node.func, ast.Attribute)
    }

    assert "assert_total_balance_unchanged" not in called_methods
    assert "get_request_id_from_response" in called_methods

    usage_call = next(
        node
        for node in calls
        if isinstance(node.func, ast.Attribute)
        and node.func.attr == "query_usage_records_by_request_id_for_billing"
    )
    retry_keyword = next(
        keyword
        for keyword in usage_call.keywords
        if keyword.arg == "retry_policy"
    )
    assert isinstance(retry_keyword.value, ast.Name)
    assert retry_keyword.value.id == "USAGE_RECORD_READ_RETRY_POLICY"
