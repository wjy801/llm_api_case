from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import hashlib
import json
import re
from typing import Iterable, Sequence

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator

from quality.flaky_identity import (
    normalize_flaky_environment,
    normalize_stored_execution_profile,
)
from quality.flaky_models import FlakyRuleConfig
from quality.models import RunKind


COMPARABILITY_RULE_VERSION = "flaky-comparability.v1"
NORMAL_ADMISSION_RULE_VERSION = "flaky-normal-admission.v1"
PROBE_EVIDENCE_RULE_VERSION = "flaky-probe-evidence.v1"
DETECTION_TRANSITION_VERSION = "transition-v1"
GOVERNANCE_EVENT_VERSION = "governance-event-v1"


class AdmissionStatus(str, Enum):
    ELIGIBLE = "ELIGIBLE"
    INELIGIBLE = "INELIGIBLE"


class DetectionState(str, Enum):
    UNOBSERVED = "UNOBSERVED"
    OBSERVING = "OBSERVING"
    STABLE = "STABLE"
    SUSPECTED = "SUSPECTED"
    CONFIRMED = "CONFIRMED"


class AttemptStatus(str, Enum):
    ACTIVE = "ACTIVE"
    READY_TO_CLOSE = "READY_TO_CLOSE"
    FAILED = "FAILED"
    INCONCLUSIVE = "INCONCLUSIVE"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"
    CLOSED = "CLOSED"


class ProbeClassification(str, Enum):
    COUNT_PASS = "COUNT_PASS"
    TRUSTED_FAIL = "TRUSTED_FAIL"
    NON_COUNTING = "NON_COUNTING"


class ProbeEffectStatus(str, Enum):
    APPLIED = "APPLIED"
    AUDIT_ONLY = "AUDIT_ONLY"


class ProbeOutcome(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    SKIP = "SKIP"
    XFAIL = "XFAIL"
    XPASS = "XPASS"
    NO_DATA = "NO_DATA"


class FrozenV3Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)


class GovernancePolicy(FrozenV3Model):
    schema_version: str = "flaky-governance-policy.v1"
    normal_admission_rule_version: str = NORMAL_ADMISSION_RULE_VERSION
    probe_evidence_rule_version: str = PROBE_EVIDENCE_RULE_VERSION
    skip_decision_rule_version: str = "flaky-skip-decision.v1"
    required_consecutive_passes: int = Field(default=5, ge=1)
    min_interval_minutes: int = Field(default=30, ge=0)
    max_attempt_age_hours: int = Field(default=72, ge=1)
    max_non_counting_runs: int = Field(default=3, ge=1)
    snapshot_max_age_minutes: int = Field(default=15, ge=1)
    allowed_branches: tuple[str, ...] = ("dev3",)
    include_path_prefixes: tuple[str, ...] = ("module/smoke/",)
    exclude_path_prefixes: tuple[str, ...] = ()

    @computed_field
    @property
    def revision(self) -> str:
        payload = self.model_dump(mode="json", exclude={"revision"})
        return f"sha256:{_sha256(payload)}"


DEFAULT_GOVERNANCE_POLICY = GovernancePolicy()


class ComparabilityFacts(FrozenV3Model):
    configuration_revision: str
    environment: str
    execution_profile: str
    sut_revision: str
    test_definition_digest: str

    @field_validator("configuration_revision", "sut_revision")
    @classmethod
    def _revision(cls, value: str) -> str:
        return _required_text(value)

    @field_validator("environment")
    @classmethod
    def _environment(cls, value: str) -> str:
        return normalize_flaky_environment(value)

    @field_validator("execution_profile")
    @classmethod
    def _profile(cls, value: str) -> str:
        return normalize_stored_execution_profile(value)

    @field_validator("test_definition_digest")
    @classmethod
    def _test_digest(cls, value: str) -> str:
        normalized = _required_text(value)
        if re.fullmatch(r"sha256:[0-9a-f]{64}", normalized) is None:
            raise ValueError("test_definition_digest must be sha256:<64 lowercase hex>")
        return normalized


