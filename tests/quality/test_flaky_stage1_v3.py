from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
import json
import sqlite3

import pytest

from quality.flaky_store import (
    MIGRATIONS_DIRECTORY,
    FlakyStore,
    FlakyStoreError,
    migrate_store,
)
from quality.flaky_store.migration import validate_store_schema
from quality.flaky_store.repository import FlakyRepository
from quality.flaky_store.v3_service import (
    FlakyV3Service,
    NormalCaseEvidence,
    NormalImportRequest,
    ProbeImportRequest,
    RecoveryCancelRequest,
    RecoveryCloseRequest,
    RecoveryStartRequest,
)
from quality.cli import main
from quality.classifier import FINGERPRINT_VERSION
from quality.flaky_v3 import (
    ComparabilityFacts,
    DEFAULT_GOVERNANCE_POLICY,
    NormalCaseAdmissionFacts,
    NormalRunAdmissionFacts,
    ProbeOutcome,
)
from quality.models import IntegrityStatus, RunKind, RunRecord, RunStatus


SHA_A = "a" * 40
SHA_B = "b" * 40


def test_runtime_requires_explicit_migration_and_migrate_is_idempotent(tmp_path):
    database = (tmp_path / "flaky.sqlite3").resolve()
    service = FlakyV3Service(database)

    with pytest.raises(FlakyStoreError) as captured:
        service.check_invariants()
    assert captured.value.code == "database_not_found"

    first = migrate_store(database)
    second = migrate_store(database)

    assert first.previous_schema_version == 0
    assert first.schema_version == 4
    assert first.migration_applied is True
    assert first.backup_path is not None and first.backup_path.is_file()
    assert second.previous_schema_version == 4
    assert second.migration_applied is False
    assert second.backup_path is None
    assert service.check_invariants()["status"] == "OK"


def test_runtime_validation_refuses_pending_schema_without_applying_it(tmp_path):
    database = (tmp_path / "pending.sqlite3").resolve()
    database.touch()
    repository = FlakyRepository(database, busy_timeout_ms=100)

    with repository.connection(require_existing=True) as connection:
        with pytest.raises(FlakyStoreError) as captured:
            validate_store_schema(connection, repository, MIGRATIONS_DIRECTORY)

    assert captured.value.code == "schema_migration_required"
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall() == []


def test_normal_cohorts_are_isolated_and_ineligible_case_is_audit_only(tmp_path):
    database = (tmp_path / "normal.sqlite3").resolve()
    migrate_store(database)
    service = FlakyV3Service(database)
    start = datetime(2026, 9, 1, 1, 0, tzinfo=UTC)

    first = _normal_request("normal-1", start, configuration_revision="config-a")
    second = _normal_request(
        "normal-2", start + timedelta(minutes=1), configuration_revision="config-b"
    )
    rejected = _normal_request(
        "normal-3",
        start + timedelta(minutes=2),
        configuration_revision="config-a",
        case_facts=NormalCaseAdmissionFacts(
            lifecycle_valid=True,
            infrastructure_failure=True,
            outcome="fail",
        ),
    )

    assert service.import_normal(first, now=start)["observation_count"] == 1
    assert service.import_normal(second, now=start + timedelta(minutes=1))[
        "observation_count"
    ] == 1
    assert service.import_normal(rejected, now=start + timedelta(minutes=2))[
        "observation_count"
    ] == 0

    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        projections = connection.execute(
            "SELECT detection_state, sample_size FROM flaky_detection_projection"
        ).fetchall()
        assert [(row["detection_state"], row["sample_size"]) for row in projections] == [
            ("OBSERVING", 1),
            ("OBSERVING", 1),
        ]
        assert connection.execute(
            "SELECT COUNT(*) FROM flaky_normal_observation"
        ).fetchone()[0] == 2
        admission = connection.execute(
            """SELECT primary_reason_code FROM flaky_evidence_admission
               WHERE run_id = 'normal-3' AND scope = 'CASE'"""
        ).fetchone()[0]
        assert admission == "case_infrastructure_failure"


