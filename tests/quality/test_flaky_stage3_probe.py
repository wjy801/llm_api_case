from __future__ import annotations

from datetime import UTC, datetime, timedelta
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import threading
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from quality.classifier import FINGERPRINT_VERSION
from quality.flaky_dashboard import create_app
from quality.flaky_probe import (
    CsrfProtector,
    DispatchResult,
    DispatchResultKind,
    FixedJenkinsClient,
    JenkinsObservation,
    JenkinsObservationKind,
    ProbeControlService,
    ProbeCreateRequest,
    ProbeRuntimeConfig,
    build_probe_envelope,
    canonical_json,
    sign_probe_envelope,
)
from quality.cli import main as cli_main
from quality.probe_job import (
    _import_round,
    _select_probe_candidate,
    _validate_controller_checkout,
)
from quality.flaky_store import MIGRATIONS_DIRECTORY, FlakyStoreError, migrate_store
from quality.flaky_store.v3_service import (
    FlakyV3Service,
    ProbeImportRequest,
    RecoveryCloseRequest,
)
from quality.flaky_v3 import DEFAULT_GOVERNANCE_POLICY, ProbeOutcome
from quality.models import IntegrityStatus, RunKind, RunRecord, RunStatus


NOW = datetime(2026, 9, 1, 8, 0, tzinfo=UTC)
TARGET_SHA = "b" * 40
CONTROLLER_SHA = "c" * 40
SECRET = b"stage-3-evidence-key-material-0001"


class FakeJenkins:
    def __init__(self, result: DispatchResult | None = None) -> None:
        self.result = result or DispatchResult(DispatchResultKind.QUEUED, queue_id=41)
        self.tokens: list[str] = []
        self.observation = JenkinsObservation(JenkinsObservationKind.UNKNOWN)

    def dispatch(self, *, trigger_id: str, dispatch_token: str, plan_digest: str):
        self.tokens.append(dispatch_token)
        return self.result

    def observe(self, trigger):
        return self.observation

    def cancel(self, trigger):
        return self.observation


def test_create_is_idempotent_and_global_capacity_is_transactional(tmp_path):
    database, control, governance_id = _control(tmp_path)
    request = _create_request(governance_id)

    created = control.create_attempt(request, now=NOW)
    replay = control.create_attempt(request, now=NOW + timedelta(seconds=1))

    assert created["created"] is True
    assert replay == {**created, "created": False}
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM flaky_probe_capacity_slot").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM flaky_probe_plan").fetchone()[0] == 1
        token = connection.execute("SELECT token_hash FROM flaky_probe_trigger").fetchone()[0]
    assert token is None


def test_dispatch_unknown_is_not_retried_and_claim_is_at_most_once(tmp_path):
    _database, control, governance_id = _control(tmp_path)
    created = control.create_attempt(_create_request(governance_id), now=NOW)
    unknown = FakeJenkins(DispatchResult(DispatchResultKind.UNKNOWN, error_code="response_lost"))

    result = control.dispatch_once(unknown, now=NOW + timedelta(seconds=1))

    assert result["status"] == "DISPATCH_UNKNOWN"
    assert control.dispatch_once(unknown, now=NOW + timedelta(seconds=2)) == {"status": "IDLE"}
    claimed = control.claim(
        trigger_id=created["trigger_id"],
        dispatch_token=unknown.tokens[0],
        plan_digest=created["plan_digest"],
        job_full_name="quality/probe",
        build_number=7,
        now=NOW + timedelta(seconds=3),
    )
    assert claimed["status"] == "RUNNING"
    with pytest.raises(FlakyStoreError, match="another build"):
        control.claim(
            trigger_id=created["trigger_id"],
            dispatch_token=unknown.tokens[0],
            plan_digest=created["plan_digest"],
            job_full_name="quality/probe",
            build_number=8,
            now=NOW + timedelta(seconds=4),
        )


