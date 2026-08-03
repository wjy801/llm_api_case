from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from quality.config import QualityRuntimeConfig
from quality.models import IntegrityStatus, RunStatus
from run_orchestration import quality_pipeline


def _config(tmp_path):
    return QualityRuntimeConfig(
        enabled=True,
        run_id="run-1",
        execution_id=None,
        output_dir=tmp_path / "quality",
    )


def test_quality_pipeline_preserves_the_stage_order(monkeypatch, tmp_path):
    events = []
    merge_result = SimpleNamespace(
        integrity_status=IntegrityStatus.COMPLETE,
        integrity_issues=(),
    )
    monkeypatch.setattr(
        quality_pipeline.quality_fact_merge_stage,
        "merge_quality_facts",
        lambda *args, **kwargs: events.append("fact-merge") or merge_result,
    )
    monkeypatch.setattr(
        quality_pipeline.quality_run_record,
        "write_final_run_record",
        lambda *args, **kwargs: events.append("run-record"),
    )
    monkeypatch.setattr(
        quality_pipeline.quality_semantic_stage,
        "run_semantic_stage",
        lambda *args, **kwargs: events.append("semantic"),
    )
    monkeypatch.setattr(
        quality_pipeline.quality_metrics_stage,
        "run_metrics_stage",
        lambda *args, **kwargs: events.append("metrics"),
    )
    imported = object()
    monkeypatch.setattr(
        quality_pipeline.quality_flaky_stage,
        "run_flaky_history_stage",
        lambda *args, **kwargs: events.append("flaky-import") or imported,
    )

    def state_stage(config, result):
        assert result is imported
        events.append("flaky-state")

    monkeypatch.setattr(
        quality_pipeline.quality_flaky_stage,
        "run_flaky_state_stage",
        state_stage,
    )
    quality_pipeline.finalize_quality_run(
        _config(tmp_path),
        start_time=datetime(2026, 8, 1, tzinfo=UTC),
        expected_execution_ids=("serial-pool",),
        expected_case_count=1,
        junit_files=(),
        status=RunStatus.FINISHED,
    )

    assert events == [
        "fact-merge",
        "run-record",
        "semantic",
        "metrics",
        "flaky-import",
        "flaky-state",
    ]


def test_quality_pipeline_stops_after_fact_merge_failure(monkeypatch, tmp_path):
    events = []
    monkeypatch.setattr(
        quality_pipeline.quality_fact_merge_stage,
        "merge_quality_facts",
        lambda *args, **kwargs: events.append("fact-merge") or None,
    )
    monkeypatch.setattr(
        quality_pipeline.quality_run_record,
        "write_final_run_record",
        lambda *args, **kwargs: events.append("unexpected"),
    )

    quality_pipeline.finalize_quality_run(
        _config(tmp_path),
        start_time=datetime(2026, 8, 1, tzinfo=UTC),
        expected_execution_ids=(),
        expected_case_count=0,
        junit_files=(),
        status=RunStatus.PARTIAL,
    )

    assert events == ["fact-merge"]