class AdmissionResult(FrozenV3Model):
    status: AdmissionStatus
    reason_codes: tuple[str, ...]
    primary_reason_code: str
    policy_revision: str
    rule_version: str


class NormalRunAdmissionFacts(FrozenV3Model):
    run_kind: RunKind
    source_job_allowed: bool
    branch_allowed: bool
    environment_allowed: bool
    execution_profile_allowed: bool
    run_finished: bool
    versions_compatible: bool
    artifacts_trusted: bool
    integrity_eligible: bool
    comparability_valid: bool
    rerun_supported: bool = True
    contract_values_known: bool = True


class NormalCaseAdmissionFacts(FrozenV3Model):
    lifecycle_valid: bool
    expected_outcome_excluded: bool = False
    collection_failure: bool = False
    failure_evidence_valid: bool = True
    infrastructure_failure: bool = False
    classification_known: bool = True
    outcome: str

    @field_validator("outcome")
    @classmethod
    def _outcome(cls, value: str) -> str:
        normalized = _required_text(value).casefold()
        if normalized not in {"pass", "fail"}:
            raise ValueError("outcome must be pass or fail")
        return normalized


_RUN_REASON_PRIORITY = {
    "normal_run_kind_mismatch": 10,
    "normal_source_job_not_allowed": 20,
    "normal_branch_not_allowed": 30,
    "normal_environment_not_allowed": 40,
    "normal_execution_profile_not_allowed": 50,
    "normal_run_not_finished": 60,
    "normal_version_incompatible": 70,
    "normal_artifact_untrusted": 80,
    "normal_integrity_ineligible": 90,
    "normal_comparability_missing": 100,
    "normal_rerun_unsupported": 110,
    "normal_unknown_contract_value": 120,
    "normal_eligible": 1000,
}

_CASE_REASON_PRIORITY = {
    "case_lifecycle_invalid": 10,
    "case_expected_outcome_excluded": 20,
    "case_collection_failure": 30,
    "case_failure_evidence_invalid": 40,
    "case_infrastructure_failure": 50,
    "case_classification_unknown": 60,
    "case_fail_eligible": 1000,
    "case_pass_eligible": 1000,
}


def comparability_fingerprint(facts: ComparabilityFacts) -> str:
    payload = facts.model_dump(mode="json")
    return f"flaky-comparability-v1-{_sha256(payload)}"


def evaluate_normal_run_admission(
    facts: NormalRunAdmissionFacts,
    *,
    policy_revision: str,
    rule_version: str = NORMAL_ADMISSION_RULE_VERSION,
) -> AdmissionResult:
    reasons: list[str] = []
    checks = (
        (facts.run_kind is RunKind.NORMAL, "normal_run_kind_mismatch"),
        (facts.source_job_allowed, "normal_source_job_not_allowed"),
        (facts.branch_allowed, "normal_branch_not_allowed"),
        (facts.environment_allowed, "normal_environment_not_allowed"),
        (facts.execution_profile_allowed, "normal_execution_profile_not_allowed"),
        (facts.run_finished, "normal_run_not_finished"),
        (facts.versions_compatible, "normal_version_incompatible"),
        (facts.artifacts_trusted, "normal_artifact_untrusted"),
        (facts.integrity_eligible, "normal_integrity_ineligible"),
        (facts.comparability_valid, "normal_comparability_missing"),
        (facts.rerun_supported, "normal_rerun_unsupported"),
        (facts.contract_values_known, "normal_unknown_contract_value"),
    )
    reasons.extend(code for passed, code in checks if not passed)
    if not reasons:
        reasons.append("normal_eligible")
    return _admission_result(reasons, _RUN_REASON_PRIORITY, policy_revision, rule_version)