def test_reconcile_moves_interrupted_dispatch_to_unknown_without_releasing_capacity(tmp_path):
    database, control, governance_id = _control(tmp_path)
    created = control.create_attempt(_create_request(governance_id), now=NOW)
    claimed = control._claim_dispatch(NOW + timedelta(seconds=1))
    assert claimed is not None

    gateway = FakeJenkins()
    gateway.observation = JenkinsObservation(
        JenkinsObservationKind.UNKNOWN,
        error_code="jenkins_receipt_unknown",
    )
    reconciled = control.reconcile_once(gateway, now=NOW + timedelta(minutes=1))

    assert reconciled["status"] == "DISPATCH_UNKNOWN"
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT last_error_code FROM flaky_probe_trigger WHERE trigger_id=?",
            (created["trigger_id"],),
        ).fetchone()[0] == "jenkins_receipt_unknown"
        assert connection.execute(
            "SELECT trigger_id FROM flaky_probe_capacity_slot WHERE slot_id=1"
        ).fetchone()[0] == created["trigger_id"]
    assert control.dispatch_once(gateway, now=NOW + timedelta(minutes=2)) == {"status": "IDLE"}


def test_signed_round_imports_atomically_and_tampering_is_rejected(tmp_path):
    database, control, governance_id = _control(tmp_path)
    created, token = _running(control, governance_id)
    round_row = control.authorize_round(created["attempt_id"], now=NOW + timedelta(minutes=1))
    control.start_round(
        created["attempt_id"], round_row["round_no"],
        actual_target_commit_sha=TARGET_SHA, now=NOW + timedelta(minutes=1, seconds=1),
    )
    request = _probe_import(control, created, round_row, NOW + timedelta(minutes=1, seconds=1))
    service = FlakyV3Service(database, probe_evidence_keys={"key-v1": SECRET})

    imported = service.import_probe(request, now=NOW + timedelta(minutes=2))

    assert imported["classification"] == "COUNT_PASS"
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        evidence = connection.execute("SELECT * FROM flaky_probe_evidence").fetchone()
        round_after = connection.execute("SELECT * FROM flaky_probe_round").fetchone()
    assert evidence["envelope_verified"] == 1
    assert round_after["status"] == "IMPORTED"
    assert round_after["evidence_id"] == evidence["evidence_id"]

    next_round = control.authorize_round(created["attempt_id"], now=NOW + timedelta(minutes=32))
    control.start_round(
        created["attempt_id"], next_round["round_no"],
        actual_target_commit_sha=TARGET_SHA, now=NOW + timedelta(minutes=32, seconds=1),
    )
    tampered = _probe_import(control, created, next_round, NOW + timedelta(minutes=32, seconds=1))
    legacy_envelope = tampered.envelope.model_copy(
        update={"schema_version": "flaky-probe-envelope.v1"}
    )
    legacy_envelope = legacy_envelope.model_copy(
        update={
            "signature": sign_probe_envelope(
                legacy_envelope.signing_payload,
                SECRET,
            )
        }
    )
    with pytest.raises(FlakyStoreError) as legacy_error:
        service.import_probe(
            ProbeImportRequest(**{**tampered.__dict__, "envelope": legacy_envelope}),
            now=NOW + timedelta(minutes=33),
        )
    assert legacy_error.value.code == "probe_envelope_invalid"
    for changes in (
        {"outcome": "FAIL", "trusted_failure": True},
        {"rerun_supported": False},
        {"diagnostic_codes": ("tampered",)},
    ):
        bad = tampered.envelope.model_copy(update=changes)
        with pytest.raises(FlakyStoreError, match="signature"):
            service.import_probe(
                ProbeImportRequest(**{**tampered.__dict__, "envelope": bad}),
                now=NOW + timedelta(minutes=33),
            )


def test_unknown_cancel_keeps_capacity_until_jenkins_confirms(tmp_path):
    database, control, governance_id = _control(tmp_path)
    created = control.create_attempt(_create_request(governance_id), now=NOW)
    gateway = FakeJenkins(
        DispatchResult(DispatchResultKind.UNKNOWN, error_code="response_lost")
    )
    control.dispatch_once(gateway, now=NOW + timedelta(seconds=1))

    cancelled = control.request_cancel(
        created["attempt_id"],
        actor="operator",
        reason="stop rehearsal",
        expected_row_version=2,
        now=NOW + timedelta(seconds=2),
        gateway=gateway,
    )

    assert cancelled["status"] == "CANCEL_REQUESTED"
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT trigger_id FROM flaky_probe_capacity_slot WHERE slot_id=1"
        ).fetchone()[0] == created["trigger_id"]


