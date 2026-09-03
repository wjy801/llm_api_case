"""Trusted, environment-only entry points used by the fixed Probe Jenkins job."""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

from quality.flaky_probe import (
    ProbeControlService,
    load_probe_controller_runtime_config,
)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1 or args[0] not in {"claim", "plan", "authorize", "start", "import", "finalize"}:
        print("usage: python -m quality.probe_job claim|plan|authorize|start|import|finalize", file=sys.stderr)
        return 2
    try:
        controller_root = Path(_env("QUALITY_FLAKY_CONTROLLER_ROOT")).resolve(strict=True)
        runtime = load_probe_controller_runtime_config(repository_root=controller_root)
        if runtime.warning is not None:
            raise ValueError(runtime.warning)
        _validate_controller_checkout(controller_root, runtime)
        database_path = Path(_env("QUALITY_FLAKY_DB_PATH"))
        if not database_path.is_absolute():
            raise ValueError("QUALITY_FLAKY_DB_PATH must be an absolute shared path")
        database_path = database_path.resolve()
        if database_path == controller_root or controller_root in database_path.parents:
            raise ValueError("QUALITY_FLAKY_DB_PATH must be outside the controller checkout")
        service = ProbeControlService(database_path, runtime)
        now = datetime.now(UTC)
        action = args[0]
        if action == "claim":
            token = _env("DISPATCH_TOKEN")
            result = service.claim(
                trigger_id=_env("TRIGGER_ID"),
                dispatch_token=token,
                plan_digest=_env("PLAN_DIGEST"),
                job_full_name=_env("JOB_NAME"),
                build_number=int(_env("BUILD_NUMBER")),
                now=now,
            )
            token = ""
        elif action == "plan":
            result = service.get_plan(_env("PROBE_ATTEMPT_ID")).model_dump(mode="json")
        elif action == "authorize":
            result = service.authorize_round(_env("PROBE_ATTEMPT_ID"), now=now)
        elif action == "start":
            result = service.start_round(
                _env("PROBE_ATTEMPT_ID"),
                int(_env("PROBE_ROUND_NO")),
                actual_target_commit_sha=_env("PROBE_ACTUAL_TARGET_SHA"),
                now=now,
            )
        elif action == "import":
            result = _import_round(service, runtime, now)
        else:
            result = service.finalize_build(_env("TRIGGER_ID"), now=now)
    except Exception as error:
        code = getattr(error, "code", "probe_job_failed")
        print(json.dumps({"status": "ERROR", "error_code": code}, sort_keys=True))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


def _env(name: str) -> str:
    value = os.environ.get(name)
    if value is None or not value.strip():
        raise ValueError(f"required environment variable {name} is missing")
    return value.strip()


