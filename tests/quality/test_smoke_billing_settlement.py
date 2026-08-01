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

    assert len(post_call_balance_queries) == 5
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
