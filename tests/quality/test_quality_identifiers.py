from __future__ import annotations

from datetime import UTC, datetime
import uuid

import pytest

from quality.identifiers import (
    build_case_id,
    build_execution_id,
    build_failure_fingerprint,
    build_failure_message_hash,
    build_interface_id,
    build_invocation_id,
    build_param_hash,
    build_run_id,
    build_url_template,
    new_request_event_id,
    normalize_failure_message,
    normalize_nodeid,
)
from quality.models import CasePhase, Protocol


FIXED_TIME = datetime(2026, 7, 30, 8, 0, tzinfo=UTC)
FIXED_UUID = uuid.UUID("12345678-1234-4234-9234-1234567890ab")


def test_build_run_id_is_deterministic_for_jenkins_and_local_runs():
    jenkins_id = build_run_id("API CASE", "123", FIXED_TIME, FIXED_UUID)
    local_id = build_run_id(timestamp=FIXED_TIME, random_uuid=FIXED_UUID)

    assert jenkins_id == "API-CASE-123-20260730T080000Z-12345678"
    assert local_id == "local-20260730T080000Z-12345678"


def test_build_run_id_rejects_partial_jenkins_identity_and_naive_time():
    with pytest.raises(ValueError, match="provided together"):
        build_run_id(job_name="API_CASE", timestamp=FIXED_TIME, random_uuid=FIXED_UUID)

    with pytest.raises(ValueError, match="timezone"):
        build_run_id(
            timestamp=datetime(2026, 7, 30, 8, 0),
            random_uuid=FIXED_UUID,
        )


def test_execution_id_uses_safe_stage_and_positive_index():
    assert build_execution_id("parallel pool", 1) == "parallel-pool-1"

    with pytest.raises(ValueError, match="greater than or equal"):
        build_execution_id("parallel", 0)


def test_nodeid_normalization_is_cross_platform_and_removes_parameters():
    windows = r"module\smoke\test_demo.py::TestDemo::test_case[user-123-token]"
    unix = "module/smoke/test_demo.py::TestDemo::test_case[other-param]"

    windows_result = normalize_nodeid(windows)

    assert windows_result.parameter_id == "user-123-token"
    assert build_case_id(windows) == "module/smoke/test_demo.py::TestDemo::test_case"
    assert build_case_id(unix) == build_case_id(windows)


def test_nodeid_normalization_supports_nested_brackets_in_parameter_id():
    nodeid = "tests/test_demo.py::test_case[payload[value]]"

    normalized = normalize_nodeid(nodeid)

    assert normalized.stable_nodeid == "tests/test_demo.py::test_case"
    assert normalized.parameter_id == "payload[value]"


def test_param_hash_is_stable_ordered_and_redacts_sensitive_values():
    first = build_param_hash({"safe": 1, "api_key": "first-secret"})
    reordered = build_param_hash({"api_key": "second-secret", "safe": 1})
    changed = build_param_hash({"safe": 2, "api_key": "first-secret"})

    assert len(first) == 16
    assert first == reordered
    assert changed != first


def test_invocation_id_is_stable_and_changes_with_each_identity_part():
    first = build_invocation_id("run-1", "case-1", "param-1")

    assert first.startswith("inv-")
    assert len(first) == 28
    assert build_invocation_id("run-1", "case-1", "param-1") == first
    assert build_invocation_id("run-2", "case-1", "param-1") != first
    assert build_invocation_id("run-1", "case-2", "param-1") != first
    assert build_invocation_id("run-1", "case-1", "param-2") != first


def test_request_event_id_is_a_unique_uuid():
    first = new_request_event_id()
    second = new_request_event_id()

    assert uuid.UUID(first)
    assert uuid.UUID(second)
    assert first != second


def test_interface_id_removes_host_query_and_templates_dynamic_segments():
    interface_id = build_interface_id(
        "post",
        "https://host//v1/tasks/123/12345678-1234-4234-9234-1234567890ab/abcdef1234567890?token=secret",
        Protocol.POLLING,
    )

    assert interface_id == "POST /v1/tasks/{id}/{uuid}/{hash} polling"


def test_url_template_uses_same_normalization_as_interface_id():
    path = "https://host/v1/tasks/12345?token=secret"

    assert build_url_template(path) == "/v1/tasks/{id}"
    assert build_interface_id("GET", path) == "GET /v1/tasks/{id} http"


def test_interface_id_preserves_semantic_hyphenated_model_name():
    interface_id = build_interface_id(
        "GET",
        "/v1/models/claude-3-5-sonnet-20241022",
    )

    assert interface_id == "GET /v1/models/claude-3-5-sonnet-20241022 http"


def test_interface_id_templates_semantic_prefix_with_long_dynamic_hex_suffix():
    interface_id = build_interface_id(
        "GET",
        "/v1/media/tasks/not-exist-a3aa746cefb144c980fea17e2728e3d3",
    )

    assert interface_id == "GET /v1/media/tasks/{hash} http"


def test_failure_normalization_removes_dynamic_and_sensitive_values():
    first_message = (
        "2026-07-30T08:00:00Z AssertionError at 0xABCDEF: "
        "request https://host/v1/tasks/123?token=first-secret returned 500 "
        "for id 12345678-1234-4234-9234-1234567890ab"
    )
    second_message = (
        "2026-07-31T09:30:00Z AssertionError at 0x123456: "
        "request https://host/v1/tasks/456?token=second-secret returned 500 "
        "for id 87654321-4321-4321-8321-ba0987654321"
    )

    normalized = normalize_failure_message(first_message)
    first = build_failure_fingerprint(CasePhase.CALL, "AssertionError", first_message)
    second = build_failure_fingerprint("call", "AssertionError", second_message)

    assert "first-secret" not in normalized
    assert "2026-07-30" not in normalized
    assert "0xABCDEF" not in normalized
    assert first == second
    assert first.startswith("fail-call-assertionerror-")
    assert build_failure_message_hash(first_message) == build_failure_message_hash(second_message)


def test_failure_fingerprint_preserves_stable_http_status_semantics():
    status_500 = build_failure_fingerprint("call", "AssertionError", "expected 200 got 500")
    status_404 = build_failure_fingerprint("call", "AssertionError", "expected 200 got 404")

    assert status_500 != status_404
