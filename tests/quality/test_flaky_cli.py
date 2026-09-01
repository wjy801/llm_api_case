import json

import pytest

from quality.cli import main


pytestmark = pytest.mark.usefixtures("legacy_flaky_runtime")


def test_flaky_cli_import_history_reset_and_db_check(
    p0_artifact_factory,
    tmp_path,
    capsys,
):
    artifacts = p0_artifact_factory()
    database = tmp_path / "history.sqlite3"

    assert main(
        [
            "flaky-import",
            "--run-id",
            "run-1",
            "--output-dir",
            str(artifacts.output_dir),
            "--db",
            str(database),
        ]
    ) == 0
    imported = json.loads(capsys.readouterr().out)
    assert imported["status"] == "IMPORTED"
    assert imported["inserted_count"] == 1
    assert "<local-path>" in imported["artifact_ref"]

    assert main(
        [
            "flaky-history",
            "--db",
            str(database),
            "--case-id",
            "module/test_demo.py::test_case",
            "--environment",
            "overseas",
        ]
    ) == 0
    history = json.loads(capsys.readouterr().out)
    assert history["count"] == 1
    assert history["observations"][0]["observation_outcome"] == "pass"

    assert main(
        [
            "flaky-reset-epoch",
            "--db",
            str(database),
            "--case-id",
            "module/test_demo.py::test_case",
            "--environment",
            "overseas",
            "--execution-profile",
            "serial",
            "--actor",
            "owner",
            "--reason",
            "assertion changed",
        ]
    ) == 0
    reset = json.loads(capsys.readouterr().out)
    assert reset["previous_epoch"] == 1
    assert reset["new_epoch"] == 2



def test_flaky_cli_explicit_migrate_and_read_only_check(tmp_path, capsys):
    database = (tmp_path / "v3.sqlite3").resolve()
    assert main(["flaky-db-migrate", "--db", str(database)]) == 0
    migrated = json.loads(capsys.readouterr().out)
    assert migrated["database_schema_version"] == 3
    assert migrated["migration_applied"] is True

    assert main(["flaky-db-check", "--db", str(database)]) == 0
    checked = json.loads(capsys.readouterr().out)
    assert checked["database_schema_version"] == 3
    assert checked["status"] == "OK"


def test_flaky_cli_returns_two_for_untrusted_artifact(
    p0_artifact_factory,
    tmp_path,
    capsys,
):
    artifacts = p0_artifact_factory()
    with (artifacts.merged / "failures.jsonl").open("a", encoding="utf-8") as handle:
        handle.write("{}\n")

    result = main(
        [
            "flaky-import",
            "--run-id",
            "run-1",
            "--output-dir",
            str(artifacts.output_dir),
            "--db",
            str(tmp_path / "history.sqlite3"),
        ]
    )

    assert result == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "FAILED"
    assert payload["issues"][0]["code"] == "artifact_hash_mismatch"


def test_flaky_cli_rejects_relative_database_path(p0_artifact_factory, capsys):
    artifacts = p0_artifact_factory()

    result = main(
        [
            "flaky-import",
            "--run-id",
            "run-1",
            "--output-dir",
            str(artifacts.output_dir),
            "--db",
            "relative.sqlite3",
        ]
    )

    assert result == 2
    assert "absolute" in capsys.readouterr().err


def test_flaky_history_query_does_not_expose_exception_or_request_payload(
    p0_artifact_factory,
    tmp_path,
    capsys,
):
    artifacts = p0_artifact_factory(outcome="fail")
    database = tmp_path / "history.sqlite3"
    assert main(
        [
            "flaky-import",
            "--run-id",
            "run-1",
            "--output-dir",
            str(artifacts.output_dir),
            "--db",
            str(database),
        ]
    ) == 0
    capsys.readouterr()

    assert main(
        [
            "flaky-history",
            "--db",
            str(database),
            "--case-id",
            "module/test_demo.py::test_case",
        ]
    ) == 0
    output = capsys.readouterr().out

    assert "normalized_message" not in output
    assert "request_body" not in output
    assert "response_body" not in output
    assert "fail-call-assertionerror-demo" in output
