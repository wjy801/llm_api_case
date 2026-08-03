from datetime import UTC, datetime

from quality.config import QualityRuntimeConfig
from quality.flaky_models import (
    FlakyEvaluationResult,
    FlakyEvaluationStatus,
    FlakyImportResult,
    FlakyImportStatus,
)
from quality.models import IntegrityStatus, RunStatus
from run_orchestration import (
    quality_fact_merge_stage,
    quality_flaky_stage,
    quality_pipeline,
    quality_run_record,
    quality_semantic_stage,
)


def _config(tmp_path, **updates):
    values = {
        "enabled": True,
        "run_id": "run-1",
        "execution_id": None,
        "output_dir": tmp_path / "quality",
        "semantic_enabled": True,
        "flaky_history_enabled": True,
        "flaky_database_path": tmp_path / "history.sqlite3",
    }
    values.update(updates)
    return QualityRuntimeConfig(**values)


def _stub_fact_finalize(monkeypatch, events):
    monkeypatch.setattr(
        quality_fact_merge_stage,
        "merge_quality_run",
        lambda request: type(
            "MergeResult",
            (),
            {
                "integrity_status": IntegrityStatus.COMPLETE,
                "integrity_issues": (),
            },
        )(),
    )
    monkeypatch.setattr(
        quality_run_record,
        "write_json_atomic",
        lambda path, value: events.append("run-record"),
    )


def test_flaky_import_runs_after_semantic_merge_and_is_independent(
    monkeypatch,
    tmp_path,
):
    events = []
    _stub_fact_finalize(monkeypatch, events)
    monkeypatch.setattr(
        quality_semantic_stage,
        "merge_semantic_run",
        lambda request: events.append("semantic"),
    )
    monkeypatch.setattr(
        quality_flaky_stage,
        "import_flaky_history",
        lambda request: events.append("flaky")
        or FlakyImportResult(
            run_id="run-1",
            status=FlakyImportStatus.IMPORTED,
            inserted_count=1,
        ),
    )

    quality_pipeline.finalize_quality_run(
        _config(tmp_path),
        start_time=datetime(2026, 8, 1, tzinfo=UTC),
        expected_execution_ids=("serial-pool",),
        expected_case_count=1,
        junit_files=(),
        status=RunStatus.FINISHED,
    )

    assert events == ["run-record", "semantic", "flaky"]


def test_semantic_failure_does_not_block_flaky_import(monkeypatch, tmp_path):
    events = []
    _stub_fact_finalize(monkeypatch, events)
    monkeypatch.setattr(
        quality_semantic_stage,
        "merge_semantic_run",
        lambda request: (_ for _ in ()).throw(OSError("semantic unavailable")),
    )
    monkeypatch.setattr(
        quality_flaky_stage,
        "import_flaky_history",
        lambda request: events.append("flaky")
        or FlakyImportResult(run_id="run-1", status=FlakyImportStatus.NOOP),
    )

    quality_pipeline.finalize_quality_run(
        _config(tmp_path),
        start_time=datetime(2026, 8, 1, tzinfo=UTC),
        expected_execution_ids=("serial-pool",),
        expected_case_count=1,
        junit_files=(),
        status=RunStatus.FINISHED,
    )

    assert events[-1] == "flaky"


def test_flaky_import_exception_is_fail_open(monkeypatch, tmp_path, capsys):
    events = []
    _stub_fact_finalize(monkeypatch, events)
    monkeypatch.setattr(quality_semantic_stage, "merge_semantic_run", lambda request: None)
    monkeypatch.setattr(
        quality_flaky_stage,
        "import_flaky_history",
        lambda request: (_ for _ in ()).throw(OSError("database unavailable")),
    )

    quality_pipeline.finalize_quality_run(
        _config(tmp_path),
        start_time=datetime(2026, 8, 1, tzinfo=UTC),
        expected_execution_ids=("serial-pool",),
        expected_case_count=1,
        junit_files=(),
        status=RunStatus.FINISHED,
    )

    assert "Quality Flaky history import failed open" in capsys.readouterr().out


