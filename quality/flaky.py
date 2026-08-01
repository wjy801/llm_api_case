from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Sequence

from quality.flaky_models import (
    CaseObservation,
    FlakyEvidence,
    FlakyHistoryEntry,
    FlakyProjection,
    FlakyRuleConfig,
    FlakyState,
    FlakyTransitionDecision,
    ObservationOutcome,
)


DEFAULT_FLAKY_RULE_CONFIG = FlakyRuleConfig()


def build_result_signature(observation: CaseObservation) -> str:
    if observation.observation_outcome is ObservationOutcome.PASS:
        return "pass"
    if not observation.failure_id:
        raise ValueError("fail observation must include failure_id")
    return f"fail:{observation.failure_id}"


def sort_observations(
    observations: Sequence[FlakyHistoryEntry],
) -> tuple[FlakyHistoryEntry, ...]:
    return tuple(
        sorted(
            observations,
            key=lambda item: (
                item.observed_at,
                item.run_end_time,
                item.run_id,
                item.observation_id,
            ),
        )
    )


def derive_evidence_window(
    observations: Sequence[FlakyHistoryEntry],
    config: FlakyRuleConfig = DEFAULT_FLAKY_RULE_CONFIG,
) -> FlakyEvidence:
    ordered = sort_observations(observations)
    if not ordered:
        raise ValueError("at least one observation is required")
    window = ordered[-config.evidence_window_size :]
    signatures = [build_result_signature(item) for item in window]
    outcomes = [item.observation_outcome for item in window]
    pass_count = sum(item is ObservationOutcome.PASS for item in outcomes)
    fail_count = len(outcomes) - pass_count
    outcome_switches = sum(
        left is not right for left, right in zip(outcomes, outcomes[1:])
    )
    signature_switches = sum(
        left != right for left, right in zip(signatures, signatures[1:])
    )
    failure_ids = {
        item.failure_id
        for item in window
        if item.observation_outcome is ObservationOutcome.FAIL and item.failure_id
    }
    trailing = 1
    for signature in reversed(signatures[:-1]):
        if signature != signatures[-1]:
            break
        trailing += 1
    refs = window[-config.max_transition_evidence_refs :]
    return FlakyEvidence(
        total_observation_count=len(ordered),
        sample_size=len(window),
        evidence_window_size=config.evidence_window_size,
        pass_count=pass_count,
        fail_count=fail_count,
        outcome_switch_count=outcome_switches,
        signature_switch_count=signature_switches,
        distinct_failure_fingerprint_count=len(failure_ids),
        trailing_same_signature_count=trailing,
        latest_signature=signatures[-1],
        observation_ids=tuple(item.observation_id for item in refs),
        run_ids=tuple(dict.fromkeys(item.run_id for item in refs)),
    )


