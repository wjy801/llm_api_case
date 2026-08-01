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
