from __future__ import annotations

from datetime import UTC, datetime
import sqlite3
import uuid

from quality.flaky_models import (
    EpochResetRequest,
    EpochResetResult,
    FLAKY_ENVIRONMENT_RULE_VERSION,
    FLAKY_EXECUTION_PROFILE_RULE_VERSION,
    FLAKY_IDENTITY_RULE_VERSION,
)

from .contracts import FlakyStoreError
from .repository import FlakyRepository


def reset_epoch(
    connection: sqlite3.Connection,
    repository: FlakyRepository,
    request: EpochResetRequest,
    *,
    epoch_scope_key: str,
) -> EpochResetResult:
    previous_epoch = repository.epoch_current(
        connection,
        epoch_scope_key=epoch_scope_key,
        case_id=request.case_id,
        environment=request.environment,
        execution_profile=request.execution_profile,
    )
    if previous_epoch is None:
        raise FlakyStoreError(
            "epoch_scope_not_found",
            "epoch scope does not exist; no placeholder scope was created",
        )
    active_governance = (
        repository.active_governance_for_epoch(
            connection, epoch_scope_key, previous_epoch
        )
        if repository.has_governance_table(connection)
        else False
    )
    if active_governance:
        raise FlakyStoreError(
            "active_governance_exists",
            "epoch reset is blocked by ACTIVE/RECOVERING governance",
        )
    new_epoch = previous_epoch + 1
    created_at = datetime.now(UTC)
    override_id = f"override-v1-{uuid.uuid4().hex}"
    repository.update_epoch_scope(
        connection,
        epoch_scope_key,
        new_epoch=new_epoch,
        identity_rule_version=FLAKY_IDENTITY_RULE_VERSION,
        environment_rule_version=FLAKY_ENVIRONMENT_RULE_VERSION,
        execution_profile_rule_version=FLAKY_EXECUTION_PROFILE_RULE_VERSION,
        updated_at=created_at,
    )
    repository.insert_epoch_reset_override(
        connection,
        override_id=override_id,
        epoch_scope_key=epoch_scope_key,
        previous_epoch=previous_epoch,
        new_epoch=new_epoch,
        actor=request.actor,
        reason=request.reason,
        created_at=created_at,
    )
    return EpochResetResult(
        override_id=override_id,
        epoch_scope_key=epoch_scope_key,
        case_id=request.case_id,
        environment=request.environment,
        execution_profile=request.execution_profile,
        previous_epoch=previous_epoch,
        new_epoch=new_epoch,
        actor=request.actor,
        reason=request.reason,
        created_at=created_at,
    )