def test_probe_run_cannot_be_written_through_normal_import(tmp_path):
    database = (tmp_path / "normal-probe-isolation.sqlite3").resolve()
    migrate_store(database)
    service = FlakyV3Service(database)
    started = datetime(2026, 9, 1, 1, 0, tzinfo=UTC)
    normal = _normal_request("normal-shaped-probe", started)
    probe_run = _run(
        "normal-shaped-probe",
        started,
        RunKind.FLAKY_PROBE,
        attempt_id="attempt-untrusted",
        trigger_id="trigger-untrusted",
        plan_digest="plan-untrusted",
        round_no=1,
    )
    request = NormalImportRequest(
        run=probe_run,
        manifest=_manifest(probe_run),
        source_digest=_digest(probe_run.run_id),
        admission_facts=normal.admission_facts,
        cases=normal.cases,
    )

    result = service.import_normal(request, now=started)

    assert result["observation_count"] == 0
    assert result["run_admission"]["primary_reason_code"] == (
        "normal_run_kind_mismatch"
    )
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM flaky_normal_observation"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM flaky_probe_evidence"
        ).fetchone()[0] == 0


def test_probe_attempt_isolated_from_normal_and_requires_manual_close(tmp_path):
    database = (tmp_path / "probe.sqlite3").resolve()
    migrate_store(database)
    service = FlakyV3Service(database)
    started = datetime(2026, 9, 1, 2, 0, tzinfo=UTC)
    normal = _normal_request("normal-seed", started)
    service.import_normal(normal, now=started)
    flaky_key = _flaky_key(database)
    service.quarantine(
        flaky_key=flaky_key,
        owner="quality-owner",
        actor="operator",
        reason="confirmed flaky",
        request_id="quarantine-request",
        expires_at=started + timedelta(days=10),
        now=started,
    )
    recovery = service.recovery_start(
        RecoveryStartRequest(
            flaky_key=flaky_key,
            target_commit_sha=SHA_B,
            actor="operator",
            reason="verify fix",
            request_id="12345678-1234-4234-8234-123456789abc",
            expected_row_version=1,
        ),
        now=started + timedelta(minutes=1),
    )
    attempt = recovery["attempt"]
    trigger = recovery["trigger"]

    normal_during_recovery = _normal_request(
        "normal-during-recovery", started + timedelta(minutes=2)
    )
    service.import_normal(normal_during_recovery, now=started + timedelta(minutes=2))
    unchanged = service.recovery_status(flaky_key)
    assert unchanged["governance"]["status"] == "RECOVERING"
    assert unchanged["attempt"]["status"] == "ACTIVE"

    for round_no in range(1, 6):
        evidence_time = started + timedelta(minutes=31 * round_no)
        probe = _probe_request(
            f"probe-{round_no}",
            evidence_time,
            attempt_id=attempt["attempt_id"],
            trigger_id=trigger["trigger_id"],
            plan_digest=trigger["plan_digest"],
            round_no=round_no,
        )
        result = service.import_probe(probe, now=evidence_time + timedelta(seconds=1))
        assert result["classification"] == "COUNT_PASS"

    ready = service.recovery_status(flaky_key)
    assert ready["attempt"]["status"] == "READY_TO_CLOSE"
    assert ready["governance"]["status"] == "RECOVERING"

    closed = service.recovery_close(
        RecoveryCloseRequest(
            attempt_id=attempt["attempt_id"],
            actor="operator",
            reason="five trusted Probe passes",
            expected_row_version=2,
            verified_branch_head=SHA_B,
        ),
        now=started + timedelta(hours=4),
    )
    assert closed["attempt"]["status"] == "CLOSED"
    assert closed["governance"]["status"] == "CLOSED"
    status = service.recovery_status(flaky_key)
    assert status["detection_generation"] == 2
    assert status["detection_state"] == "UNOBSERVED"


