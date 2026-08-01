from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
import json

import requests
import quality.observation_report as observation_report_module

from common.base_request import BaseRequest
from quality.aggregator import QualityMergeRequest, merge_quality_run
from quality.flaky_models import (
    FlakyEvaluationResult,
    FlakyEvaluationStatus,
    FlakyImportResult,
    FlakyImportStatus,
    FlakyState,
    FlakyStateSummary,
    ProjectionStatus,
)
from quality.cli import main as quality_cli
from quality.metrics import RunMetricsAggregationRequest, aggregate_run_metrics
from quality.models import (
    CasePhase,
    CaseResult,
    CaseStatus,
    RunRecord,
    RunStatus,
)
from quality.observation_models import P1ReportStatus, SourceExpectation, SourceStatus
from quality.observation_report import (
    P1ObservationRequest,
    generate_p1_observation_report,
)
from quality.report import QualityReportRequest, generate_quality_report
from quality.semantic_aggregator import SemanticMergeRequest, merge_semantic_run
from quality.semantic_models import TrafficRole
from quality.storage import write_json_atomic


class _Config:
    base_url = "https://example.com"
    api_key = "secret"
    timeout = 5


def _response() -> requests.Response:
    response = requests.Response()
    response.status_code = 200
    response.url = "https://example.com/v1/items"
    response._content = json.dumps(
        {"usage": {"prompt_tokens": 2, "completion_tokens": 3}}
    ).encode("utf-8")
    response.headers["Content-Type"] = "application/json"
    return response


def _build_all_sources(semantic_runtime) -> None:
    client = BaseRequest(config=_Config())
    client.session.request = lambda method, url, **kwargs: _response()  # type: ignore[method-assign]
    client.get(
        "/v1/items",
        _attach_log=False,
        _quality_traffic_role=TrafficRole.WORKLOAD,
    )
    start = datetime(2026, 8, 1, 1, 0, tzinfo=UTC)
    semantic_runtime.p0.record_case(
        CaseResult(
            run_id=semantic_runtime.run_context.run_id,
            execution_id=semantic_runtime.run_context.execution_id,
            worker_id=semantic_runtime.run_context.worker_id,
            case_id=semantic_runtime.case_context.case_id,
            invocation_id=semantic_runtime.case_context.invocation_id,
            nodeid=semantic_runtime.case_context.nodeid,
            param_hash=semantic_runtime.case_context.param_hash,
            phase=CasePhase.CALL,
            raw_status=CaseStatus.PASSED,
            final_status=CaseStatus.PASSED,
            duration_ms=1,
            start_time=start,
            end_time=start + timedelta(milliseconds=1),
        )
    )
    p0 = merge_quality_run(
        QualityMergeRequest(
            run_id=semantic_runtime.run_context.run_id,
            output_dir=semantic_runtime.output_dir,
        )
    )
    write_json_atomic(
        semantic_runtime.output_dir / "run.json",
        RunRecord(
            run_id=semantic_runtime.run_context.run_id,
            trigger="local",
            environment="test",
            start_time=start,
            end_time=start + timedelta(seconds=1),
            status=RunStatus.FINISHED,
            integrity_status=p0.integrity_status,
            integrity_issues=p0.integrity_issues,
        ),
    )
    generate_quality_report(
        QualityReportRequest(
            run_id=semantic_runtime.run_context.run_id,
            output_dir=semantic_runtime.output_dir,
        )
    )
    merge_semantic_run(
        SemanticMergeRequest(
            run_id=semantic_runtime.run_context.run_id,
            output_dir=semantic_runtime.output_dir,
        )
    )
    aggregate_run_metrics(
        RunMetricsAggregationRequest(
            run_id=semantic_runtime.run_context.run_id,
            output_dir=semantic_runtime.output_dir,
        )
    )
    write_json_atomic(
        semantic_runtime.output_dir / "flaky-import.json",
        FlakyImportResult(
            run_id=semantic_runtime.run_context.run_id,
            status=FlakyImportStatus.NOOP,
        ),
    )
    write_json_atomic(
        semantic_runtime.output_dir / "flaky-evaluation.json",
        FlakyEvaluationResult(
            run_id=semantic_runtime.run_context.run_id,
            status=FlakyEvaluationStatus.NOOP,
            evaluated_at=start + timedelta(seconds=2),
        ),
    )


def test_observation_report_commits_complete_artifacts(semantic_runtime):
    _build_all_sources(semantic_runtime)

    result = generate_p1_observation_report(
        P1ObservationRequest(
            run_id=semantic_runtime.run_context.run_id,
            output_dir=semantic_runtime.output_dir,
        )
    )

    assert result.write_status == "complete"
    assert result.report_status is P1ReportStatus.COMPLETE
    assert result.report is not None
    assert result.report.overview.workload_operation_count == 1
    assert result.report.usage_coverage is not None
    assert result.report.usage_coverage.input_tokens.total == 2
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["write_status"] == "complete"
    assert manifest["output_hashes"]["json"] == hashlib.sha256(
        result.json_path.read_bytes()
    ).hexdigest()
    assert manifest["output_hashes"]["markdown"] == hashlib.sha256(
        result.markdown_path.read_bytes()
    ).hexdigest()
    payload = json.loads(result.json_path.read_text(encoding="utf-8"))
    assert payload["report_status"] == "complete"
    assert any(
        item["metric_name"] == "operation.success_rate"
        for item in payload["metrics"]["observations"]
    )
    markdown = result.markdown_path.read_text(encoding="utf-8")
    assert "“已隔离（QUARANTINED）”是治理标签" in markdown
    assert "P1 报告状态只表示观察数据完整性" in markdown
    assert "报告状态：完整（`complete`）" in markdown
    assert "业务逻辑调用：共 1 次" in markdown
    assert "逻辑调用成功率（`operation.success_rate`）" in markdown
    assert "输入 Token（`input tokens`）" in markdown


