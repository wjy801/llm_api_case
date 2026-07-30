from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from quality.models import (
    SCHEMA_VERSION,
    BusinessStatus,
    CasePhase,
    CaseResult,
    CaseStatus,
    Confidence,
    CostSource,
    FailureCategory,
    FailureFingerprintSource,
    FailureRecord,
    GateDecision,
    GateResult,
    GateRuleDecision,
    IntegrityIssue,
    IntegrityStatus,
    IssueSeverity,
    OwnerDomain,
    Protocol,
    QualitySummary,
    RequestCost,
    RequestMetric,
    RequestUsage,
    RunRecord,
    RunStatus,
)


START_TIME = datetime(2026, 7, 30, 8, 0, tzinfo=UTC)
END_TIME = START_TIME + timedelta(seconds=1)


def test_top_level_models_dump_stable_json_values():
    issue = IntegrityIssue(
        run_id="run-1",
        severity=IssueSeverity.WARN,
        source="pytest_plugin",
        code="missing_case_result",
        message="case result was not written",
        created_at=START_TIME,
    )
    run = RunRecord(
        run_id="run-1",
        job_name="API_CASE",
        build_number="123",
        branch="main",
        commit_sha="abcdef",
        trigger="jenkins",
        environment="china-test",
        start_time=START_TIME,
        end_time=END_TIME,
        status=RunStatus.FINISHED,
        integrity_status=IntegrityStatus.DEGRADED,
        integrity_issues=(issue,),
    )
    case = CaseResult(
        run_id="run-1",
        execution_id="parallel-pool-1",
        worker_id="gw0",
        case_id="module/test_demo.py::test_case",
        invocation_id="inv-1",
        nodeid="module/test_demo.py::test_case[param]",
        param_hash="abc123",
        phase=CasePhase.CALL,
        raw_status=CaseStatus.PASSED,
        final_status=CaseStatus.PASSED,
        duration_ms=12.5,
        start_time=START_TIME,
        end_time=END_TIME,
        evidence_refs={"junit": "reports/unit-tests.xml"},
    )
    request = RequestMetric(
        run_id="run-1",
        execution_id="parallel-pool-1",
        worker_id="gw0",
        case_id=case.case_id,
        invocation_id=case.invocation_id,
        request_event_id="request-1",
        interface_id="POST /v1/chat/completions http",
        method="post",
        url_template="/v1/chat/completions",
        protocol=Protocol.HTTP,
        attempt_index=1,
        status_code=200,
        business_status=BusinessStatus.SUCCESS,
        duration_ms=18,
        usage=RequestUsage(input_tokens=10, output_tokens=5),
        cost=RequestCost(amount=0.12, source=CostSource.ESTIMATED, price_version="v1"),
    )
    failure_source = FailureFingerprintSource(
        phase=CasePhase.CALL,
        error_type="AssertionError",
        message_hash="message-hash",
        interface_id=request.interface_id,
        assert_location="module/test_demo.py:88",
    )
    failure = FailureRecord(
        run_id="run-1",
        failure_id="failure-1",
        case_id=case.case_id,
        invocation_id=case.invocation_id,
        phase=CasePhase.CALL,
        category=FailureCategory.PRODUCT_DEFECT,
        owner_domain=OwnerDomain.PRODUCT,
        confidence=Confidence.HIGH,
        error_type="AssertionError",
        normalized_message="expected 200 got 500",
        fingerprint_source=failure_source,
    )
    summary = QualitySummary(
        run_id="run-1",
        case_total=1,
        case_passed=1,
        case_failed=0,
        case_error=0,
        case_skipped=0,
        raw_pass_rate=1,
        final_pass_rate=1,
        retry_passed=0,
        request_total=1,
        request_success_rate=1,
        http_5xx_count=0,
        timeout_count=0,
        unknown_failure_count=0,
        integrity_status=IntegrityStatus.COMPLETE,
    )
    gate = GateDecision(
        run_id="run-1",
        overall=GateResult.PASS,
        rules=(
            GateRuleDecision(
                rule_id="p0.timeout.rate",
                rule_version="1",
                target="run",
                actual=0,
                threshold=0.05,
                sample_size=1,
                decision=GateResult.PASS,
                evidence=("request-1",),
            ),
        ),
    )

    dumped = [
        model.model_dump(mode="json")
        for model in (issue, run, case, request, failure, summary, gate)
    ]

    assert all(item["schema_version"] == SCHEMA_VERSION for item in dumped)
    assert dumped[1]["status"] == "finished"
    assert dumped[2]["phase"] == "call"
    assert dumped[3]["method"] == "POST"
    assert dumped[3]["protocol"] == "http"
    assert dumped[4]["category"] == "PRODUCT_DEFECT"
    assert dumped[6]["overall"] == "PASS"


