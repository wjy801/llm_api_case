from __future__ import annotations

from contextvars import Context
from pathlib import Path

import pytest

from quality.runtime_context import (
    QualityCaseContext,
    QualityRunContext,
    clear_case_context,
    clear_run_context,
    get_case_context,
    get_run_context,
    reset_case_context,
    reset_run_context,
    set_case_context,
    set_run_context,
)


@pytest.fixture(autouse=True)
def clear_quality_contexts():
    clear_case_context()
    clear_run_context()
    yield
    clear_case_context()
    clear_run_context()


def test_contexts_default_to_none_and_support_custom_defaults():
    run_default = QualityRunContext("run-default", "exec-1", "master", Path("reports"))
    case_default = QualityCaseContext("case-default", "inv-default", "nodeid", "hash")

    assert get_run_context() is None
    assert get_case_context() is None
    assert get_run_context(run_default) is run_default
    assert get_case_context(case_default) is case_default


def test_run_context_nested_tokens_restore_previous_value():
    outer = QualityRunContext("run-outer", "exec-1", "master", Path("reports/outer"))
    inner = QualityRunContext("run-inner", "exec-2", "gw0", Path("reports/inner"))
    outer_token = set_run_context(outer)
    inner_token = set_run_context(inner)

    assert get_run_context() == inner

    reset_run_context(inner_token)
    assert get_run_context() == outer

    reset_run_context(outer_token)
    assert get_run_context() is None


def test_case_context_nested_tokens_restore_after_exception():
    outer = QualityCaseContext("case-outer", "inv-outer", "outer-node", "hash-outer")
    inner = QualityCaseContext("case-inner", "inv-inner", "inner-node", "hash-inner")
    outer_token = set_case_context(outer)

    try:
        inner_token = set_case_context(inner)
        try:
            raise RuntimeError("test error")
        finally:
            reset_case_context(inner_token)
    except RuntimeError:
        pass

    assert get_case_context() == outer
    reset_case_context(outer_token)


def test_run_and_case_contexts_do_not_pollute_each_other():
    run = QualityRunContext("run-1", "exec-1", "master", Path("reports"))
    case = QualityCaseContext("case-1", "inv-1", "nodeid", "hash")

    set_run_context(run)
    assert get_run_context() == run
    assert get_case_context() is None

    set_case_context(case)
    assert get_run_context() == run
    assert get_case_context() == case

    clear_case_context()
    assert get_case_context() is None
    assert get_run_context() == run


def test_contextvars_are_isolated_between_context_objects():
    first = Context()
    second = Context()
    first_value = QualityCaseContext("case-1", "inv-1", "node-1", "hash-1")
    second_value = QualityCaseContext("case-2", "inv-2", "node-2", "hash-2")

    first.run(set_case_context, first_value)
    second.run(set_case_context, second_value)

    assert first.run(get_case_context) == first_value
    assert second.run(get_case_context) == second_value
    assert get_case_context() is None


def test_context_models_validate_required_identity_and_normalize_path():
    context = QualityRunContext("run-1", "exec-1", "master", "reports/quality")  # type: ignore[arg-type]

    assert context.output_dir == Path("reports/quality")

    with pytest.raises(ValueError, match="case_id"):
        QualityCaseContext(" ", "inv-1", "nodeid", "hash")
