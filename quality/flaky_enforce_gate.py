from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field

from master_service import CollectedTestCase, collect_test_case_items
from quality.flaky_identity import (
    FLAKY_ENVIRONMENT_RULE_VERSION,
    FLAKY_EXECUTION_PROFILE_RULE_VERSION,
    FLAKY_IDENTITY_RULE_VERSION,
    build_epoch_scope_key,
    build_flaky_key,
    normalize_flaky_environment,
    normalize_stored_execution_profile,
)
from quality.flaky_read import FlakyReadService
from quality.flaky_shadow import (
    DECISION_FILE_NAME,
    RECONCILIATION_FILE_NAME,
    SNAPSHOT_FILE_NAME,
    SUPPORTED_DATABASE_SCHEMA_VERSION,
    DecisionPlan,
    ReconciliationResult,
    SkipSnapshot,
    read_decision_plan,
    read_reconciliation,
    read_snapshot,
)
from quality.flaky_store import FlakyStoreError, migrate_store
from quality.storage import write_json_atomic


CANARY_SCHEMA_VERSION = "flaky-enforce-canary.v1"
GATE_SCHEMA_VERSION = "flaky-enforce-gate.v1"
CANARY_RELATIVE_PATH = Path("module/smoke/test_flaky_enforce_canary.py")
CANARY_CASE_COUNT = 6


class GateModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)


class CanaryPreparation(GateModel):
    schema_version: Literal["flaky-enforce-canary.v1"] = CANARY_SCHEMA_VERSION
    status: Literal["READY"] = "READY"
    database_schema_version: int
    case_count: int = Field(ge=1)
    governance_count: int = Field(ge=1)
    environment: str
    execution_profile: str
    content_checksum: str


class EnforceGateResult(GateModel):
    schema_version: Literal["flaky-enforce-gate.v1"] = GATE_SCHEMA_VERSION
    status: Literal["READY", "PASSED", "BLOCKED", "ROLLBACK_REQUIRED"]
    phase: Literal["PLAN", "EXECUTION"]
    expected_mode: Literal["enforce", "off"]
    run_id: str
    planned_count: int = Field(ge=0)
    run_count: int = Field(ge=0)
    planned_skip_count: int = Field(ge=0)
    actual_governance_skip_count: int = Field(ge=0)
    max_skip_count: int = Field(ge=0)
    rollback_required: bool
    diagnostic_codes: tuple[str, ...] = ()
    content_checksum: str