def test_cancel_request_observes_after_ack_and_releases_slot_on_confirmation(tmp_path):
    database, control, governance_id = _control(tmp_path)
    created, _token = _running(control, governance_id)

    class CancelThenObserve:
        def cancel(self, trigger):
            return JenkinsObservation(
                JenkinsObservationKind.UNKNOWN,
                build_number=7,
                error_code="jenkins_cancel_requested",
            )

        def observe(self, trigger):
            return JenkinsObservation(
                JenkinsObservationKind.COMPLETED, build_number=7
            )

    gateway = CancelThenObserve()
    requested = control.request_cancel(
        created["attempt_id"],
        actor="operator",
        reason="stop rehearsal",
        expected_row_version=2,
        now=NOW + timedelta(seconds=3),
        gateway=gateway,
    )
    settled = control.reconcile_once(gateway, now=NOW + timedelta(seconds=4))

    assert requested["status"] == "CANCEL_REQUESTED"
    assert settled["status"] == "CANCELLED"
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM flaky_probe_capacity_slot"
        ).fetchone()[0] == 0


def test_terminal_build_waits_once_for_late_import_then_abandons(tmp_path):
    database, control, governance_id = _control(tmp_path)
    created, _token = _running(control, governance_id)
    round_row = control.authorize_round(
        created["attempt_id"], now=NOW + timedelta(minutes=1)
    )
    control.start_round(
        created["attempt_id"],
        round_row["round_no"],
        actual_target_commit_sha=TARGET_SHA,
        now=NOW + timedelta(minutes=1, seconds=1),
    )
    gateway = FakeJenkins()
    gateway.observation = JenkinsObservation(JenkinsObservationKind.COMPLETED)

    first = control.reconcile_once(gateway, now=NOW + timedelta(minutes=2))
    with sqlite3.connect(database) as connection:
        first_deadline = connection.execute(
            "SELECT next_reconcile_at FROM flaky_probe_trigger WHERE trigger_id=?",
            (created["trigger_id"],),
        ).fetchone()[0]
    second = control.reconcile_once(gateway, now=NOW + timedelta(minutes=6))
    with sqlite3.connect(database) as connection:
        second_deadline = connection.execute(
            "SELECT next_reconcile_at FROM flaky_probe_trigger WHERE trigger_id=?",
            (created["trigger_id"],),
        ).fetchone()[0]
    settled = control.reconcile_once(
        gateway, now=NOW + timedelta(minutes=7, seconds=1)
    )

    assert first["status"] == second["status"] == "RUNNING"
    assert first_deadline == second_deadline
    assert settled["status"] == "FAILED"
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT status FROM flaky_probe_round WHERE attempt_id=?",
            (created["attempt_id"],),
        ).fetchone()[0] == "ABANDONED"
        assert connection.execute(
            "SELECT status FROM flaky_verification_attempt WHERE attempt_id=?",
            (created["attempt_id"],),
        ).fetchone()[0] == "INCONCLUSIVE"


def test_missing_p0_imports_controller_origin_non_counting(tmp_path, monkeypatch):
    database, control, governance_id = _control(tmp_path)
    created, _token = _running(control, governance_id)
    round_row = control.authorize_round(
        created["attempt_id"], now=NOW + timedelta(minutes=1)
    )
    control.start_round(
        created["attempt_id"],
        round_row["round_no"],
        actual_target_commit_sha=TARGET_SHA,
        now=NOW + timedelta(minutes=1, seconds=1),
    )
    key_file = tmp_path / "evidence.key"
    key_file.write_bytes(SECRET)
    runtime = replace(
        control.runtime,
        evidence_hmac_key_file=key_file,
        evidence_key_id="key-v1",
    )
    monkeypatch.setenv("QUALITY_OUTPUT_DIR", str(tmp_path / "missing-p0"))
    monkeypatch.setenv("QUALITY_RUN_ID", round_row["run_id"])
    monkeypatch.setenv("PROBE_ATTEMPT_ID", created["attempt_id"])
    monkeypatch.setenv("PROBE_ROUND_NO", str(round_row["round_no"]))
    monkeypatch.setenv("TRIGGER_ID", created["trigger_id"])
    monkeypatch.setenv("BUILD_NUMBER", "7")

    imported = _import_round(control, runtime, NOW + timedelta(minutes=2))

    assert imported["classification"] == "NON_COUNTING"
    assert imported["reason_code"] == "probe_evidence_untrusted"
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        evidence = connection.execute("SELECT * FROM flaky_probe_evidence").fetchone()
        assert evidence["p0_bundle_status"] == "MISSING"
        assert evidence["p0_trusted"] == 0
        assert connection.execute(
            "SELECT status FROM flaky_probe_round WHERE attempt_id=?",
            (created["attempt_id"],),
        ).fetchone()[0] == "IMPORTED"


