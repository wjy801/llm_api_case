from __future__ import annotations

import json
import sqlite3

from quality.flaky_importer import import_flaky_history, write_flaky_v3_state_report
from quality.flaky_models import FlakyImportRequest, FlakyImportStatus
from quality.flaky_models import FlakyEvaluationStatus
from quality.flaky_store import migrate_store


def test_v3_import_audits_unattested_p0_without_fabricating_observation(
    p0_artifact_factory,
    tmp_path,
):
    artifacts = p0_artifact_factory(
        job_name="approved-smoke",
        build_number="17",
    )
    database = (tmp_path / "flaky.sqlite3").resolve()
    migrate_store(database)

    result = import_flaky_history(
        FlakyImportRequest(
            run_id=artifacts.run.run_id,
            quality_output_dir=artifacts.output_dir,
            database_path=database,
        )
    )

    assert result.status is FlakyImportStatus.NO_DATA
    assert result.database_schema_version == 4
    assert result.quick_check == "ok"
    assert result.eligible_count == 0
    assert result.excluded_count == 1
    assert result.inserted_count == 0
    assert result.excluded_reasons == {"normal_source_job_not_allowed": 1}
    assert result.issues[-1].code == "normal_source_job_not_allowed"
    assert "normal_comparability_missing" in result.issues[-1].summary

    with sqlite3.connect(database) as connection:
        imported = connection.execute(
            "SELECT eligible_count, excluded_count FROM flaky_import_run WHERE run_id = ?",
            (artifacts.run.run_id,),
        ).fetchone()
        observation_count = connection.execute(
            "SELECT COUNT(*) FROM flaky_normal_observation WHERE run_id = ?",
            (artifacts.run.run_id,),
        ).fetchone()[0]
        admission = connection.execute(
            "SELECT status, reason_codes_json FROM flaky_evidence_admission "
            "WHERE run_id = ? AND scope = 'RUN'",
            (artifacts.run.run_id,),
        ).fetchone()

    assert imported == (0, 1)
    assert observation_count == 0
    assert admission[0] == "INELIGIBLE"
    assert "normal_comparability_missing" in json.loads(admission[1])


def test_v3_import_is_idempotent_for_same_p0_artifacts(
    p0_artifact_factory,
    tmp_path,
):
    artifacts = p0_artifact_factory()
    database = (tmp_path / "flaky.sqlite3").resolve()
    migrate_store(database)
    request = FlakyImportRequest(
        run_id=artifacts.run.run_id,
        quality_output_dir=artifacts.output_dir,
        database_path=database,
    )

    first = import_flaky_history(request)
    second = import_flaky_history(request)

    assert first.status is FlakyImportStatus.NO_DATA
    assert second.status is FlakyImportStatus.NOOP
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM flaky_import_run WHERE run_id = ?",
            (artifacts.run.run_id,),
        ).fetchone()[0] == 1


def test_v3_state_report_does_not_call_the_legacy_evaluator(
    p0_artifact_factory,
    tmp_path,
):
    artifacts = p0_artifact_factory()
    database = (tmp_path / "flaky.sqlite3").resolve()
    migrate_store(database)
    imported = import_flaky_history(
        FlakyImportRequest(
            run_id=artifacts.run.run_id,
            quality_output_dir=artifacts.output_dir,
            database_path=database,
        )
    )

    result = write_flaky_v3_state_report(
        quality_output_dir=artifacts.output_dir,
        import_result=imported,
    )

    assert result.status is FlakyEvaluationStatus.NO_DATA
    assert result.database_schema_version == 4
    assert result.quick_check == "ok"
    assert result.issues[0].code == "v3_normal_evidence_not_admitted"
