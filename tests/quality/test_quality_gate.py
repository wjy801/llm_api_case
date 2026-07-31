from __future__ import annotations

import pytest

from quality.gate import ShadowGateConfig, ShadowGateContext, evaluate_shadow_gate
from quality.models import FailureCategory, GateResult, IntegrityStatus, QualitySummary


def _summary(**overrides) -> QualitySummary:
    values = {
        "run_id": "run-1",
        "case_total": 1,
        "case_passed": 1,
        "case_failed": 0,
        "case_error": 0,
        "case_skipped": 0,
        "raw_pass_rate": 1,
        "final_pass_rate": 1,
        "retry_passed": 0,
        "request_total": 20,
        "request_success_rate": 1,
        "http_5xx_count": 0,
        "timeout_count": 0,
        "unknown_failure_count": 0,
        "integrity_status": IntegrityStatus.COMPLETE,
    }
    values.update(overrides)
    return QualitySummary(**values)


def _evaluate(*, categories=None, summary=None, available=True, integrity=IntegrityStatus.COMPLETE):
    return evaluate_shadow_gate(
        ShadowGateContext(
            run_id="run-1",
            input_available=available,
            integrity_status=integrity,
            summary=summary or _summary(),
            failure_category_counts=categories or {},
        )
    )


def _rule(decision, rule_id):
    return next(rule for rule in decision.rules if rule.rule_id == rule_id)


@pytest.mark.parametrize(
    ("category", "rule_id"),
    [
        (FailureCategory.PRODUCT_DEFECT, "p0.failure.product_defect"),
        (FailureCategory.CONFIGURATION, "p0.failure.configuration"),
    ],
)
def test_confirmed_product_or_configuration_failure_blocks(category, rule_id):
    decision = _evaluate(categories={category.value: 1})

    assert _rule(decision, rule_id).decision is GateResult.BLOCK
    assert decision.overall is GateResult.BLOCK


@pytest.mark.parametrize(
    ("category", "rule_id"),
    [
        (FailureCategory.FRAMEWORK_DEFECT, "p0.failure.framework_defect"),
        (FailureCategory.UNKNOWN, "p0.failure.unknown"),
    ],
)
def test_framework_or_unknown_failure_warns(category, rule_id):
    decision = _evaluate(categories={category.value: 1})

    assert _rule(decision, rule_id).decision is GateResult.WARN
    assert decision.overall is GateResult.WARN


def test_request_rates_warn_only_when_sample_is_sufficient():
    decision = _evaluate(
        summary=_summary(request_total=20, request_success_rate=0.9, http_5xx_count=2, timeout_count=1)
    )

    assert _rule(decision, "p0.request.http_5xx_rate").decision is GateResult.WARN
    assert _rule(decision, "p0.request.timeout_rate").decision is GateResult.PASS


def test_all_rules_pass_with_complete_and_sufficient_data():
    decision = _evaluate()

    assert decision.overall is GateResult.PASS
    assert all(rule.decision is GateResult.PASS for rule in decision.rules)


def test_small_request_sample_is_no_data_and_overall_is_not_pass():
    decision = _evaluate(
        summary=_summary(request_total=1, request_success_rate=0, timeout_count=1)
    )

    assert _rule(decision, "p0.request.timeout_rate").decision is GateResult.NO_DATA
    assert decision.overall is GateResult.WARN


def test_unavailable_input_is_no_data():
    decision = _evaluate(available=False)

    assert decision.overall is GateResult.NO_DATA
    assert decision.rules[0].decision is GateResult.NO_DATA


def test_disabled_shadow_gate_still_returns_stable_decision():
    decision = evaluate_shadow_gate(
        ShadowGateContext(
            run_id="run-1",
            input_available=True,
            integrity_status=IntegrityStatus.COMPLETE,
            summary=_summary(),
        ),
        ShadowGateConfig(enabled=False),
    )

    assert decision.overall is GateResult.NO_DATA
    assert decision.rules[0].rule_id == "p0.shadow_gate.enabled"