def test_generic_connection_error_is_dispatch_unknown(tmp_path):
    credential = tmp_path / "jenkins.credential"
    credential.write_text("service:token", encoding="utf-8")
    runtime = ProbeRuntimeConfig(
        requested_enabled=True,
        enabled=True,
        jenkins_origin="https://jenkins.example.test",
        job_full_name="quality/probe",
        credential_file=credential,
        controller_commit_sha=CONTROLLER_SHA,
        controller_jenkinsfile_sha256="d" * 64,
    )

    class BrokenSession:
        def post(self, *args, **kwargs):
            raise ConnectionError("connection reset after send")

    result = FixedJenkinsClient(runtime, session=BrokenSession()).dispatch(
        trigger_id="trigger-1",
        dispatch_token="secret-token",
        plan_digest=f"sha256:{'a' * 64}",
    )

    assert result.kind is DispatchResultKind.UNKNOWN


def test_dashboard_lifespan_runs_dispatch_and_reconcile_loop(tmp_path):
    database, _control_service, _governance_id = _control(tmp_path)
    dispatched = threading.Event()
    reconciled = threading.Event()

    class LoopControl:
        def dispatch_once(self, gateway, *, now):
            dispatched.set()
            raise RuntimeError("kill switch blocks dispatch")

        def reconcile_once(self, gateway, *, now):
            reconciled.set()
            return {"status": "IDLE"}

    app = create_app(
        database,
        probe_control=LoopControl(),
        probe_gateway=FakeJenkins(),
        probe_poll_interval_seconds=0.01,
    )
    with TestClient(app):
        assert dispatched.wait(1)
        assert reconciled.wait(1)
    assert app.state.probe_loop_task.done()


def test_controller_checkout_verifies_commit_and_jenkinsfile_digest(
    tmp_path, monkeypatch
):
    controller_root = tmp_path.resolve()
    jenkinsfile = controller_root / "Jenkinsfile.probe"
    jenkinsfile.write_text("pipeline {}\n", encoding="utf-8")
    digest = hashlib.sha256(jenkinsfile.read_bytes()).hexdigest()
    runtime = ProbeRuntimeConfig(
        requested_enabled=True,
        enabled=True,
        jenkins_origin="https://jenkins.example.test",
        job_full_name="quality/probe",
        controller_commit_sha=CONTROLLER_SHA,
        controller_jenkinsfile_sha256=digest,
    )
    monkeypatch.chdir(controller_root)
    def clean_checkout(command, **_kwargs):
        output = f"{CONTROLLER_SHA}\n" if command[1:] == ["rev-parse", "HEAD"] else ""
        return SimpleNamespace(stdout=output)

    monkeypatch.setattr("quality.probe_job.subprocess.run", clean_checkout)

    _validate_controller_checkout(controller_root, runtime)

    with pytest.raises(ValueError, match="Jenkinsfile digest"):
        _validate_controller_checkout(
            controller_root,
            replace(runtime, controller_jenkinsfile_sha256="a" * 64),
        )

    def dirty_checkout(command, **_kwargs):
        output = (
            f"{CONTROLLER_SHA}\n"
            if command[1:] == ["rev-parse", "HEAD"]
            else " M quality/flaky_probe.py\n"
        )
        return SimpleNamespace(stdout=output)

    monkeypatch.setattr("quality.probe_job.subprocess.run", dirty_checkout)
    with pytest.raises(ValueError, match="working tree"):
        _validate_controller_checkout(controller_root, runtime)


