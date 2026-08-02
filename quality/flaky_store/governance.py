from __future__ import annotations

from datetime import UTC, datetime
import sqlite3
import uuid

from quality.flaky import DEFAULT_FLAKY_RULE_CONFIG, derive_evidence_window
from quality.flaky_models import (
    FlakyGovernanceRecord,
    FlakyManualActionRequest,
    FlakyQuarantineRequest,
    FlakyState,
    FlakyStateRecord,
    FlakyTransitionRecord,
    GovernanceStatus,
    TransitionTrigger,
)

from .contracts import FlakyStoreError
from .projection import transition_record
from .repository import (
    FlakyRepository,
)


def confirm_flaky(
    connection: sqlite3.Connection,
    repository: FlakyRepository,
    request: FlakyManualActionRequest,
) -> FlakyStateRecord:
    return manual_override(
        connection,
        repository,
        request,
        action="confirm_flaky",
        allowed_states=(FlakyState.SUSPECTED,),
        target_state=FlakyState.CONFIRMED,
    )


def mark_not_flaky(
    connection: sqlite3.Connection,
    repository: FlakyRepository,
    request: FlakyManualActionRequest,
) -> FlakyStateRecord:
    return manual_override(
        connection,
        repository,
        request,
        action="mark_not_flaky",
        allowed_states=(FlakyState.SUSPECTED, FlakyState.CONFIRMED),
        target_state=FlakyState.STABLE,
    )


def quarantine(
    connection: sqlite3.Connection,
    repository: FlakyRepository,
    request: FlakyQuarantineRequest,
) -> FlakyGovernanceRecord:
    now = datetime.now(UTC)
    if request.expires_at.astimezone(UTC) <= now:
        raise FlakyStoreError("invalid_expiry", "expires_at must be in the future")
    state = repository.require_state(connection, request.flaky_key)
    if state.current_state is not FlakyState.CONFIRMED:
        raise FlakyStoreError(
            "invalid_state_transition",
            "quarantine requires current_state=CONFIRMED",
        )
    repository.require_no_open_governance(connection, request.flaky_key)
    governance_id = f"governance-v1-{uuid.uuid4().hex}"
    repository.insert_governance(
        connection,
        governance_id=governance_id,
        flaky_key=request.flaky_key,
        owner=request.owner,
        reason=request.reason,
        actor=request.actor,
        created_at=now,
        expires_at=request.expires_at,
    )
    transition = manual_transition(
        connection,
        repository,
        state,
        to_state=FlakyState.QUARANTINED,
        reason_code="manual_quarantine",
        actor=request.actor,
        now=now,
    )
    repository.update_state_transition(
        connection,
        request.flaky_key,
        state=FlakyState.QUARANTINED,
        transition_id=transition.transition_id,
        updated_at=now,
    )
    return repository.governance_by_id(connection, governance_id)


def start_recovery(
    connection: sqlite3.Connection,
    repository: FlakyRepository,
    request: FlakyManualActionRequest,
) -> FlakyGovernanceRecord:
    now = datetime.now(UTC)
    state = repository.require_state(connection, request.flaky_key)
    if state.current_state is not FlakyState.QUARANTINED:
        raise FlakyStoreError(
            "invalid_state_transition",
            "start recovery requires current_state=QUARANTINED",
        )
    governance = repository.require_open_governance(
        connection,
        request.flaky_key,
        GovernanceStatus.ACTIVE,
    )
    repository.start_governance_recovery(
        connection,
        governance.governance_id,
        actor=request.actor,
        started_at=now,
        reason=request.reason,
        anchor_observation_id=state.latest_observation_id,
    )
    transition = manual_transition(
        connection,
        repository,
        state,
        to_state=FlakyState.RECOVERING,
        reason_code="manual_recovery_started",
        actor=request.actor,
        now=now,
    )
    repository.update_state_transition(
        connection,
        request.flaky_key,
        state=FlakyState.RECOVERING,
        transition_id=transition.transition_id,
        updated_at=now,
    )
    return repository.governance_by_id(connection, governance.governance_id)