def test_probe_replay_handles_out_of_order_duplicate_and_late_evidence(tmp_path):
    database = (tmp_path / "probe-replay.sqlite3").resolve()
    migrate_store(database)
    service = FlakyV3Service(database)
    started = datetime(2026, 9, 2, 2, 0, tzinfo=UTC)
    service.import_normal(_normal_request("normal-seed", started), now=started)
    flaky_key = _flaky_key(database)
    service.quarantine(
        flaky_key=flaky_key,
        owner="owner",
        actor="operator",
        reason="confirmed flaky",
        request_id="quarantine-replay",
        expires_at=started + timedelta(days=10),
        now=started,
    )
    recovery = service.recovery_start(
        RecoveryStartRequest(
            flaky_key=flaky_key,
            target_commit_sha=SHA_B,
            actor="operator",
            reason="verify",
            request_id="22345678-1234-4234-8234-123456789abc",
            expected_row_version=1,
        ),
        now=started + timedelta(minutes=1),
    )
    attempt = recovery["attempt"]
    trigger = recovery["trigger"]
    for round_no in (3, 1, 5, 2, 4):
        evidence_time = started + timedelta(minutes=31 * round_no)
        service.import_probe(
            _probe_request(
                f"replay-{round_no}",
                evidence_time,
                attempt_id=attempt["attempt_id"],
                trigger_id=trigger["trigger_id"],
                plan_digest=trigger["plan_digest"],
                round_no=round_no,
            ),
            now=evidence_time + timedelta(seconds=1),
        )
    assert service.recovery_status(flaky_key)["attempt"]["status"] == "READY_TO_CLOSE"

    duplicate = service.import_probe(
        _probe_request(
            "replay-duplicate-round",
            started + timedelta(minutes=32),
            attempt_id=attempt["attempt_id"],
            trigger_id=trigger["trigger_id"],
            plan_digest=trigger["plan_digest"],
            round_no=1,
        ),
        now=started + timedelta(hours=3),
    )
    assert duplicate["effect_status"] == "AUDIT_ONLY"

    failed = service.import_probe(
        _probe_request(
            "replay-trusted-fail",
            started + timedelta(hours=4),
            attempt_id=attempt["attempt_id"],
            trigger_id=trigger["trigger_id"],
            plan_digest=trigger["plan_digest"],
            round_no=6,
            outcome=ProbeOutcome.FAIL,
            trusted_failure=True,
        ),
        now=started + timedelta(hours=4, seconds=1),
    )
    assert failed["classification"] == "TRUSTED_FAIL"
    assert service.recovery_status(flaky_key)["attempt"]["status"] == "FAILED"

    late = service.import_probe(
        _probe_request(
            "replay-late",
            started + timedelta(hours=5),
            attempt_id=attempt["attempt_id"],
            trigger_id=trigger["trigger_id"],
            plan_digest=trigger["plan_digest"],
            round_no=7,
        ),
        now=started + timedelta(hours=5, seconds=1),
    )
    assert late["reason_code"] == "probe_attempt_inactive"
    assert late["effect_status"] == "AUDIT_ONLY"
    assert service.recovery_status(flaky_key)["governance"]["status"] == "ACTIVE"
    assert service.check_invariants()["status"] == "OK"


