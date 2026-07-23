from __future__ import annotations

import pytest

from governance.flaky_gate import evaluate_flaky_gate


pytestmark = pytest.mark.flaky_governance


def test_default_gate_blocks_failed_and_retry_failed():
    decision = evaluate_flaky_gate({"failed": 1, "retry_failed": 1, "retry_passed": 0})

    assert decision.should_fail is True
    assert "失败 1 条" in decision.messages[0]
    assert "重试不通过 1 条" in decision.messages[1]


def test_default_gate_warns_retry_passed_without_blocking():
    decision = evaluate_flaky_gate({"failed": 0, "retry_failed": 0, "retry_passed": 1})

    assert decision.should_fail is False
    assert decision.messages == ("Flaky 门禁警告：重试通过 1 条。",)


def test_strict_gate_blocks_retry_passed():
    decision = evaluate_flaky_gate(
        {"failed": 0, "retry_failed": 0, "retry_passed": 1},
        fail_on_retry_passed=True,
    )

    assert decision.should_fail is True
    assert decision.messages == ("Flaky 严格门禁阻断：重试通过 1 条。",)
