from __future__ import annotations

from datetime import UTC, datetime

from quality.classifier import FailureEvidence, classify_failure
from quality.models import (
    BusinessStatus,
    CasePhase,
    FailureCategory,
    OwnerDomain,
    Protocol,
    RequestMetric,
)


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
        "status_code": 500,
        "business_status": BusinessStatus.FAILED,
        "duration_ms": 10,
    }
    values.update(overrides)
    return RequestMetric(**values)


def _evidence(**overrides):
    values = {
        "run_id": "run-1",
        "case_id": "module/test_demo.py::test_case",
        "invocation_id": "inv-1",
        "phase": CasePhase.CALL,
        "error_type": "AssertionError",
        "message": "expected status code 200 got 500",
        "assert_location": "module/test_demo.py:10",
        "request_metrics": (_request(),),
    }
    values.update(overrides)
    return FailureEvidence(**values)


def test_classifier_marks_clear_configuration_error():
    failure = classify_failure(_evidence(error_type="ValidationError", message="missing required API key"))

    assert failure.category is FailureCategory.CONFIGURATION
    assert failure.owner_domain is OwnerDomain.CONFIGURATION


def test_classifier_keeps_ambiguous_assertion_unknown_without_request_evidence():
    failure = classify_failure(_evidence(message="assert False", request_metrics=()))

    assert failure.category is FailureCategory.UNKNOWN
    assert failure.owner_domain is OwnerDomain.UNKNOWN


def test_classifier_marks_rate_limit_as_transient():
    failure = classify_failure(
        _evidence(
            message="rate limit exceeded",
            request_metrics=(_request(status_code=429, retryable=True),),
        )
    )

    assert failure.category is FailureCategory.TRANSIENT
    assert failure.owner_domain is OwnerDomain.ENVIRONMENT


def test_classifier_fingerprint_is_stable_across_dynamic_values():
    first = classify_failure(_evidence(message=f"{datetime.now(UTC).isoformat()} expected 200 got 500 token=one"))
    second = classify_failure(_evidence(message="2026-07-31T00:00:00Z expected 200 got 500 token=two"))

    assert first.failure_id == second.failure_id
    assert "token=one" not in first.normalized_message
