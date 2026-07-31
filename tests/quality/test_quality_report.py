from __future__ import annotations

from datetime import UTC, datetime
import json

from quality.aggregator import QualityMergeRequest, merge_quality_run
from quality.models import (
    BusinessStatus,
    CasePhase,
    CaseResult,
    CaseStatus,
    GateDecision,
    GateResult,
    IntegrityStatus,
    Protocol,
    QualitySummary,
    RequestMetric,
)
from quality.report import QualityReportRequest, generate_quality_report
from quality.storage import append_jsonl, write_json_atomic


START = datetime(2026, 7, 31, 8, 0, tzinfo=UTC)


def _case(phase: CasePhase, **overrides) -> CaseResult:
    values = {
        "run_id": "run-1",
        "execution_id": "serial-pool",
        "worker_id": "master",
        "case_id": "module/test_demo.py::test_case",
        "invocation_id": "inv-1",
        "nodeid": "module/test_demo.py::test_case",
        "param_hash": "param",
        "phase": phase,
        "raw_status": CaseStatus.PASSED,
        "final_status": CaseStatus.PASSED,
        "duration_ms": 1,
        "start_time": START,
        "end_time": START,
    }
    values.update(overrides)
    return CaseResult(**values)


def _request(event_id: str, **overrides) -> RequestMetric:
    values = {
        "run_id": "run-1",
        "execution_id": "serial-pool",
        "worker_id": "master",
        "case_id": "module/test_demo.py::test_case",
        "invocation_id": "inv-1",
        "request_event_id": event_id,
        "interface_id": "GET /v1/items/{id} http",
        "method": "GET",
        "url_template": "/v1/items/{id}",
        "protocol": Protocol.HTTP,
        "attempt_index": 1,
        "status_code": 200,
        "business_status": BusinessStatus.SUCCESS,
        "duration_ms": 10,
    }
    values.update(overrides)
    return RequestMetric(**values)


def _merged_output(tmp_path, *, requests=()):
    output_dir = tmp_path / "quality"
    case_shard = output_dir / "shards" / "cases-serial-pool-master.jsonl"
    for phase in (CasePhase.SETUP, CasePhase.CALL, CasePhase.TEARDOWN):
        append_jsonl(case_shard, _case(phase))
    request_shard = output_dir / "shards" / "requests-serial-pool-master.jsonl"
    for request in requests:
        append_jsonl(request_shard, request)
    merge_quality_run(
        QualityMergeRequest(
            run_id="run-1",
            output_dir=output_dir,
            expected_execution_ids=("serial-pool",),
            expected_case_count=1,
        )
    )
    write_json_atomic(output_dir / "run.json", {"run_id": "run-1"})
    return output_dir


def test_report_generates_summary_gate_and_markdown_from_verified_snapshot(tmp_path):
    output_dir = _merged_output(
        tmp_path,
        requests=(
            _request("request-1"),
            _request(
                "request-2",
                status_code=500,
                business_status=BusinessStatus.FAILED,
                timeout=True,
                error_type="TimeoutError",
            ),
        ),
    )

    result = generate_quality_report(
        QualityReportRequest(run_id="run-1", output_dir=output_dir, min_request_samples=1)
    )

    summary = json.loads(result.summary_path.read_text(encoding="utf-8"))
    gate = json.loads(result.gate_report_json_path.read_text(encoding="utf-8"))
    assert summary["summary"]["case_total"] == 1
    assert summary["summary"]["request_total"] == 2
    assert summary["summary"]["http_5xx_count"] == 1
    assert gate["overall"] == "WARN"
    assert QualitySummary.model_validate(summary["summary"]).case_total == 1
    assert GateDecision.model_validate(gate["decision"]).overall is GateResult.WARN
    assert "P0 质量影子门禁报告" in result.gate_report_md_path.read_text(encoding="utf-8")