def prepare_nonproduction_canary(
    database_path: str | Path,
    *,
    repository_root: str | Path,
    environment: str,
    execution_profile: str,
    now: datetime | None = None,
) -> CanaryPreparation:
    root = Path(repository_root).resolve(strict=True)
    database = Path(database_path)
    if not database.is_absolute():
        raise FlakyStoreError(
            "canary_database_path_invalid",
            "canary database path must be absolute",
        )
    database = database.resolve(strict=False)
    if database.is_relative_to(root):
        raise FlakyStoreError(
            "canary_database_inside_repository",
            "canary database must be outside the repository",
        )
    if database.exists():
        raise FlakyStoreError(
            "canary_database_exists",
            "canary preparation requires a new database path",
        )
    if not database.parent.is_dir():
        raise FlakyStoreError(
            "canary_database_parent_missing",
            "canary database parent must already exist",
        )

    normalized_environment = normalize_flaky_environment(environment)
    normalized_profile = normalize_stored_execution_profile(execution_profile)
    target = root / CANARY_RELATIVE_PATH
    if not target.is_file():
        raise FlakyStoreError(
            "canary_target_missing",
            "tracked canary target does not exist",
        )
    cases = tuple(collect_test_case_items(target))
    _validate_canary_cases(cases)

    migrated = migrate_store(database)
    timestamp = _aware_utc(now or datetime.now(UTC))
    stamp = timestamp.isoformat()
    expires_at = (timestamp + timedelta(days=1)).isoformat()
    scope_keys: set[str] = set()
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN IMMEDIATE")
        for index, case in enumerate(cases):
            case_id = _required(case.case_id, "case_id")
            param_hash = _required(case.param_hash, "param_hash")
            scope_key = build_epoch_scope_key(
                case_id,
                normalized_environment,
                normalized_profile,
            )
            if scope_key not in scope_keys:
                connection.execute(
                    """INSERT INTO flaky_case_epoch(
                           epoch_scope_key, case_id, environment, execution_profile,
                           current_epoch, identity_rule_version,
                           environment_rule_version, execution_profile_rule_version,
                           created_at, updated_at
                       ) VALUES(?,?,?,?,1,?,?,?,?,?)""",
                    (
                        scope_key,
                        case_id,
                        normalized_environment,
                        normalized_profile,
                        FLAKY_IDENTITY_RULE_VERSION,
                        FLAKY_ENVIRONMENT_RULE_VERSION,
                        FLAKY_EXECUTION_PROFILE_RULE_VERSION,
                        stamp,
                        stamp,
                    ),
                )
                scope_keys.add(scope_key)
            flaky_key = build_flaky_key(
                case_id,
                param_hash,
                normalized_environment,
                normalized_profile,
                1,
            )
            suffix = hashlib.sha256(flaky_key.encode("utf-8")).hexdigest()
            governance_id = f"governance-canary-{suffix}"
            connection.execute(
                """INSERT INTO flaky_identity(
                       flaky_key, epoch_scope_key, case_id, param_hash,
                       environment, execution_profile, state_epoch,
                       current_detection_generation, created_at, updated_at
                   ) VALUES(?,?,?,?,?,?,1,1,?,?)""",
                (
                    flaky_key,
                    scope_key,
                    case_id,
                    param_hash,
                    normalized_environment,
                    normalized_profile,
                    stamp,
                    stamp,
                ),
            )
            connection.execute(
                """INSERT INTO flaky_governance(
                       governance_id, flaky_key, status, owner, reason, created_by,
                       created_at, expires_at, row_version, legacy_governance
                   ) VALUES(?,?,'ACTIVE','stage5-canary',
                            'non-production enforce canary','stage5-canary',?,?,1,0)""",
                (governance_id, flaky_key, stamp, expires_at),
            )
            connection.execute(
                """INSERT INTO flaky_governance_event(
                       event_id, governance_id, event_type, causal_id,
                       from_status, to_status, actor, reason, created_at
                   ) VALUES(?,?,'quarantined',?,NULL,'ACTIVE','stage5-canary',
                            'non-production enforce canary',?)""",
                (
                    f"event-canary-{suffix}",
                    governance_id,
                    governance_id,
                    stamp,
                ),
            )
        connection.commit()

    source = FlakyReadService(database).snapshot_source()
    if len(source.candidates) != CANARY_CASE_COUNT:
        raise FlakyStoreError(
            "canary_seed_count_mismatch",
            "canary database does not contain the expected active governance set",
        )
    content = {
        "schema_version": CANARY_SCHEMA_VERSION,
        "status": "READY",
        "database_schema_version": migrated.schema_version,
        "case_count": len(cases),
        "governance_count": len(source.candidates),
        "environment": normalized_environment,
        "execution_profile": normalized_profile,
    }
    return CanaryPreparation(**content, content_checksum=_checksum(content))