def replay_observations(
    observations: Sequence[FlakyHistoryEntry],
    config: FlakyRuleConfig = DEFAULT_FLAKY_RULE_CONFIG,
) -> FlakyProjection:
    ordered = sort_observations(observations)
    if not ordered:
        raise ValueError("at least one observation is required")

    state: FlakyState | None = None
    stable_signature: str | None = None
    transitions: list[FlakyTransitionDecision] = []
    for index, observation in enumerate(ordered, start=1):
        prefix = ordered[:index]
        evidence = derive_evidence_window(prefix, config)
        previous = state
        reason: str | None = None
        if state is None:
            state = FlakyState.OBSERVING
            reason = "first_observation"
        elif state is FlakyState.OBSERVING:
            if evidence.signature_switch_count > 0:
                state = FlakyState.SUSPECTED
                reason = (
                    "outcome_changed"
                    if evidence.outcome_switch_count > 0
                    else "failure_fingerprint_changed"
                )
            elif evidence.sample_size >= config.stable_min_samples:
                state = FlakyState.STABLE
                stable_signature = evidence.latest_signature
                reason = "consistent_signature_threshold_met"
        elif state is FlakyState.STABLE:
            if stable_signature is None:
                stable_signature = evidence.latest_signature
            latest_signature = build_result_signature(observation)
            if latest_signature != stable_signature:
                state = FlakyState.SUSPECTED
                reason = "stable_signature_broken"
        elif state is FlakyState.SUSPECTED:
            if _confirmation_met(evidence, config):
                state = FlakyState.CONFIRMED
                reason = "confirmation_threshold_met"
            elif (
                evidence.trailing_same_signature_count
                >= config.suspected_clear_signature_streak
            ):
                state = FlakyState.STABLE
                stable_signature = evidence.latest_signature
                reason = "suspected_cleared_by_streak"
        elif state is FlakyState.CONFIRMED:
            pass
        else:
            raise ValueError(f"automatic replay does not accept state {state.value}")

        if reason is not None:
            transitions.append(
                FlakyTransitionDecision(
                    from_state=previous,
                    to_state=state,
                    reason_code=reason,
                    trigger_observation_id=observation.observation_id,
                    evidence=evidence,
                )
            )

    final_evidence = derive_evidence_window(ordered, config)
    stable_outcome, stable_failure_id = _signature_parts(
        stable_signature if state is FlakyState.STABLE else None
    )
    return FlakyProjection(
        current_state=state,
        detected_state=state,
        stable_outcome=stable_outcome,
        stable_failure_id=stable_failure_id,
        evidence=final_evidence,
        transitions=tuple(transitions),
    )


@dataclass(frozen=True)
class RecoveryEvaluation:
    target_state: FlakyState | None
    reason_code: str | None
    evidence: FlakyEvidence | None
    stable_outcome: ObservationOutcome | None = None
    stable_failure_id: str | None = None


def evaluate_recovery(
    observations_after_anchor: Sequence[FlakyHistoryEntry],
    config: FlakyRuleConfig = DEFAULT_FLAKY_RULE_CONFIG,
) -> RecoveryEvaluation:
    ordered = sort_observations(observations_after_anchor)
    if not ordered:
        return RecoveryEvaluation(None, None, None)
    evidence = derive_evidence_window(ordered, config)
    if evidence.signature_switch_count > 0:
        return RecoveryEvaluation(
            FlakyState.CONFIRMED,
            "recovery_regressed",
            evidence,
        )
    if evidence.trailing_same_signature_count >= config.recovery_signature_streak:
        outcome, failure_id = _signature_parts(evidence.latest_signature)
        return RecoveryEvaluation(
            FlakyState.STABLE,
            "recovery_stable_streak_met",
            evidence,
            stable_outcome=outcome,
            stable_failure_id=failure_id,
        )
    return RecoveryEvaluation(None, None, evidence)


def build_transition_id(
    *,
    flaky_key: str,
    from_state: FlakyState | None,
    to_state: FlakyState,
    trigger_type: str,
    reason_code: str,
    trigger_observation_id: str | None,
    rule_version: str,
    projection_version: str,
) -> str:
    payload = {
        "flaky_key": flaky_key,
        "from_state": from_state.value if from_state else None,
        "to_state": to_state.value,
        "trigger_type": trigger_type,
        "reason_code": reason_code,
        "trigger_observation_id": trigger_observation_id,
        "rule_version": rule_version,
        "projection_version": projection_version,
    }
    canonical = json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return f"transition-v1-{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


def _confirmation_met(evidence: FlakyEvidence, config: FlakyRuleConfig) -> bool:
    return (
        evidence.sample_size >= config.confirmed_min_samples
        and evidence.pass_count >= config.confirmed_min_pass_count
        and evidence.fail_count >= config.confirmed_min_fail_count
        and evidence.outcome_switch_count
        >= config.confirmed_min_outcome_switches
    )


def _signature_parts(
    signature: str | None,
) -> tuple[ObservationOutcome | None, str | None]:
    if signature is None:
        return None, None
    if signature == "pass":
        return ObservationOutcome.PASS, None
    prefix = "fail:"
    if signature.startswith(prefix) and len(signature) > len(prefix):
        return ObservationOutcome.FAIL, signature[len(prefix) :]
    raise ValueError(f"invalid result signature: {signature!r}")
