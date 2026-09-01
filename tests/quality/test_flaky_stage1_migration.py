from __future__ import annotations

import multiprocessing
from pathlib import Path
import shutil
import sqlite3

import pytest

from quality.flaky_store import MIGRATIONS_DIRECTORY, FlakyStoreError, migrate_store
from quality.flaky_store import backup
from quality.flaky_store.v3_service import FlakyV3Service
from quality.flaky_store.writer_lock import database_writer_lock


def _hold_writer_lock(database: str, acquired, release) -> None:
    with database_writer_lock(Path(database), timeout_ms=1000):
        acquired.set()
        release.wait(5)


def _contend_public_write(database: str, output) -> None:
    try:
        FlakyV3Service(Path(database), busy_timeout_ms=50).quarantine(
            flaky_key="missing",
            owner="owner",
            actor="actor",
            reason="reason",
            request_id="request",
            expires_at=__import__("datetime").datetime.now(__import__("datetime").UTC)
            + __import__("datetime").timedelta(hours=1),
            now=__import__("datetime").datetime.now(__import__("datetime").UTC),
        )
    except FlakyStoreError as error:
        output.put(error.code)


def test_two_processes_share_one_writer_lock_and_timeout_has_no_side_effect(tmp_path):
    database = (tmp_path / "locked.sqlite3").resolve()
    migrate_store(database)
    context = multiprocessing.get_context("spawn")
    acquired = context.Event()
    release = context.Event()
    output = context.Queue()
    holder = context.Process(
        target=_hold_writer_lock, args=(str(database), acquired, release)
    )
    contender = context.Process(
        target=_contend_public_write, args=(str(database), output)
    )
    holder.start()
    assert acquired.wait(5)
    contender.start()
    contender.join(5)
    release.set()
    holder.join(5)

    assert contender.exitcode == 0
    assert holder.exitcode == 0
    assert output.get(timeout=1) == "db_writer_lock_timeout"
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM flaky_governance").fetchone()[0] == 0


def test_synthetic_v2_recovering_governance_migrates_to_safe_v3_state(tmp_path):
    database = (tmp_path / "v2.sqlite3").resolve()
    v2_migrations = tmp_path / "v2-migrations"
    v2_migrations.mkdir()
    for name in ("0001_observation_store.sql", "0002_flaky_state_machine.sql"):
        shutil.copy2(MIGRATIONS_DIRECTORY / name, v2_migrations / name)
    assert migrate_store(database, migrations_directory=v2_migrations).schema_version == 2
    _insert_v2_fixture(database)

    result = migrate_store(database)

    assert result.previous_schema_version == 2
    assert result.schema_version == 3
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        identity = connection.execute("SELECT * FROM flaky_identity").fetchone()
        assert identity["legacy_detected_state"] == "CONFIRMED"
        assert identity["current_detection_generation"] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM flaky_detection_projection"
        ).fetchone()[0] == 0
        governance = connection.execute("SELECT * FROM flaky_governance").fetchone()
        assert governance["status"] == "ACTIVE"
        assert governance["legacy_governance"] == 1
        event = connection.execute("SELECT event_type FROM flaky_governance_event").fetchone()
        assert event["event_type"] == "legacy_recovery_requires_new_attempt"


def test_checksum_tamper_and_too_new_schema_are_rejected(tmp_path):
    checksum_db = (tmp_path / "checksum.sqlite3").resolve()
    migrate_store(checksum_db)
    with sqlite3.connect(checksum_db) as connection:
        connection.execute(
            "UPDATE schema_migration SET checksum = 'tampered' WHERE version = 1"
        )
    with pytest.raises(FlakyStoreError) as captured:
        FlakyV3Service(checksum_db).check_invariants()
    assert captured.value.code == "migration_checksum_mismatch"

    too_new_db = (tmp_path / "too-new.sqlite3").resolve()
    migrate_store(too_new_db)
    with sqlite3.connect(too_new_db) as connection:
        connection.execute(
            """INSERT INTO schema_migration(version, name, checksum, applied_at)
               VALUES (4, '0004_future.sql', 'future', '2026-09-01T00:00:00Z')"""
        )
    with pytest.raises(FlakyStoreError) as captured:
        FlakyV3Service(too_new_db).check_invariants()
    assert captured.value.code == "schema_too_new"


