from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
import json
from pathlib import Path

import pytest

from quality.flaky import build_transition_id, replay_observations
from quality.flaky_identity import (
    build_flaky_key,
    normalize_execution_profile,
    normalize_flaky_environment,
)
from quality.flaky_models import (
    CasePhase,
    CaseStatus,
    FlakyHistoryEntry,
    FlakyState,
    ObservationOutcome,
)


_FIXTURE_PATH = (
    Path(__file__).parent / "fixtures" / "flaky_stage0_contract" / "replay_cases.json"
)
_CONTRACT = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
_REPLAY_CASES = {
    item["id"]: item for item in _CONTRACT["current_replay_cases"]
}
_FLAKY_KEY = build_flaky_key(
    "module/smoke/test_demo.py::test_case",
    "param-a",
    "overseas",
    "serial",
    1,
)


def _history(case_id: str, signatures: list[str]) -> tuple[FlakyHistoryEntry, ...]:
    started_at = datetime(2026, 8, 1, tzinfo=UTC)
    case_slug = case_id.lower().replace("-", "")
    entries = []
    for index, signature in enumerate(signatures, start=1):
        outcome = (
            ObservationOutcome.PASS
            if signature == "pass"
            else ObservationOutcome.FAIL
        )
        event_time = started_at + timedelta(minutes=index * 30)
        entries.append(
            FlakyHistoryEntry(
                observation_id=f"observation-{case_slug}-{index:02d}",
                run_id=f"run-{case_slug}-{index:02d}",
                invocation_id=f"invocation-{case_slug}-{index:02d}",
                flaky_key=_FLAKY_KEY,
                epoch_scope_key=f"epoch-scope-{case_slug}",
                case_id="module/smoke/test_demo.py::test_case",
                param_hash="param-a",
                environment="overseas",
                execution_profile="serial",
                state_epoch=1,
                decisive_phase=CasePhase.CALL,
                raw_status=(
                    CaseStatus.PASSED
                    if outcome is ObservationOutcome.PASS
                    else CaseStatus.FAILED
                ),
                final_status=(
                    CaseStatus.PASSED
                    if outcome is ObservationOutcome.PASS
                    else CaseStatus.FAILED
                ),
                observation_outcome=outcome,
                failure_id=None if outcome is ObservationOutcome.PASS else signature[5:],
                failure_category=(
                    None
                    if outcome is ObservationOutcome.PASS
                    else "PRODUCT_DEFECT"
                ),
                observed_at=event_time,
                identity_rule_version="flaky-identity.v1",
                environment_rule_version="flaky-environment.v1",
                execution_profile_rule_version="flaky-execution-profile.v1",
                observation_rule_version="flaky-observation.v1",
                fingerprint_version="failure-fingerprint.v1",
                artifact_ref=f"artifact-{case_slug}-{index:02d}",
                source_digest=f"digest-{case_slug}-{index:02d}",
                run_end_time=event_time + timedelta(seconds=20),
                imported_at=event_time + timedelta(seconds=30),
            )
        )
    return tuple(entries)


def _transition_ids(entries: tuple[FlakyHistoryEntry, ...]) -> list[str]:
    projection = replay_observations(entries)
    return [
        build_transition_id(
            flaky_key=_FLAKY_KEY,
            from_state=item.from_state,
            to_state=item.to_state,
            trigger_type="observation",
            reason_code=item.reason_code,
            trigger_observation_id=item.trigger_observation_id,
            rule_version="flaky-state.v1",
            projection_version="flaky-projection.v1",
        )
        for item in projection.transitions
    ]


def test_contract_schema_and_run_kinds_are_frozen():
    assert _CONTRACT["schema_version"] == "flaky-stage0-contract.v1"
    assert _CONTRACT["run_kinds"] == ["NORMAL", "FLAKY_PROBE", "LEGACY_UNKNOWN"]


def test_identity_contract_is_frozen():
    assert _CONTRACT["identity"] == {
        "schema_version": "flaky-identity.v1",
        "fields": [
            "case_id",
            "param_hash",
            "environment",
            "execution_profile",
            "state_epoch",
        ],
        "environments": ["china", "overseas"],
        "standard_execution_profiles": [
            "serial",
            "parallel",
            "manual-serial",
            "manual-parallel",
        ],
        "key_prefix": "flaky-v1-",
    }


def test_comparability_contract_is_frozen():
    assert _CONTRACT["comparability"]["fields"] == [
        "configuration_revision",
        "environment",
        "execution_profile",
        "sut_revision",
        "test_definition_digest",
    ]