def evaluate_normal_case_admission(
    facts: NormalCaseAdmissionFacts,
    *,
    policy_revision: str,
    rule_version: str = NORMAL_ADMISSION_RULE_VERSION,
) -> AdmissionResult:
    reasons: list[str] = []
    checks = (
        (facts.lifecycle_valid, "case_lifecycle_invalid"),
        (not facts.expected_outcome_excluded, "case_expected_outcome_excluded"),
        (not facts.collection_failure, "case_collection_failure"),
        (facts.failure_evidence_valid, "case_failure_evidence_invalid"),
        (not facts.infrastructure_failure, "case_infrastructure_failure"),
        (facts.classification_known, "case_classification_unknown"),
    )
    reasons.extend(code for passed, code in checks if not passed)
    if not reasons:
        reasons.append(
            "case_pass_eligible" if facts.outcome == "pass" else "case_fail_eligible"
        )
    return _admission_result(reasons, _CASE_REASON_PRIORITY, policy_revision, rule_version)


@dataclass(frozen=True)
class DetectionObservation:
    observation_id: str
    run_id: str
    observed_at: datetime
    outcome: str
    failure_fingerprint: str | None = None

    @property
    def signature(self) -> str:
        if self.outcome == "pass":
            return "pass"
        if self.outcome != "fail" or not self.failure_fingerprint:
            raise ValueError("fail observation requires failure_fingerprint")
        return f"fail:{self.failure_fingerprint}"


@dataclass(frozen=True)
class DetectionTransition:
    transition_id: str
    from_state: DetectionState | None
    to_state: DetectionState
    reason_code: str
    trigger_observation_id: str


@dataclass(frozen=True)
class DetectionProjection:
    state: DetectionState
    sample_size: int
    pass_count: int
    fail_count: int
    outcome_switch_count: int
    signature_switch_count: int
    distinct_failure_fingerprint_count: int
    trailing_same_signature_count: int
    stable_outcome: str | None
    stable_failure_fingerprint: str | None
    latest_observation_id: str
    transitions: tuple[DetectionTransition, ...]


def replay_detection_cohort(
    observations: Sequence[DetectionObservation],
    *,
    flaky_key: str,
    detection_generation: int,
    fingerprint: str,
    config: FlakyRuleConfig = FlakyRuleConfig(),
) -> DetectionProjection:
    if detection_generation < 1:
        raise ValueError("detection_generation must be at least 1")
    ordered = tuple(
        sorted(observations, key=lambda item: (item.observed_at, item.run_id, item.observation_id))
    )
    if not ordered:
        raise ValueError("at least one observation is required")
    state: DetectionState | None = None
    stable_signature: str | None = None
    transitions: list[DetectionTransition] = []
    for index, observation in enumerate(ordered, start=1):
        evidence = _detection_evidence(ordered[:index], config.evidence_window_size)
        previous = state
        reason: str | None = None
        if state is None:
            state = DetectionState.OBSERVING
            reason = "comparability_cohort_started"
        elif state is DetectionState.OBSERVING:
            if evidence["signature_switch_count"] > 0:
                state = DetectionState.SUSPECTED
                reason = (
                    "outcome_changed"
                    if evidence["outcome_switch_count"] > 0
                    else "failure_fingerprint_changed"
                )
            elif evidence["sample_size"] >= config.stable_min_samples:
                state = DetectionState.STABLE
                stable_signature = observation.signature
                reason = "consistent_signature_threshold_met"
        elif state is DetectionState.STABLE:
            if stable_signature is None:
                stable_signature = observation.signature
            if observation.signature != stable_signature:
                state = DetectionState.SUSPECTED
                reason = "stable_signature_broken"
        elif state is DetectionState.SUSPECTED:
            if (
                evidence["sample_size"] >= config.confirmed_min_samples
                and evidence["outcome_switch_count"] >= config.confirmed_min_outcome_switches
                and evidence["pass_count"] >= config.confirmed_min_pass_count
                and evidence["fail_count"] >= config.confirmed_min_fail_count
            ):
                state = DetectionState.CONFIRMED
                reason = "confirmation_threshold_met"
            elif evidence["trailing_same_signature_count"] >= config.suspected_clear_signature_streak:
                state = DetectionState.STABLE
                stable_signature = observation.signature
                reason = "suspected_cleared_by_streak"
        if reason is not None:
            transition_payload = {
                "comparability_fingerprint": fingerprint,
                "detection_generation": detection_generation,
                "flaky_key": flaky_key,
                "from_state": previous.value if previous else None,
                "reason_code": reason,
                "rule_version": config.rule_version,
                "to_state": state.value,
                "trigger_observation_id": observation.observation_id,
            }
            transitions.append(
                DetectionTransition(
                    transition_id=f"{DETECTION_TRANSITION_VERSION}-{_sha256(transition_payload)}",
                    from_state=previous,
                    to_state=state,
                    reason_code=reason,
                    trigger_observation_id=observation.observation_id,
                )
            )
    final = _detection_evidence(ordered, config.evidence_window_size)
    stable_outcome: str | None = None
    stable_failure: str | None = None
    if state is DetectionState.STABLE and stable_signature:
        if stable_signature == "pass":
            stable_outcome = "pass"
        else:
            stable_outcome = "fail"
            stable_failure = stable_signature.removeprefix("fail:")
    return DetectionProjection(
        state=state,
        sample_size=final["sample_size"],
        pass_count=final["pass_count"],
        fail_count=final["fail_count"],
        outcome_switch_count=final["outcome_switch_count"],
        signature_switch_count=final["signature_switch_count"],
        distinct_failure_fingerprint_count=final["distinct_failure_fingerprint_count"],
        trailing_same_signature_count=final["trailing_same_signature_count"],
        stable_outcome=stable_outcome,
        stable_failure_fingerprint=stable_failure,
        latest_observation_id=ordered[-1].observation_id,
        transitions=tuple(transitions),
    )