def test_probe_duplicate_run_is_noop_and_interval_boundary_is_exact(tmp_path):
    database = (tmp_path / "probe-boundary.sqlite3").resolve()
    service, started, flaky_key = _seed_governance(database, "probe-boundary")
    recovery = service.recovery_start(
        RecoveryStartRequest(
            flaky_key=flaky_key,
            target_commit_sha=SHA_B,
            actor="operator",
            reason="verify",
            request_id="13345678-1234-4234-8234-123456789abc",
            expected_row_version=1,
        ),
        now=started + timedelta(minutes=1),
    )
    attempt = recovery["attempt"]
    trigger = recovery["trigger"]
    first = _probe_request(
        "probe-boundary-first",
        started + timedelta(minutes=31),
        attempt_id=attempt["attempt_id"],
        trigger_id=trigger["trigger_id"],
        plan_digest=trigger["plan_digest"],
        round_no=1,
    )
    assert service.import_probe(
        first, now=started + timedelta(minutes=31, seconds=1)
    )["classification"] == "COUNT_PASS"
    duplicate = service.import_probe(
        first, now=started + timedelta(minutes=31, seconds=2)
    )
    assert duplicate["status"] == "NOOP"

    too_soon = service.import_probe(
        _probe_request(
            "probe-boundary-too-soon",
            started + timedelta(minutes=60, seconds=59),
            attempt_id=attempt["attempt_id"],
            trigger_id=trigger["trigger_id"],
            plan_digest=trigger["plan_digest"],
            round_no=2,
        ),
        now=started + timedelta(minutes=61),
    )
    assert too_soon["reason_code"] == "probe_interval_too_short"
    assert too_soon["effect_status"] == "AUDIT_ONLY"

    exact = service.import_probe(
        _probe_request(
            "probe-boundary-exact",
            started + timedelta(minutes=61),
            attempt_id=attempt["attempt_id"],
            trigger_id=trigger["trigger_id"],
            plan_digest=trigger["plan_digest"],
            round_no=3,
        ),
        now=started + timedelta(minutes=61, seconds=1),
    )
    assert exact["classification"] == "COUNT_PASS"
    assert exact["effect_status"] == "APPLIED"
    assert service.recovery_status(flaky_key)["attempt"]["counted_passes"] == 2


def test_three_non_counting_quota_categories_end_attempt_inconclusive(tmp_path):
    database = (tmp_path / "probe-quota.sqlite3").resolve()
    migrate_store(database)
    service = FlakyV3Service(database)
    started = datetime(2026, 9, 3, 2, 0, tzinfo=UTC)
    service.import_normal(_normal_request("quota-seed", started), now=started)
    flaky_key = _flaky_key(database)
    service.quarantine(
        flaky_key=flaky_key,
        owner="owner",
        actor="operator",
        reason="confirmed flaky",
        request_id="quarantine-quota",
        expires_at=started + timedelta(days=10),
        now=started,
    )
    recovery = service.recovery_start(
        RecoveryStartRequest(
            flaky_key=flaky_key,
            target_commit_sha=SHA_B,
            actor="operator",
            reason="verify",
            request_id="32345678-1234-4234-8234-123456789abc",
            expected_row_version=1,
        ),
        now=started + timedelta(minutes=1),
    )
    attempt = recovery["attempt"]
    trigger = recovery["trigger"]
    variants = (
        {"p0_trusted": False},
        {"rerun_supported": False},
        {"outcome": ProbeOutcome.SKIP},
    )
    reasons = []
    for round_no, variant in enumerate(variants, start=1):
        evidence_time = started + timedelta(minutes=31 * round_no)
        result = service.import_probe(
            _probe_request(
                f"quota-{round_no}",
                evidence_time,
                attempt_id=attempt["attempt_id"],
                trigger_id=trigger["trigger_id"],
                plan_digest=trigger["plan_digest"],
                round_no=round_no,
                **variant,
            ),
            now=evidence_time + timedelta(seconds=1),
        )
        reasons.append(result["reason_code"])
    assert reasons == [
        "probe_evidence_untrusted",
        "probe_rerun_unsupported",
        "probe_outcome_not_countable",
    ]
    status = service.recovery_status(flaky_key)
    assert status["attempt"]["status"] == "INCONCLUSIVE"
    assert status["attempt"]["non_counting_runs"] == 3
    assert status["governance"]["status"] == "ACTIVE"
    assert service.check_invariants()["status"] == "OK"


