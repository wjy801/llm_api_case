import sqlite3

import pytest

pytestmark = pytest.mark.usefixtures("legacy_flaky_runtime")

from quality.flaky_importer import (
    build_epoch_scope_key,
    check_flaky_database,
    import_flaky_history,
    prepare_flaky_import,
    query_flaky_history,
    reset_flaky_epoch,
)
from quality.flaky_models import EpochResetRequest, FlakyImportRequest, FlakyImportStatus
from quality.flaky_store import MIGRATIONS_DIRECTORY, FlakyStore, FlakyStoreError
from quality.storage import write_json_atomic


def _request(artifacts, database):
    return FlakyImportRequest(
        run_id=artifacts.run.run_id,
        quality_output_dir=artifacts.output_dir,
        database_path=database,
    )


def test_explicitly_migrated_legacy_fixture_imports_without_runtime_migration(
    p0_artifact_factory,
    tmp_path,
):
    artifacts = p0_artifact_factory()
    database = tmp_path / "history.sqlite3"

    result = import_flaky_history(_request(artifacts, database))
    check = check_flaky_database(database)

    assert result.status is FlakyImportStatus.IMPORTED
    assert result.inserted_count == 1
    assert result.migration_applied is False
    assert result.backup_created is False
    assert check.schema_version == 2
    assert check.quick_check == "ok"
    assert check.run_count == 1
    assert check.observation_count == 1
    backup = database.with_name(f"{database.name}.pre-migration.bak")
    assert backup.is_file()
    with sqlite3.connect(backup) as connection:
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"


def test_schema_contains_history_and_iteration_three_state_tables(
    p0_artifact_factory,
    tmp_path,
):
    artifacts = p0_artifact_factory()
    database = tmp_path / "history.sqlite3"
    import_flaky_history(_request(artifacts, database))

    with sqlite3.connect(database) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }

    assert {
        "schema_migration",
        "flaky_import_run",
        "flaky_case_epoch",
        "case_observation",
        "flaky_override",
    } <= tables
    assert {"flaky_state", "flaky_transition", "flaky_governance"} <= tables


def test_same_source_reimport_is_noop_and_counts_do_not_change(
    p0_artifact_factory,
    tmp_path,
):
    artifacts = p0_artifact_factory()
    database = tmp_path / "history.sqlite3"

    first = import_flaky_history(_request(artifacts, database))
    second = import_flaky_history(_request(artifacts, database))
    check = check_flaky_database(database)

    assert first.status is FlakyImportStatus.IMPORTED
    assert second.status is FlakyImportStatus.NOOP
    assert second.inserted_count == 0
    assert check.run_count == 1
    assert check.observation_count == 1


def test_importer_version_change_does_not_turn_same_source_into_conflict(
    p0_artifact_factory,
    tmp_path,
):
    artifacts = p0_artifact_factory()
    database = tmp_path / "history.sqlite3"
    first_request = _request(artifacts, database)
    second_request = first_request.model_copy(update={"importer_version": "importer-v2"})

    first = import_flaky_history(first_request)
    second = import_flaky_history(second_request)

    assert first.status is FlakyImportStatus.IMPORTED
    assert second.status is FlakyImportStatus.NOOP


def test_same_run_with_changed_source_digest_is_rejected_without_overwrite(
    p0_artifact_factory,
    tmp_path,
):
    artifacts = p0_artifact_factory()
    database = tmp_path / "history.sqlite3"
    first = import_flaky_history(_request(artifacts, database))
    changed_run = artifacts.run.model_copy(update={"branch": "changed-branch"})
    write_json_atomic(artifacts.output_dir / "run.json", changed_run)

    second = import_flaky_history(_request(artifacts, database))
    check = check_flaky_database(database)

    assert first.status is FlakyImportStatus.IMPORTED
    assert second.status is FlakyImportStatus.FAILED
    assert second.issues[0].code == "run_source_conflict"
    assert check.run_count == 1
    assert check.observation_count == 1


