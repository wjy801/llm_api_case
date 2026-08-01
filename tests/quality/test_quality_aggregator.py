from __future__ import annotations

from datetime import UTC, datetime
import json

from quality.aggregator import QualityMergeRequest, merge_quality_run
from quality.models import (
    CasePhase,
    CaseResult,
    CaseStatus,
    IntegrityStatus,
    RequestMetric,
    BusinessStatus,
    Protocol,
)
from quality.storage import append_jsonl, read_jsonl


START = datetime(2026, 7, 31, 8, 0, tzinfo=UTC)


def _case(**overrides):
    values = {
        "run_id": "run-1",
        "execution_id": "serial-pool",
        "worker_id": "master",
        "case_id": "module/test_demo.py::test_case",
        "invocation_id": "inv-1",
        "nodeid": "module/test_demo.py::test_case",
        "param_hash": "param",
        "phase": CasePhase.CALL,
        "raw_status": CaseStatus.PASSED,
        "final_status": CaseStatus.PASSED,
        "duration_ms": 1,
        "start_time": START,
        "end_time": START,
    }
    values.update(overrides)
    return CaseResult(**values)


def _request(**overrides):
    values = {
        "run_id": "run-1",
        "execution_id": "serial-pool",
        "worker_id": "master",
        "case_id": "module/test_demo.py::test_case",
        "invocation_id": "inv-1",
        "request_event_id": "request-1",
        "interface_id": "GET /v1/items/{id} http",
        "method": "GET",
        "url_template": "/v1/items/{id}",
        "protocol": Protocol.HTTP,
        "attempt_index": 1,
        "status_code": 200,
        "business_status": BusinessStatus.SUCCESS,
        "duration_ms": 1,
    }
    values.update(overrides)
    return RequestMetric(**values)


def test_merge_filters_foreign_run_deduplicates_and_writes_manifest(tmp_path):
    output_dir = tmp_path / "quality"
    shard = output_dir / "shards" / "cases-serial-pool-master.jsonl"
    append_jsonl(shard, _case())
    append_jsonl(shard, _case())
    append_jsonl(shard, _case(run_id="other-run", invocation_id="inv-other"))
    request_shard = output_dir / "shards" / "requests-serial-pool-master.jsonl"
    append_jsonl(request_shard, _request())

    result = merge_quality_run(
        QualityMergeRequest(
            run_id="run-1",
            output_dir=output_dir,
            expected_execution_ids=("serial-pool",),
            expected_case_count=1,
        )
    )

    assert result.integrity_status is IntegrityStatus.COMPLETE
    assert len(read_jsonl(output_dir / "merged" / "case-results.jsonl")) == 1
    assert len(read_jsonl(output_dir / "merged" / "request-metrics.jsonl")) == 1
    manifest = json.loads((output_dir / "merged" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "complete"
    assert manifest["source_shards"][0]["foreign_run_records"] == 1
    assert manifest["source_shards"][0]["exact_duplicates"] == 1


def test_merge_recovers_valid_lines_and_classifies_failed_case_with_junit(tmp_path):
    output_dir = tmp_path / "quality"
    case_shard = output_dir / "shards" / "cases-serial-pool-master.jsonl"
    append_jsonl(
        case_shard,
        _case(raw_status=CaseStatus.FAILED, final_status=CaseStatus.FAILED),
    )
    case_shard.write_text(case_shard.read_text(encoding="utf-8") + "not-json\n", encoding="utf-8")
    request_shard = output_dir / "shards" / "requests-serial-pool-master.jsonl"
    append_jsonl(request_shard, _request(status_code=500, business_status=BusinessStatus.FAILED))
    junit = output_dir / "junit" / "quality.xml"
    junit.parent.mkdir(parents=True)
    junit.write_text(
        """
        <testsuite>
          <testcase classname="c" name="n">
            <properties>
              <property name="quality_case_id" value="module/test_demo.py::test_case" />
              <property name="quality_invocation_id" value="inv-1" />
            </properties>
            <failure type="AssertionError" message="expected status code 200 got 500">module/test_demo.py:9</failure>
          </testcase>
        </testsuite>
        """,
        encoding="utf-8",
    )

    result = merge_quality_run(
        QualityMergeRequest(
            run_id="run-1",
            output_dir=output_dir,
            expected_execution_ids=("serial-pool",),
            expected_case_count=1,
            junit_files=(junit,),
        )
    )

    assert result.integrity_status is IntegrityStatus.DEGRADED
    cases = read_jsonl(output_dir / "merged" / "case-results.jsonl")
    failures = read_jsonl(output_dir / "merged" / "failures.jsonl")
    issues = read_jsonl(output_dir / "merged" / "integrity-issues.jsonl")
    assert cases[0]["failure_id"] == failures[0]["failure_id"]
    assert failures[0]["category"] in {"PRODUCT_DEFECT", "UNKNOWN"}
    assert any(issue["code"] == "invalid_jsonl_line" for issue in issues)


def test_merge_treats_setup_skip_and_passing_teardown_as_skipped(tmp_path):
    output_dir = tmp_path / "quality"
    case_shard = output_dir / "shards" / "cases-serial-pool-master.jsonl"
    append_jsonl(
        case_shard,
        _case(
            phase=CasePhase.SETUP,
            raw_status=CaseStatus.SKIPPED,
            final_status=CaseStatus.SKIPPED,
        ),
    )
    append_jsonl(case_shard, _case(phase=CasePhase.TEARDOWN))
    junit = output_dir / "junit" / "quality.xml"
    junit.parent.mkdir(parents=True)
    junit.write_text(
        """
        <testsuite tests="1" skipped="1">
          <testcase classname="c" name="n">
            <properties>
              <property name="quality_case_id" value="module/test_demo.py::test_case" />
              <property name="quality_invocation_id" value="inv-1" />
            </properties>
            <skipped message="sample" />
          </testcase>
        </testsuite>
        """,
        encoding="utf-8",
    )

    result = merge_quality_run(
        QualityMergeRequest(
            run_id="run-1",
            output_dir=output_dir,
            expected_execution_ids=("serial-pool",),
            expected_case_count=1,
            junit_files=(junit,),
        )
    )

    assert result.integrity_status is IntegrityStatus.COMPLETE
    issues = read_jsonl(output_dir / "merged" / "integrity-issues.jsonl")
    assert not any(issue["code"] == "junit_status_mismatch" for issue in issues)
