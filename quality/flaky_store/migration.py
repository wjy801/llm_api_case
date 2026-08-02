from __future__ import annotations

from datetime import UTC, datetime
import hashlib
from pathlib import Path
import sqlite3
from typing import Sequence

from . import backup
from .contracts import FlakyStoreError, Migration, StoreInitialization
from .repository import FlakyRepository, quick_check, utc_text


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


def initialize_store(
    connection: sqlite3.Connection,
    repository: FlakyRepository,
    migrations_directory: Path,
) -> StoreInitialization:
    check = quick_check(connection)
    migrations = load_migrations(migrations_directory)
    applied = repository.read_applied_migrations(connection)
    validate_applied_migrations(applied, migrations)
    pending = [migration for migration in migrations if migration.version not in applied]
    backup_created = False
    if pending:
        backup.create_pre_migration_backup(connection, repository)
        backup_created = True
        apply_migrations(connection, pending)
        applied = repository.read_applied_migrations(connection)
        validate_applied_migrations(applied, migrations)
        check = quick_check(connection)
    return StoreInitialization(
        schema_version=max(applied, default=0),
        quick_check=check,
        migration_applied=bool(pending),
        backup_created=backup_created,
    )
