from datetime import UTC, datetime, timedelta

import pytest

from quality.flaky_importer import import_flaky_history
from quality.flaky_models import (
    FlakyImportRequest,
    FlakyManualActionRequest,
    FlakyQuarantineRequest,
    FlakyState,
    GovernanceResolution,
    GovernanceStatus,
)
from quality.flaky_store import FlakyStore, FlakyStoreError


def _evaluate(factory, store, database, run_id, outcome):
    artifacts = factory(run_id=run_id, outcome=outcome)
    imported = import_flaky_history(
        FlakyImportRequest(
            run_id=run_id,
            quality_output_dir=artifacts.output_dir,
            database_path=database,
        )
    )
    assert imported.inserted_count == 1
    return store.evaluate_run(run_id)


def _suspected(factory, database):
    store = FlakyStore(database)
    _evaluate(factory, store, database, "run-1", "pass")
    _evaluate(factory, store, database, "run-2", "fail")
    return store, store.states(case_id="module/test_demo.py::test_case")[0]


def _confirmed(factory, database):
    store, state = _suspected(factory, database)
    state = store.confirm_flaky(
        FlakyManualActionRequest(
            flaky_key=state.flaky_key,
            actor="reviewer",
            reason="confirmed by trusted history",
        )
    )
    return store, state


def test_mark_not_flaky_keeps_evidence_and_establishes_new_anchor(
    p0_artifact_factory,
    tmp_path,
):
    database = tmp_path / "history.sqlite3"
    store, suspected = _suspected(p0_artifact_factory, database)

    stable = store.mark_not_flaky(
        FlakyManualActionRequest(
            flaky_key=suspected.flaky_key,
            actor="reviewer",
            reason="failure was caused by a controlled deployment",
        )
    )
    history_count = len(store.history(case_id=stable.case_id))
    _evaluate(p0_artifact_factory, store, database, "run-3", "fail")
    after = store.states(case_id=stable.case_id)[0]

    assert stable.current_state is FlakyState.STABLE
    assert stable.evaluation_anchor_observation_id == stable.latest_observation_id
    assert history_count == 2
    assert after.current_state is FlakyState.STABLE
    assert len(store.history(case_id=stable.case_id)) == 3


def test_quarantine_cancel_closes_governance_without_declaring_stable(
    p0_artifact_factory,
    tmp_path,
):
    database = tmp_path / "history.sqlite3"
    store, confirmed = _confirmed(p0_artifact_factory, database)
    expiry = datetime.now(UTC) + timedelta(hours=1)
    governance = store.quarantine(
        FlakyQuarantineRequest(
            flaky_key=confirmed.flaky_key,
            owner="owner",
            actor="reviewer",
            reason="investigate nondeterminism",
            expires_at=expiry,
        )
    )

    overdue = store.governance(
        overdue=True,
        query_time=expiry + timedelta(seconds=1),
    )
    cancelled = store.cancel_quarantine(
        FlakyManualActionRequest(
            flaky_key=confirmed.flaky_key,
            actor="reviewer",
            reason="quarantine command targeted the wrong deployment",
        )
    )
    closed = store.governance(status=GovernanceStatus.CLOSED)[0]

    assert overdue[0].governance_id == governance.governance_id
    assert cancelled.current_state is FlakyState.CONFIRMED
    assert closed.resolution is GovernanceResolution.CANCELLED


def test_recovery_signature_change_regresses_and_requires_new_quarantine(
    p0_artifact_factory,
    tmp_path,
):
    database = tmp_path / "history.sqlite3"
    store, confirmed = _confirmed(p0_artifact_factory, database)
    store.quarantine(
        FlakyQuarantineRequest(
            flaky_key=confirmed.flaky_key,
            owner="owner",
            actor="reviewer",
            reason="investigate nondeterminism",
            expires_at=datetime.now(UTC) + timedelta(days=1),
        )
    )
    store.start_recovery(
        FlakyManualActionRequest(
            flaky_key=confirmed.flaky_key,
            actor="owner",
            reason="candidate fix deployed",
        )
    )

    _evaluate(p0_artifact_factory, store, database, "run-3", "pass")
    _evaluate(p0_artifact_factory, store, database, "run-4", "fail")

    state = store.states(case_id=confirmed.case_id)[0]
    closed = store.governance(status=GovernanceStatus.CLOSED)[0]
    assert state.current_state is FlakyState.CONFIRMED
    assert closed.resolution is GovernanceResolution.REGRESSED
    with pytest.raises(FlakyStoreError) as captured:
        store.start_recovery(
            FlakyManualActionRequest(
                flaky_key=confirmed.flaky_key,
                actor="owner",
                reason="invalid second recovery",
            )
        )
    assert captured.value.code == "invalid_state_transition"
