from __future__ import annotations

from datetime import UTC, datetime
import sqlite3
from typing import Sequence

from quality.flaky_models import (
    CaseObservation,
    CaseObservationCandidate,
    FlakyRunMetadata,
)

from .contracts import FlakyStoreError, StoreImportOutcome, StoreInitialization
from .repository import FlakyRepository


def validate_import_candidates(
    metadata: FlakyRunMetadata,
    candidates: Sequence[CaseObservationCandidate],
) -> None:
    if len(candidates) != metadata.eligible_count:
        raise FlakyStoreError(
            "eligible_count_mismatch",
            "candidate count does not match metadata eligible_count",
        )


def import_run(
    connection: sqlite3.Connection,
    repository: FlakyRepository,
    metadata: FlakyRunMetadata,
    candidates: Sequence[CaseObservationCandidate],
    *,
    initialization: StoreInitialization,
) -> StoreImportOutcome:
    existing_digest = repository.source_digest_for_run(connection, metadata.run_id)
    if existing_digest is not None:
        if existing_digest == metadata.source_digest:
            return StoreImportOutcome(
                imported=False,
                inserted_count=0,
                initialization=initialization,
            )
        raise FlakyStoreError(
            "run_source_conflict",
            f"run_id {metadata.run_id!r} already has a different source digest",
        )

    digest_owner = repository.run_id_for_source_digest(
        connection, metadata.source_digest
    )
    if digest_owner is not None:
        raise FlakyStoreError(
            "source_digest_conflict",
            "source digest is already associated with another run_id",
        )

    imported_at = datetime.now(UTC)
    observations = [
        materialize_observation(connection, repository, candidate, imported_at)
        for candidate in sorted(
            candidates,
            key=lambda item: (
                item.case_id,
                item.param_hash,
                item.execution_profile,
                item.invocation_id,
            ),
        )
    ]
    repository.insert_import_run(connection, metadata, imported_at)
    for observation in observations:
        repository.insert_observation(connection, observation)

    inserted = repository.observation_count_for_run(connection, metadata.run_id)
    if inserted != len(observations):
        raise FlakyStoreError(
            "observation_count_mismatch",
            "committed observation count would not match eligible count",
        )
    return StoreImportOutcome(
        imported=True,
        inserted_count=inserted,
        initialization=initialization,
    )


def materialize_observation(
    connection: sqlite3.Connection,
    repository: FlakyRepository,
    candidate: CaseObservationCandidate,
    now: datetime,
) -> CaseObservation:
    from quality.flaky_importer import (
        build_epoch_scope_key,
        build_flaky_key,
        build_observation_id,
    )

    epoch_scope_key = build_epoch_scope_key(
        candidate.case_id,
        candidate.environment,
        candidate.execution_profile,
    )
    scope = repository.epoch_scope(connection, epoch_scope_key)
    if scope is None:
        repository.insert_epoch_scope(connection, epoch_scope_key, candidate, now)
        state_epoch = 1
    else:
        expected = {
            "case_id": candidate.case_id,
            "environment": candidate.environment,
            "execution_profile": candidate.execution_profile,
            "identity_rule_version": candidate.identity_rule_version,
            "environment_rule_version": candidate.environment_rule_version,
            "execution_profile_rule_version": candidate.execution_profile_rule_version,
        }
        for field, value in expected.items():
            if scope[field] != value:
                raise FlakyStoreError(
                    "epoch_scope_conflict",
                    f"epoch scope field {field!r} is incompatible with current rules",
                )
        state_epoch = int(scope["current_epoch"])
        current_versions = repository.epoch_rule_versions(
            connection, epoch_scope_key, state_epoch
        )
        desired_versions = (
            candidate.identity_rule_version,
            candidate.environment_rule_version,
            candidate.execution_profile_rule_version,
            candidate.observation_rule_version,
            candidate.fingerprint_version,
        )
        for versions in current_versions:
            if tuple(versions) != desired_versions:
                raise FlakyStoreError(
                    "epoch_rule_version_conflict",
                    "current epoch contains observations produced by incompatible rules; reset epoch explicitly",
                )

    flaky_key = build_flaky_key(
        candidate.case_id,
        candidate.param_hash,
        candidate.environment,
        candidate.execution_profile,
        state_epoch,
    )
    observation_id = build_observation_id(candidate.run_id, flaky_key)
    if repository.observation_conflict_exists(
        connection,
        observation_id,
        candidate.run_id,
        flaky_key,
    ):
        raise FlakyStoreError(
            "observation_conflict",
            "observation identity already exists outside a run-level no-op",
        )
    return CaseObservation(
        **candidate.model_dump(),
        observation_id=observation_id,
        flaky_key=flaky_key,
        epoch_scope_key=epoch_scope_key,
        state_epoch=state_epoch,
    )