class ProbeAdmissionFacts(FrozenV3Model):
    duplicate_run: bool = False
    attempt_active: bool
    plan_matches: bool
    evidence_trusted: bool
    rerun_supported: bool = True
    outcome: ProbeOutcome
    trusted_failure: bool = False
    interval_satisfied: bool = True
    diagnostic_codes: tuple[str, ...] = ()


class ProbeAdmissionResult(FrozenV3Model):
    classification: ProbeClassification
    reason_code: str
    consumes_non_counting_quota: bool
    effect_status: ProbeEffectStatus
    diagnostic_codes: tuple[str, ...]
    rule_version: str = PROBE_EVIDENCE_RULE_VERSION


def classify_probe_evidence(facts: ProbeAdmissionFacts) -> ProbeAdmissionResult:
    classification = ProbeClassification.NON_COUNTING
    consumes = False
    effect = ProbeEffectStatus.AUDIT_ONLY
    if facts.duplicate_run:
        reason = "probe_duplicate_run"
    elif not facts.attempt_active:
        reason = "probe_attempt_inactive"
    elif not facts.plan_matches:
        reason = "probe_plan_mismatch"
    elif not facts.evidence_trusted:
        reason = "probe_evidence_untrusted"
        consumes = True
        effect = ProbeEffectStatus.APPLIED
    elif not facts.rerun_supported:
        reason = "probe_rerun_unsupported"
        consumes = True
        effect = ProbeEffectStatus.APPLIED
    elif facts.outcome in {
        ProbeOutcome.SKIP,
        ProbeOutcome.XFAIL,
        ProbeOutcome.XPASS,
        ProbeOutcome.NO_DATA,
    }:
        reason = "probe_outcome_not_countable"
        consumes = True
        effect = ProbeEffectStatus.APPLIED
    elif facts.outcome is ProbeOutcome.FAIL and facts.trusted_failure:
        classification = ProbeClassification.TRUSTED_FAIL
        reason = "probe_trusted_fail"
        effect = ProbeEffectStatus.APPLIED
    elif facts.outcome is ProbeOutcome.PASS and not facts.interval_satisfied:
        reason = "probe_interval_too_short"
    elif facts.outcome is ProbeOutcome.PASS:
        classification = ProbeClassification.COUNT_PASS
        reason = "probe_count_pass"
        effect = ProbeEffectStatus.APPLIED
    else:
        reason = "probe_evidence_untrusted"
        consumes = True
        effect = ProbeEffectStatus.APPLIED
    return ProbeAdmissionResult(
        classification=classification,
        reason_code=reason,
        consumes_non_counting_quota=consumes,
        effect_status=effect,
        diagnostic_codes=tuple(sorted(set(facts.diagnostic_codes))),
    )


