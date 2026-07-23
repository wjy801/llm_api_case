from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class FlakyGateDecision:
    should_fail: bool
    messages: tuple[str, ...]


def evaluate_flaky_gate(
    summary: Mapping[str, Any],
    *,
    fail_on_retry_passed: bool = False,
) -> FlakyGateDecision:
    failed = _count(summary, "failed")
    retry_failed = _count(summary, "retry_failed")
    retry_passed = _count(summary, "retry_passed")
    messages: list[str] = []

    if failed > 0:
        messages.append(f"Flaky 门禁阻断：失败 {failed} 条。")
    if retry_failed > 0:
        messages.append(f"Flaky 门禁阻断：重试不通过 {retry_failed} 条。")
    if fail_on_retry_passed and retry_passed > 0:
        messages.append(f"Flaky 严格门禁阻断：重试通过 {retry_passed} 条。")
    elif retry_passed > 0:
        messages.append(f"Flaky 门禁警告：重试通过 {retry_passed} 条。")

    return FlakyGateDecision(
        should_fail=failed > 0 or retry_failed > 0 or (fail_on_retry_passed and retry_passed > 0),
        messages=tuple(messages),
    )


def _count(summary: Mapping[str, Any], key: str) -> int:
    value = summary.get(key, 0)
    if isinstance(value, int):
        return value
    return int(value)
