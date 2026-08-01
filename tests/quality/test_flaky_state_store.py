from datetime import UTC, datetime, timedelta
import sqlite3

import pytest

from quality.flaky_importer import import_flaky_history, reset_flaky_epoch
from quality.flaky_models import (
    EpochResetRequest,
    FlakyImportRequest,
    FlakyManualActionRequest,
    FlakyQuarantineRequest,
    FlakyState,
    GovernanceResolution,
    GovernanceStatus,
)
from quality.flaky_store import FlakyStore, FlakyStoreError


def _import_and_evaluate(factory, database, run_id, outcome="pass"):
    artifacts = factory(run_id=run_id, outcome=outcome)
    imported = import_flaky_history(
        FlakyImportRequest(
            run_id=run_id,
            quality_output_dir=artifacts.output_dir,
            database_path=database,
        )
    )
    assert imported.inserted_count == 1
    return FlakyStore(database).evaluate_run(run_id)


def _state(store):
    return store.states(case_id="module/test_demo.py::test_case")[0]


def test_run_evaluation_bootstraps_and_advances_one_projection(
    p0_artifact_factory,
    tmp_path,
):
    database = tmp_path / "history.sqlite3"
    first = _import_and_evaluate(p0_artifact_factory, database, "run-1")
    second = _import_and_evaluate(p0_artifact_factory, database, "run-2")
    third = _import_and_evaluate(p0_artifact_factory, database, "run-3")
    store = FlakyStore(database)
    state = _state(store)
    check = store.check_database()

    assert first.transitioned_count == 1
    assert second.transitioned_count == 0
    assert third.transitioned_count == 1
    assert state.current_state is FlakyState.STABLE
    assert state.sample_size == 3
    assert check.missing_projection_count == 0
    assert check.stale_projection_count == 0
    assert check.state_count == 1
    assert check.transition_count == 2


def test_evaluation_is_idempotent_for_the_same_run(
    p0_artifact_factory,
    tmp_path,
):
    database = tmp_path / "history.sqlite3"
    _import_and_evaluate(p0_artifact_factory, database, "run-1")
    store = FlakyStore(database)

    repeated = store.evaluate_run("run-1")
    check = store.check_database()

    assert repeated.transitioned_count == 0
    assert repeated.status.value == "NOOP"
    assert check.transition_count == 1


def test_manual_governance_recovery_closes_only_after_five_new_observations(
    p0_artifact_factory,
    tmp_path,
):
    database = tmp_path / "history.sqlite3"
    _import_and_evaluate(p0_artifact_factory, database, "run-1", "pass")
    _import_and_evaluate(p0_artifact_factory, database, "run-2", "fail")
    store = FlakyStore(database)
    suspected = _state(store)
    assert suspected.current_state is FlakyState.SUSPECTED

    confirmed = store.confirm_flaky(
        FlakyManualActionRequest(
            flaky_key=suspected.flaky_key,
            actor="reviewer",
            reason="pass/fail changed across trusted runs",
        )
    )
    assert confirmed.current_state is FlakyState.CONFIRMED
    governance = store.quarantine(
        FlakyQuarantineRequest(
            flaky_key=suspected.flaky_key,
            owner="case-owner",
            actor="reviewer",
            reason="isolate while the cause is fixed",
            expires_at=datetime.now(UTC) + timedelta(days=7),
        )
    )
    assert governance.status is GovernanceStatus.ACTIVE
    recovering = store.start_recovery(
        FlakyManualActionRequest(
            flaky_key=suspected.flaky_key,
            actor="case-owner",
            reason="fix has been deployed",
        )
    )
    assert recovering.status is GovernanceStatus.RECOVERING

    for index in range(3, 8):
        result = _import_and_evaluate(
            p0_artifact_factory,
            database,
            f"run-{index}",
            "pass",
        )

    assert result.recovered
    recovered_state = _state(store)
    assert recovered_state.current_state is FlakyState.STABLE
    assert (
        recovered_state.evaluation_anchor_observation_id
        == recovered_state.latest_observation_id
    )
    closed = store.governance(status=GovernanceStatus.CLOSED)[0]
    assert closed.resolution is GovernanceResolution.RECOVERED


