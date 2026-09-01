from __future__ import annotations

import sqlite3

import pytest

pytestmark = pytest.mark.usefixtures("legacy_flaky_runtime")

from quality.flaky_importer import prepare_flaky_import
from quality.flaky_models import FlakyImportRequest, FlakyManualActionRequest
from quality.flaky_store import FlakyStore
from quality.flaky_store import repository as repository_module


def _prepare(factory, database, run_id="run-1", outcome="pass"):
    artifacts = factory(run_id=run_id, outcome=outcome)
    return prepare_flaky_import(
        FlakyImportRequest(
            run_id=run_id,
            quality_output_dir=artifacts.output_dir,
            database_path=database,
        )
    )


def _counts(database):
    with sqlite3.connect(database) as connection:
        return {
            table: connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            for table in (
                "flaky_import_run",
                "flaky_case_epoch",
                "case_observation",
                "flaky_state",
                "flaky_transition",
                "flaky_override",
            )
        }


def test_import_failure_rolls_back_run_observation_and_new_epoch(
    p0_artifact_factory, tmp_path, monkeypatch
):
    database = tmp_path / "history.sqlite3"
    prepared = _prepare(p0_artifact_factory, database)
    store = FlakyStore(database)

    monkeypatch.setattr(
        store.repository,
        "insert_observation",
        lambda connection, observation: (_ for _ in ()).throw(
            RuntimeError("injected observation failure")
        ),
    )

    with pytest.raises(RuntimeError, match="injected observation failure"):
        store.import_run(prepared.metadata, prepared.candidates)

    counts = _counts(database)
    assert counts["flaky_import_run"] == 0
    assert counts["flaky_case_epoch"] == 0
    assert counts["case_observation"] == 0


def test_projection_failure_rolls_back_state_and_transition_together(
    p0_artifact_factory, tmp_path, monkeypatch
):
    database = tmp_path / "history.sqlite3"
    prepared = _prepare(p0_artifact_factory, database)
    store = FlakyStore(database)
    store.import_run(prepared.metadata, prepared.candidates)

    monkeypatch.setattr(
        store.repository,
        "insert_transition",
        lambda connection, transition: (_ for _ in ()).throw(
            RuntimeError("injected transition failure")
        ),
    )

    with pytest.raises(RuntimeError, match="injected transition failure"):
        store.evaluate_run("run-1")

    counts = _counts(database)
    assert counts["flaky_state"] == 0
    assert counts["flaky_transition"] == 0


def test_governance_failure_rolls_back_transition_and_override(
    p0_artifact_factory, tmp_path, monkeypatch
):
    database = tmp_path / "history.sqlite3"
    store = FlakyStore(database)
    for run_id, outcome in (("run-1", "pass"), ("run-2", "fail")):
        prepared = _prepare(p0_artifact_factory, database, run_id, outcome)
        store.import_run(prepared.metadata, prepared.candidates)
        store.evaluate_run(run_id)
    state = store.states(case_id="module/test_demo.py::test_case")[0]
    before = _counts(database)

    monkeypatch.setattr(
        store.repository,
        "insert_override",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("injected override failure")
        ),
    )

    with pytest.raises(RuntimeError, match="injected override failure"):
        store.confirm_flaky(
            FlakyManualActionRequest(
                flaky_key=state.flaky_key,
                actor="reviewer",
                reason="fault injection",
            )
        )

    after = _counts(database)
    assert after["flaky_transition"] == before["flaky_transition"]
    assert after["flaky_override"] == before["flaky_override"]
    assert (
        store.states(case_id="module/test_demo.py::test_case")[0].current_state.value
        == "SUSPECTED"
    )


def test_one_public_write_uses_one_main_connection(
    p0_artifact_factory, tmp_path, monkeypatch
):
    database = tmp_path / "history.sqlite3"
    prepared = _prepare(p0_artifact_factory, database)
    store = FlakyStore(database)
    original_connect = sqlite3.connect
    main_connections = 0

    def counting_connect(path, *args, **kwargs):
        nonlocal main_connections
        if path == database:
            main_connections += 1
        return original_connect(path, *args, **kwargs)

    monkeypatch.setattr(repository_module.sqlite3, "connect", counting_connect)

    store.import_run(prepared.metadata, prepared.candidates)

    assert main_connections == 1
