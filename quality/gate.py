from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from quality.models import (
    FailureCategory,
    GateDecision,
    GateMode,
    GateResult,
    GateRuleDecision,
    IntegrityStatus,
    QualitySummary,
)


GATE_RULESET_VERSION = "p0-shadow-gate.v1"


@dataclass(frozen=True)
class ShadowGateConfig:
    enabled: bool = True
    min_request_samples: int = 20
    http_5xx_warn_rate: float = 0.02
    timeout_warn_rate: float = 0.05

    def __post_init__(self) -> None:
        if self.min_request_samples < 0:
            raise ValueError("min_request_samples must be greater than or equal to 0")
        if not 0 <= self.http_5xx_warn_rate <= 1:
            raise ValueError("http_5xx_warn_rate must be between 0 and 1")
        if not 0 <= self.timeout_warn_rate <= 1:
            raise ValueError("timeout_warn_rate must be between 0 and 1")


@dataclass(frozen=True)
class ShadowGateContext:
    run_id: str
    input_available: bool
    integrity_status: IntegrityStatus
    summary: QualitySummary | None = None
    failure_category_counts: Mapping[str, int] = field(default_factory=dict)
    failure_evidence: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    input_evidence: tuple[str, ...] = ()


def evaluate_shadow_gate(
    context: ShadowGateContext,
    config: ShadowGateConfig | None = None,
) -> GateDecision:
    gate_config = config or ShadowGateConfig()
    if not gate_config.enabled:
        return GateDecision(
            run_id=context.run_id,
            mode=GateMode.SHADOW,
            overall=GateResult.NO_DATA,
            rules=(
                _rule(
                    "p0.shadow_gate.enabled",
                    target="run",
                    actual=False,
                    threshold=True,
                    sample_size=0,
                    decision=GateResult.NO_DATA,
                    evidence=("shadow gate disabled",),
                ),
            ),
        )

    rules: list[GateRuleDecision] = []
    if not context.input_available:
        rules.append(
            _rule(
                "p0.integrity.available",
                target="merged",
                actual=False,
                threshold=True,
                sample_size=0,
                decision=GateResult.NO_DATA,
                evidence=context.input_evidence or ("quality merged snapshot is not available",),
            )
        )
        return GateDecision(
            run_id=context.run_id,
            mode=GateMode.SHADOW,
            overall=GateResult.NO_DATA,
            rules=tuple(rules),
        )

    if context.integrity_status is IntegrityStatus.FAILED:
        rules.append(
            _rule(
                "p0.integrity.available",
                target="merged",
                actual=context.integrity_status.value,
                threshold=IntegrityStatus.COMPLETE.value,
                sample_size=0,
                decision=GateResult.NO_DATA,
                evidence=context.input_evidence or ("quality integrity status is failed",),
            )
        )
    else:
        rules.append(
            _rule(
                "p0.integrity.available",
                target="merged",
                actual=True,
                threshold=True,
                sample_size=0,
                decision=GateResult.PASS,
                evidence=("manifest is complete and output hashes match",),
            )
        )

    rules.append(_integrity_degraded_rule(context))
    rules.extend(_failure_rules(context))
    rules.extend(_request_rules(context, gate_config))
    return GateDecision(
        run_id=context.run_id,
        mode=GateMode.SHADOW,
        overall=_overall(context, rules),
        rules=tuple(rules),
    )


def _integrity_degraded_rule(context: ShadowGateContext) -> GateRuleDecision:
    if context.integrity_status is IntegrityStatus.DEGRADED:
        return _rule(
            "p0.integrity.degraded",
            target="merged",
            actual=context.integrity_status.value,
            threshold=IntegrityStatus.COMPLETE.value,
            sample_size=0,
            decision=GateResult.WARN,
            evidence=context.input_evidence or ("quality integrity status is degraded",),
        )
    return _rule(
        "p0.integrity.degraded",
        target="merged",
        actual=context.integrity_status.value,
        threshold=IntegrityStatus.COMPLETE.value,
        sample_size=0,
        decision=GateResult.PASS,
        evidence=("quality integrity status is not degraded",),
    )


