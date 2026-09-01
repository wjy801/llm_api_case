from datetime import UTC, datetime, timedelta
import json

import pytest

from quality.cli import main


pytestmark = pytest.mark.usefixtures("legacy_flaky_runtime")


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


def test_manual_detection_cli_requires_full_projection_identity(capsys):
    result = main(
        [
            "flaky-confirm",
            "--db",
            "D:/missing.sqlite3",
            "--flaky-key",
            "flaky-key",
            "--actor",
            "reviewer",
            "--reason",
            "trusted evidence",
        ]
    )

    assert result == 2
    assert "--detection-generation" in capsys.readouterr().err


def test_legacy_start_recovery_command_is_removed(capsys):
    result = main(["flaky-start-recovery"])

    assert result == 2
    assert "invalid choice" in capsys.readouterr().err
