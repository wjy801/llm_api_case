from __future__ import annotations

from datetime import UTC, datetime
import sqlite3
from typing import Sequence

from quality.flaky import (
    DEFAULT_FLAKY_RULE_CONFIG,
    build_transition_id,
    derive_evidence_window,
    replay_observations,
)
from quality.flaky_models import (
    FlakyEvaluationResult,
    FlakyEvaluationStatus,
    FlakyHistoryEntry,
    FlakyImportIssue,
    FlakyRuleConfig,
    FlakyState,
    FlakyStateRecord,
    FlakyTransitionRecord,
    ObservationOutcome,
    ProjectionStatus,
    TransitionTrigger,
)
from quality.models import IssueSeverity

from .contracts import (
    FlakyStoreError,
    ProjectionPlan,
    StoreInitialization,
    required_text,
)
from .repository import FlakyRepository


def evaluate_run(
    connection: sqlite3.Connection,
    repository: FlakyRepository,
    run_id: str,
    *,
    initialization: StoreInitialization,
    config: FlakyRuleConfig = DEFAULT_FLAKY_RULE_CONFIG,
) -> FlakyEvaluationResult:
    run_id = required_text(run_id, "run_id")
    evaluated_at = datetime.now(UTC)
    if not repository.imported_run_exists(connection, run_id):
        raise FlakyStoreError("run_not_found", f"run_id {run_id!r} is not imported")
    flaky_keys = repository.flaky_keys_for_run(connection, run_id)
    if not flaky_keys:
        return FlakyEvaluationResult(
            run_id=run_id,
            status=FlakyEvaluationStatus.NO_DATA,
            evaluated_at=evaluated_at,
            database_schema_version=initialization.schema_version,
            quick_check=initialization.quick_check,
            issues=(
                FlakyImportIssue(
                    severity=IssueSeverity.INFO,
                    code="run_has_no_observations",
                    summary="The imported run has no eligible Case observations.",
                ),
            ),
        )
    plans = tuple(
        build_projection_plan(
            connection,
            repository,
            flaky_key,
            now=evaluated_at,
            config=config,
            trigger_run_id=run_id,
        )
        for flaky_key in flaky_keys
    )
    for plan in plans:
        write_projection_plan(connection, repository, plan)

    summaries = {
        key: repository.state_summary(connection, key)
        for key in flaky_keys
    }
    transitioned = tuple(
        transition
        for plan in plans
        for transition in plan.transitions
    )
    newly_suspected = tuple(
        summaries[item.flaky_key]
        for item in transitioned
        if item.to_state is FlakyState.SUSPECTED
    )
    newly_confirmed = tuple(
        summaries[item.flaky_key]
        for item in transitioned
        if item.to_state is FlakyState.CONFIRMED
    )
    recovered = tuple(
        summaries[item.flaky_key]
        for item in transitioned
        if item.reason_code == "recovery_stable_streak_met"
    )
    ongoing_confirmed = tuple(
        summary
        for plan, summary in zip(plans, summaries.values())
        if not plan.transitions
        and summary.current_state is FlakyState.CONFIRMED
    )
    quarantined = tuple(
        summary
        for summary in summaries.values()
        if summary.current_state is FlakyState.QUARANTINED
    )
    recovering = tuple(
        summary
        for summary in summaries.values()
        if summary.current_state is FlakyState.RECOVERING
    )
    overdue = repository.overdue_summaries(connection, evaluated_at)
    changed_count = sum(plan.changed for plan in plans)
    stale_count = sum(
        plan.state.projection_status is ProjectionStatus.STALE for plan in plans
    )
    return FlakyEvaluationResult(
        run_id=run_id,
        status=(
            FlakyEvaluationStatus.EVALUATED
            if changed_count
            else FlakyEvaluationStatus.NOOP
        ),
        evaluated_at=evaluated_at,
        affected_count=len(flaky_keys),
        evaluated_count=len(plans),
        transitioned_count=len({item.flaky_key for item in transitioned}),
        stale_count=stale_count,
        newly_suspected=newly_suspected,
        newly_confirmed=newly_confirmed,
        ongoing_confirmed=ongoing_confirmed,
        quarantined=quarantined,
        recovering=recovering,
        recovered=recovered,
        overdue=overdue,
        transitions=transitioned,
        database_schema_version=initialization.schema_version,
        quick_check=initialization.quick_check,
    )


