from __future__ import annotations

import os
from pathlib import Path
import sqlite3
import uuid

from .contracts import FlakyStoreError
from .repository import FlakyRepository, quick_check, translate_sqlite_error


def create_pre_migration_backup(
    connection: sqlite3.Connection,
    repository: FlakyRepository,
) -> Path:
    backup_path = repository.database_path.with_name(
        f"{repository.database_path.name}.pre-migration.bak"
    )
    temporary_path = backup_path.with_name(
        f".{backup_path.name}.{uuid.uuid4().hex}.tmp"
    )
    destination: sqlite3.Connection | None = None
    try:
        destination = sqlite3.connect(temporary_path)
        connection.backup(destination)
        if quick_check(destination) != "ok":
            raise FlakyStoreError(
                "backup_check_failed",
                "pre-migration backup did not pass quick_check",
            )
        destination.close()
        destination = None
        os.replace(temporary_path, backup_path)
        return backup_path
    except sqlite3.Error as error:
        raise translate_sqlite_error(error, code="backup_failed") from error
    finally:
        if destination is not None:
            destination.close()
        temporary_path.unlink(missing_ok=True)