def test_v1_to_v3_identity_conflict_is_preflighted_without_database_changes(tmp_path):
    database = (tmp_path / "v1-conflict.sqlite3").resolve()
    v1_migrations = tmp_path / "v1-migrations"
    v1_migrations.mkdir()
    shutil.copy2(
        MIGRATIONS_DIRECTORY / "0001_observation_store.sql",
        v1_migrations / "0001_observation_store.sql",
    )
    assert migrate_store(database, migrations_directory=v1_migrations).schema_version == 1
    database.with_name(f"{database.name}.pre-migration.bak").unlink()
    _insert_v1_identity_conflict(database)

    with pytest.raises(FlakyStoreError) as captured:
        migrate_store(database)

    assert captured.value.code == "migration_identity_conflict"
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT MAX(version) FROM schema_migration"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM case_observation"
        ).fetchone()[0] == 2
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE name = 'flaky_identity'"
        ).fetchone()[0] == 0
    assert not database.with_name(f"{database.name}.pre-migration.bak").exists()


def test_valid_v1_database_migrates_directly_to_v3(tmp_path):
    database = (tmp_path / "v1-valid.sqlite3").resolve()
    v1_migrations = tmp_path / "valid-v1-migrations"
    v1_migrations.mkdir()
    shutil.copy2(
        MIGRATIONS_DIRECTORY / "0001_observation_store.sql",
        v1_migrations / "0001_observation_store.sql",
    )
    migrate_store(database, migrations_directory=v1_migrations)
    _insert_v1_identity_conflict(database)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "DELETE FROM case_observation WHERE run_id = 'run-v1-b'"
        )
        connection.execute(
            "DELETE FROM flaky_case_epoch WHERE epoch_scope_key = 'scope-v1-b'"
        )
        connection.execute("DELETE FROM flaky_import_run WHERE run_id = 'run-v1-b'")

    result = migrate_store(database)

    assert result.previous_schema_version == 1
    assert result.schema_version == 3
    assert FlakyV3Service(database).check_invariants()["status"] == "OK"


def test_v2_orphan_governance_blocks_v3_migration_without_partial_changes(tmp_path):
    database = (tmp_path / "v2-orphan.sqlite3").resolve()
    v2_migrations = tmp_path / "orphan-v2-migrations"
    v2_migrations.mkdir()
    for name in ("0001_observation_store.sql", "0002_flaky_state_machine.sql"):
        shutil.copy2(MIGRATIONS_DIRECTORY / name, v2_migrations / name)
    migrate_store(database, migrations_directory=v2_migrations)
    _insert_v2_fixture(database)
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("DELETE FROM flaky_state")
        connection.execute("DELETE FROM case_observation")

    with pytest.raises(FlakyStoreError) as captured:
        migrate_store(database)

    assert captured.value.code == "migration_orphan_governance"
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT MAX(version) FROM schema_migration"
        ).fetchone()[0] == 2
        assert connection.execute(
            "SELECT COUNT(*) FROM flaky_governance"
        ).fetchone()[0] == 1


def test_backup_failure_leaves_schema_unchanged(tmp_path, monkeypatch):
    database = (tmp_path / "backup-failure.sqlite3").resolve()

    def fail_backup(*_args, **_kwargs):
        raise FlakyStoreError("backup_failed", "synthetic backup failure")

    monkeypatch.setattr(backup, "create_pre_migration_backup", fail_backup)
    with pytest.raises(FlakyStoreError) as captured:
        migrate_store(database)

    assert captured.value.code == "backup_failed"
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table'"
        ).fetchone()[0] == 0


def test_pre_migration_backup_can_be_restored_and_migrated(tmp_path):
    database = (tmp_path / "restore-source.sqlite3").resolve()
    v2_migrations = tmp_path / "restore-v2-migrations"
    v2_migrations.mkdir()
    for name in ("0001_observation_store.sql", "0002_flaky_state_machine.sql"):
        shutil.copy2(MIGRATIONS_DIRECTORY / name, v2_migrations / name)
    migrate_store(database, migrations_directory=v2_migrations)
    _insert_v2_fixture(database)

    migrated = migrate_store(database)
    restored = (tmp_path / "restored.sqlite3").resolve()
    assert migrated.backup_path is not None
    shutil.copy2(migrated.backup_path, restored)
    assert migrate_store(restored).schema_version == 3
    assert FlakyV3Service(restored).check_invariants()["status"] == "OK"