def cancel_quarantine(
    connection: sqlite3.Connection,
    repository: FlakyRepository,
    request: FlakyManualActionRequest,
) -> FlakyStateRecord:
    now = datetime.now(UTC)
    state = repository.require_state(connection, request.flaky_key)
    if state.current_state is not FlakyState.QUARANTINED:
        raise FlakyStoreError(
            "invalid_state_transition",
            "cancel quarantine requires current_state=QUARANTINED",
        )
    governance = repository.require_open_governance(
        connection,
        request.flaky_key,
        GovernanceStatus.ACTIVE,
    )
    transition = manual_transition(
        connection,
        repository,
        state,
        to_state=FlakyState.CONFIRMED,
        reason_code="manual_quarantine_cancelled",
        actor=request.actor,
        now=now,
    )
    repository.close_governance(
        connection,
        governance.governance_id,
        closed_at=now,
        resolution="cancelled",
    )
    repository.insert_override(
        connection,
        state,
        action="cancel_quarantine",
        to_state=FlakyState.CONFIRMED,
        actor=request.actor,
        reason=request.reason,
        now=now,
    )
    repository.update_state_after_cancel(
        connection,
        request.flaky_key,
        transition_id=transition.transition_id,
        updated_at=now,
    )
    return repository.state_by_key(connection, request.flaky_key)


def manual_override(
    connection: sqlite3.Connection,
    repository: FlakyRepository,
    request: FlakyManualActionRequest,
    *,
    action: str,
    allowed_states: tuple[FlakyState, ...],
    target_state: FlakyState,
) -> FlakyStateRecord:
    now = datetime.now(UTC)
    state = repository.require_state(connection, request.flaky_key)
    if state.current_state not in allowed_states:
        allowed = ", ".join(item.value for item in allowed_states)
        raise FlakyStoreError(
            "invalid_state_transition",
            f"{action} requires current_state in [{allowed}]",
        )
    repository.require_no_open_governance(connection, request.flaky_key)
    transition = manual_transition(
        connection,
        repository,
        state,
        to_state=target_state,
        reason_code=f"manual_{action}",
        actor=request.actor,
        now=now,
    )
    repository.insert_override(
        connection,
        state,
        action=action,
        to_state=target_state,
        actor=request.actor,
        reason=request.reason,
        now=now,
    )
    stable_outcome: str | None = None
    stable_failure_id: str | None = None
    anchor: str | None = state.evaluation_anchor_observation_id
    if target_state is FlakyState.STABLE:
        latest = repository.observations_for_key(connection, state.flaky_key)[-1]
        stable_outcome = latest.observation_outcome.value
        stable_failure_id = latest.failure_id
        anchor = latest.observation_id
    repository.update_state_after_override(
        connection,
        state.flaky_key,
        target_state=target_state,
        stable_outcome=stable_outcome,
        stable_failure_id=stable_failure_id,
        anchor=anchor,
        transition_id=transition.transition_id,
        updated_at=now,
    )
    return repository.state_by_key(connection, request.flaky_key)


def manual_transition(
    connection: sqlite3.Connection,
    repository: FlakyRepository,
    state: FlakyStateRecord,
    *,
    to_state: FlakyState,
    reason_code: str,
    actor: str,
    now: datetime,
) -> FlakyTransitionRecord:
    observations = repository.observations_for_key(connection, state.flaky_key)
    evidence = derive_evidence_window(observations, DEFAULT_FLAKY_RULE_CONFIG)
    transition = transition_record(
        flaky_key=state.flaky_key,
        from_state=state.current_state,
        to_state=to_state,
        trigger_type=TransitionTrigger.MANUAL,
        reason_code=reason_code,
        trigger_observation_id=state.latest_observation_id,
        evidence=evidence,
        actor=actor,
        created_at=now,
        config=DEFAULT_FLAKY_RULE_CONFIG,
    )
    repository.insert_transition(connection, transition)
    return transition