def test_version_is_fixed_and_extra_fields_are_rejected():
    base_values = {
        "run_id": "run-1",
        "trigger": "local",
        "environment": "china-test",
        "start_time": START_TIME,
        "status": RunStatus.FINISHED,
        "integrity_status": IntegrityStatus.COMPLETE,
    }

    with pytest.raises(ValidationError, match="schema_version"):
        RunRecord(schema_version="quality.v2", **base_values)

    with pytest.raises(ValidationError, match="extra_forbidden"):
        RunRecord(unexpected=True, **base_values)


def test_models_are_frozen():
    usage = RequestUsage(input_tokens=1)

    with pytest.raises(ValidationError, match="frozen"):
        usage.input_tokens = 2  # type: ignore[misc]


@pytest.mark.parametrize(
    ("field_name", "field_value"),
    [
        ("attempt_index", 0),
        ("duration_ms", -1),
    ],
)
def test_request_metric_rejects_invalid_numeric_boundaries(field_name, field_value):
    values = {
        "run_id": "run-1",
        "execution_id": "manual-pytest-1",
        "worker_id": "master",
        "case_id": "test_demo.py::test_case",
        "invocation_id": "inv-1",
        "request_event_id": "request-1",
        "interface_id": "GET /v1/models http",
        "method": "GET",
        "url_template": "/v1/models",
        "protocol": Protocol.HTTP,
        "attempt_index": 1,
        "business_status": BusinessStatus.SUCCESS,
        "duration_ms": 1,
    }
    values[field_name] = field_value

    with pytest.raises(ValidationError, match=field_name):
        RequestMetric(**values)


def test_models_reject_naive_datetime_and_reversed_time_range():
    naive_time = datetime(2026, 7, 30, 8, 0)

    with pytest.raises(ValidationError, match="timezone"):
        IntegrityIssue(
            run_id="run-1",
            severity=IssueSeverity.ERROR,
            source="writer",
            code="write_failed",
            message="write failed",
            created_at=naive_time,
        )

    with pytest.raises(ValidationError, match="end_time"):
        RunRecord(
            run_id="run-1",
            trigger="local",
            environment="china-test",
            start_time=END_TIME,
            end_time=START_TIME,
            status=RunStatus.FINISHED,
            integrity_status=IntegrityStatus.COMPLETE,
        )


def test_usage_cost_summary_and_gate_reject_negative_values():
    with pytest.raises(ValidationError, match="input_tokens"):
        RequestUsage(input_tokens=-1)

    with pytest.raises(ValidationError, match="amount"):
        RequestCost(amount=-0.1)

    with pytest.raises(ValidationError, match="finite_number"):
        RequestCost(amount=float("nan"))

    with pytest.raises(ValidationError, match="raw_pass_rate"):
        QualitySummary(
            run_id="run-1",
            case_total=1,
            case_passed=1,
            case_failed=0,
            case_error=0,
            case_skipped=0,
            raw_pass_rate=1.1,
            final_pass_rate=1,
            retry_passed=0,
            request_total=0,
            request_success_rate=0,
            http_5xx_count=0,
            timeout_count=0,
            unknown_failure_count=0,
            integrity_status=IntegrityStatus.COMPLETE,
        )

    with pytest.raises(ValidationError, match="sample_size"):
        GateRuleDecision(
            rule_id="rule",
            rule_version="1",
            target="run",
            actual=0,
            threshold=0,
            sample_size=-1,
            decision=GateResult.NO_DATA,
        )