def test_interrupted_run_writes_no_data_report_and_does_not_import(
    monkeypatch,
    tmp_path,
):
    events = []
    _stub_fact_finalize(monkeypatch, events)
    monkeypatch.setattr(quality_semantic_stage, "merge_semantic_run", lambda request: None)
    monkeypatch.setattr(
        quality_flaky_stage,
        "import_flaky_history",
        lambda request: (_ for _ in ()).throw(AssertionError("must not import")),
    )
    monkeypatch.setattr(
        quality_flaky_stage,
        "write_flaky_no_data_report",
        lambda **kwargs: events.append(kwargs["code"])
        or FlakyImportResult(run_id="run-1", status=FlakyImportStatus.NO_DATA),
    )

    quality_pipeline.finalize_quality_run(
        _config(tmp_path),
        start_time=datetime(2026, 8, 1, tzinfo=UTC),
        expected_execution_ids=("serial-pool",),
        expected_case_count=1,
        junit_files=(),
        status=RunStatus.INTERRUPTED,
    )

    assert "run_not_finished" in events


def test_enabled_without_valid_path_is_no_data_not_fallback_database(
    monkeypatch,
    tmp_path,
):
    events = []
    _stub_fact_finalize(monkeypatch, events)
    monkeypatch.setattr(quality_semantic_stage, "merge_semantic_run", lambda request: None)
    monkeypatch.setattr(
        quality_flaky_stage,
        "import_flaky_history",
        lambda request: (_ for _ in ()).throw(AssertionError("must not import")),
    )
    monkeypatch.setattr(
        quality_flaky_stage,
        "write_flaky_no_data_report",
        lambda **kwargs: events.append(kwargs["code"])
        or FlakyImportResult(run_id="run-1", status=FlakyImportStatus.NO_DATA),
    )

    quality_pipeline.finalize_quality_run(
        _config(
            tmp_path,
            flaky_database_path=None,
            flaky_history_warning="QUALITY_FLAKY_DB_PATH is required",
        ),
        start_time=datetime(2026, 8, 1, tzinfo=UTC),
        expected_execution_ids=("serial-pool",),
        expected_case_count=1,
        junit_files=(),
        status=RunStatus.FINISHED,
    )

    assert "invalid_flaky_history_configuration" in events
    assert not (tmp_path / "quality" / "flaky.sqlite3").exists()


def test_flaky_state_evaluation_runs_after_committed_history_import(
    monkeypatch,
    tmp_path,
):
    events = []
    _stub_fact_finalize(monkeypatch, events)
    monkeypatch.setattr(
        quality_semantic_stage,
        "merge_semantic_run",
        lambda request: events.append("semantic"),
    )
    monkeypatch.setattr(
        quality_flaky_stage,
        "import_flaky_history",
        lambda request: events.append("history")
        or FlakyImportResult(
            run_id="run-1",
            status=FlakyImportStatus.IMPORTED,
            inserted_count=1,
        ),
    )
    monkeypatch.setattr(
        quality_flaky_stage,
        "evaluate_flaky_state",
        lambda *args, **kwargs: events.append("state")
        or FlakyEvaluationResult(
            run_id="run-1",
            status=FlakyEvaluationStatus.EVALUATED,
            evaluated_at=datetime(2026, 8, 1, tzinfo=UTC),
        ),
    )

    quality_pipeline.finalize_quality_run(
        _config(tmp_path, flaky_state_enabled=True),
        start_time=datetime(2026, 8, 1, tzinfo=UTC),
        expected_execution_ids=("serial-pool",),
        expected_case_count=1,
        junit_files=(),
        status=RunStatus.FINISHED,
    )

    assert events == ["run-record", "semantic", "history", "state"]


def test_flaky_state_failure_is_fail_open(monkeypatch, tmp_path, capsys):
    events = []
    _stub_fact_finalize(monkeypatch, events)
    monkeypatch.setattr(quality_semantic_stage, "merge_semantic_run", lambda request: None)
    monkeypatch.setattr(
        quality_flaky_stage,
        "import_flaky_history",
        lambda request: FlakyImportResult(
            run_id="run-1",
            status=FlakyImportStatus.NOOP,
        ),
    )
    monkeypatch.setattr(
        quality_flaky_stage,
        "evaluate_flaky_state",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("state unavailable")),
    )

    quality_pipeline.finalize_quality_run(
        _config(tmp_path, flaky_state_enabled=True),
        start_time=datetime(2026, 8, 1, tzinfo=UTC),
        expected_execution_ids=("serial-pool",),
        expected_case_count=1,
        junit_files=(),
        status=RunStatus.FINISHED,
    )

    assert "Quality Flaky state evaluation failed open" in capsys.readouterr().out