def test_five_signed_passes_require_build_completion_before_manual_close(tmp_path):
    database, control, governance_id = _control(tmp_path)
    created, _token = _running(control, governance_id)
    service = FlakyV3Service(database, probe_evidence_keys={"key-v1": SECRET})
    for index in range(5):
        started = NOW + timedelta(minutes=1 + 31 * index)
        round_row = control.authorize_round(created["attempt_id"], now=started)
        control.start_round(
            created["attempt_id"], round_row["round_no"],
            actual_target_commit_sha=TARGET_SHA, now=started,
        )
        service.import_probe(_probe_import(control, created, round_row, started), now=started + timedelta(seconds=2))

    with pytest.raises(FlakyStoreError) as pending:
        service.recovery_close(
            RecoveryCloseRequest(created["attempt_id"], "operator", "verified", 2, TARGET_SHA),
            now=NOW + timedelta(hours=3),
        )
    assert pending.value.code == "probe_trigger_not_terminal"

    control.finalize_build(created["trigger_id"], now=NOW + timedelta(hours=3))
    with sqlite3.connect(database) as connection:
        evidence_id = connection.execute(
            "SELECT evidence_id FROM flaky_probe_evidence ORDER BY round_no LIMIT 1"
        ).fetchone()[0]
        connection.execute(
            "UPDATE flaky_probe_evidence SET rerun_supported=0 WHERE evidence_id=?",
            (evidence_id,),
        )
    with pytest.raises(FlakyStoreError) as tampered:
        service.recovery_close(
            RecoveryCloseRequest(created["attempt_id"], "operator", "verified", 2, TARGET_SHA),
            now=NOW + timedelta(hours=3, milliseconds=500),
        )
    assert tampered.value.code == "probe_envelope_binding_mismatch"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE flaky_probe_evidence SET rerun_supported=1 WHERE evidence_id=?",
            (evidence_id,),
        )
        original_envelope_json, original_signature = connection.execute(
            "SELECT envelope_json, envelope_signature FROM flaky_probe_evidence WHERE evidence_id=?",
            (evidence_id,),
        ).fetchone()
    original_payload = json.loads(original_envelope_json)
    for field, value in (("environment", "china"), ("execution_profile", "parallel")):
        changed = build_probe_envelope(
            secret=SECRET,
            **{**original_payload, field: value},
        )
        with sqlite3.connect(database) as connection:
            connection.execute(
                """UPDATE flaky_probe_evidence
                   SET envelope_json=?, envelope_signature=? WHERE evidence_id=?""",
                (canonical_json(changed.signing_payload), changed.signature, evidence_id),
            )
        with pytest.raises(FlakyStoreError) as identity_tampered:
            service.recovery_close(
                RecoveryCloseRequest(
                    created["attempt_id"], "operator", "verified", 2, TARGET_SHA
                ),
                now=NOW + timedelta(hours=3, milliseconds=750),
            )
        assert identity_tampered.value.code == "probe_envelope_binding_mismatch"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """UPDATE flaky_probe_evidence
               SET envelope_json=?, envelope_signature=? WHERE evidence_id=?""",
            (original_envelope_json, original_signature, evidence_id),
        )
    closed = service.recovery_close(
        RecoveryCloseRequest(created["attempt_id"], "operator", "verified", 2, TARGET_SHA),
        now=NOW + timedelta(hours=3, seconds=1),
    )
    assert closed["attempt"]["status"] == "CLOSED"
    assert closed["governance"]["status"] == "CLOSED"
    assert service.check_invariants()["status"] == "OK"


@pytest.mark.parametrize(
    ("environment", "execution_profile"),
    (("china", "serial"), ("overseas", "parallel")),
)
def test_signed_probe_with_wrong_execution_identity_cannot_count(
    tmp_path,
    environment,
    execution_profile,
):
    database, control, governance_id = _control(tmp_path)
    created, _token = _running(control, governance_id)
    started = NOW + timedelta(minutes=1)
    round_row = control.authorize_round(created["attempt_id"], now=started)
    control.start_round(
        created["attempt_id"],
        round_row["round_no"],
        actual_target_commit_sha=TARGET_SHA,
        now=started,
    )
    request = _probe_import(
        control,
        created,
        round_row,
        started,
        environment=environment,
        execution_profile=execution_profile,
    )

    imported = FlakyV3Service(
        database,
        probe_evidence_keys={"key-v1": SECRET},
    ).import_probe(request, now=started + timedelta(seconds=2))

    assert imported["classification"] == "NON_COUNTING"
    assert imported["effect_status"] == "AUDIT_ONLY"
    assert imported["reason_code"] == "probe_plan_mismatch"


