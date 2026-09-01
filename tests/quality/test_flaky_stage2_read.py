from __future__ import annotations

from datetime import UTC, datetime
import json
import sqlite3

import pytest

from quality.cli import main
from quality.flaky_read import FlakyReadService
from quality.flaky_store import FlakyStoreError, migrate_store


NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


def _database(tmp_path):
    database = (tmp_path / "flaky.sqlite3").resolve()
    migrate_store(database)
    with sqlite3.connect(database) as connection:
        for index, (owner, case_id, status, expires) in enumerate(
            (
                ("owner%literal", "module/smoke/test_a.py::test_case", "ACTIVE", "2026-08-31T00:00:00+00:00"),
                ("owner-b", "module/smoke_extra/test_b.py::test_case", "CLOSED", "2026-10-01T00:00:00+00:00"),
                ("owner-c", "module/smoke/test_c.py::test_case", "RECOVERING", "2026-10-01T00:00:00+00:00"),
            ),
            start=1,
        ):
            key = f"flaky-{index}"
            created = f"2026-08-0{index}T00:00:00+00:00"
            connection.execute(
                """INSERT INTO flaky_identity (
                       flaky_key, epoch_scope_key, case_id, param_hash,
                       environment, execution_profile, state_epoch,
                       current_detection_generation, created_at, updated_at
                   ) VALUES (?, ?, ?, ?, 'overseas', 'serial', 1, 1, ?, ?)""",
                (key, f"scope-{index}", case_id, f"param-{index}", created, created),
            )
            connection.execute(
                """INSERT INTO flaky_governance (
                       governance_id, flaky_key, status, owner, reason,
                       created_by, created_at, expires_at,
                       recovery_started_by, recovery_started_at, recovery_reason,
                       closed_at, closed_by, close_reason, resolution
                   ) VALUES (?, ?, ?, ?, 'reason', 'actor', ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    f"governance-{index}",
                    key,
                    status,
                    owner,
                    created,
                    expires,
                    "actor" if status == "RECOVERING" else None,
                    created if status == "RECOVERING" else None,
                    "recover" if status == "RECOVERING" else None,
                    created if status == "CLOSED" else None,
                    "actor" if status == "CLOSED" else None,
                    "closed" if status == "CLOSED" else None,
                    "recovered" if status == "CLOSED" else None,
                ),
            )
        connection.execute(
            """INSERT INTO flaky_governance_event (
                   event_id, governance_id, event_type, causal_id,
                   to_status, actor, reason, created_at
               ) VALUES (
                   'event-1', 'governance-1', 'quarantined', 'cause-1',
                   'ACTIVE', 'actor', 'reason', '2026-08-01T00:01:00+00:00'
            )"""
        )
        connection.execute(
            """INSERT INTO flaky_detection_projection (
                   flaky_key, detection_generation, comparability_fingerprint,
                   detection_state, sample_size, pass_count, fail_count,
                   outcome_switch_count, signature_switch_count,
                   distinct_failure_fingerprint_count,
                   trailing_same_signature_count, stable_outcome,
                   latest_observation_id, rule_version, created_at, updated_at
               ) VALUES (
                   'flaky-1', 1, 'fingerprint-1', 'STABLE', 3, 3, 0,
                   0, 0, 0, 3, 'pass', 'observation-1',
                   'flaky-detection.v1',
                   '2026-08-01T00:00:00+00:00',
                   '2026-08-01T00:00:00+00:00'
               )"""
        )
    return database


def test_read_service_summary_pagination_filters_and_same_data_as_of(tmp_path):
    service = FlakyReadService(_database(tmp_path), clock=lambda: NOW)

    summary = service.summary()
    first = service.governance_page(page_size=1)
    second = service.governance_page(page_size=1, cursor=first.next_cursor)
    wildcard = service.governance_page(keyword="owner%literal")
    overdue = service.governance_page(overdue=True)
    scoped = service.governance_page(case_path="module/smoke")

    assert summary.database_health == "OK"
    assert summary.database_schema_version == 4
    assert summary.mode_requested == summary.mode_effective == "off"
    assert summary.governance_counts == {"ACTIVE": 1, "CLOSED": 1, "RECOVERING": 1}
    assert summary.overdue_count == 1
    assert first.data_as_of == NOW
    assert first.items[0].governance_id == "governance-1"
    assert first.items[0].detection_projections[0].stable_outcome == "pass"
    assert first.next_cursor is not None
    assert second.items[0].governance_id == "governance-2"
    assert [item.governance_id for item in wildcard.items] == ["governance-1"]
    assert [item.governance_id for item in overdue.items] == ["governance-1"]
    assert [item.governance_id for item in scoped.items] == [
        "governance-1",
        "governance-3",
    ]


@pytest.mark.parametrize(
    "requested,effective",
    (("shadow", "shadow"), ("enforce", "off"), ("unsafe", "off")),
)
def test_summary_cli_uses_runtime_requested_and_effective_mode(
    tmp_path,
    monkeypatch,
    capsys,
    requested,
    effective,
):
    database = _database(tmp_path)
    monkeypatch.setenv("QUALITY_FLAKY_AUTO_SKIP_ENABLE", "1")
    monkeypatch.setenv("QUALITY_FLAKY_SKIP_MODE", requested)

    assert main(["flaky-dashboard-summary", "--db", str(database)]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["mode_requested"] == requested
    assert payload["mode_effective"] == effective


def test_read_service_detail_timeline_and_snapshot_candidates(tmp_path):
    service = FlakyReadService(_database(tmp_path), clock=lambda: NOW)

    detail = service.case_detail("flaky-1")
    source = service.snapshot_source()

    assert detail.identity["case_id"] == "module/smoke/test_a.py::test_case"
    assert [item.event_id for item in detail.timeline] == ["event-1"]
    assert source.database_schema_version == 4
    assert [item.flaky_key for item in source.candidates] == ["flaky-1", "flaky-3"]


def test_read_service_rejects_unbounded_and_invalid_queries(tmp_path):
    service = FlakyReadService(_database(tmp_path), clock=lambda: NOW)

    for page_size in (0, 101):
        with pytest.raises(FlakyStoreError) as captured:
            service.governance_page(page_size=page_size)
        assert captured.value.code == "invalid_page_size"
    with pytest.raises(FlakyStoreError) as captured:
        service.governance_page(keyword="x" * 129)
    assert captured.value.code == "invalid_query"
    with pytest.raises(FlakyStoreError) as captured:
        service.governance_page(cursor="not-a-cursor")
    assert captured.value.code == "invalid_cursor"