def evaluate_enforce_gate(
    artifact_directory: str | Path,
    *,
    run_id: str,
    expected_mode: Literal["enforce", "off"],
    max_skip_count: int,
    require_executed: bool,
) -> EnforceGateResult:
    if max_skip_count < 0:
        raise ValueError("max_skip_count must be greater than or equal to zero")
    run_id = _required(run_id, "run_id")
    directory = Path(artifact_directory)
    snapshot: SkipSnapshot | None = None
    plan: DecisionPlan | None = None
    reconciliation: ReconciliationResult | None = None
    diagnostics: list[str] = []
    try:
        snapshot = read_snapshot(
            directory / SNAPSHOT_FILE_NAME,
            expected_run_id=run_id,
        )
    except Exception as error:
        diagnostics.append(_error_code(error, "snapshot_artifact_invalid"))
    try:
        plan = read_decision_plan(
            directory / DECISION_FILE_NAME,
            expected_run_id=run_id,
        )
    except Exception as error:
        diagnostics.append(_error_code(error, "decision_artifact_invalid"))
    try:
        reconciliation = read_reconciliation(directory / RECONCILIATION_FILE_NAME)
    except Exception as error:
        diagnostics.append(_error_code(error, "reconciliation_artifact_invalid"))

    if snapshot is not None:
        _check_snapshot(snapshot, expected_mode, diagnostics)
    if plan is not None:
        _check_plan(plan, expected_mode, max_skip_count, diagnostics)
    if snapshot is not None and plan is not None:
        if (
            plan.snapshot_id != snapshot.snapshot_id
            or plan.snapshot_checksum != snapshot.content_checksum
        ):
            diagnostics.append("snapshot_decision_mismatch")
        if plan.policy_revision != snapshot.policy_revision:
            diagnostics.append("snapshot_policy_mismatch")
    if plan is not None and reconciliation is not None:
        if (
            reconciliation.run_id != plan.run_id
            or reconciliation.decisions_checksum != plan.content_checksum
        ):
            diagnostics.append("decision_reconciliation_mismatch")
        _check_reconciliation(
            plan,
            reconciliation,
            expected_mode,
            require_executed,
            diagnostics,
        )

    planned_count = len(plan.decisions) if plan is not None else 0
    run_count = plan.run_count if plan is not None else 0
    planned_skip_count = plan.skip_count if plan is not None else 0
    actual_skip_count = (
        reconciliation.actual_governance_skip_count
        if reconciliation is not None
        else 0
    )
    codes = tuple(sorted(set(diagnostics)))
    phase: Literal["PLAN", "EXECUTION"] = (
        "EXECUTION" if require_executed else "PLAN"
    )
    rollback_required = bool(codes and require_executed and expected_mode == "enforce")
    status: Literal["READY", "PASSED", "BLOCKED", "ROLLBACK_REQUIRED"]
    if rollback_required:
        status = "ROLLBACK_REQUIRED"
    elif codes:
        status = "BLOCKED"
    elif require_executed:
        status = "PASSED"
    else:
        status = "READY"
    content = {
        "schema_version": GATE_SCHEMA_VERSION,
        "status": status,
        "phase": phase,
        "expected_mode": expected_mode,
        "run_id": run_id,
        "planned_count": planned_count,
        "run_count": run_count,
        "planned_skip_count": planned_skip_count,
        "actual_governance_skip_count": actual_skip_count,
        "max_skip_count": max_skip_count,
        "rollback_required": rollback_required,
        "diagnostic_codes": codes,
    }
    return EnforceGateResult(**content, content_checksum=_checksum(content))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Flaky Enforce non-production canary")
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--db", required=True)
    prepare.add_argument("--repository-root", required=True)
    prepare.add_argument("--environment", choices=["china", "overseas"], required=True)
    prepare.add_argument("--execution-profile", choices=["serial", "parallel"], required=True)
    prepare.add_argument("--output")
    prepare.add_argument("--acknowledge-nonproduction", action="store_true")
    verify = subparsers.add_parser("verify")
    verify.add_argument("--artifact-dir", required=True)
    verify.add_argument("--run-id", required=True)
    verify.add_argument("--expect-mode", choices=["enforce", "off"], required=True)
    verify.add_argument("--max-skip-count", type=int, required=True)
    verify.add_argument("--require-executed", action="store_true")
    verify.add_argument("--output")
    parsed = parser.parse_args(argv)
    try:
        if parsed.command == "prepare":
            if not parsed.acknowledge_nonproduction:
                raise FlakyStoreError(
                    "canary_nonproduction_ack_required",
                    "canary preparation requires explicit non-production acknowledgement",
                )
            result: GateModel = prepare_nonproduction_canary(
                parsed.db,
                repository_root=parsed.repository_root,
                environment=parsed.environment,
                execution_profile=parsed.execution_profile,
            )
        else:
            result = evaluate_enforce_gate(
                parsed.artifact_dir,
                run_id=parsed.run_id,
                expected_mode=parsed.expect_mode,
                max_skip_count=parsed.max_skip_count,
                require_executed=parsed.require_executed,
            )
        payload = result.model_dump(mode="json")
        if parsed.output:
            write_json_atomic(Path(parsed.output), payload)
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if payload["status"] in {"READY", "PASSED"} else 2
    except Exception as error:
        payload = {
            "schema_version": GATE_SCHEMA_VERSION,
            "status": "BLOCKED",
            "error_code": _error_code(error, "canary_operation_failed"),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 2


def _validate_canary_cases(cases: tuple[CollectedTestCase, ...]) -> None:
    expected_path = CANARY_RELATIVE_PATH.as_posix()
    if len(cases) != CANARY_CASE_COUNT:
        raise FlakyStoreError(
            "canary_collection_count_mismatch",
            "canary target must collect exactly six cases",
        )
    if any(
        not case.case_id
        or not case.param_hash
        or case.normalized_case_path != expected_path
        or case.markers & {"skip", "skipif", "xfail"}
        for case in cases
    ):
        raise FlakyStoreError(
            "canary_collection_identity_invalid",
            "canary cases must have complete identities and no business skip markers",
        )


def _check_snapshot(
    snapshot: SkipSnapshot,
    expected_mode: str,
    diagnostics: list[str],
) -> None:
    expected_status = "READY" if expected_mode == "enforce" else "DISABLED"
    if snapshot.status != expected_status:
        diagnostics.append("gate_snapshot_status_mismatch")
    if snapshot.branch != "dev3":
        diagnostics.append("gate_branch_not_allowed")
    if snapshot.database_schema_version != SUPPORTED_DATABASE_SCHEMA_VERSION:
        diagnostics.append("gate_database_schema_mismatch")
    if snapshot.mode_effective != expected_mode:
        diagnostics.append("gate_snapshot_mode_mismatch")
    if snapshot.diagnostic_codes:
        diagnostics.append("gate_snapshot_degraded")


def _check_plan(
    plan: DecisionPlan,
    expected_mode: str,
    max_skip_count: int,
    diagnostics: list[str],
) -> None:
    if plan.mode_effective != expected_mode:
        diagnostics.append("gate_plan_mode_mismatch")
    if plan.integrity_status != "OK" or plan.fail_open_count:
        diagnostics.append("gate_plan_degraded")
    business_skips = sum(
        item.decision == "SKIP" and item.business_marker_present
        for item in plan.decisions
    )
    if business_skips:
        diagnostics.append("gate_business_skip_conflict")
    if expected_mode == "enforce":
        if plan.would_skip_count:
            diagnostics.append("gate_shadow_decision_present")
        if plan.run_count:
            diagnostics.append("gate_unmatched_case_present")
        if not 1 <= plan.skip_count <= max_skip_count:
            diagnostics.append("gate_skip_budget_exceeded")
    else:
        if plan.skip_count or plan.would_skip_count:
            diagnostics.append("gate_rollback_skip_present")
        if plan.run_count != len(plan.decisions):
            diagnostics.append("gate_rollback_run_count_mismatch")


def _check_reconciliation(
    plan: DecisionPlan,
    reconciliation: ReconciliationResult,
    expected_mode: str,
    require_executed: bool,
    diagnostics: list[str],
) -> None:
    if require_executed:
        expected_actual = plan.skip_count if expected_mode == "enforce" else 0
        if reconciliation.status != "OK":
            diagnostics.append("gate_reconciliation_degraded")
        if reconciliation.actual_governance_skip_count != expected_actual:
            diagnostics.append("gate_actual_skip_mismatch")
        if reconciliation.observed_count != reconciliation.planned_count:
            diagnostics.append("gate_observed_count_mismatch")
    elif reconciliation.status != "NOT_EXECUTED":
        diagnostics.append("gate_plan_only_execution_observed")


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("time must include timezone information")
    return value.astimezone(UTC)


def _required(value: object, name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{name} must not be empty")
    return text


def _error_code(error: Exception, fallback: str) -> str:
    if isinstance(error, FlakyStoreError):
        return error.code
    return fallback


def _checksum(payload: dict[str, object]) -> str:
    canonical = json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