def test_report_rejects_hash_mismatch_and_emits_no_data(tmp_path):
    output_dir = _merged_output(tmp_path)
    case_output = output_dir / "merged" / "case-results.jsonl"
    case_output.write_text(case_output.read_text(encoding="utf-8") + "{}\n", encoding="utf-8")

    result = generate_quality_report(QualityReportRequest(run_id="run-1", output_dir=output_dir))

    summary = json.loads(result.summary_path.read_text(encoding="utf-8"))
    assert result.overall is GateResult.NO_DATA
    assert result.integrity_status is IntegrityStatus.FAILED
    assert "hash mismatch" in summary["integrity"]["report_warnings"][0]


def test_small_request_sample_is_explained_in_report(tmp_path):
    output_dir = _merged_output(
        tmp_path,
        requests=(
            _request(
                "request-1",
                status_code=None,
                business_status=BusinessStatus.FAILED,
                timeout=True,
                error_type="TimeoutError",
            ),
        ),
    )

    result = generate_quality_report(
        QualityReportRequest(run_id="run-1", output_dir=output_dir, min_request_samples=20)
    )

    gate = json.loads(result.gate_report_json_path.read_text(encoding="utf-8"))
    timeout_rule = next(
        rule for rule in gate["rules"] if rule["rule_id"] == "p0.request.timeout_rate"
    )
    assert timeout_rule["decision"] == "NO_DATA"
    assert timeout_rule["sample_size"] == 1
    assert gate["overall"] == "WARN"


def test_report_folds_phase_rows_into_final_invocation_statuses(tmp_path):
    output_dir = tmp_path / "quality"
    shard = output_dir / "shards" / "cases-serial-pool-master.jsonl"
    records = [
        _case(CasePhase.SETUP, invocation_id="inv-failed", case_id="case-failed", nodeid="case-failed"),
        _case(
            CasePhase.CALL,
            invocation_id="inv-failed",
            case_id="case-failed",
            nodeid="case-failed",
            raw_status=CaseStatus.FAILED,
            final_status=CaseStatus.FAILED,
        ),
        _case(CasePhase.TEARDOWN, invocation_id="inv-failed", case_id="case-failed", nodeid="case-failed"),
        _case(
            CasePhase.SETUP,
            invocation_id="inv-error",
            case_id="case-error",
            nodeid="case-error",
            raw_status=CaseStatus.ERROR,
            final_status=CaseStatus.ERROR,
        ),
        _case(
            CasePhase.CALL,
            invocation_id="inv-xfailed",
            case_id="case-xfailed",
            nodeid="case-xfailed",
            raw_status=CaseStatus.XFAILED,
            final_status=CaseStatus.XFAILED,
        ),
        _case(
            CasePhase.CALL,
            invocation_id="inv-xpassed",
            case_id="case-xpassed",
            nodeid="case-xpassed",
            raw_status=CaseStatus.XPASSED,
            final_status=CaseStatus.XPASSED,
        ),
    ]
    for record in records:
        append_jsonl(shard, record)
    merge_quality_run(
        QualityMergeRequest(
            run_id="run-1",
            output_dir=output_dir,
            expected_execution_ids=("serial-pool",),
            expected_case_count=4,
        )
    )
    write_json_atomic(output_dir / "run.json", {"run_id": "run-1"})

    result = generate_quality_report(QualityReportRequest(run_id="run-1", output_dir=output_dir))

    summary = json.loads(result.summary_path.read_text(encoding="utf-8"))["summary"]
    assert summary["case_total"] == 4
    assert summary["case_passed"] == 1
    assert summary["case_failed"] == 1
    assert summary["case_error"] == 1
    assert summary["case_skipped"] == 1
    report = json.loads(result.summary_path.read_text(encoding="utf-8"))
    assert report["failures"]["occurrence_count"] == 2
    assert report["failures"]["affected_invocation_count"] == 2
    assert sum(
        item["fingerprint_count"] for item in report["failures"]["categories"].values()
    ) >= 1