@pytest.mark.parametrize(
    ("run_environment", "candidate_environment", "execution_profile"),
    (("china", "china", "serial"), ("overseas", "overseas", "parallel")),
)
def test_controller_rejects_candidate_from_wrong_execution_identity(
    run_environment,
    candidate_environment,
    execution_profile,
):
    plan = SimpleNamespace(
        case_id="module/smoke/test_probe.py::test_case",
        param_hash="param-stage3",
        environment="overseas",
        execution_profile="serial",
    )
    candidate = SimpleNamespace(
        case_id=plan.case_id,
        param_hash=plan.param_hash,
        environment=candidate_environment,
        execution_profile=execution_profile,
    )
    prepared = SimpleNamespace(
        run=SimpleNamespace(environment=run_environment),
        candidates=(candidate,),
    )

    selected, diagnostic = _select_probe_candidate(plan, prepared)

    assert selected is candidate
    assert diagnostic == "probe_execution_identity_mismatch"


def test_cli_stage3_close_fetches_head_and_loads_key_with_switch_off(
    tmp_path, monkeypatch
):
    database, control, governance_id = _control(tmp_path)
    created, _token = _running(control, governance_id)
    service = FlakyV3Service(database, probe_evidence_keys={"key-v1": SECRET})
    for index in range(5):
        started = NOW + timedelta(minutes=1 + 31 * index)
        round_row = control.authorize_round(created["attempt_id"], now=started)
        control.start_round(
            created["attempt_id"],
            round_row["round_no"],
            actual_target_commit_sha=TARGET_SHA,
            now=started,
        )
        service.import_probe(
            _probe_import(control, created, round_row, started),
            now=started + timedelta(seconds=2),
        )
    control.finalize_build(created["trigger_id"], now=NOW + timedelta(hours=3))
    key_file = tmp_path / "close-evidence.key"
    key_file.write_bytes(SECRET)
    monkeypatch.setenv("QUALITY_FLAKY_TRIGGER_ENABLE", "0")
    monkeypatch.setenv("QUALITY_FLAKY_EVIDENCE_HMAC_KEY_FILE", str(key_file))
    monkeypatch.setenv("QUALITY_FLAKY_EVIDENCE_KEY_ID", "key-v1")
    monkeypatch.setattr(
        "quality.cli.GitTargetResolver.resolve_dev3", lambda _self: TARGET_SHA
    )

    exit_code = cli_main(
        [
            "flaky-recovery-close",
            "--db", str(database),
            "--attempt-id", created["attempt_id"],
            "--actor", "operator",
            "--reason", "fresh head verified",
            "--expected-row-version", "2",
            "--verified-branch-head", "a" * 40,
        ]
    )

    assert exit_code == 0
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT status FROM flaky_verification_attempt WHERE attempt_id=?",
            (created["attempt_id"],),
        ).fetchone()[0] == "CLOSED"


def test_dashboard_post_enforces_origin_csrf_body_and_idempotent_status(tmp_path):
    database, control, governance_id = _control(tmp_path)
    csrf = CsrfProtector(b"csrf-secret-material-for-stage-three", clock=lambda: NOW)
    client = TestClient(
        create_app(
            database,
            probe_control=control,
            csrf_protector=csrf,
            dashboard_origin="http://dashboard.test",
        ),
        base_url="http://dashboard.test",
    )
    page = client.get("/governance")
    token = client.cookies.get("flaky_probe_csrf")
    assert page.status_code == 200 and token
    payload = {"reason": "verify fix", "row_version": 1, "request_id": "12345678-1234-4234-8234-123456789abc"}
    path = f"/api/v1/governances/{governance_id}/probe-attempts"
    headers = {"Origin": "http://dashboard.test", "X-CSRF-Token": token}
    assert client.post(path, json=payload, headers={**headers, "Origin": "http://evil.test"}).status_code == 403
    assert client.post(path, json={**payload, "extra": True}, headers=headers).status_code == 400
    first = client.post(path, json=payload, headers=headers)
    replay = client.post(path, json=payload, headers=headers)
    assert first.status_code == 201
    assert replay.status_code == 200
    assert first.json()["attempt_id"] == replay.json()["attempt_id"]