def rebuild_states(
    connection: sqlite3.Connection,
    repository: FlakyRepository,
    *,
    apply: bool,
    initialization: StoreInitialization,
    config: FlakyRuleConfig = DEFAULT_FLAKY_RULE_CONFIG,
) -> dict[str, object]:
    now = datetime.now(UTC)
    keys = repository.all_flaky_keys(connection)
    plans = tuple(
        build_projection_plan(
            connection,
            repository,
            key,
            now=now,
            config=config,
            trigger_run_id=None,
        )
        for key in keys
    )
    if apply:
        for plan in plans:
            write_projection_plan(connection, repository, plan)
    return {
        "mode": "apply" if apply else "dry-run",
        "schema_version": initialization.schema_version,
        "quick_check": initialization.quick_check,
        "key_count": len(keys),
        "changed_count": sum(plan.changed for plan in plans),
        "transition_count": sum(len(plan.transitions) for plan in plans),
        "rule_version": config.rule_version,
        "projection_version": config.projection_version,
    }


def build_projection_plan(
    connection: sqlite3.Connection,
    repository: FlakyRepository,
    flaky_key: str,
    *,
    now: datetime,
    config: FlakyRuleConfig,
    trigger_run_id: str | None,
) -> ProjectionPlan:
    observations = repository.observations_for_key(connection, flaky_key)
    if not observations:
        raise FlakyStoreError(
            "projection_has_no_observations",
            f"flaky_key {flaky_key!r} has no observations",
        )
    existing = repository.state_or_none(connection, flaky_key)
    if existing is not None and (
        existing.rule_version != config.rule_version
        or existing.projection_version != config.projection_version
    ):
        raise FlakyStoreError(
            "incompatible_projection_version",
            "stored Flaky state requires an explicit versioned rebuild",
        )

    automatic = replay_observations(observations, config)
    latest = observations[-1]
    evidence = automatic.evidence
    current_state = automatic.current_state
    detected_state = automatic.detected_state
    stable_outcome = automatic.stable_outcome
    stable_failure_id = automatic.stable_failure_id
    evaluation_anchor = (
        existing.evaluation_anchor_observation_id if existing is not None else None
    )
    reason_code: str | None = None

    if existing is not None and existing.current_state is FlakyState.CONFIRMED:
        current_state = FlakyState.CONFIRMED
        detected_state = FlakyState.CONFIRMED
        stable_outcome = None
        stable_failure_id = None
    elif existing is not None and existing.evaluation_anchor_observation_id is not None:
        (
            current_state,
            detected_state,
            evidence,
            stable_outcome,
            stable_failure_id,
            reason_code,
        ) = evaluate_from_manual_anchor(existing, observations, config)

    transitions: list[FlakyTransitionRecord] = []
    if existing is None:
        for decision in automatic.transitions:
            transitions.append(
                transition_record(
                    flaky_key=flaky_key,
                    from_state=decision.from_state,
                    to_state=decision.to_state,
                    trigger_type=TransitionTrigger.BOOTSTRAP,
                    reason_code=decision.reason_code,
                    trigger_observation_id=decision.trigger_observation_id,
                    evidence=decision.evidence,
                    actor=None,
                    created_at=now,
                    config=config,
                )
            )
    elif existing.current_state is not current_state:
        old_latest_index = next(
            (
                index
                for index, item in enumerate(observations)
                if item.observation_id == existing.latest_observation_id
            ),
            None,
        )
        has_new_observation = (
            existing.total_observation_count < len(observations)
        )
        late = has_new_observation and (
            old_latest_index is None
            or old_latest_index == len(observations) - 1
            or old_latest_index < len(observations) - 2
        )
        trigger_type = (
            TransitionTrigger.REPROJECTION if late else TransitionTrigger.OBSERVATION
        )
        if late:
            reason_code = "late_observation_reprojection"
        if reason_code is None:
            reason_code = projection_reason(automatic, current_state)
        trigger_observation = next(
            (
                observation
                for observation in observations
                if trigger_run_id is not None
                and observation.run_id == trigger_run_id
            ),
            latest,
        )
        transitions.append(
            transition_record(
                flaky_key=flaky_key,
                from_state=existing.current_state,
                to_state=current_state,
                trigger_type=trigger_type,
                reason_code=reason_code,
                trigger_observation_id=trigger_observation.observation_id,
                evidence=evidence,
                actor=None,
                created_at=now,
                config=config,
            )
        )

    transitions = [
        transition
        for transition in transitions
        if not repository.transition_exists(connection, transition.transition_id)
    ]
    last_transition_id = (
        transitions[-1].transition_id
        if transitions
        else existing.last_transition_id if existing is not None else None
    )
    first = observations[0]
    state = FlakyStateRecord(
        flaky_key=flaky_key,
        epoch_scope_key=first.epoch_scope_key,
        case_id=first.case_id,
        param_hash=first.param_hash,
        environment=first.environment,
        execution_profile=first.execution_profile,
        state_epoch=first.state_epoch,
        current_state=current_state,
        detected_state=detected_state,
        stable_outcome=stable_outcome,
        stable_failure_id=stable_failure_id,
        total_observation_count=len(observations),
        sample_size=evidence.sample_size,
        evidence_window_size=evidence.evidence_window_size,
        pass_count=evidence.pass_count,
        fail_count=evidence.fail_count,
        outcome_switch_count=evidence.outcome_switch_count,
        signature_switch_count=evidence.signature_switch_count,
        distinct_failure_fingerprint_count=(
            evidence.distinct_failure_fingerprint_count
        ),
        trailing_same_signature_count=evidence.trailing_same_signature_count,
        evaluation_anchor_observation_id=evaluation_anchor,
        latest_observation_id=latest.observation_id,
        latest_run_id=latest.run_id,
        latest_observed_at=latest.observed_at,
        last_transition_id=last_transition_id,
        rule_version=config.rule_version,
        projection_version=config.projection_version,
        projection_status=ProjectionStatus.CURRENT,
        created_at=existing.created_at if existing is not None else now,
        updated_at=now,
    )
    changed = existing is None or state_payload(state) != state_payload(existing)
    return ProjectionPlan(
        state=state,
        transitions=tuple(transitions),
        changed=changed,
        close_governance_id=None,
        governance_resolution=None,
    )