def test_recovery_commands_reject_stale_row_versions_without_partial_changes(tmp_path):
    database = (tmp_path / "row-version.sqlite3").resolve()
    service, started, flaky_key = _seed_governance(database, "row-version")

    with pytest.raises(FlakyStoreError) as captured:
        service.recovery_start(
            RecoveryStartRequest(
                flaky_key=flaky_key,
                target_commit_sha=SHA_B,
                actor="operator",
                reason="stale start",
                request_id="42345678-1234-4234-8234-123456789abc",
                expected_row_version=2,
            ),
            now=started + timedelta(minutes=1),
        )
    assert captured.value.code == "row_version_conflict"
    unchanged = service.recovery_status(flaky_key)
    assert unchanged["governance"]["status"] == "ACTIVE"
    assert unchanged["governance"]["row_version"] == 1
    assert unchanged["attempt"] is None

    recovery = service.recovery_start(
        RecoveryStartRequest(
            flaky_key=flaky_key,
            target_commit_sha=SHA_B,
            actor="operator",
            reason="valid start",
            request_id="52345678-1234-4234-8234-123456789abc",
            expected_row_version=1,
        ),
        now=started + timedelta(minutes=2),
    )
    attempt_id = recovery["attempt"]["attempt_id"]
    replayed = service.recovery_start(
        RecoveryStartRequest(
            flaky_key=flaky_key,
            target_commit_sha=SHA_B,
            actor="operator",
            reason="valid start",
            request_id="52345678-1234-4234-8234-123456789abc",
            expected_row_version=1,
        ),
        now=started + timedelta(minutes=2, seconds=1),
    )
    assert replayed["attempt"]["attempt_id"] == attempt_id
    with pytest.raises(FlakyStoreError) as captured:
        service.recovery_start(
            RecoveryStartRequest(
                flaky_key=flaky_key,
                target_commit_sha=SHA_A,
                actor="operator",
                reason="different payload",
                request_id="52345678-1234-4234-8234-123456789abc",
                expected_row_version=1,
            ),
            now=started + timedelta(minutes=2, seconds=2),
        )
    assert captured.value.code == "idempotency_conflict"
    with pytest.raises(FlakyStoreError) as captured:
        service.recovery_cancel(
            RecoveryCancelRequest(
                attempt_id=attempt_id,
                actor="operator",
                reason="stale cancel",
                expected_row_version=1,
            ),
            now=started + timedelta(minutes=3),
        )
    assert captured.value.code == "row_version_conflict"
    unchanged = service.recovery_status(flaky_key)
    assert unchanged["governance"]["status"] == "RECOVERING"
    assert unchanged["governance"]["row_version"] == 2
    assert unchanged["attempt"]["status"] == "ACTIVE"

    cancelled = service.recovery_cancel(
        RecoveryCancelRequest(
            attempt_id=attempt_id,
            actor="operator",
            reason="valid cancel",
            expected_row_version=2,
        ),
        now=started + timedelta(minutes=4),
    )
    assert cancelled["attempt"]["status"] == "CANCELLED"
    assert cancelled["governance"]["status"] == "ACTIVE"
    assert cancelled["governance"]["row_version"] == 3


def test_recovery_close_rejects_stale_row_version_and_preserves_ready_attempt(tmp_path):
    database = (tmp_path / "close-row-version.sqlite3").resolve()
    service, started, flaky_key = _seed_governance(database, "close-row-version")
    recovery = service.recovery_start(
        RecoveryStartRequest(
            flaky_key=flaky_key,
            target_commit_sha=SHA_B,
            actor="operator",
            reason="verify",
            request_id="62345678-1234-4234-8234-123456789abc",
            expected_row_version=1,
        ),
        now=started + timedelta(minutes=1),
    )
    attempt = recovery["attempt"]
    trigger = recovery["trigger"]
    _import_passing_probe_rounds(service, started, attempt, trigger)

    with pytest.raises(FlakyStoreError) as captured:
        service.recovery_close(
            RecoveryCloseRequest(
                attempt_id=attempt["attempt_id"],
                actor="operator",
                reason="stale close",
                expected_row_version=1,
                verified_branch_head=SHA_B,
            ),
            now=started + timedelta(hours=4),
        )
    assert captured.value.code == "row_version_conflict"
    unchanged = service.recovery_status(flaky_key)
    assert unchanged["attempt"]["status"] == "READY_TO_CLOSE"
    assert unchanged["governance"]["status"] == "RECOVERING"
    assert unchanged["detection_generation"] == 1


