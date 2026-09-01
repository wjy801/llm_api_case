from __future__ import annotations

from contextlib import contextmanager
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
    StoreMigrationResult,
)
from .migration import (
    MIGRATIONS_DIRECTORY,
    migrate_store,
    validate_store_schema,
)
from .repository import FlakyRepository
from .writer_lock import database_writer_lock
from .v3_service import (
    FlakyV3Service,
    NormalImportRequest,
    ProbeImportRequest,
    RecoveryCancelRequest,
    RecoveryCloseRequest,
    RecoveryStartRequest,
)


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
        self.v3 = FlakyV3Service(
            self.database_path,
            busy_timeout_ms=self.busy_timeout_ms,
            migrations_directory=self.migrations_directory,
        )

    def migrate(self) -> StoreMigrationResult:
        return migrate_store(
            self.database_path,
            busy_timeout_ms=self.busy_timeout_ms,
            migrations_directory=self.migrations_directory,
        )

    def import_run(
        self,
        metadata: FlakyRunMetadata,
        candidates: Sequence[CaseObservationCandidate],
    ) -> StoreImportOutcome:
        import_service.validate_import_candidates(metadata, candidates)
        with self._write_connection() as (connection, initialization):
            self._require_legacy_schema(initialization.schema_version)
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
        with self._write_connection() as (connection, initialization):
            self._require_legacy_schema(initialization.schema_version)
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
        with self.repository.connection(require_existing=True, read_only=True) as connection:
            initialization = validate_store_schema(
                connection, self.repository, self.migrations_directory
            )
            if initialization.schema_version >= 3:
                raise FlakyStoreError(
                    "legacy_projection_query_disabled",
                    "v3 current state queries must use detection projections",
                )
            return self.repository.states(
                connection,
                case_id=case_id,
                param_hash=param_hash,
                environment=environment,
                execution_profile=execution_profile,
                state_epoch=state_epoch,
            )

    def confirm_flaky(self, request: FlakyManualActionRequest) -> FlakyStateRecord:
        raise FlakyStoreError(
            "projection_identity_required",
            "detection_generation and comparability_fingerprint are required",
        )

    def mark_not_flaky(self, request: FlakyManualActionRequest) -> FlakyStateRecord:
        raise FlakyStoreError(
            "projection_identity_required",
            "detection_generation and comparability_fingerprint are required",
        )

    def quarantine(self, request: FlakyQuarantineRequest) -> FlakyGovernanceRecord:
        return self._legacy_governance_write(governance.quarantine, request)

    def start_recovery(
        self,
        request: FlakyManualActionRequest,
    ) -> FlakyGovernanceRecord:
        raise FlakyStoreError(
            "legacy_recovery_command_removed",
            "use flaky-recovery-start so a verification attempt is created",
        )

    def cancel_quarantine(
        self,
        request: FlakyManualActionRequest,
    ) -> FlakyStateRecord:
        return self._legacy_governance_write(governance.cancel_quarantine, request)

    def governance(
        self,
        *,
        status: GovernanceStatus | None = None,
        overdue: bool = False,
        query_time: datetime | None = None,
    ) -> tuple[FlakyGovernanceRecord, ...]:
        with self.repository.connection(require_existing=True, read_only=True) as connection:
            validate_store_schema(connection, self.repository, self.migrations_directory)
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
        if apply:
            with self._write_connection() as (connection, initialization):
                self._require_legacy_schema(initialization.schema_version)
                return projection.rebuild_states(
                    connection,
                    self.repository,
                    apply=True,
                    initialization=initialization,
                    config=config,
                )
        with self.repository.connection(require_existing=True, read_only=True) as connection:
            initialization = validate_store_schema(
                connection, self.repository, self.migrations_directory
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
        with self._write_connection() as (connection, _initialization):
            self._require_legacy_schema(_initialization.schema_version)
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
        with self.repository.connection(require_existing=True, read_only=True) as connection:
            validate_store_schema(connection, self.repository, self.migrations_directory)
            return self.repository.history(
                connection,
                case_id=case_id,
                param_hash=param_hash,
                environment=environment,
                execution_profile=execution_profile,
                state_epoch=state_epoch,
            )

    def check_database(self) -> FlakyDatabaseCheck:
        with self.repository.connection(require_existing=True, read_only=True) as connection:
            initialization = validate_store_schema(
                connection, self.repository, self.migrations_directory
            )
            return self.repository.check_database(connection, initialization)

    def import_normal(self, request: NormalImportRequest, *, now: datetime):
        return self.v3.import_normal(request, now=now)

    def import_probe(self, request: ProbeImportRequest, *, now: datetime):
        return self.v3.import_probe(request, now=now)

    def recovery_start(self, request: RecoveryStartRequest, *, now: datetime):
        return self.v3.recovery_start(request, now=now)

    def recovery_status(self, flaky_key: str):
        return self.v3.recovery_status(flaky_key)

    def recovery_close(self, request: RecoveryCloseRequest, *, now: datetime):
        return self.v3.recovery_close(request, now=now)

    def recovery_cancel(self, request: RecoveryCancelRequest, *, now: datetime):
        return self.v3.recovery_cancel(request, now=now)

    def _legacy_governance_write(self, operation, request):
        with self._write_connection() as (connection, _initialization):
            self._require_legacy_schema(_initialization.schema_version)
            return operation(connection, self.repository, request)

    @staticmethod
    def _require_legacy_schema(schema_version: int) -> None:
        if schema_version >= 3:
            raise FlakyStoreError(
                "legacy_write_disabled",
                "v3 keeps legacy detection tables read-only",
            )

    @contextmanager
    def _write_connection(self):
        if not self.database_path.is_file():
            raise FlakyStoreError(
                "schema_migration_required",
                "run flaky-db-migrate before using the Flaky store",
            )
        with database_writer_lock(
            self.database_path,
            timeout_ms=self.busy_timeout_ms,
        ):
            with self.repository.connection(require_existing=True) as connection:
                initialization = validate_store_schema(
                    connection, self.repository, self.migrations_directory
                )
                with self.repository.transaction(connection):
                    yield connection, initialization
