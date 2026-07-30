from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar

import pytest

from common.context_executor import submit_with_context


def test_submit_with_context_propagates_snapshot_per_task():
    value = ContextVar("value", default="missing")

    with ThreadPoolExecutor(max_workers=2) as executor:
        value.set("first")
        first = submit_with_context(executor, value.get)
        value.set("second")
        second = submit_with_context(executor, value.get)

    assert first.result() == "first"
    assert second.result() == "second"


def test_submit_with_context_supports_concurrent_tasks_without_reusing_context():
    value = ContextVar("value", default="missing")
    value.set("shared")

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [submit_with_context(executor, value.get) for _ in range(40)]

    assert [future.result() for future in futures] == ["shared"] * 40


def test_submit_with_context_preserves_return_value_and_exception():
    with ThreadPoolExecutor(max_workers=1) as executor:
        success = submit_with_context(executor, lambda left, right: left + right, 2, 3)
        failure = submit_with_context(
            executor,
            lambda: (_ for _ in ()).throw(ValueError("broken")),
        )

    assert success.result() == 5
    with pytest.raises(ValueError, match="broken"):
        failure.result()