@dataclass(frozen=True)
class AttemptEvidence:
    run_id: str
    round_no: int
    trusted_started_at: datetime
    classification: ProbeClassification
    consumes_non_counting_quota: bool
    effect_status: ProbeEffectStatus


@dataclass(frozen=True)
class AttemptRecalculation:
    status: AttemptStatus
    counted_passes: int
    non_counting_runs: int
    end_reason: str | None


def recalculate_attempt(
    evidence: Sequence[AttemptEvidence],
    *,
    now: datetime,
    expires_at: datetime,
    required_consecutive_passes: int,
    max_non_counting_runs: int,
) -> AttemptRecalculation:
    ordered = sorted(
        (item for item in evidence if item.effect_status is ProbeEffectStatus.APPLIED),
        key=lambda item: (item.round_no, item.trusted_started_at, item.run_id),
    )
    counted = 0
    non_counting = 0
    for item in ordered:
        if item.classification is ProbeClassification.TRUSTED_FAIL:
            return AttemptRecalculation(
                AttemptStatus.FAILED, counted, non_counting, "probe_trusted_fail"
            )
        if item.classification is ProbeClassification.COUNT_PASS:
            counted += 1
        elif item.consumes_non_counting_quota:
            non_counting += 1
    if non_counting >= max_non_counting_runs:
        return AttemptRecalculation(
            AttemptStatus.INCONCLUSIVE,
            counted,
            non_counting,
            "probe_non_counting_quota_exhausted",
        )
    if now >= expires_at:
        return AttemptRecalculation(
            AttemptStatus.EXPIRED, counted, non_counting, "attempt_expired"
        )
    if counted >= required_consecutive_passes:
        return AttemptRecalculation(
            AttemptStatus.READY_TO_CLOSE, counted, non_counting, None
        )
    return AttemptRecalculation(AttemptStatus.ACTIVE, counted, non_counting, None)


def build_governance_event_id(
    *, governance_id: str, event_type: str, causal_id: str
) -> str:
    return f"{GOVERNANCE_EVENT_VERSION}-{_sha256({'causal_id': causal_id, 'event_type': event_type, 'governance_id': governance_id})}"


def _admission_result(
    reasons: Iterable[str],
    priorities: dict[str, int],
    policy_revision: str,
    rule_version: str,
) -> AdmissionResult:
    ordered = tuple(sorted(set(reasons), key=lambda item: (priorities[item], item)))
    eligible = all(priorities[item] == 1000 for item in ordered)
    return AdmissionResult(
        status=AdmissionStatus.ELIGIBLE if eligible else AdmissionStatus.INELIGIBLE,
        reason_codes=ordered,
        primary_reason_code=ordered[0],
        policy_revision=_required_text(policy_revision),
        rule_version=_required_text(rule_version),
    )


def _detection_evidence(
    observations: Sequence[DetectionObservation], window_size: int
) -> dict[str, int]:
    window = observations[-window_size:]
    signatures = [item.signature for item in window]
    outcomes = [item.outcome for item in window]
    trailing = 1
    for signature in reversed(signatures[:-1]):
        if signature != signatures[-1]:
            break
        trailing += 1
    return {
        "sample_size": len(window),
        "pass_count": sum(item == "pass" for item in outcomes),
        "fail_count": sum(item == "fail" for item in outcomes),
        "outcome_switch_count": sum(a != b for a, b in zip(outcomes, outcomes[1:])),
        "signature_switch_count": sum(
            a != b for a, b in zip(signatures, signatures[1:])
        ),
        "distinct_failure_fingerprint_count": len(
            {item.failure_fingerprint for item in window if item.failure_fingerprint}
        ),
        "trailing_same_signature_count": trailing,
    }


def _sha256(payload: object) -> str:
    canonical = json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _required_text(value: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise ValueError("value must not be empty")
    return normalized
