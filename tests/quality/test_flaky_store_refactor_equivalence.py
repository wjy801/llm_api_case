from __future__ import annotations

import json
from pathlib import Path
import sqlite3

import pytest

pytestmark = pytest.mark.usefixtures("legacy_flaky_runtime")

from quality.flaky_importer import prepare_flaky_import
from quality.flaky_models import FlakyImportRequest
from quality.flaky_store import FlakyStore


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "flaky_store_refactor"
DYNAMIC_COLUMNS = {
    "schema_migration": {"applied_at"},
    "flaky_import_run": {"imported_at"},
    "flaky_case_epoch": {"created_at", "updated_at"},
    "flaky_state": {"created_at", "updated_at"},
    "flaky_transition": {"created_at"},
}


def _snapshot(path: Path) -> dict:
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        objects = [
            dict(row)
            for row in connection.execute(
                """
                SELECT type, name, tbl_name, sql
                FROM sqlite_master
                WHERE name NOT LIKE 'sqlite_%'
                ORDER BY type, name
                """
            ).fetchall()
        ]
        tables = [item["name"] for item in objects if item["type"] == "table"]
        data = {}
        for table in tables:
            rows = [
                dict(row)
                for row in connection.execute(f'SELECT * FROM "{table}"').fetchall()
            ]
            for row in rows:
                for name in DYNAMIC_COLUMNS.get(table, set()):
                    if name in row:
                        row[name] = "<dynamic>"
                if table == "flaky_import_run":
                    row["artifact_ref"] = "<artifact_ref>"
            data[table] = sorted(
                rows,
                key=lambda item: json.dumps(item, sort_keys=True, default=str),
            )
        return {"objects": objects, "data": data}


def _prepare(factory, database, run_id, outcome):
    artifacts = factory(run_id=run_id, outcome=outcome)
    return prepare_flaky_import(
        FlakyImportRequest(
            run_id=run_id,
            quality_output_dir=artifacts.output_dir,
            database_path=database,
        )
    )


def test_two_run_database_matches_the_frozen_pre_refactor_snapshot(
    p0_artifact_factory, tmp_path
):
    database = tmp_path / "history.sqlite3"
    store = FlakyStore(database)
    evaluations = []

    for run_id, outcome in (("run-1", "pass"), ("run-2", "fail")):
        prepared = _prepare(p0_artifact_factory, database, run_id, outcome)
        imported = store.import_run(prepared.metadata, prepared.candidates)
        assert imported.imported is True
        evaluations.append(store.evaluate_run(run_id))

    expected = json.loads(
        (FIXTURE_DIR / "expected-two-run-snapshot.json").read_text(encoding="utf-8")
    )
    actual = _snapshot(database)
    assert actual["objects"] == expected["objects"]
    for table, rows in expected["data"].items():
        if table != "flaky_import_run":
            assert actual["data"][table] == rows
    assert [row["run_id"] for row in actual["data"]["flaky_import_run"]] == [
        "run-1",
        "run-2",
    ]
    assert [item.transitioned_count for item in evaluations] == [1, 1]
    assert store.states(case_id="module/test_demo.py::test_case")[0].sample_size == 2
    assert len(store.history(case_id="module/test_demo.py::test_case")) == 2
    assert store.check_database().quick_check == "ok"
