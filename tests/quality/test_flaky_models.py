from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from quality.flaky_importer import (
    build_epoch_scope_key,
    build_flaky_key,
    normalize_execution_profile,
)
from quality.flaky_models import (
    CaseObservation,
    CaseObservationCandidate,
    EpochResetRequest,
    FlakyImportRequest,
    ObservationOutcome,
)
from quality.models import CasePhase, CaseStatus


def _candidate(**updates):
    values = {
        "run_id": "run-1",
        "invocation_id": "inv-1",
        "case_id": "module/test_demo.py::test_case",
        "param_hash": "param-a",
        "environment": "overseas",
        "execution_profile": "serial",
        "decisive_phase": CasePhase.CALL,
        "raw_status": CaseStatus.PASSED,
        "final_status": CaseStatus.PASSED,
        "observation_outcome": ObservationOutcome.PASS,
        "observed_at": datetime(2026, 8, 1, tzinfo=UTC),
        "fingerprint_version": "failure-fingerprint.v1",
    }
    values.update(updates)
    return CaseObservationCandidate(**values)


def test_flaky_key_is_stable_and_separates_comparison_dimensions():
    base = build_flaky_key("case", "param", "overseas", "serial", 1)

    assert base == build_flaky_key("case", "param", "overseas", "serial", 1)
    assert base != build_flaky_key("case", "other", "overseas", "serial", 1)
    assert base != build_flaky_key("case", "param", "china", "serial", 1)
    assert base != build_flaky_key("case", "param", "overseas", "parallel", 1)
    assert base != build_flaky_key("case", "param", "overseas", "serial", 2)
    assert base.startswith("flaky-v1-")


def test_epoch_scope_does_not_include_param_hash():
    assert build_epoch_scope_key("case", "china", "serial") == build_epoch_scope_key(
        "case", "china", "serial"
    )


def test_worker_number_does_not_split_parallel_profile():
    assert normalize_execution_profile("parallel-pool", "gw0") == "parallel"
    assert normalize_execution_profile("parallel-pool", "gw17") == "parallel"
    assert normalize_execution_profile("manual-pytest", "gw0") == "manual-parallel"
    assert normalize_execution_profile("manual-pytest", "gw17") == "manual-parallel"


def test_custom_execution_profile_is_sanitized_and_bounded():
    profile = normalize_execution_profile("Release Candidate / " + "x" * 100, "master")

    assert profile.startswith("custom:release-candidate-")
    assert len(profile.removeprefix("custom:")) <= 64


def test_fail_candidate_requires_failure_id():
    with pytest.raises(ValidationError, match="failure_id"):
        _candidate(
            raw_status=CaseStatus.FAILED,
            final_status=CaseStatus.FAILED,
            observation_outcome=ObservationOutcome.FAIL,
        )


def test_pass_candidate_rejects_failure_id():
    with pytest.raises(ValidationError, match="failure_id"):
        _candidate(failure_id="fail-1")


def test_epoch_reset_requires_actor_and_reason():
    with pytest.raises(ValidationError):
        EpochResetRequest(
            case_id="case",
            environment="overseas",
            execution_profile="serial",
            actor="",
            reason="changed",
        )


def test_automatic_import_requires_absolute_database_path():
    with pytest.raises(ValidationError, match="absolute"):
        FlakyImportRequest(
            run_id="run-1",
            quality_output_dir=Path("reports/quality"),
            database_path=Path("flaky.sqlite3"),
        )


def test_observation_contract_has_no_cost_or_interface_metric_fields():
    forbidden = {
        "amount",
        "currency",
        "duration_ms",
        "input_tokens",
        "output_tokens",
        "p95",
        "request_body",
        "response_body",
    }

    assert forbidden.isdisjoint(CaseObservation.model_fields)
