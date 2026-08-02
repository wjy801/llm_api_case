from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Sequence

from quality.flaky import DEFAULT_FLAKY_RULE_CONFIG
from quality.flaky_models import (
    CaseObservationCandidate,
    EpochResetRequest,
    EpochResetResult,
    FlakyDatabaseCheck,
    FlakyEvaluationResult,
    FlakyGovernanceRecord,
    FlakyHistoryEntry,
    FlakyManualActionRequest,
    FlakyQuarantineRequest,
    FlakyRuleConfig,
    FlakyRunMetadata,
    FlakyStateRecord,
    GovernanceStatus,
)

from . import epoch, governance, import_service, projection
from .contracts import (
    DEFAULT_BUSY_TIMEOUT_MS,
    FlakyStoreError,
    StoreImportOutcome,
)
from .migration import MIGRATIONS_DIRECTORY, initialize_store
from .repository import FlakyRepository


class FlakyStore:
    def __init__(
        self,
        database_path: str | Path,
        *,
        busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
        migrations_directory: str | Path = MIGRATIONS_DIRECTORY,
    ) -> None:
        self.database_path = Path(database_path)
        if not self.database_path.is_absolute():
            raise FlakyStoreError(
                "invalid_database_path",
                "Flaky history database path must be absolute",
            )
        if busy_timeout_ms < 0:
            raise ValueError("busy_timeout_ms must be greater than or equal to 0")
        self.busy_timeout_ms = busy_timeout_ms
        self.migrations_directory = Path(migrations_directory)
        self.repository = FlakyRepository(
            self.database_path,
            busy_timeout_ms=self.busy_timeout_ms,
        )

    def import_run(
        self,
        metadata: FlakyRunMetadata,
        candidates: Sequence[CaseObservationCandidate],
    ) -> StoreImportOutcome:
        import_service.validate_import_candidates(metadata, candidates)
        with self.repository.connection(require_existing=False) as connection:
            initialization = initialize_store(
                connection, self.repository, self.migrations_directory
            )
            with self.repository.transaction(connection):
                return import_service.import_run(
                    connection,
                    self.repository,
                    metadata,
                    candidates,
                    initialization=initialization,
                )

    def evaluate_run(
        self,
        run_id: str,
        *,
        config: FlakyRuleConfig = DEFAULT_FLAKY_RULE_CONFIG,
    ) -> FlakyEvaluationResult:
        with self.repository.connection(require_existing=True) as connection:
            initialization = initialize_store(
                connection, self.repository, self.migrations_directory
            )
            with self.repository.transaction(connection):
                return projection.evaluate_run(
                    connection,
                    self.repository,
                    run_id,
                    initialization=initialization,
                    config=config,
                )

    def states(
        self,
        *,
        case_id: str,
        param_hash: str | None = None,
        environment: str | None = None,
        execution_profile: str | None = None,
        state_epoch: int | None = None,
    ) -> tuple[FlakyStateRecord, ...]:
        with self.repository.connection(require_existing=True) as connection:
            initialize_store(connection, self.repository, self.migrations_directory)
            return self.repository.states(
                connection,
                case_id=case_id,
                param_hash=param_hash,
                environment=environment,
                execution_profile=execution_profile,
                state_epoch=state_epoch,
            )

    def confirm_flaky(self, request: FlakyManualActionRequest) -> FlakyStateRecord:
        return self._governance_write(governance.confirm_flaky, request)

    def mark_not_flaky(self, request: FlakyManualActionRequest) -> FlakyStateRecord:
        return self._governance_write(governance.mark_not_flaky, request)

    def quarantine(self, request: FlakyQuarantineRequest) -> FlakyGovernanceRecord:
        return self._governance_write(governance.quarantine, request)

    def start_recovery(
        self,
        request: FlakyManualActionRequest,
    ) -> FlakyGovernanceRecord:
        return self._governance_write(governance.start_recovery, request)

    def cancel_quarantine(
        self,
        request: FlakyManualActionRequest,
    ) -> FlakyStateRecord:
        return self._governance_write(governance.cancel_quarantine, request)

    def governance(
        self,
        *,
        status: GovernanceStatus | None = None,
        overdue: bool = False,
        query_time: datetime | None = None,
    ) -> tuple[FlakyGovernanceRecord, ...]:
        with self.repository.connection(require_existing=True) as connection:
            initialize_store(connection, self.repository, self.migrations_directory)
            return self.repository.governance(
                connection,
                status=status,
                overdue=overdue,
                query_time=query_time,
            )

    def rebuild_states(
        self,
        *,
        apply: bool,
        config: FlakyRuleConfig = DEFAULT_FLAKY_RULE_CONFIG,
    ) -> dict[str, object]:
        with self.repository.connection(require_existing=True) as connection:
            initialization = initialize_store(
                connection, self.repository, self.migrations_directory
            )
            if apply:
                with self.repository.transaction(connection):
                    return projection.rebuild_states(
                        connection,
                        self.repository,
                        apply=True,
                        initialization=initialization,
                        config=config,
                    )
            return projection.rebuild_states(
                connection,
                self.repository,
                apply=False,
                initialization=initialization,
                config=config,
            )

    def reset_epoch(
        self,
        request: EpochResetRequest,
        *,
        epoch_scope_key: str,
    ) -> EpochResetResult:
        with self.repository.connection(require_existing=True) as connection:
            initialize_store(connection, self.repository, self.migrations_directory)
            with self.repository.transaction(connection):
                return epoch.reset_epoch(
                    connection,
                    self.repository,
                    request,
                    epoch_scope_key=epoch_scope_key,
                )

    def history(
        self,
        *,
        case_id: str,
        param_hash: str | None = None,
        environment: str | None = None,
        execution_profile: str | None = None,
        state_epoch: int | None = None,
    ) -> tuple[FlakyHistoryEntry, ...]:
        with self.repository.connection(require_existing=True) as connection:
            initialize_store(connection, self.repository, self.migrations_directory)
            return self.repository.history(
                connection,
                case_id=case_id,
                param_hash=param_hash,
                environment=environment,
                execution_profile=execution_profile,
                state_epoch=state_epoch,
            )

    def check_database(self) -> FlakyDatabaseCheck:
        with self.repository.connection(require_existing=True) as connection:
            initialization = initialize_store(
                connection, self.repository, self.migrations_directory
            )
            return self.repository.check_database(connection, initialization)

    def _governance_write(self, operation, request):
        with self.repository.connection(require_existing=True) as connection:
            initialize_store(connection, self.repository, self.migrations_directory)
            with self.repository.transaction(connection):
                return operation(connection, self.repository, request)
