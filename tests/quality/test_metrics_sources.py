from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json

import requests

from common.base_request import BaseRequest
from quality.aggregator import QualityMergeRequest, merge_quality_run
from quality.cli import main as quality_cli
from quality.metrics import RunMetricsAggregationRequest, aggregate_run_metrics
from quality.metrics_models import RunMetricsStatus
from quality.models import (
    CasePhase,
    CaseResult,
    CaseStatus,
    IntegrityStatus,
    RunRecord,
    RunStatus,
)
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


def _build_sources(semantic_runtime):
    client = BaseRequest(config=_Config())
    client.session.request = lambda method, url, **kwargs: _response()  # type: ignore[method-assign]
    client.get(
        "/v1/items",
        _attach_log=False,
        _quality_traffic_role=TrafficRole.WORKLOAD,
    )
    now = datetime(2026, 8, 1, 1, 0, tzinfo=UTC)
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
            start_time=now,
            end_time=now + timedelta(milliseconds=1),
        )
    )
    p0 = merge_quality_run(
        QualityMergeRequest(
            run_id=semantic_runtime.run_context.run_id,
            output_dir=semantic_runtime.output_dir,
        )
    )
    start = now
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
    merge_semantic_run(
        SemanticMergeRequest(
            run_id=semantic_runtime.run_context.run_id,
            output_dir=semantic_runtime.output_dir,
        )
    )


def test_metrics_aggregate_writes_versioned_current_run_artifact(semantic_runtime):
    _build_sources(semantic_runtime)

    result = aggregate_run_metrics(
        RunMetricsAggregationRequest(
            run_id=semantic_runtime.run_context.run_id,
            output_dir=semantic_runtime.output_dir,
        )
    )

    assert result.status is RunMetricsStatus.AGGREGATED
    assert result.operation_count == result.request_group_count == result.request_event_count == 1
    assert result.metrics is not None
    assert result.metrics.run_metrics is not None
    assert result.metrics.run_metrics.usage.input_tokens.total == 2
    assert result.metrics.run_metrics.usage.output_tokens.total == 3
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["write_status"] == "complete"
    assert manifest["output_hashes"]["run_metrics"]


def test_metrics_rejects_tampered_semantic_output(semantic_runtime):
    _build_sources(semantic_runtime)
    operations = semantic_runtime.output_dir / "semantic" / "merged" / "operations.jsonl"
    operations.write_text(operations.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    result = aggregate_run_metrics(
        RunMetricsAggregationRequest(
            run_id=semantic_runtime.run_context.run_id,
            output_dir=semantic_runtime.output_dir,
        )
    )

    assert result.status is RunMetricsStatus.FAILED
    assert result.issues[0].code == "semantic_operations_hash_mismatch"
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["write_status"] == "failed"
    assert manifest["output_hashes"] == {}


def test_metrics_cli_exit_code_describes_artifact_trust(semantic_runtime, capsys):
    _build_sources(semantic_runtime)

    exit_code = quality_cli(
        [
            "metrics-aggregate",
            "--run-id",
            semantic_runtime.run_context.run_id,
            "--output-dir",
            str(semantic_runtime.output_dir),
        ]
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "status=aggregated" in output
    assert "manifest=metrics/manifest.json" in output
