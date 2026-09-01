from __future__ import annotations

from datetime import UTC, datetime
import hashlib
from pathlib import Path
import sqlite3
from typing import Sequence

from . import backup
from .contracts import (
    DEFAULT_BUSY_TIMEOUT_MS,
    FlakyStoreError,
    Migration,
    StoreInitialization,
    StoreMigrationResult,
)
from .repository import FlakyRepository, quick_check, utc_text
from .writer_lock import database_writer_lock


MIGRATIONS_DIRECTORY = Path(__file__).resolve().parent / "migrations"


def load_migrations(directory: Path) -> tuple[Migration, ...]:
    if not directory.is_dir():
        raise FlakyStoreError(
            "migration_directory_missing",
            "Flaky history migration directory does not exist",
        )
    migrations: list[Migration] = []
    for path in sorted(directory.glob("[0-9][0-9][0-9][0-9]_*.sql")):
        try:
            version = int(path.name.split("_", 1)[0])
        except ValueError as error:
            raise FlakyStoreError("invalid_migration_name", path.name) from error
        raw = path.read_bytes()
        try:
            sql = raw.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
        except UnicodeDecodeError as error:
            raise FlakyStoreError(
                "invalid_migration_encoding",
                f"migration {path.name!r} must be UTF-8",
            ) from error
        migrations.append(
            Migration(
                version=version,
                name=path.name,
                checksum=hashlib.sha256(sql.encode("utf-8")).hexdigest(),
                sql=sql,
            )
        )
    versions = [migration.version for migration in migrations]
    if not migrations or versions != list(range(1, len(migrations) + 1)):
        raise FlakyStoreError(
            "invalid_migration_sequence",
            "migration versions must be contiguous and start at 1",
        )
    return tuple(migrations)


def validate_applied_migrations(
    applied: dict[int, str],
    available: Sequence[Migration],
) -> None:
    available_by_version = {migration.version: migration for migration in available}
    for version, checksum in applied.items():
        migration = available_by_version.get(version)
        if migration is None:
            raise FlakyStoreError(
                "schema_too_new",
                f"database migration version {version} is not supported by this code",
            )
        if migration.checksum != checksum:
            raise FlakyStoreError(
                "migration_checksum_mismatch",
                f"migration checksum mismatch for version {version}",
            )
    if applied and sorted(applied) != list(range(1, max(applied) + 1)):
        raise FlakyStoreError(
            "migration_history_gap",
            "database migration history is not contiguous",
        )


def apply_migrations(
    connection: sqlite3.Connection,
    migrations: Sequence[Migration],
) -> None:
    statements = ["BEGIN IMMEDIATE;"]
    for migration in migrations:
        name_literal = migration.name.replace("'", "''")
        checksum_literal = migration.checksum.replace("'", "''")
        applied_at_literal = utc_text(datetime.now(UTC)).replace("'", "''")
        statements.append(
            f"{migration.sql}\n"
            "INSERT INTO schema_migration (version, name, checksum, applied_at) "
            f"VALUES ({migration.version}, '{name_literal}', "
            f"'{checksum_literal}', '{applied_at_literal}');"
        )
    statements.append("COMMIT;")
    try:
        connection.executescript("\n".join(statements))
    except sqlite3.Error as error:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        names = ", ".join(migration.name for migration in migrations)
        raise FlakyStoreError(
            "migration_failed",
            f"migration batch [{names}] failed: {error}",
        ) from error


def validate_store_schema(
    connection: sqlite3.Connection,
    repository: FlakyRepository,
    migrations_directory: Path,
) -> StoreInitialization:
    check = quick_check(connection)
    migrations = load_migrations(migrations_directory)
    applied = repository.read_applied_migrations(connection)
    validate_applied_migrations(applied, migrations)
    pending = [migration for migration in migrations if migration.version not in applied]
    if pending:
        raise FlakyStoreError(
            "schema_migration_required",
            f"database schema requires migration {pending[0].version}",
        )
    return StoreInitialization(
        schema_version=max(applied, default=0),
        quick_check=check,
        migration_applied=False,
        backup_created=False,
    )