def write_projection_plan(
    connection: sqlite3.Connection,
    repository: FlakyRepository,
    plan: ProjectionPlan,
) -> None:
    if plan.changed:
        repository.upsert_state(connection, plan.state)
    for transition in plan.transitions:
        repository.insert_transition(connection, transition)

def transition_record(
    *,
    flaky_key: str,
    from_state: FlakyState | None,
    to_state: FlakyState,
    trigger_type: TransitionTrigger,
    reason_code: str,
    trigger_observation_id: str | None,
    evidence,
    actor: str | None,
    created_at: datetime,
    config: FlakyRuleConfig,
) -> FlakyTransitionRecord:
    transition_id = build_transition_id(
        flaky_key=flaky_key,
        from_state=from_state,
        to_state=to_state,
        trigger_type=trigger_type.value,
        reason_code=reason_code,
        trigger_observation_id=trigger_observation_id,
        rule_version=config.rule_version,
        projection_version=config.projection_version,
    )
    return FlakyTransitionRecord(
        transition_id=transition_id,
        flaky_key=flaky_key,
        from_state=from_state,
        to_state=to_state,
        trigger_type=trigger_type,
        reason_code=reason_code,
        rule_version=config.rule_version,
        projection_version=config.projection_version,
        sample_size=evidence.sample_size,
        trigger_observation_id=trigger_observation_id,
        evidence_observation_ids=evidence.observation_ids,
        evidence_run_ids=evidence.run_ids,
        actor=actor,
        created_at=created_at,
    )