def _insert_v1_identity_conflict(database: Path) -> None:
    time = "2026-09-01T00:00:00.000000Z"
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        for suffix, scope, case_id in (
            ("a", "scope-v1-a", "module/test_a.py::test_case"),
            ("b", "scope-v1-b", "module/test_b.py::test_case"),
        ):
            connection.execute(
                """INSERT INTO flaky_import_run VALUES (
                    ?, ?, 'local', ?, NULL, NULL, 'dev3',
                    'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', 'overseas',
                    'finished', 'complete', ?, ?, 'quality.v1', 'quality.merge.v1',
                    'failure-fingerprint.v1', 'h1', 'h2', 'h3', 'h4', 'h5',
                    'legacy-importer', 'flaky-identity.v1', 'flaky-environment.v1',
                    'flaky-execution-profile.v1', 'flaky-observation.v1', 1, 0, ?
                )""",
                (f"run-v1-{suffix}", f"digest-v1-{suffix}", f"local:run-v1-{suffix}", time, time, time),
            )
            connection.execute(
                """INSERT INTO flaky_case_epoch VALUES (
                    ?, ?, 'overseas', 'serial', 1, 'flaky-identity.v1',
                    'flaky-environment.v1', 'flaky-execution-profile.v1', ?, ?
                )""",
                (scope, case_id, time, time),
            )
            connection.execute(
                """INSERT INTO case_observation VALUES (
                    ?, ?, ?, 'conflicting-flaky-key', ?, ?, 'param',
                    'overseas', 'serial', 1, 'call', 'passed', 'passed', 'pass',
                    NULL, NULL, ?, 'flaky-identity.v1', 'flaky-environment.v1',
                    'flaky-execution-profile.v1', 'flaky-observation.v1',
                    'failure-fingerprint.v1'
                )""",
                (
                    f"observation-v1-{suffix}",
                    f"run-v1-{suffix}",
                    f"invocation-v1-{suffix}",
                    scope,
                    case_id,
                    time,
                ),
            )


def _insert_v2_fixture(database: Path) -> None:
    time = "2026-09-01T00:00:00.000000Z"
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(
            """INSERT INTO flaky_import_run VALUES (
                'run-v2', 'digest-v2', 'local', 'local:run-v2', NULL, NULL,
                'dev3', 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', 'overseas',
                'finished', 'complete', ?, ?, 'quality.v1', 'quality.merge.v1',
                'failure-fingerprint.v1', 'h1', 'h2', 'h3', 'h4', 'h5',
                'legacy-importer', 'flaky-identity.v1', 'flaky-environment.v1',
                'flaky-execution-profile.v1', 'flaky-observation.v1', 1, 0, ?
            )""",
            (time, time, time),
        )
        connection.execute(
            """INSERT INTO flaky_case_epoch VALUES (
                'scope-v2', 'module/smoke/test_v2.py::test_case', 'overseas',
                'serial', 1, 'flaky-identity.v1', 'flaky-environment.v1',
                'flaky-execution-profile.v1', ?, ?
            )""",
            (time, time),
        )
        connection.execute(
            """INSERT INTO case_observation VALUES (
                'observation-v2', 'run-v2', 'inv-v2', 'flaky-v2-key', 'scope-v2',
                'module/smoke/test_v2.py::test_case', 'param', 'overseas', 'serial',
                1, 'call', 'failed', 'failed', 'fail', 'failure-v2',
                'PRODUCT_DEFECT', ?, 'flaky-identity.v1', 'flaky-environment.v1',
                'flaky-execution-profile.v1', 'flaky-observation.v1',
                'failure-fingerprint.v1'
            )""",
            (time,),
        )
        connection.execute(
            """INSERT INTO flaky_state VALUES (
                'flaky-v2-key', 'scope-v2', 'module/smoke/test_v2.py::test_case',
                'param', 'overseas', 'serial', 1, 'RECOVERING', 'CONFIRMED',
                NULL, NULL, 1, 1, 20, 0, 1, 0, 0, 1, 1, NULL,
                'observation-v2', 'run-v2', ?, NULL, 'flaky-state.v1',
                'flaky-projection.v1', 'CURRENT', ?, ?
            )""",
            (time, time, time),
        )
        connection.execute(
            """INSERT INTO flaky_governance VALUES (
                'governance-v2', 'flaky-v2-key', 'RECOVERING', 'owner', 'reason',
                'actor', ?, '2026-10-01T00:00:00.000000Z', 'actor', ?,
                'recover', 'observation-v2', NULL, NULL
            )""",
            (time, time),
        )