def initialize_store(
    connection: sqlite3.Connection,
    repository: FlakyRepository,
    migrations_directory: Path,
) -> StoreInitialization:
    """Compatibility name for runtime validation; it never applies migrations."""
    return validate_store_schema(connection, repository, migrations_directory)


def migrate_store(
    database_path: str | Path,
    *,
    busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
    migrations_directory: str | Path = MIGRATIONS_DIRECTORY,
) -> StoreMigrationResult:
    path = Path(database_path)
    if not path.is_absolute():
        raise FlakyStoreError(
            "invalid_database_path",
            "Flaky history database path must be absolute",
        )
    repository = FlakyRepository(path, busy_timeout_ms=busy_timeout_ms)
    repository.validate_path(require_existing=False)
    directory = Path(migrations_directory)
    with database_writer_lock(path, timeout_ms=busy_timeout_ms):
        with repository.connection(require_existing=False) as connection:
            check = quick_check(connection)
            migrations = load_migrations(directory)
            applied = repository.read_applied_migrations(connection)
            validate_applied_migrations(applied, migrations)
            previous = max(applied, default=0)
            pending = tuple(
                migration for migration in migrations if migration.version not in applied
            )
            backup_path: Path | None = None
            if pending:
                _preflight_v3_migration(repository, connection, previous, pending)
                backup_path = backup.create_pre_migration_backup(connection, repository)
                apply_migrations(connection, pending)
                applied = repository.read_applied_migrations(connection)
                validate_applied_migrations(applied, migrations)
                check = quick_check(connection)
            return StoreMigrationResult(
                previous_schema_version=previous,
                schema_version=max(applied, default=0),
                migration_applied=bool(pending),
                backup_path=backup_path,
                quick_check=check,
                checksums=dict(sorted(applied.items())),
            )


def _preflight_v3_migration(
    repository: FlakyRepository,
    connection: sqlite3.Connection,
    previous_version: int,
    pending: Sequence[Migration],
) -> None:
    if not any(migration.version == 3 for migration in pending):
        return
    if previous_version >= 2:
        _validate_v2_for_v3(connection)
        return

    with repository.in_memory_copy(connection) as preflight:
        prerequisites = tuple(
            migration for migration in pending if migration.version < 3
        )
        apply_migrations(preflight, prerequisites)
        _validate_v2_for_v3(preflight)


def _validate_v2_for_v3(connection: sqlite3.Connection) -> None:
    identities: dict[str, tuple[object, ...]] = {}
    rows = connection.execute(
        """
        SELECT flaky_key, epoch_scope_key, case_id, param_hash,
               environment, execution_profile, state_epoch
        FROM case_observation
        UNION ALL
        SELECT flaky_key, epoch_scope_key, case_id, param_hash,
               environment, execution_profile, state_epoch
        FROM flaky_state
        """
    ).fetchall()
    for row in rows:
        key = str(row["flaky_key"])
        identity = tuple(row[index] for index in range(1, 7))
        previous = identities.setdefault(key, identity)
        if previous != identity:
            raise FlakyStoreError(
                "migration_identity_conflict",
                f"v2 flaky_key {key!r} maps to conflicting identities",
            )
    orphan = connection.execute(
        """
        SELECT governance.governance_id
        FROM flaky_governance AS governance
        LEFT JOIN flaky_state AS state ON state.flaky_key = governance.flaky_key
        LEFT JOIN case_observation AS observation
          ON observation.flaky_key = governance.flaky_key
        WHERE state.flaky_key IS NULL AND observation.flaky_key IS NULL
        LIMIT 1
        """
    ).fetchone()
    if orphan is not None:
        raise FlakyStoreError(
            "migration_orphan_governance",
            f"v2 governance {orphan['governance_id']!r} has no resolvable identity",
        )
