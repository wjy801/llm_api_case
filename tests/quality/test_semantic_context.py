from __future__ import annotations

from quality.semantic_context import get_operation_context, operation_scope
from quality.semantic_models import OperationKind, TrafficRole
from quality.storage import read_jsonl


def test_nested_operation_scope_reuses_parent_without_duplicate_operation(semantic_runtime):
    with operation_scope(
        OperationKind.ASYNC_TASK,
        name="media_generation",
        role=TrafficRole.WORKLOAD,
        model_id="model-a",
    ) as parent:
        with operation_scope(
            OperationKind.HTTP,
            name="create",
            role=TrafficRole.WORKLOAD,
        ) as nested:
            assert nested == parent
            assert get_operation_context() == parent

    operations = read_jsonl(semantic_runtime.semantic.paths.operations)
    assert len(operations) == 1
    assert operations[0]["operation_kind"] == "async_task"
    assert operations[0]["model_id"] == "model-a"


def test_operation_scope_exception_keeps_original_exception(semantic_runtime):
    try:
        with operation_scope(OperationKind.HTTP, name="failed"):
            raise RuntimeError("business failure")
    except RuntimeError as error:
        assert str(error) == "business failure"

    operation = read_jsonl(semantic_runtime.semantic.paths.operations)[0]
    assert operation["outcome"] == "failed"
