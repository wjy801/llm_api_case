from __future__ import annotations

import ast
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BILLING_CASES = (
    (
        PROJECT_ROOT / "module" / "smoke" / "test_call_billing_correctness.py",
        "TestCallBillingCorrectness",
        "test_sync_image_model_call_billing_deduction_matches_usage_quota",
        "assert_successful_usage_record",
    ),
    (
        PROJECT_ROOT / "module" / "smoke" / "test_call_billing_correctness.py",
        "TestCallBillingCorrectness",
        "test_text_model_call_billing_deduction_matches_usage_quota",
        "assert_successful_usage_record",
    ),
    (
        PROJECT_ROOT / "module" / "smoke" / "test_call_billing_correctness.py",
        "TestCallBillingCorrectness",
        "test_concurrent_text_model_call_billing_deduction_matches_usage_quota_sum",
        "assert_successful_usage_record",
    ),
    (
        PROJECT_ROOT / "module" / "smoke" / "test_call_billing_correctness.py",
        "TestCallBillingCorrectness",
        "test_failed_sync_image_model_call_does_not_deduct_balance",
        "assert_usage_record_not_charged",
    ),
    (
        PROJECT_ROOT / "module" / "smoke" / "test_图片生成同步调用.py",
        "TestSyncImageGeneration",
        "test_f8_03_sync_image_generation_billing_deduction_matches_usage_quota",
        "assert_successful_usage_record",
    ),
    (
        PROJECT_ROOT / "module" / "smoke" / "test_图片生成同步调用.py",
        "TestSyncImageGeneration",
        "test_f8_04_sync_image_generation_failed_call_does_not_deduct_balance",
        "assert_usage_record_not_charged",
    ),
    (
        PROJECT_ROOT / "module" / "smoke" / "test_图片生成异步调用.py",
        "TestAsyncImageGeneration",
        "test_f8_10_async_image_generation_billing_deduction_matches_usage_quota",
        "assert_successful_usage_record",
    ),
)


def test_billing_cases_use_request_scoped_usage_without_balance_snapshots() -> None:
    for source_file, class_name, function_name, assertion_name in BILLING_CASES:
        function = _function(source_file, class_name, function_name)
        called_methods = {
            node.func.attr
            for node in ast.walk(function)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }

        assert "query_usage_records_by_request_id_for_billing" in called_methods
        assert assertion_name in called_methods
        assert "query_account_balance_for_billing" not in called_methods
        assert "query_account_balance_after_settlement_for_billing" not in called_methods


def test_billing_usage_queries_keep_the_shared_get_retry_policy() -> None:
    for source_file, class_name, function_name, _ in BILLING_CASES:
        function = _function(source_file, class_name, function_name)
        usage_calls = [
            node
            for node in ast.walk(function)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "query_usage_records_by_request_id_for_billing"
        ]

        assert usage_calls
        for call in usage_calls:
            retry_keyword = next(
                keyword for keyword in call.keywords if keyword.arg == "retry_policy"
            )
            assert isinstance(retry_keyword.value, ast.Name)
            assert retry_keyword.value.id == "SMOKE_GET_RETRY_POLICY"


def _function(
    source_file: Path,
    class_name: str,
    function_name: str,
) -> ast.FunctionDef:
    tree = ast.parse(source_file.read_text(encoding="utf-8"))
    return next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == class_name
        for node in node.body
        if isinstance(node, ast.FunctionDef) and node.name == function_name
    )