def test_migration_0004_rejects_live_legacy_probe(tmp_path):
    migrations = tmp_path / "migrations-v3"
    migrations.mkdir()
    for name in ("0001_observation_store.sql", "0002_flaky_state_machine.sql", "0003_v3_state_machine.sql"):
        shutil.copy2(MIGRATIONS_DIRECTORY / name, migrations / name)
    database = (tmp_path / "legacy.sqlite3").resolve()
    migrate_store(database, migrations_directory=migrations)
    _seed_governance(database)
    stamp = NOW.isoformat()
    with sqlite3.connect(database) as connection:
        connection.execute(
            """UPDATE flaky_governance SET status='RECOVERING', row_version=2,
                   recovery_started_by='operator', recovery_started_at=?, recovery_reason='legacy'
               WHERE governance_id='governance-stage3'""",
            (stamp,),
        )
        connection.execute(
            """INSERT INTO flaky_verification_attempt(
                   attempt_id, governance_id, attempt_no, status, target_commit_sha,
                   policy_revision, required_consecutive_passes, min_interval_minutes,
                   max_non_counting_runs, counted_passes, non_counting_runs, started_by,
                   start_reason, started_at, expires_at, created_at, updated_at
               ) VALUES('legacy-attempt','governance-stage3',1,'ACTIVE',?,?,5,30,3,0,0,
                        'operator','legacy',?,?,?,?)""",
            (TARGET_SHA, DEFAULT_GOVERNANCE_POLICY.revision, stamp,
             (NOW + timedelta(hours=72)).isoformat(), stamp, stamp),
        )
        connection.execute(
            """INSERT INTO flaky_probe_trigger
               VALUES('legacy-trigger','legacy-attempt','legacy-request',?,?, 'PENDING',?,?)""",
            (f"sha256:{'d' * 64}", TARGET_SHA, stamp, stamp),
        )
    with pytest.raises(FlakyStoreError) as captured:
        migrate_store(database)
    assert captured.value.code == "migration_live_probe_attempt"


def test_probe_jenkinsfile_has_fixed_parameters_and_claims_before_checkout():
    text = (Path(__file__).parents[2] / "Jenkinsfile.probe").read_text(encoding="utf-8")
    assert "agent none" in text
    assert "timeout(time: 73, unit: 'HOURS')" in text
    assert "disableConcurrentBuilds(abortPrevious: false)" in text
    assert text.count("name: 'TRIGGER_ID'") == 1
    assert text.count("name: 'DISPATCH_TOKEN'") == 1
    assert text.count("name: 'PLAN_DIGEST'") == 1
    assert "SMOKE_TARGET" not in text
    assert text.index("quality.probe_job claim") < text.index("checkout(")
    assert "sleep time: 30, unit: 'MINUTES'" in text
    assert "QUALITY_FLAKY_CONTROLLER_ROOT" in text
    assert "QUALITY_FLAKY_TARGET_PYTHON" in text
    assert "credentialsId: 'flaky-probe-db'" not in text
    assert "allowEmpty: true" in text
    assert text.count("'QUALITY_FLAKY_DB_PATH='") == 2
    assert text.count("'DISPATCH_TOKEN='") == 2
    assert "PROBE_CONTROLLER_NODE_NAME" in text
    assert "PROBE_CONTROLLER_OS_IDENTITY" in text
    assert text.count("Probe target must use a different Jenkins node and OS identity") == 2

    checklist = (
        Path(__file__).parents[2]
        / "dev"
        / "flaky治理MVP阶段3JenkinsProbeJob配置清单.md"
    ).read_text(encoding="utf-8")
    assert "不得同时配置 `probe-target-restricted` label" in checklist
    assert "使用不同的受限 OS 身份" in checklist
    assert "target 身份验证 controller root、数据库和密钥文件均不可读" in checklist


def _control(tmp_path):
    database = (tmp_path / "flaky.sqlite3").resolve()
    migrate_store(database)
    _seed_governance(database)
    runtime = ProbeRuntimeConfig(
        requested_enabled=True,
        enabled=True,
        jenkins_origin="https://jenkins.example.test",
        job_full_name="quality/probe",
        controller_commit_sha=CONTROLLER_SHA,
        controller_jenkinsfile_sha256="d" * 64,
    )
    return database, ProbeControlService(database, runtime, target_resolver=lambda: TARGET_SHA), "governance-stage3"