def test_db_check_detects_illegal_evidence_and_reverse_state_mismatch(tmp_path):
    database = (tmp_path / "db-check-corruption.sqlite3").resolve()
    service, started, flaky_key = _seed_governance(database, "db-check")
    recovery = service.recovery_start(
        RecoveryStartRequest(
            flaky_key=flaky_key,
            target_commit_sha=SHA_B,
            actor="operator",
            reason="verify",
            request_id="14345678-1234-4234-8234-123456789abc",
            expected_row_version=1,
        ),
        now=started + timedelta(minutes=1),
    )
    attempt = recovery["attempt"]
    trigger = recovery["trigger"]
    service.import_probe(
        _probe_request(
            "db-check-probe",
            started + timedelta(minutes=31),
            attempt_id=attempt["attempt_id"],
            trigger_id=trigger["trigger_id"],
            plan_digest=trigger["plan_digest"],
            round_no=1,
        ),
        now=started + timedelta(minutes=31, seconds=1),
    )
    with sqlite3.connect(database) as connection:
        connection.execute(
            """UPDATE flaky_probe_evidence
               SET classification = 'NON_COUNTING',
                   consumes_non_counting_quota = 1
               WHERE run_id = 'db-check-probe'"""
        )
        connection.execute(
            """UPDATE flaky_verification_attempt
               SET status = 'CANCELLED', ended_at = ?, end_reason = 'corrupted'
               WHERE attempt_id = ?""",
            (
                (started + timedelta(minutes=32)).isoformat(),
                attempt["attempt_id"],
            ),
        )

    checked = service.check_invariants()
    assert checked["status"] == "FAILED"
    assert checked["issue_codes"] == [
        "illegal_applied_probe_evidence",
        "recovering_governance_without_live_attempt",
    ]


def test_v3_current_state_query_never_exposes_legacy_projection(tmp_path):
    database = (tmp_path / "legacy-projection.sqlite3").resolve()
    migrate_store(database)

    with pytest.raises(FlakyStoreError) as captured:
        FlakyStore(database).states(case_id="module/smoke/test_demo.py::test_case")

    assert captured.value.code == "legacy_projection_query_disabled"


def test_recovery_cli_start_status_cancel_and_stable_error_code(tmp_path, capsys):
    database = (tmp_path / "recovery-cli-cancel.sqlite3").resolve()
    _service, _started, flaky_key = _seed_governance(database, "cli-cancel")
    start_args = [
        "flaky-recovery-start",
        "--db",
        str(database),
        "--flaky-key",
        flaky_key,
        "--target-commit-sha",
        SHA_B,
        "--actor",
        "operator",
        "--reason",
        "verify fix",
        "--request-id",
        "72345678-1234-4234-8234-123456789abc",
        "--expected-row-version",
        "1",
    ]
    assert main(start_args) == 0
    started = json.loads(capsys.readouterr().out)
    attempt_id = started["attempt"]["attempt_id"]
    assert started["governance"]["status"] == "RECOVERING"

    assert main(
        [
            "flaky-recovery-status",
            "--db",
            str(database),
            "--flaky-key",
            flaky_key,
        ]
    ) == 0
    assert json.loads(capsys.readouterr().out)["attempt"]["status"] == "ACTIVE"

    assert main(
        [
            "flaky-recovery-cancel",
            "--db",
            str(database),
            "--attempt-id",
            attempt_id,
            "--actor",
            "operator",
            "--reason",
            "cancel verification",
            "--expected-row-version",
            "2",
        ]
    ) == 0
    cancelled = json.loads(capsys.readouterr().out)
    assert cancelled["attempt"]["status"] == "CANCELLED"
    assert cancelled["governance"]["status"] == "ACTIVE"

    stale_args = start_args.copy()
    stale_args[stale_args.index("72345678-1234-4234-8234-123456789abc")] = (
        "82345678-1234-4234-8234-123456789abc"
    )
    assert main(stale_args) == 2
    failed = json.loads(capsys.readouterr().out)
    assert failed["schema_version"] == "quality.flaky-cli-error.v1"
    assert failed["error_code"] == "row_version_conflict"


