from datetime import UTC, datetime, timedelta

from quality.flaky import derive_evidence_window, evaluate_recovery, replay_observations
from quality.flaky_models import (
    CasePhase,
    CaseStatus,
    FlakyHistoryEntry,
    FlakyState,
    ObservationOutcome,
)


def _history(*signatures: str) -> tuple[FlakyHistoryEntry, ...]:
    started = datetime(2026, 8, 1, tzinfo=UTC)
    entries = []
    for index, signature in enumerate(signatures):
        outcome = ObservationOutcome.PASS if signature == "pass" else ObservationOutcome.FAIL
        failure_id = None if outcome is ObservationOutcome.PASS else signature.split(":", 1)[1]
        entries.append(
            FlakyHistoryEntry(
                observation_id=f"observation-{index:02d}",
                run_id=f"run-{index:02d}",
                invocation_id=f"invocation-{index:02d}",
                flaky_key="flaky-key",
                epoch_scope_key="epoch-scope",
                case_id="module/test_demo.py::test_case",
                param_hash="param-hash",
                environment="overseas",
                execution_profile="serial",
                state_epoch=1,
                decisive_phase=CasePhase.CALL,
                raw_status=(CaseStatus.PASSED if outcome is ObservationOutcome.PASS else CaseStatus.FAILED),
                final_status=(CaseStatus.PASSED if outcome is ObservationOutcome.PASS else CaseStatus.FAILED),
                observation_outcome=outcome,
                failure_id=failure_id,
                failure_category=None,
                observed_at=started + timedelta(minutes=index),
                identity_rule_version="flaky-identity.v1",
                environment_rule_version="flaky-environment.v1",
                execution_profile_rule_version="flaky-execution-profile.v1",
                observation_rule_version="flaky-observation.v1",
                fingerprint_version="failure-fingerprint.v1",
                artifact_ref=f"artifact-{index}",
                source_digest=f"digest-{index}",
                run_end_time=started + timedelta(minutes=index, seconds=30),
                imported_at=started + timedelta(minutes=index, seconds=40),
            )
        )
    return tuple(entries)


def test_observing_and_stable_require_three_consistent_signatures():
    assert replay_observations(_history("pass")).current_state is FlakyState.OBSERVING
    assert replay_observations(_history("pass", "pass")).current_state is FlakyState.OBSERVING
    assert replay_observations(_history("pass", "pass", "pass")).current_state is FlakyState.STABLE
    stable_fail = replay_observations(_history("fail:A", "fail:A", "fail:A"))
    assert stable_fail.current_state is FlakyState.STABLE
    assert stable_fail.stable_failure_id == "A"


def test_first_signature_change_is_only_suspected():
    assert replay_observations(_history("pass", "fail:A")).current_state is FlakyState.SUSPECTED
    assert replay_observations(_history("fail:A", "fail:B")).current_state is FlakyState.SUSPECTED


def test_repeated_pass_fail_switches_confirm_but_all_fail_does_not():
    confirmed = replay_observations(_history("pass", "fail:A", "fail:A", "pass"))
    assert confirmed.current_state is FlakyState.CONFIRMED
    all_fail = replay_observations(_history("fail:A", "fail:B", "fail:A", "fail:B"))
    assert all_fail.current_state is FlakyState.SUSPECTED


def test_suspected_clears_after_five_signatures_but_confirmed_is_sticky():
    cleared = replay_observations(_history("pass", "fail:A", *("pass",) * 5))
    assert cleared.current_state is FlakyState.STABLE
    sticky = replay_observations(
        _history("pass", "fail:A", "fail:A", "pass", *("pass",) * 5)
    )
    assert sticky.current_state is FlakyState.CONFIRMED


def test_recovery_requires_five_consistent_post_anchor_observations():
    pending = evaluate_recovery(_history(*( "pass",) * 4))
    assert pending.target_state is None
    recovered = evaluate_recovery(_history(*( "pass",) * 5))
    assert recovered.target_state is FlakyState.STABLE
    regressed = evaluate_recovery(_history("pass", "fail:A"))
    assert regressed.target_state is FlakyState.CONFIRMED


def test_evidence_window_is_capped_at_twenty_real_observations():
    evidence = derive_evidence_window(_history(*( "pass",) * 25))
    assert evidence.total_observation_count == 25
    assert evidence.sample_size == 20
    assert len(evidence.observation_ids) == 20
