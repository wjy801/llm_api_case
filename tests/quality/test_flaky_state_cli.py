from datetime import UTC, datetime, timedelta
import json

from quality.cli import main


def _import(factory, database, run_id, outcome):
    artifacts = factory(run_id=run_id, outcome=outcome)
    assert main(
        [
            "flaky-import",
            "--run-id",
            run_id,
            "--output-dir",
            str(artifacts.output_dir),
            "--db",
            str(database),
        ]
    ) == 0
    return artifacts


def test_state_evaluate_query_and_rebuild_cli(
    p0_artifact_factory,
    tmp_path,
    capsys,
):
    database = tmp_path / "history.sqlite3"
    artifacts = _import(p0_artifact_factory, database, "run-1", "pass")
    capsys.readouterr()

    assert main(
        [
            "flaky-state-evaluate",
            "--db",
            str(database),
            "--run-id",
            "run-1",
            "--output-dir",
            str(artifacts.output_dir),
        ]
    ) == 0
    evaluated = json.loads(capsys.readouterr().out)
    assert evaluated["status"] == "EVALUATED"
    assert evaluated["transitioned_count"] == 1
    assert evaluated["transitions"][0]["reason_code"] == "first_observation"
    assert evaluated["transitions"][0]["evidence_observation_ids"]
    assert (artifacts.output_dir / "flaky-evaluation.json").is_file()

    assert main(
        [
            "flaky-state",
            "--db",
            str(database),
            "--case-id",
            "module/test_demo.py::test_case",
        ]
    ) == 0
    queried = json.loads(capsys.readouterr().out)
    assert queried["count"] == 1
    assert queried["states"][0]["current_state"] == "OBSERVING"

    assert main(
        ["flaky-state-rebuild", "--db", str(database), "--dry-run"]
    ) == 0
    rebuilt = json.loads(capsys.readouterr().out)
    assert rebuilt["mode"] == "dry-run"
    assert rebuilt["changed_count"] == 0


def test_manual_governance_cli_requires_valid_state_and_audit_fields(
    p0_artifact_factory,
    tmp_path,
    capsys,
):
    database = tmp_path / "history.sqlite3"
    first = _import(p0_artifact_factory, database, "run-1", "pass")
    capsys.readouterr()
    assert main(
        [
            "flaky-state-evaluate",
            "--db",
            str(database),
            "--run-id",
            "run-1",
            "--output-dir",
            str(first.output_dir),
        ]
    ) == 0
    capsys.readouterr()
    second = _import(p0_artifact_factory, database, "run-2", "fail")
    capsys.readouterr()
    assert main(
        [
            "flaky-state-evaluate",
            "--db",
            str(database),
            "--run-id",
            "run-2",
            "--output-dir",
            str(second.output_dir),
        ]
    ) == 0
    evaluated = json.loads(capsys.readouterr().out)
    flaky_key = evaluated["newly_suspected"][0]["flaky_key"]

    assert main(
        [
            "flaky-confirm",
            "--db",
            str(database),
            "--flaky-key",
            flaky_key,
            "--actor",
            "reviewer",
            "--reason",
            "trusted pass/fail evidence",
        ]
    ) == 0
    confirmed = json.loads(capsys.readouterr().out)
    assert confirmed["current_state"] == "CONFIRMED"

    expiry = (datetime.now(UTC) + timedelta(days=2)).isoformat()
    assert main(
        [
            "flaky-quarantine",
            "--db",
            str(database),
            "--flaky-key",
            flaky_key,
            "--owner",
            "case-owner",
            "--actor",
            "reviewer",
            "--reason",
            "active investigation",
            "--expires-at",
            expiry,
        ]
    ) == 0
    governance = json.loads(capsys.readouterr().out)
    assert governance["status"] == "ACTIVE"

    assert main(
        [
            "flaky-governance-list",
            "--db",
            str(database),
            "--status",
            "ACTIVE",
        ]
    ) == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed["count"] == 1
    assert listed["governance"][0]["owner"] == "case-owner"