def test_required_metrics_hash_mismatch_degrades_without_hiding_p0(semantic_runtime):
    _build_all_sources(semantic_runtime)
    metrics_path = semantic_runtime.output_dir / "metrics" / "run-metrics.json"
    metrics_path.write_text(metrics_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    result = generate_p1_observation_report(
        P1ObservationRequest(
            run_id=semantic_runtime.run_context.run_id,
            output_dir=semantic_runtime.output_dir,
        )
    )

    assert result.report_status is P1ReportStatus.DEGRADED
    assert result.report is not None
    assert result.report.p0 is not None
    metrics_source = next(
        item for item in result.report.sources if item.source_name == "run_metrics"
    )
    assert metrics_source.status is SourceStatus.INCOMPATIBLE
    assert result.report.metrics is None


def test_disabled_flaky_sources_are_not_reported_as_failures(semantic_runtime):
    _build_all_sources(semantic_runtime)
    (semantic_runtime.output_dir / "flaky-import.json").unlink()
    (semantic_runtime.output_dir / "flaky-evaluation.json").unlink()

    result = generate_p1_observation_report(
        P1ObservationRequest(
            run_id=semantic_runtime.run_context.run_id,
            output_dir=semantic_runtime.output_dir,
            flaky_import_expectation=SourceExpectation.DISABLED,
            flaky_evaluation_expectation=SourceExpectation.DISABLED,
        )
    )

    assert result.report_status is P1ReportStatus.COMPLETE
    assert result.report is not None
    assert result.report.overview.required_source_failure_count == 0
    assert [item.status for item in result.report.sources[-2:]] == [
        SourceStatus.DISABLED,
        SourceStatus.DISABLED,
    ]


def test_stale_flaky_projection_is_degraded_and_not_actionable_confirmed(
    semantic_runtime,
):
    _build_all_sources(semantic_runtime)
    summary = FlakyStateSummary(
        flaky_key="flaky-key-1",
        case_id=semantic_runtime.case_context.case_id,
        param_hash=semantic_runtime.case_context.param_hash,
        environment="test",
        execution_profile="serial",
        state_epoch=1,
        current_state=FlakyState.CONFIRMED,
        detected_state=FlakyState.CONFIRMED,
        sample_size=4,
        projection_status=ProjectionStatus.STALE,
        latest_run_id=semantic_runtime.run_context.run_id,
        latest_observation_id="observation-1",
        transition_reason="confirmation_threshold_met",
    )
    write_json_atomic(
        semantic_runtime.output_dir / "flaky-evaluation.json",
        FlakyEvaluationResult(
            run_id=semantic_runtime.run_context.run_id,
            status=FlakyEvaluationStatus.EVALUATED,
            evaluated_at=datetime(2026, 8, 1, 1, 1, tzinfo=UTC),
            affected_count=1,
            evaluated_count=1,
            stale_count=1,
            newly_confirmed=(summary,),
        ),
    )

    result = generate_p1_observation_report(
        P1ObservationRequest(
            run_id=semantic_runtime.run_context.run_id,
            output_dir=semantic_runtime.output_dir,
        )
    )

    assert result.report_status is P1ReportStatus.DEGRADED
    assert result.report is not None
    codes = {item.attention_code for item in result.report.attention_items}
    assert "flaky_projection_stale" in codes
    assert "flaky_newly_confirmed" not in codes


def test_p1_report_cli_replays_artifacts_without_database(semantic_runtime, capsys):
    _build_all_sources(semantic_runtime)

    exit_code = quality_cli(
        [
            "p1-report",
            "--run-id",
            semantic_runtime.run_context.run_id,
            "--output-dir",
            str(semantic_runtime.output_dir),
            "--metrics-source",
            "required",
            "--flaky-source",
            "required",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["write_status"] == "complete"
    assert payload["report_status"] == "complete"
    assert "db" not in payload


def test_report_write_failure_leaves_non_complete_manifest(
    semantic_runtime, monkeypatch
):
    _build_all_sources(semantic_runtime)
    monkeypatch.setattr(
        observation_report_module,
        "_write_text_atomic",
        lambda path, content: (_ for _ in ()).throw(OSError("disk unavailable")),
    )

    result = generate_p1_observation_report(
        P1ObservationRequest(
            run_id=semantic_runtime.run_context.run_id,
            output_dir=semantic_runtime.output_dir,
        )
    )

    assert result.write_status == "failed"
    assert result.report is None
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["write_status"] == "failed"
    assert manifest["output_hashes"] == {}


def test_replay_keeps_business_sections_and_attention_deterministic(semantic_runtime):
    _build_all_sources(semantic_runtime)
    request = P1ObservationRequest(
        run_id=semantic_runtime.run_context.run_id,
        output_dir=semantic_runtime.output_dir,
    )

    first = generate_p1_observation_report(request)
    second = generate_p1_observation_report(request)

    assert first.report is not None and second.report is not None
    first_payload = first.report.model_dump(mode="json")
    second_payload = second.report.model_dump(mode="json")
    first_payload.pop("generated_at")
    second_payload.pop("generated_at")
    first_payload["overview"].pop("generated_at")
    second_payload["overview"].pop("generated_at")
    assert first_payload == second_payload