def observations_after_anchor(
    observations: Sequence[FlakyHistoryEntry],
    anchor_id: str | None,
) -> tuple[FlakyHistoryEntry, ...]:
    if anchor_id is None:
        raise FlakyStoreError(
            "recovery_anchor_missing",
            "RECOVERING governance has no recovery observation anchor",
        )
    for index, observation in enumerate(observations):
        if observation.observation_id == anchor_id:
            return tuple(observations[index + 1 :])
    raise FlakyStoreError(
        "recovery_anchor_not_found",
        "recovery observation anchor is not present in the current epoch",
    )

def evaluate_from_manual_anchor(
    existing: FlakyStateRecord,
    observations: Sequence[FlakyHistoryEntry],
    config: FlakyRuleConfig,
):
    anchor = existing.evaluation_anchor_observation_id
    anchor_index = next(
        (
            index
            for index, observation in enumerate(observations)
            if observation.observation_id == anchor
        ),
        None,
    )
    if anchor_index is None:
        raise FlakyStoreError(
            "evaluation_anchor_not_found",
            "manual evaluation anchor is not present in the current epoch",
        )
    scoped = tuple(observations[anchor_index:])
    evidence = derive_evidence_window(scoped, config)
    current = existing.current_state
    stable_outcome = existing.stable_outcome
    stable_failure_id = existing.stable_failure_id
    reason: str | None = None
    stable_signature = (
        "pass"
        if stable_outcome is ObservationOutcome.PASS
        else f"fail:{stable_failure_id}"
        if stable_outcome is ObservationOutcome.FAIL and stable_failure_id
        else None
    )
    if current is FlakyState.STABLE:
        if stable_signature is None:
            raise FlakyStoreError(
                "stable_signature_missing",
                "anchored STABLE state has no stable result signature",
            )
        if evidence.latest_signature != stable_signature:
            current = FlakyState.SUSPECTED
            stable_outcome = None
            stable_failure_id = None
            reason = "stable_signature_broken"
    elif current is FlakyState.SUSPECTED:
        confirmation_met = (
            evidence.sample_size >= config.confirmed_min_samples
            and evidence.pass_count >= config.confirmed_min_pass_count
            and evidence.fail_count >= config.confirmed_min_fail_count
            and evidence.outcome_switch_count
            >= config.confirmed_min_outcome_switches
        )
        if confirmation_met:
            current = FlakyState.CONFIRMED
            stable_outcome = None
            stable_failure_id = None
            reason = "confirmation_threshold_met"
        elif (
            evidence.trailing_same_signature_count
            >= config.suspected_clear_signature_streak
        ):
            current = FlakyState.STABLE
            stable_outcome, stable_failure_id = signature_values(
                evidence.latest_signature
            )
            reason = "suspected_cleared_by_streak"
    elif current is FlakyState.CONFIRMED:
        stable_outcome = None
        stable_failure_id = None
    else:
        raise FlakyStoreError(
            "invalid_anchored_state",
            f"manual anchor cannot be evaluated from {current.value}",
        )
    return (
        current,
        current,
        evidence,
        stable_outcome,
        stable_failure_id,
        reason,
    )

def signature_values(
    signature: str,
) -> tuple[ObservationOutcome, str | None]:
    if signature == "pass":
        return ObservationOutcome.PASS, None
    if signature.startswith("fail:") and len(signature) > len("fail:"):
        return ObservationOutcome.FAIL, signature[len("fail:") :]
    raise ValueError(f"invalid result signature: {signature!r}")

def projection_reason(projection, target_state: FlakyState) -> str:
    for transition in reversed(projection.transitions):
        if transition.to_state is target_state:
            return transition.reason_code
    return "projection_state_changed"

def evidence_from_state(
    state: FlakyStateRecord,
    observations: Sequence[FlakyHistoryEntry],
    config: FlakyRuleConfig,
):
    del state
    return derive_evidence_window(observations, config)


def state_payload(state: FlakyStateRecord) -> dict[str, object]:
    return state.model_dump(exclude={"updated_at"}, mode="python")