def test_policy_revision_is_canonical_and_frozen():
    policy = _CONTRACT["policy"]["value"]
    canonical = json.dumps(
        policy,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    revision = f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"

    assert revision == _CONTRACT["policy"]["revision"]


def test_normal_admission_reason_priority_is_frozen():
    assert _CONTRACT["normal_admission"]["statuses"] == [
        "ELIGIBLE",
        "INELIGIBLE",
    ]
    assert _CONTRACT["normal_admission"]["run_reason_codes"][0] == [
        10,
        "normal_run_kind_mismatch",
    ]
    assert _CONTRACT["normal_admission"]["run_reason_codes"][-1] == [
        1000,
        "normal_eligible",
    ]


def test_probe_classification_contract_is_frozen():
    assert _CONTRACT["probe_evidence"]["classifications"] == [
        "COUNT_PASS",
        "TRUSTED_FAIL",
        "NON_COUNTING",
    ]
    assert _CONTRACT["probe_evidence"]["reason_codes"][-1] == [
        1000,
        "probe_count_pass",
    ]


def test_only_governance_states_authorize_skip():
    contract = _CONTRACT["skip_decision"]
    assert contract["decisions"] == ["RUN", "WOULD_SKIP", "SKIP"]
    assert contract["governance_statuses_authorizing_skip"] == [
        "ACTIVE",
        "RECOVERING",
    ]
    assert "CONFIRMED" in contract["detected_states_not_authorizing_skip"]


@pytest.mark.parametrize("case", _CONTRACT["current_replay_cases"], ids=lambda x: x["id"])
def test_current_replay_contract(case):
    projection = replay_observations(_history(case["id"], case["signatures"]))

    assert projection.current_state is FlakyState(case["expected_state"])
    assert (
        projection.stable_outcome.value if projection.stable_outcome is not None else None
    ) == case["expected_stable_outcome"]
    assert [item.reason_code for item in projection.transitions] == case[
        "expected_transition_reasons"
    ]


def test_replay_is_independent_of_arrival_order():
    entries = _history("R-03", _REPLAY_CASES["R-03"]["signatures"])
    order = next(
        item["input_order"]
        for item in _CONTRACT["future_boundary_cases"]
        if item["id"] == "R-09"
    )
    shuffled = tuple(entries[index - 1] for index in order)

    assert replay_observations(shuffled) == replay_observations(entries)
    assert _transition_ids(shuffled) == _transition_ids(entries)


def test_r03_transition_ids_are_frozen():
    case = _REPLAY_CASES["R-03"]

    assert _transition_ids(_history("R-03", case["signatures"])) == case[
        "expected_transition_ids"
    ]


def test_public_identity_entry_matches_importer_compatibility_aliases():
    from quality import flaky_importer
    from quality import flaky_identity

    assert flaky_importer.build_flaky_key is flaky_identity.build_flaky_key
    assert flaky_importer.build_epoch_scope_key is flaky_identity.build_epoch_scope_key


def test_identity_normalization_and_key_are_stable():
    assert normalize_flaky_environment(" China ") == "china"
    assert normalize_execution_profile("parallel-pool", "gw17") == "parallel"
    assert _FLAKY_KEY == (
        "flaky-v1-006c2593dd6b88e814e85f0502cc6ffb9182cdd601846add6a4d713802262bc7"
    )


def test_stage3_adds_v3_and_v4_migrations_after_frozen_stage0_assets():
    migration_names = sorted(
        path.name
        for path in (
            Path(__file__).parents[2] / "quality" / "flaky_store" / "migrations"
        ).glob("*.sql")
    )

    assert migration_names == [
        "0001_observation_store.sql",
        "0002_flaky_state_machine.sql",
        "0003_v3_state_machine.sql",
        "0004_probe_dispatch.sql",
    ]


def test_stage0_does_not_add_governance_skip_to_pytest_plugins():
    quality_root = Path(__file__).parents[2] / "quality"
    plugin_source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in quality_root.glob("*pytest*.py")
    )

    assert "FLAKY_QUARANTINED" not in plugin_source
    assert "flaky-skip-decisions" not in plugin_source


def test_future_boundary_case_ids_are_complete():
    assert {item["id"] for item in _CONTRACT["future_boundary_cases"]} == {
        "R-07",
        "R-08",
        "R-09",
        "R-10",
    }