def test_recovery_cli_closes_only_ready_attempt(tmp_path, capsys):
    database = (tmp_path / "recovery-cli-close.sqlite3").resolve()
    service, started, flaky_key = _seed_governance(database, "cli-close")
    assert main(
        [
            "flaky-recovery-start",
            "--db",
            str(database),
            "--flaky-key",
            flaky_key,
            "--target-commit-sha",
            SHA_B,
            "--actor",
            "operator",
            "--reason",
            "verify fix",
            "--request-id",
            "92345678-1234-4234-8234-123456789abc",
            "--expected-row-version",
            "1",
        ]
    ) == 0
    recovery = json.loads(capsys.readouterr().out)
    attempt = recovery["attempt"]
    trigger = recovery["trigger"]
    _import_passing_probe_rounds(service, started, attempt, trigger)

    assert main(
        [
            "flaky-recovery-close",
            "--db",
            str(database),
            "--attempt-id",
            attempt["attempt_id"],
            "--actor",
            "operator",
            "--reason",
            "trusted Probe evidence complete",
            "--expected-row-version",
            "2",
            "--verified-branch-head",
            SHA_B,
        ]
    ) == 0
    closed = json.loads(capsys.readouterr().out)
    assert closed["attempt"]["status"] == "CLOSED"
    assert closed["governance"]["status"] == "CLOSED"
    assert service.recovery_status(flaky_key)["detection_generation"] == 2


def _seed_governance(database, suffix: str):
    migrate_store(database)
    service = FlakyV3Service(database)
    started = datetime.now(UTC).replace(microsecond=0)
    service.import_normal(_normal_request(f"normal-{suffix}", started), now=started)
    flaky_key = _flaky_key(database)
    service.quarantine(
        flaky_key=flaky_key,
        owner="owner",
        actor="operator",
        reason="confirmed flaky",
        request_id=f"quarantine-{suffix}",
        expires_at=started + timedelta(days=10),
        now=started,
    )
    return service, started, flaky_key


def _import_passing_probe_rounds(service, started, attempt, trigger) -> None:
    for round_no in range(1, 6):
        evidence_time = started + timedelta(minutes=31 * round_no)
        service.import_probe(
            _probe_request(
                f"{attempt['attempt_id']}-probe-{round_no}",
                evidence_time,
                attempt_id=attempt["attempt_id"],
                trigger_id=trigger["trigger_id"],
                plan_digest=trigger["plan_digest"],
                round_no=round_no,
            ),
            now=evidence_time + timedelta(seconds=1),
        )


def _normal_request(
    run_id: str,
    when: datetime,
    *,
    configuration_revision: str = "config-a",
    case_facts: NormalCaseAdmissionFacts | None = None,
) -> NormalImportRequest:
    run = _run(run_id, when, RunKind.NORMAL)
    case = NormalCaseEvidence(
        case_id="module/smoke/test_demo.py::test_case",
        param_hash="param-hash",
        environment="overseas",
        execution_profile="serial",
        state_epoch=1,
        comparability=ComparabilityFacts(
            configuration_revision=configuration_revision,
            environment="overseas",
            execution_profile="serial",
            sut_revision="sut-v1",
            test_definition_digest=f"sha256:{'c' * 64}",
        ),
        admission_facts=case_facts
        or NormalCaseAdmissionFacts(lifecycle_valid=True, outcome="pass"),
        observed_at=when,
        failure_fingerprint=(
            "failure-a"
            if case_facts is not None and case_facts.outcome == "fail"
            else None
        ),
    )
    return NormalImportRequest(
        run=run,
        manifest=_manifest(run),
        source_digest=_digest(run_id),
        admission_facts=NormalRunAdmissionFacts(
            run_kind=RunKind.NORMAL,
            source_job_allowed=True,
            branch_allowed=True,
            environment_allowed=True,
            execution_profile_allowed=True,
            run_finished=True,
            versions_compatible=True,
            artifacts_trusted=True,
            integrity_eligible=True,
            comparability_valid=True,
        ),
        cases=(case,),
    )