def _validate_controller_checkout(controller_root: Path, runtime) -> None:
    if Path.cwd().resolve() != controller_root:
        raise ValueError("Probe controller command must run from QUALITY_FLAKY_CONTROLLER_ROOT")
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=controller_root,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ValueError("Probe controller commit cannot be verified") from error
    if completed.stdout.strip() != runtime.controller_commit_sha:
        raise ValueError("Probe controller checkout does not match the configured commit")
    try:
        status = subprocess.run(
            ["git", "status", "--porcelain=v1", "--untracked-files=all"],
            cwd=controller_root,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ValueError("Probe controller working tree cannot be verified") from error
    if status.stdout.strip():
        raise ValueError("Probe controller working tree must match the configured commit")
    jenkinsfile = controller_root / "Jenkinsfile.probe"
    try:
        digest = hashlib.sha256(jenkinsfile.read_bytes()).hexdigest()
    except OSError as error:
        raise ValueError("Probe controller Jenkinsfile cannot be read") from error
    if digest != runtime.controller_jenkinsfile_sha256:
        raise ValueError("Probe controller Jenkinsfile digest does not match configuration")


def _select_probe_candidate(plan, prepared):
    identity_matches = [
        item
        for item in prepared.candidates
        if item.case_id == plan.case_id and item.param_hash == plan.param_hash
    ]
    if len(identity_matches) != 1:
        return None, "probe_identity_not_unique"
    candidate = identity_matches[0]
    if not (
        prepared.run.environment == plan.environment
        and candidate.environment == plan.environment
        and candidate.environment == prepared.run.environment
        and candidate.execution_profile == plan.execution_profile
    ):
        return candidate, "probe_execution_identity_mismatch"
    return candidate, None


def _import_round(service: ProbeControlService, runtime, now: datetime) -> dict[str, object]:
    from quality.flaky_importer import prepare_flaky_import
    from quality.flaky_models import FlakyImportRequest, ObservationOutcome
    from quality.flaky_probe import build_probe_envelope, validate_p0_bundle
    from quality.flaky_store.v3_service import FlakyV3Service, ProbeImportRequest
    from quality.flaky_v3 import ProbeOutcome

    output_dir = Path(_env("QUALITY_OUTPUT_DIR")).resolve()
    run_id = _env("QUALITY_RUN_ID")
    plan = service.get_plan(_env("PROBE_ATTEMPT_ID"))
    round_row = service.get_round(plan.attempt_id, int(_env("PROBE_ROUND_NO")))
    bundle_status, manifest_hash, file_hashes, bundle_diagnostics = validate_p0_bundle(output_dir)
    prepared = None
    diagnostics: tuple[str, ...] = tuple(bundle_diagnostics)
    if bundle_status == "VALID":
        try:
            prepared = prepare_flaky_import(
                FlakyImportRequest(
                    run_id=run_id,
                    quality_output_dir=output_dir,
                    database_path=service.database_path,
                )
            )
        except Exception:
            bundle_status = "INVALID"
            manifest_hash = None
            file_hashes = {}
            diagnostics = tuple(sorted(set((*diagnostics, "probe_p0_structure_invalid"))))
    if prepared is None:
        run, manifest, source_digest = _controller_origin_invalid_p0(
            plan=plan,
            round_row=round_row,
            runtime=runtime,
            run_id=run_id,
            now=now,
            diagnostics=diagnostics,
        )
        outcome = ProbeOutcome.NO_DATA
        trusted_failure = False
    else:
        candidate, mismatch_code = _select_probe_candidate(plan, prepared)
        observed_environment = prepared.run.environment
        observed_execution_profile = (
            candidate.execution_profile if candidate is not None else plan.execution_profile
        )
        if mismatch_code is None:
            outcome = (
                ProbeOutcome.PASS
                if candidate.observation_outcome is ObservationOutcome.PASS
                else ProbeOutcome.FAIL
            )
            trusted_failure = outcome is ProbeOutcome.FAIL
        else:
            outcome = ProbeOutcome.NO_DATA
            trusted_failure = False
            diagnostics = tuple(sorted(set((*diagnostics, mismatch_code))))
        run = prepared.run
        manifest = prepared.manifest
        source_digest = prepared.metadata.source_digest
    if prepared is None:
        observed_environment = plan.environment
        observed_execution_profile = plan.execution_profile
    secret_file = runtime.evidence_hmac_key_file
    if secret_file is None:
        raise ValueError("evidence HMAC key file is required")
    secret = secret_file.read_bytes().strip()
    if run.end_time is None or run.jenkins_build_number is None:
        raise ValueError("finished Probe run metadata is required")
    envelope = build_probe_envelope(
        secret=secret,
        key_id=runtime.evidence_key_id,
        attempt_id=plan.attempt_id,
        trigger_id=_env("TRIGGER_ID"),
        plan_digest=plan.plan_digest,
        round_no=int(_env("PROBE_ROUND_NO")),
        run_id=run_id,
        target_commit_sha=plan.target_commit_sha,
        controller_commit_sha=plan.controller_commit_sha,
        environment=observed_environment,
        execution_profile=observed_execution_profile,
        jenkins_origin_id=str(runtime.jenkins_origin),
        job_full_name=str(runtime.job_full_name),
        build_number=int(run.jenkins_build_number),
        trusted_started_at=round_row["started_at"],
        trusted_finished_at=now,
        p0_bundle_status=bundle_status,
        p0_manifest_sha256=manifest_hash,
        p0_file_hashes=file_hashes,
        outcome=outcome.value,
        trusted_failure=trusted_failure,
        rerun_supported=True,
        diagnostic_codes=diagnostics,
    )
    importer = FlakyV3Service(
        service.database_path,
        probe_evidence_keys={runtime.evidence_key_id: secret},
    )
    imported = importer.import_probe(
        ProbeImportRequest(
            run=run,
            manifest=manifest,
            source_digest=source_digest,
            outcome=outcome,
            trusted_started_at=datetime.fromisoformat(
                str(round_row["started_at"]).replace("Z", "+00:00")
            ),
            p0_trusted=bundle_status == "VALID",
            trusted_failure=trusted_failure,
            diagnostic_codes=diagnostics,
            envelope=envelope,
        ),
        now=now,
    )
    attempt_status = importer.recovery_status(plan.flaky_key)["attempt"]
    return {
        **imported,
        "attempt_status": (
            attempt_status["status"] if attempt_status is not None else None
        ),
    }


def _controller_origin_invalid_p0(
    *,
    plan,
    round_row: dict[str, object],
    runtime,
    run_id: str,
    now: datetime,
    diagnostics: tuple[str, ...],
):
    from quality.classifier import FINGERPRINT_VERSION
    from quality.models import IntegrityStatus, RunKind, RunRecord, RunStatus

    started_at = datetime.fromisoformat(
        str(round_row["started_at"]).replace("Z", "+00:00")
    )
    build_number = _env("BUILD_NUMBER")
    run = RunRecord(
        run_id=run_id,
        job_name=str(runtime.job_full_name),
        build_number=build_number,
        branch="dev3",
        commit_sha=plan.target_commit_sha,
        trigger="jenkins",
        environment=plan.environment,
        start_time=started_at,
        end_time=now,
        status=RunStatus.FINISHED,
        integrity_status=IntegrityStatus.FAILED,
        run_kind=RunKind.FLAKY_PROBE,
        policy_revision=plan.policy_revision,
        controller_commit_sha=plan.controller_commit_sha,
        attempt_id=plan.attempt_id,
        trigger_id=_env("TRIGGER_ID"),
        plan_digest=plan.plan_digest,
        round_no=int(round_row["round_no"]),
        target_commit_sha=plan.target_commit_sha,
        jenkins_job_name=str(runtime.job_full_name),
        jenkins_build_number=build_number,
        fact_schema_version=plan.fact_schema_version,
        plugin_version="quality-plugin.v1",
    )
    dumped = run.model_dump(mode="json")
    empty_digest = hashlib.sha256(b"").hexdigest()
    manifest: dict[str, object] = {
        "manifest_version": "quality.merge.v2",
        "schema_version": "quality.v2",
        "run_id": run_id,
        "status": "complete",
        "merge_version": "p0-merge.v1",
        "fingerprint_version": FINGERPRINT_VERSION,
        "integrity_status": run.integrity_status.value,
        "output_hashes": {
            "case-results": empty_digest,
            "failures": empty_digest,
            "integrity-issues": empty_digest,
        },
    }
    for field in (
        "run_kind", "policy_revision", "controller_commit_sha", "attempt_id",
        "trigger_id", "plan_digest", "round_no", "target_commit_sha",
        "jenkins_job_name", "jenkins_build_number", "fact_schema_version", "plugin_version",
    ):
        manifest[field] = dumped[field]
    source_payload = {
        "schema_version": "probe-controller-origin-invalid-p0.v1",
        "run": dumped,
        "manifest": manifest,
        "diagnostic_codes": list(diagnostics),
    }
    source_digest = hashlib.sha256(
        json.dumps(
            source_payload,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    return run, manifest, source_digest


if __name__ == "__main__":
    raise SystemExit(main())