def _seed_governance(database):
    stamp = NOW.isoformat()
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(
            "INSERT INTO flaky_case_epoch VALUES(?,?,?,?,?,?,?,?,?,?)",
            ("scope-stage3", "module/smoke/test_probe.py::test_case", "overseas", "serial", 1,
             "identity-v1", "environment-v1", "execution-profile-v1", stamp, stamp),
        )
        connection.execute(
            """INSERT INTO flaky_identity(
                   flaky_key, epoch_scope_key, case_id, param_hash, environment,
                   execution_profile, state_epoch, current_detection_generation,
                   created_at, updated_at
               ) VALUES(?,?,?,?,?,?,1,1,?,?)""",
            ("flaky-stage3", "scope-stage3", "module/smoke/test_probe.py::test_case",
             "param-stage3", "overseas", "serial", stamp, stamp),
        )
        connection.execute(
            """INSERT INTO flaky_governance(
                   governance_id, flaky_key, status, owner, reason, created_by,
                   created_at, expires_at, row_version, legacy_governance
               ) VALUES(?,?,'ACTIVE','owner','confirmed','operator',?,?,1,0)""",
            ("governance-stage3", "flaky-stage3", stamp, (NOW + timedelta(days=7)).isoformat()),
        )


def _create_request(governance_id):
    return ProbeCreateRequest(
        governance_id=governance_id,
        reason="verify fix",
        row_version=1,
        request_id="12345678-1234-4234-8234-123456789abc",
    )


def _running(control, governance_id):
    created = control.create_attempt(_create_request(governance_id), now=NOW)
    gateway = FakeJenkins()
    control.dispatch_once(gateway, now=NOW + timedelta(seconds=1))
    control.claim(
        trigger_id=created["trigger_id"], dispatch_token=gateway.tokens[0],
        plan_digest=created["plan_digest"], job_full_name="quality/probe",
        build_number=7, now=NOW + timedelta(seconds=2),
    )
    return created, gateway.tokens[0]


def _probe_import(
    control,
    created,
    round_row,
    started,
    *,
    environment="overseas",
    execution_profile="serial",
):
    plan = control.get_plan(created["attempt_id"])
    finished = started + timedelta(seconds=1)
    run_id = round_row["run_id"]
    run = RunRecord(
        run_id=run_id, job_name="quality/probe", build_number="7", branch="dev3",
        commit_sha=TARGET_SHA, trigger="jenkins", environment=environment,
        start_time=started, end_time=finished, status=RunStatus.FINISHED,
        integrity_status=IntegrityStatus.COMPLETE, run_kind=RunKind.FLAKY_PROBE,
        policy_revision=plan.policy_revision, controller_commit_sha=CONTROLLER_SHA,
        attempt_id=created["attempt_id"], trigger_id=created["trigger_id"],
        plan_digest=created["plan_digest"], round_no=round_row["round_no"],
        target_commit_sha=TARGET_SHA, jenkins_job_name="quality/probe",
        jenkins_build_number="7", fact_schema_version="quality.fact.v1",
        plugin_version="quality-plugin.v1",
    )
    envelope = build_probe_envelope(
        secret=SECRET, key_id="key-v1", attempt_id=created["attempt_id"],
        trigger_id=created["trigger_id"], plan_digest=created["plan_digest"],
        round_no=round_row["round_no"], run_id=run_id, target_commit_sha=TARGET_SHA,
        controller_commit_sha=CONTROLLER_SHA, environment=environment,
        execution_profile=execution_profile, jenkins_origin_id="jenkins.example.test",
        job_full_name="quality/probe", build_number=7, trusted_started_at=started,
        trusted_finished_at=finished, p0_bundle_status="VALID",
        p0_manifest_sha256=f"sha256:{'a' * 64}",
        p0_file_hashes={"run.json": f"sha256:{'b' * 64}"}, outcome="PASS",
    )
    dumped = run.model_dump(mode="json")
    manifest = {
        "manifest_version": "quality.merge.v2", "schema_version": "quality.v2",
        "run_id": run_id, "status": "complete", "merge_version": "p0-merge.v1",
        "fingerprint_version": FINGERPRINT_VERSION,
        "integrity_status": run.integrity_status.value,
        "output_hashes": {name: hashlib.sha256(name.encode()).hexdigest() for name in ("case-results", "failures", "integrity-issues")},
    }
    for field in (
        "run_kind", "policy_revision", "controller_commit_sha", "attempt_id",
        "trigger_id", "plan_digest", "round_no", "target_commit_sha",
        "jenkins_job_name", "jenkins_build_number", "fact_schema_version", "plugin_version",
    ):
        manifest[field] = dumped[field]
    return ProbeImportRequest(
        run=run, manifest=manifest, source_digest=hashlib.sha256(run_id.encode()).hexdigest(),
        outcome=ProbeOutcome.PASS, trusted_started_at=started, p0_trusted=True,
        envelope=envelope,
    )