def _probe_request(
    run_id: str,
    when: datetime,
    *,
    attempt_id: str,
    trigger_id: str,
    plan_digest: str,
    round_no: int,
    outcome: ProbeOutcome = ProbeOutcome.PASS,
    p0_trusted: bool = True,
    rerun_supported: bool = True,
    trusted_failure: bool = False,
) -> ProbeImportRequest:
    run = _run(
        run_id,
        when,
        RunKind.FLAKY_PROBE,
        attempt_id=attempt_id,
        trigger_id=trigger_id,
        plan_digest=plan_digest,
        round_no=round_no,
    )
    return ProbeImportRequest(
        run=run,
        manifest=_manifest(run),
        source_digest=_digest(run_id),
        outcome=outcome,
        trusted_started_at=when,
        p0_trusted=p0_trusted,
        rerun_supported=rerun_supported,
        trusted_failure=trusted_failure,
    )


def _run(
    run_id: str,
    when: datetime,
    kind: RunKind,
    *,
    attempt_id: str | None = None,
    trigger_id: str | None = None,
    plan_digest: str | None = None,
    round_no: int | None = None,
) -> RunRecord:
    probe = kind is RunKind.FLAKY_PROBE
    return RunRecord(
        run_id=run_id,
        job_name="quality-job",
        build_number=run_id,
        branch="dev3",
        commit_sha=SHA_B if probe else SHA_A,
        trigger="jenkins",
        environment="overseas",
        start_time=when,
        end_time=when + timedelta(seconds=10),
        status=RunStatus.FINISHED,
        integrity_status=IntegrityStatus.COMPLETE,
        run_kind=kind,
        policy_revision=DEFAULT_GOVERNANCE_POLICY.revision,
        controller_commit_sha=SHA_A,
        attempt_id=attempt_id,
        trigger_id=trigger_id,
        plan_digest=plan_digest,
        round_no=round_no,
        target_commit_sha=SHA_B if probe else None,
        jenkins_job_name="quality-probe" if probe else None,
        jenkins_build_number=run_id if probe else None,
        fact_schema_version="quality.fact.v1",
        plugin_version="quality-plugin.v1",
    )


def _manifest(run: RunRecord) -> dict[str, object]:
    payload = {
        "manifest_version": "quality.merge.v2",
        "schema_version": "quality.v2",
        "run_id": run.run_id,
        "status": "complete",
        "merge_version": "p0-merge.v1",
        "fingerprint_version": FINGERPRINT_VERSION,
        "integrity_status": run.integrity_status.value,
        "output_hashes": {
            "case-results": _digest(f"{run.run_id}:cases"),
            "failures": _digest(f"{run.run_id}:failures"),
            "integrity-issues": _digest(f"{run.run_id}:issues"),
        },
    }
    dumped = run.model_dump(mode="json")
    for field in (
        "run_kind",
        "policy_revision",
        "controller_commit_sha",
        "attempt_id",
        "trigger_id",
        "plan_digest",
        "round_no",
        "target_commit_sha",
        "jenkins_job_name",
        "jenkins_build_number",
        "fact_schema_version",
        "plugin_version",
    ):
        payload[field] = dumped[field]
    return payload


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _flaky_key(database) -> str:
    with sqlite3.connect(database) as connection:
        return connection.execute("SELECT flaky_key FROM flaky_identity").fetchone()[0]