def test_epoch_reset_is_blocked_during_open_governance(
    p0_artifact_factory,
    tmp_path,
):
    database = tmp_path / "history.sqlite3"
    _import_and_evaluate(p0_artifact_factory, database, "run-1", "pass")
    _import_and_evaluate(p0_artifact_factory, database, "run-2", "fail")
    store = FlakyStore(database)
    state = store.confirm_flaky(
        FlakyManualActionRequest(
            flaky_key=_state(store).flaky_key,
            actor="reviewer",
            reason="confirmed manually",
        )
    )
    store.quarantine(
        FlakyQuarantineRequest(
            flaky_key=state.flaky_key,
            owner="owner",
            actor="reviewer",
            reason="active incident",
            expires_at=datetime.now(UTC) + timedelta(days=1),
        )
    )

    with pytest.raises(FlakyStoreError) as captured:
        reset_flaky_epoch(
            database,
            EpochResetRequest(
                case_id=state.case_id,
                environment=state.environment,
                execution_profile=state.execution_profile,
                actor="owner",
                reason="attempted semantic reset",
            ),
        )

    assert captured.value.code == "active_governance_exists"


def test_late_observation_reprojects_in_fixed_order_with_audited_transition(
    p0_artifact_factory,
    tmp_path,
):
    database = tmp_path / "history.sqlite3"
    for run_id in ("run-2", "run-3", "run-4"):
        _import_and_evaluate(p0_artifact_factory, database, run_id, "pass")
    store = FlakyStore(database)
    assert _state(store).current_state is FlakyState.STABLE

    _import_and_evaluate(p0_artifact_factory, database, "run-1", "fail")

    assert _state(store).current_state is FlakyState.SUSPECTED
    with sqlite3.connect(database) as connection:
        transition = connection.execute(
            """
            SELECT trigger_type, reason_code, trigger_observation_id
            FROM flaky_transition
            ORDER BY created_at DESC, transition_id DESC
            LIMIT 1
            """
        ).fetchone()
        trigger_run = connection.execute(
            "SELECT run_id FROM case_observation WHERE observation_id = ?",
            (transition[2],),
        ).fetchone()[0]
    assert transition[:2] == ("reprojection", "late_observation_reprojection")
    assert trigger_run == "run-1"


def test_projection_failure_never_rolls_back_committed_observation(
    p0_artifact_factory,
    tmp_path,
):
    database = tmp_path / "history.sqlite3"
    _import_and_evaluate(p0_artifact_factory, database, "run-1", "pass")
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE flaky_state SET rule_version = 'flaky-state.legacy'"
        )
        connection.commit()
    artifacts = p0_artifact_factory(run_id="run-2", outcome="pass")
    imported = import_flaky_history(
        FlakyImportRequest(
            run_id="run-2",
            quality_output_dir=artifacts.output_dir,
            database_path=database,
        )
    )
    assert imported.inserted_count == 1

    with pytest.raises(FlakyStoreError) as captured:
        FlakyStore(database).evaluate_run("run-2")

    assert captured.value.code == "incompatible_projection_version"
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM case_observation").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM flaky_transition").fetchone()[0] == 1


def test_state_evaluation_db_busy_is_safe_and_leaves_projection_missing(
    p0_artifact_factory,
    tmp_path,
):
    database = tmp_path / "history.sqlite3"
    artifacts = p0_artifact_factory(run_id="run-1", outcome="pass")
    imported = import_flaky_history(
        FlakyImportRequest(
            run_id="run-1",
            quality_output_dir=artifacts.output_dir,
            database_path=database,
        )
    )
    assert imported.inserted_count == 1
    locker = sqlite3.connect(database, isolation_level=None)
    locker.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(FlakyStoreError) as captured:
            FlakyStore(database, busy_timeout_ms=10).evaluate_run("run-1")
    finally:
        locker.execute("ROLLBACK")
        locker.close()

    assert captured.value.code == "db_busy"
    check = FlakyStore(database).check_database()
    assert check.observation_count == 1
    assert check.state_count == 0
    assert check.missing_projection_count == 1