def test_observation_insert_failure_rolls_back_entire_run_and_epoch_scope(
    p0_artifact_factory,
    tmp_path,
):
    artifacts = p0_artifact_factory()
    database = tmp_path / "history.sqlite3"
    prepared = prepare_flaky_import(_request(artifacts, database))
    duplicate = prepared.candidates[0].model_copy(update={"invocation_id": "inv-other"})
    metadata = prepared.metadata.model_copy(
        update={"eligible_count": 2}
    )

    with pytest.raises(FlakyStoreError):
        FlakyStore(database).import_run(metadata, (*prepared.candidates, duplicate))

    check = check_flaky_database(database)
    assert check.run_count == 0
    assert check.observation_count == 0
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM flaky_case_epoch").fetchone()[0] == 0


def test_migration_checksum_mismatch_refuses_database(
    p0_artifact_factory,
    tmp_path,
):
    artifacts = p0_artifact_factory()
    database = tmp_path / "history.sqlite3"
    import_flaky_history(_request(artifacts, database))
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE schema_migration SET checksum = 'changed' WHERE version = 1")
        connection.commit()

    with pytest.raises(FlakyStoreError) as captured:
        check_flaky_database(database)

    assert captured.value.code == "migration_checksum_mismatch"


def test_migration_batch_failure_rolls_back_all_pending_schema_changes(
    p0_artifact_factory,
    tmp_path,
):
    artifacts = p0_artifact_factory()
    database = tmp_path / "history.sqlite3"
    prepared = prepare_flaky_import(_request(artifacts, database))
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    (migrations / "0001_observation_store.sql").write_text(
        (MIGRATIONS_DIRECTORY / "0001_observation_store.sql").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (migrations / "0002_broken.sql").write_text(
        "CREATE TABLE broken (id INTEGER PRIMARY KEY);\nTHIS IS NOT SQL;\n",
        encoding="utf-8",
    )

    with pytest.raises(FlakyStoreError) as captured:
        FlakyStore(database, migrations_directory=migrations).import_run(
            prepared.metadata,
            prepared.candidates,
        )

    assert captured.value.code == "migration_failed"
    with sqlite3.connect(database) as connection:
        tables = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    assert tables == []
    backup = database.with_name(f"{database.name}.pre-migration.bak")
    assert backup.is_file()
    with sqlite3.connect(backup) as connection:
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"


def test_v2_migration_preserves_v1_reset_history_and_observations(
    p0_artifact_factory,
    tmp_path,
):
    artifacts = p0_artifact_factory()
    database = tmp_path / "history.sqlite3"
    migrations = tmp_path / "v1-migrations"
    migrations.mkdir()
    (migrations / "0001_observation_store.sql").write_text(
        (MIGRATIONS_DIRECTORY / "0001_observation_store.sql").read_text(
            encoding="utf-8"
        ),
        encoding="utf-8",
    )
    prepared = prepare_flaky_import(_request(artifacts, database))
    v1_store = FlakyStore(database, migrations_directory=migrations)
    v1_store.import_run(prepared.metadata, prepared.candidates)
    reset_request = EpochResetRequest(
        case_id="module/test_demo.py::test_case",
        environment="overseas",
        execution_profile="serial",
        actor="owner",
        reason="assertion semantics changed",
    )
    v1_store.reset_epoch(
        reset_request,
        epoch_scope_key=build_epoch_scope_key(
            reset_request.case_id,
            reset_request.environment,
            reset_request.execution_profile,
        ),
    )

    check = check_flaky_database(database)

    assert check.schema_version == 2
    assert check.observation_count == 1
    with sqlite3.connect(database) as connection:
        override = connection.execute(
            """
            SELECT action, previous_epoch, new_epoch, actor, reason,
                   flaky_key, from_state, to_state
            FROM flaky_override
            """
        ).fetchone()
    assert override == (
        "reset_epoch",
        1,
        2,
        "owner",
        "assertion semantics changed",
        None,
        None,
        None,
    )


def test_epoch_reset_preserves_old_history_and_new_run_uses_new_epoch(
    p0_artifact_factory,
    tmp_path,
):
    database = tmp_path / "history.sqlite3"
    first_artifacts = p0_artifact_factory(run_id="run-1")
    second_artifacts = p0_artifact_factory(run_id="run-2")
    assert import_flaky_history(_request(first_artifacts, database)).inserted_count == 1

    reset = reset_flaky_epoch(
        database,
        EpochResetRequest(
            case_id="module/test_demo.py::test_case",
            environment="overseas",
            execution_profile="serial",
            actor="owner",
            reason="assertion semantics changed",
        ),
    )
    assert import_flaky_history(_request(second_artifacts, database)).inserted_count == 1
    history = query_flaky_history(
        database,
        case_id="module/test_demo.py::test_case",
    )

    assert reset.previous_epoch == 1
    assert reset.new_epoch == 2
    assert [entry.state_epoch for entry in history] == [1, 2]
    assert history[0].flaky_key != history[1].flaky_key
    with sqlite3.connect(database) as connection:
        audit = connection.execute(
            "SELECT actor, reason, previous_epoch, new_epoch FROM flaky_override"
        ).fetchone()
    assert audit == ("owner", "assertion semantics changed", 1, 2)


def test_incompatible_fingerprint_version_requires_explicit_epoch_reset(
    p0_artifact_factory,
    tmp_path,
):
    database = tmp_path / "history.sqlite3"
    first_artifacts = p0_artifact_factory(run_id="run-1")
    second_artifacts = p0_artifact_factory(run_id="run-2")
    changed_manifest = dict(second_artifacts.manifest)
    changed_manifest["fingerprint_version"] = "failure-fingerprint.v2"
    write_json_atomic(second_artifacts.merged / "manifest.json", changed_manifest)
    assert import_flaky_history(_request(first_artifacts, database)).status is FlakyImportStatus.IMPORTED

    rejected = import_flaky_history(_request(second_artifacts, database))

    assert rejected.status is FlakyImportStatus.FAILED
    assert rejected.issues[0].code == "epoch_rule_version_conflict"
    reset_flaky_epoch(
        database,
        EpochResetRequest(
            case_id="module/test_demo.py::test_case",
            environment="overseas",
            execution_profile="serial",
            actor="owner",
            reason="fingerprint semantics changed",
        ),
    )
    imported = import_flaky_history(_request(second_artifacts, database))
    assert imported.status is FlakyImportStatus.IMPORTED
    assert query_flaky_history(
        database,
        case_id="module/test_demo.py::test_case",
        state_epoch=2,
    )[0].fingerprint_version == "failure-fingerprint.v2"


def test_unknown_epoch_scope_fails_without_creating_placeholder(
    p0_artifact_factory,
    tmp_path,
):
    artifacts = p0_artifact_factory()
    database = tmp_path / "history.sqlite3"
    import_flaky_history(_request(artifacts, database))

    with pytest.raises(FlakyStoreError) as captured:
        reset_flaky_epoch(
            database,
            EpochResetRequest(
                case_id="missing-case",
                environment="overseas",
                execution_profile="serial",
                actor="owner",
                reason="changed",
            ),
        )

    assert captured.value.code == "epoch_scope_not_found"
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM flaky_case_epoch").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM flaky_override").fetchone()[0] == 0


def test_busy_database_fails_safely_without_partial_second_run(
    p0_artifact_factory,
    tmp_path,
):
    database = tmp_path / "history.sqlite3"
    first = p0_artifact_factory(run_id="run-1")
    second = p0_artifact_factory(run_id="run-2")
    import_flaky_history(_request(first, database))
    prepared = prepare_flaky_import(_request(second, database))

    locker = sqlite3.connect(database, isolation_level=None)
    locker.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(FlakyStoreError) as captured:
            FlakyStore(database, busy_timeout_ms=10).import_run(
                prepared.metadata,
                prepared.candidates,
            )
    finally:
        locker.execute("ROLLBACK")
        locker.close()

    assert captured.value.code == "db_busy"
    check = check_flaky_database(database)
    assert check.run_count == 1
    assert check.observation_count == 1


def test_corrupted_database_is_not_replaced(tmp_path):
    database = tmp_path / "history.sqlite3"
    database.write_bytes(b"not-a-sqlite-database")

    with pytest.raises(FlakyStoreError) as captured:
        check_flaky_database(database)

    assert captured.value.code == "database_corrupted"
    assert database.read_bytes() == b"not-a-sqlite-database"
