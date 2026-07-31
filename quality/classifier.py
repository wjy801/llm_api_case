from __future__ import annotations

from dataclasses import dataclass

from quality.identifiers import (
    build_failure_fingerprint,
    build_failure_message_hash,
    normalize_failure_message,
)
from quality.models import (
    CasePhase,
    Confidence,
    FailureCategory,
    FailureFingerprintSource,
    FailureRecord,
    OwnerDomain,
    RequestMetric,
)


CLASSIFIER_RULE_VERSION = "p0-classifier.v1"
FINGERPRINT_VERSION = "p0-fingerprint.v1"


@dataclass(frozen=True)
class FailureEvidence:
    run_id: str
    case_id: str
    invocation_id: str
    phase: CasePhase
    error_type: str | None
    message: str | None
    assert_location: str | None = None
    junit_status: str | None = None
    request_metrics: tuple[RequestMetric, ...] = ()
    related_integrity_codes: tuple[str, ...] = ()


def classify_failure(evidence: FailureEvidence) -> FailureRecord:
    error_type = (evidence.error_type or "UnknownError").strip() or "UnknownError"
    message = evidence.message or error_type
    normalized_message = normalize_failure_message(message) or error_type
    selected_interface_id = _unique_interface_id(evidence.request_metrics)
    category, owner_domain, confidence = _classify(
        evidence,
        error_type=error_type,
        normalized_message=normalized_message,
        selected_interface_id=selected_interface_id,
    )
    failure_id = build_failure_fingerprint(
        evidence.phase,
        error_type,
        normalized_message,
        interface_id=selected_interface_id,
        assert_location=evidence.assert_location,
    )
    fingerprint_source = FailureFingerprintSource(
        phase=evidence.phase,
        error_type=error_type,
        message_hash=build_failure_message_hash(normalized_message),
        interface_id=selected_interface_id,
        assert_location=evidence.assert_location,
    )
    return FailureRecord(
        run_id=evidence.run_id,
        failure_id=failure_id,
        case_id=evidence.case_id,
        invocation_id=evidence.invocation_id,
        phase=evidence.phase,
        category=category,
        owner_domain=owner_domain,
        confidence=confidence,
        error_type=error_type,
        normalized_message=normalized_message,
        fingerprint_source=fingerprint_source,
    )


def unknown_failure(evidence: FailureEvidence, reason: str) -> FailureRecord:
    return classify_failure(
        FailureEvidence(
            run_id=evidence.run_id,
            case_id=evidence.case_id,
            invocation_id=evidence.invocation_id,
            phase=evidence.phase,
            error_type=evidence.error_type or "ClassificationError",
            message=reason,
            assert_location=evidence.assert_location,
            junit_status=evidence.junit_status,
            request_metrics=evidence.request_metrics,
            related_integrity_codes=evidence.related_integrity_codes,
        )
    )


def _classify(
    evidence: FailureEvidence,
    *,
    error_type: str,
    normalized_message: str,
    selected_interface_id: str | None,
) -> tuple[FailureCategory, OwnerDomain, Confidence]:
    text = f"{error_type} {normalized_message}".casefold()
    location = (evidence.assert_location or "").replace("\\", "/").casefold()

    if _matches_configuration(text):
        return FailureCategory.CONFIGURATION, OwnerDomain.CONFIGURATION, Confidence.HIGH
    if location.startswith("quality/") or any(code.startswith("quality_") for code in evidence.related_integrity_codes):
        return FailureCategory.FRAMEWORK_DEFECT, OwnerDomain.FRAMEWORK, Confidence.HIGH
    if _matches_environment(text):
        return FailureCategory.ENVIRONMENT, OwnerDomain.ENVIRONMENT, Confidence.HIGH
    if _matches_test_defect(text, location):
        return FailureCategory.TEST_DEFECT, OwnerDomain.TEST, Confidence.MEDIUM
    if _matches_transient(text, evidence.request_metrics):
        return FailureCategory.TRANSIENT, OwnerDomain.ENVIRONMENT, Confidence.HIGH
    if _matches_product_defect(text, evidence.request_metrics, selected_interface_id):
        return FailureCategory.PRODUCT_DEFECT, OwnerDomain.PRODUCT, Confidence.MEDIUM
    return FailureCategory.UNKNOWN, OwnerDomain.UNKNOWN, Confidence.LOW


def _matches_configuration(text: str) -> bool:
    config_terms = (
        "missing required",
        "environment variable",
        "api key",
        "apikey",
        "credential",
        "permission",
        "authorization",
        "unauthorized",
        "forbidden",
        "settings",
        "validationerror",
    )
    return any(term in text for term in config_terms)


def _matches_environment(text: str) -> bool:
    environment_terms = (
        "dns",
        "name resolution",
        "nameresolutionerror",
        "connection refused",
        "connectionerror",
        "hostunreachable",
        "network unreachable",
        "proxyerror",
        "ssl",
        "certificate",
        "disk full",
        "no space left",
    )
    return any(term in text for term in environment_terms)


def _matches_test_defect(text: str, location: str) -> bool:
    local_error = any(term in text for term in ("keyerror", "indexerror", "typeerror", "valueerror"))
    test_location = location.startswith("module/") or location.startswith("tests/")
    return local_error and test_location and "response" not in text and "status" not in text


def _matches_transient(text: str, request_metrics: tuple[RequestMetric, ...]) -> bool:
    if "rate limit" in text or "too many requests" in text:
        return True
    for metric in request_metrics:
        if metric.status_code == 429:
            return True
        if metric.retryable and (
            metric.timeout
            or (metric.error_type or "").casefold()
            in {"readtimeout", "connecttimeout", "timeout", "connectionreseterror"}
        ):
            return True
    return False


def _matches_product_defect(
    text: str,
    request_metrics: tuple[RequestMetric, ...],
    selected_interface_id: str | None,
) -> bool:
    if selected_interface_id is None:
        return False
    if any(metric.status_code == 429 for metric in request_metrics):
        return False
    contract_terms = (
        "contract",
        "schema",
        "response",
        "status code",
        "expected",
        "got",
        "assert",
    )
    return any(term in text for term in contract_terms)


def _unique_interface_id(request_metrics: tuple[RequestMetric, ...]) -> str | None:
    values = {metric.interface_id for metric in request_metrics if metric.interface_id}
    if len(values) != 1:
        return None
    return next(iter(values))