def _failure_rules(context: ShadowGateContext) -> tuple[GateRuleDecision, ...]:
    return (
        _failure_rule(
            context,
            rule_id="p0.failure.product_defect",
            category=FailureCategory.PRODUCT_DEFECT,
            positive_decision=GateResult.BLOCK,
        ),
        _failure_rule(
            context,
            rule_id="p0.failure.configuration",
            category=FailureCategory.CONFIGURATION,
            positive_decision=GateResult.BLOCK,
        ),
        _failure_rule(
            context,
            rule_id="p0.failure.framework_defect",
            category=FailureCategory.FRAMEWORK_DEFECT,
            positive_decision=GateResult.WARN,
        ),
        _failure_rule(
            context,
            rule_id="p0.failure.unknown",
            category=FailureCategory.UNKNOWN,
            positive_decision=GateResult.WARN,
        ),
    )


def _failure_rule(
    context: ShadowGateContext,
    *,
    rule_id: str,
    category: FailureCategory,
    positive_decision: GateResult,
) -> GateRuleDecision:
    count = int(context.failure_category_counts.get(category.value, 0))
    decision = positive_decision if count > 0 else GateResult.PASS
    evidence = context.failure_evidence.get(category.value, ())
    if not evidence:
        evidence = (f"{category.value} occurrence count: {count}",)
    return _rule(
        rule_id,
        target="failures",
        actual=count,
        threshold=0,
        sample_size=count,
        decision=decision,
        evidence=evidence,
    )


def _request_rules(
    context: ShadowGateContext,
    config: ShadowGateConfig,
) -> tuple[GateRuleDecision, GateRuleDecision]:
    summary = context.summary
    if summary is None:
        return (
            _request_rate_rule(
                "p0.request.http_5xx_rate",
                actual_count=0,
                total=0,
                threshold=config.http_5xx_warn_rate,
                min_samples=config.min_request_samples,
                label="HTTP 5xx",
            ),
            _request_rate_rule(
                "p0.request.timeout_rate",
                actual_count=0,
                total=0,
                threshold=config.timeout_warn_rate,
                min_samples=config.min_request_samples,
                label="timeout",
            ),
        )
    return (
        _request_rate_rule(
            "p0.request.http_5xx_rate",
            actual_count=summary.http_5xx_count,
            total=summary.request_total,
            threshold=config.http_5xx_warn_rate,
            min_samples=config.min_request_samples,
            label="HTTP 5xx",
        ),
        _request_rate_rule(
            "p0.request.timeout_rate",
            actual_count=summary.timeout_count,
            total=summary.request_total,
            threshold=config.timeout_warn_rate,
            min_samples=config.min_request_samples,
            label="timeout",
        ),
    )


def _request_rate_rule(
    rule_id: str,
    *,
    actual_count: int,
    total: int,
    threshold: float,
    min_samples: int,
    label: str,
) -> GateRuleDecision:
    rate = (actual_count / total) if total else 0.0
    if total < min_samples:
        return _rule(
            rule_id,
            target="requests",
            actual=rate,
            threshold=threshold,
            sample_size=total,
            decision=GateResult.NO_DATA,
            evidence=(f"{label} sample size {total} is below minimum {min_samples}",),
        )
    decision = GateResult.WARN if rate > threshold else GateResult.PASS
    return _rule(
        rule_id,
        target="requests",
        actual=rate,
        threshold=threshold,
        sample_size=total,
        decision=decision,
        evidence=(f"{label} count {actual_count} of {total}",),
    )


def _overall(
    context: ShadowGateContext,
    rules: list[GateRuleDecision],
) -> GateResult:
    if not context.input_available or context.integrity_status is IntegrityStatus.FAILED:
        return GateResult.NO_DATA
    decisions = [rule.decision for rule in rules]
    if GateResult.BLOCK in decisions:
        return GateResult.BLOCK
    if GateResult.WARN in decisions:
        return GateResult.WARN
    if GateResult.NO_DATA in decisions:
        return GateResult.WARN
    if decisions:
        return GateResult.PASS
    return GateResult.NO_DATA


def _rule(
    rule_id: str,
    *,
    target: str,
    actual,
    threshold,
    sample_size: int,
    decision: GateResult,
    evidence: tuple[str, ...],
) -> GateRuleDecision:
    return GateRuleDecision(
        rule_id=rule_id,
        rule_version=GATE_RULESET_VERSION,
        target=target,
        actual=actual,
        threshold=threshold,
        sample_size=sample_size,
        decision=decision,
        evidence=tuple(item[:500] for item in evidence if item),
    )
