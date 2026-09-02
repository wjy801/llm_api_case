from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import sys

from quality.aggregator import QualityMergeRequest, merge_quality_run
from quality.models import IntegrityStatus
from quality.metrics import RunMetricsAggregationRequest, aggregate_run_metrics
from quality.metrics_models import RunMetricsStatus
from quality.flaky_importer import (
    cancel_flaky_quarantine,
    check_flaky_database,
    confirm_flaky_state,
    evaluate_flaky_state,
    import_flaky_history,
    list_flaky_governance,
    mark_flaky_not_flaky,
    query_flaky_history,
    query_flaky_states,
    quarantine_flaky_state,
    rebuild_flaky_states,
    reset_flaky_epoch,
    start_flaky_recovery,
)
from quality.flaky_models import (
    EpochResetRequest,
    FlakyEvaluationStatus,
    FlakyImportRequest,
    FlakyImportStatus,
    FlakyManualActionRequest,
    FlakyQuarantineRequest,
    GovernanceStatus,
)
from quality.semantic_aggregator import SemanticMergeRequest, merge_semantic_run
from quality.flaky_store import FlakyStoreError, migrate_store
from quality.flaky_store.v3_service import (
    FlakyV3Service,
    RecoveryCancelRequest,
    RecoveryCloseRequest,
    RecoveryStartRequest,
)
from quality.flaky_probe import (
    FixedJenkinsClient,
    GitTargetResolver,
    ProbeControlService,
    load_probe_evidence_key,
    load_probe_runtime_config,
    validate_git_remote,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Quality fact, Metrics, Semantic, and Flaky tools."
    )
    subparsers = parser.add_subparsers(dest="command")
    merge_parser = subparsers.add_parser("merge", help="merge one quality run")
    merge_parser.add_argument("--run-id", required=True)
    merge_parser.add_argument("--output-dir", default="reports/quality")
    merge_parser.add_argument("--expected-execution", action="append", default=[])
    merge_parser.add_argument("--expected-case-count", type=int)
    merge_parser.add_argument("--junit", action="append", default=[])
    semantic_merge_parser = subparsers.add_parser(
        "semantic-merge",
        help="merge one quality semantic run",
    )
    semantic_merge_parser.add_argument("--run-id", required=True)
    semantic_merge_parser.add_argument("--output-dir", default="reports/quality")
    metrics_parser = subparsers.add_parser(
        "metrics-aggregate",
        help="aggregate trusted quality and semantic facts for one run",
    )
    metrics_parser.add_argument("--run-id", required=True)
    metrics_parser.add_argument("--output-dir", default="reports/quality")
    flaky_import_parser = subparsers.add_parser(
        "flaky-import",
        help="import trusted Case observations into Flaky history",
    )
    flaky_import_parser.add_argument("--run-id", required=True)
    flaky_import_parser.add_argument("--output-dir", default="reports/quality")
    flaky_import_parser.add_argument("--db", required=True)
    flaky_history_parser = subparsers.add_parser(
        "flaky-history",
        help="query Case observation history",
    )
    flaky_history_parser.add_argument("--db", required=True)
    flaky_history_parser.add_argument("--case-id", required=True)
    flaky_history_parser.add_argument("--param-hash")
    flaky_history_parser.add_argument("--environment")
    flaky_history_parser.add_argument("--execution-profile")
    flaky_history_parser.add_argument("--state-epoch", type=int)
    flaky_reset_parser = subparsers.add_parser(
        "flaky-reset-epoch",
        help="explicitly increment one Case epoch scope",
    )
    flaky_reset_parser.add_argument("--db", required=True)
    flaky_reset_parser.add_argument("--case-id", required=True)
    flaky_reset_parser.add_argument("--environment", required=True)
    flaky_reset_parser.add_argument("--execution-profile", required=True)
    flaky_reset_parser.add_argument("--actor", required=True)
    flaky_reset_parser.add_argument("--reason", required=True)
    flaky_db_check_parser = subparsers.add_parser(
        "flaky-db-check",
        help="check Flaky history schema and SQLite integrity",
    )
    flaky_db_check_parser.add_argument("--db", required=True)
    flaky_db_migrate_parser = subparsers.add_parser(
        "flaky-db-migrate",
        help="explicitly apply pending Flaky database migrations",
    )
    flaky_db_migrate_parser.add_argument("--db", required=True)
    flaky_state_evaluate_parser = subparsers.add_parser(
        "flaky-state-evaluate",
        help="evaluate Flaky state for one imported run",
    )
    flaky_state_evaluate_parser.add_argument("--db", required=True)
    flaky_state_evaluate_parser.add_argument("--run-id", required=True)
    flaky_state_evaluate_parser.add_argument(
        "--output-dir", default="reports/quality"
    )
    flaky_state_parser = subparsers.add_parser(
        "flaky-state",
        help="query current Flaky state projections",
    )
    flaky_state_parser.add_argument("--db", required=True)
    flaky_state_parser.add_argument("--case-id", required=True)
    flaky_state_parser.add_argument("--param-hash")
    flaky_state_parser.add_argument("--environment")
    flaky_state_parser.add_argument("--execution-profile")
    flaky_state_parser.add_argument("--state-epoch", type=int)
    flaky_rebuild_parser = subparsers.add_parser(
        "flaky-state-rebuild",
        help="dry-run or apply deterministic Flaky state replay",
    )
    flaky_rebuild_parser.add_argument("--db", required=True)
    rebuild_mode = flaky_rebuild_parser.add_mutually_exclusive_group(required=True)
    rebuild_mode.add_argument("--dry-run", action="store_true")
    rebuild_mode.add_argument("--apply", action="store_true")
    flaky_rebuild_parser.add_argument("--actor")
    flaky_rebuild_parser.add_argument("--reason")
    for command, help_text in (
        ("flaky-confirm", "manually confirm a SUSPECTED Flaky state"),
        ("flaky-mark-not-flaky", "manually correct a state to STABLE"),
        ("flaky-cancel-quarantine", "cancel a mistaken quarantine"),
    ):
        action_parser = subparsers.add_parser(command, help=help_text)
        action_parser.add_argument("--db", required=True)
        action_parser.add_argument("--flaky-key", required=True)
        action_parser.add_argument("--actor", required=True)
        action_parser.add_argument("--reason", required=True)
        if command in {"flaky-confirm", "flaky-mark-not-flaky"}:
            action_parser.add_argument("--detection-generation", required=True, type=int)
            action_parser.add_argument("--comparability-fingerprint", required=True)
    recovery_start_parser = subparsers.add_parser(
        "flaky-recovery-start",
        help="create a local Probe verification attempt (does not call Jenkins)",
    )
    recovery_start_parser.add_argument("--db", required=True)
    recovery_start_parser.add_argument("--flaky-key", required=True)
    recovery_start_parser.add_argument("--target-commit-sha", required=True)
    recovery_start_parser.add_argument("--actor", required=True)
    recovery_start_parser.add_argument("--reason", required=True)
    recovery_start_parser.add_argument("--request-id", required=True)
    recovery_start_parser.add_argument("--expected-row-version", required=True, type=int)
    recovery_status_parser = subparsers.add_parser(
        "flaky-recovery-status", help="show detection, governance, and attempt axes"
    )
    recovery_status_parser.add_argument("--db", required=True)
    recovery_status_parser.add_argument("--flaky-key", required=True)
    recovery_close_parser = subparsers.add_parser(
        "flaky-recovery-close", help="manually close a READY_TO_CLOSE attempt"
    )
    recovery_close_parser.add_argument("--db", required=True)
    recovery_close_parser.add_argument("--attempt-id", required=True)
    recovery_close_parser.add_argument("--actor", required=True)
    recovery_close_parser.add_argument("--reason", required=True)
    recovery_close_parser.add_argument("--expected-row-version", required=True, type=int)
    recovery_close_parser.add_argument("--verified-branch-head", required=True)
    recovery_cancel_parser = subparsers.add_parser(
        "flaky-recovery-cancel", help="cancel a live Probe verification attempt"
    )
    recovery_cancel_parser.add_argument("--db", required=True)
    recovery_cancel_parser.add_argument("--attempt-id", required=True)
    recovery_cancel_parser.add_argument("--actor", required=True)
    recovery_cancel_parser.add_argument("--reason", required=True)
    recovery_cancel_parser.add_argument("--expected-row-version", required=True, type=int)
    probe_dispatch_parser = subparsers.add_parser(
        "flaky-probe-dispatch-once", help="dispatch at most one pending Probe trigger"
    )
    probe_dispatch_parser.add_argument("--db", required=True)
    probe_reconcile_parser = subparsers.add_parser(
        "flaky-probe-reconcile-once", help="reconcile at most one Probe trigger"
    )
    probe_reconcile_parser.add_argument("--db", required=True)
    probe_claim_parser = subparsers.add_parser(
        "flaky-probe-claim", help="claim one Probe build before checkout"
    )
    probe_claim_parser.add_argument("--db", required=True)
    probe_claim_parser.add_argument("--trigger-id", required=True)
    probe_claim_parser.add_argument("--plan-digest", required=True)
    probe_claim_parser.add_argument("--job-full-name", required=True)
    probe_claim_parser.add_argument("--build-number", required=True, type=int)
    probe_claim_parser.add_argument("--dispatch-token-env", default="DISPATCH_TOKEN")
    probe_plan_parser = subparsers.add_parser(
        "flaky-probe-plan", help="read one immutable Probe plan"
    )
    probe_plan_parser.add_argument("--db", required=True)
    probe_plan_parser.add_argument("--attempt-id", required=True)
    probe_authorize_parser = subparsers.add_parser(
        "flaky-probe-authorize-round", help="authorize the next Probe round"
    )
    probe_authorize_parser.add_argument("--db", required=True)
    probe_authorize_parser.add_argument("--attempt-id", required=True)
    probe_start_parser = subparsers.add_parser(
        "flaky-probe-start-round", help="record the detached target checkout"
    )
    probe_start_parser.add_argument("--db", required=True)
    probe_start_parser.add_argument("--attempt-id", required=True)
    probe_start_parser.add_argument("--round-no", required=True, type=int)
    probe_start_parser.add_argument("--actual-target-commit-sha", required=True)
    probe_finalize_parser = subparsers.add_parser(
        "flaky-probe-finalize", help="settle a terminal Probe build"
    )
    probe_finalize_parser.add_argument("--db", required=True)
    probe_finalize_parser.add_argument("--trigger-id", required=True)
    flaky_quarantine_parser = subparsers.add_parser(
        "flaky-quarantine",
        help="create an audited quarantine governance lifecycle",
    )
    flaky_quarantine_parser.add_argument("--db", required=True)
    flaky_quarantine_parser.add_argument("--flaky-key", required=True)
    flaky_quarantine_parser.add_argument("--owner", required=True)
    flaky_quarantine_parser.add_argument("--actor", required=True)
    flaky_quarantine_parser.add_argument("--reason", required=True)
    flaky_quarantine_parser.add_argument("--expires-at", required=True)
    flaky_governance_parser = subparsers.add_parser(
        "flaky-governance-list",
        help="list Flaky governance lifecycles",
    )
    flaky_governance_parser.add_argument("--db", required=True)
    flaky_governance_parser.add_argument(
        "--status", choices=[item.value for item in GovernanceStatus]
    )
    flaky_governance_parser.add_argument("--overdue", action="store_true")
    flaky_governance_parser.add_argument("--owner")
    flaky_governance_parser.add_argument("--environment")
    flaky_governance_parser.add_argument("--execution-profile")
    flaky_governance_parser.add_argument("--case-path")
    flaky_governance_parser.add_argument("--keyword")
    flaky_governance_parser.add_argument("--cursor")
    flaky_governance_parser.add_argument("--page-size", type=int, default=50)
    flaky_summary_parser = subparsers.add_parser(
        "flaky-dashboard-summary", help="show the read-only Flaky dashboard summary"
    )
    flaky_summary_parser.add_argument("--db", required=True)
    flaky_case_parser = subparsers.add_parser(
        "flaky-case-detail", help="show one read-only Flaky case detail"
    )
    flaky_case_parser.add_argument("--db", required=True)
    flaky_case_parser.add_argument("--flaky-key", required=True)
    flaky_decisions_parser = subparsers.add_parser(
        "flaky-run-decisions", help="show one Run's immutable Shadow decision summary"
    )
    flaky_decisions_parser.add_argument("--db", required=True)
    flaky_decisions_parser.add_argument("--run-id", required=True)
    flaky_decisions_parser.add_argument("--artifact-dir", required=True)
    flaky_dashboard_parser = subparsers.add_parser(
        "flaky-dashboard", help="serve the loopback-only read-only Flaky dashboard"
    )
    flaky_dashboard_parser.add_argument("--db", required=True)
    flaky_dashboard_parser.add_argument("--artifact-dir")
    flaky_dashboard_parser.add_argument(
        "--host",
        default=os.environ.get("QUALITY_FLAKY_DASHBOARD_HOST", "127.0.0.1"),
    )
    flaky_dashboard_parser.add_argument(
        "--port",
        type=int,
        default=os.environ.get("QUALITY_FLAKY_DASHBOARD_PORT", "8765"),
    )
    try:
        parsed = parser.parse_args(argv)
    except SystemExit as error:
        return int(error.code)

    if parsed.command is None:
        parser.print_help()
        return 2
    if parsed.command == "metrics-aggregate":
        return _metrics_aggregate(parsed)
    if parsed.command == "flaky-import":
        return _flaky_import(parsed)
    if parsed.command == "flaky-history":
        return _flaky_history(parsed)
    if parsed.command == "flaky-reset-epoch":
        return _flaky_reset_epoch(parsed)
    if parsed.command == "flaky-db-check":
        return _flaky_db_check(parsed)
    if parsed.command == "flaky-db-migrate":
        return _flaky_db_migrate(parsed)
    if parsed.command in {
        "flaky-recovery-start",
        "flaky-recovery-status",
        "flaky-recovery-close",
        "flaky-recovery-cancel",
    }:
        return _flaky_recovery(parsed)
    if parsed.command in {
        "flaky-probe-dispatch-once",
        "flaky-probe-reconcile-once",
        "flaky-probe-claim",
        "flaky-probe-plan",
        "flaky-probe-authorize-round",
        "flaky-probe-start-round",
        "flaky-probe-finalize",
    }:
        return _flaky_probe(parsed)
    if parsed.command == "flaky-state-evaluate":
        return _flaky_state_evaluate(parsed)
    if parsed.command == "flaky-state":
        return _flaky_state(parsed)
    if parsed.command == "flaky-state-rebuild":
        return _flaky_state_rebuild(parsed)
    if parsed.command in {
        "flaky-confirm",
        "flaky-mark-not-flaky",
        "flaky-cancel-quarantine",
    }:
        return _flaky_manual_action(parsed)
    if parsed.command == "flaky-quarantine":
        return _flaky_quarantine(parsed)
    if parsed.command == "flaky-governance-list":
        return _flaky_governance_list(parsed)
    if parsed.command in {
        "flaky-dashboard-summary",
        "flaky-case-detail",
        "flaky-run-decisions",
    }:
        return _flaky_read_query(parsed)
    if parsed.command == "flaky-dashboard":
        return _flaky_dashboard(parsed)
    if parsed.command == "semantic-merge":
        result = merge_semantic_run(
            SemanticMergeRequest(
                run_id=parsed.run_id,
                output_dir=Path(parsed.output_dir),
            )
        )
        print(
            "quality semantic merge completed: "
            f"integrity={result.integrity_status.value}, "
            f"operations={result.operations}, "
            f"request_groups={result.request_groups}, "
            f"polling_sessions={result.polling_sessions}"
        )
        return 2 if result.integrity_status is IntegrityStatus.FAILED and result.operations == 0 else 0
    if parsed.expected_case_count is not None and parsed.expected_case_count < 0:
        print("--expected-case-count must be greater than or equal to 0", file=sys.stderr)
        return 2

    result = merge_quality_run(
        QualityMergeRequest(
            run_id=parsed.run_id,
            output_dir=Path(parsed.output_dir),
            expected_execution_ids=tuple(parsed.expected_execution),
            expected_case_count=parsed.expected_case_count,
            junit_files=tuple(Path(path) for path in parsed.junit),
            run_start_time=datetime.now(UTC),
        )
    )
    print(
        "quality merge completed: "
        f"integrity={result.integrity_status.value}, "
        f"cases={result.case_results}, "
        f"requests={result.request_metrics}, "
        f"failures={result.failure_occurrences}"
    )
    return 2 if result.integrity_status is IntegrityStatus.FAILED and result.case_results == 0 else 0


def _metrics_aggregate(parsed: argparse.Namespace) -> int:
    try:
        result = aggregate_run_metrics(
            RunMetricsAggregationRequest(
                run_id=parsed.run_id,
                output_dir=Path(parsed.output_dir),
            )
        )
    except Exception as error:
        print(
            f"quality run metrics failed: {type(error).__name__}",
            file=sys.stderr,
        )
        return 2
    issue_codes = ",".join(sorted({item.code for item in result.issues})) or "none"
    usage_summary = "unavailable"
    if result.metrics is not None and result.metrics.run_metrics is not None:
        counts = result.metrics.run_metrics.usage.completeness.counts
        usage_summary = "/".join(
            str(counts.get(name, 0)) for name in ("complete", "partial", "missing")
        )
    print(
        "quality run metrics completed: "
        f"status={result.status.value}, "
        f"operations={result.operation_count}, "
        f"request_groups={result.request_group_count}, "
        f"request_events={result.request_event_count}, "
        f"usage_complete_partial_missing={usage_summary}, "
        f"issues={issue_codes}, "
        "manifest=metrics/manifest.json"
    )
    return 2 if result.status is RunMetricsStatus.FAILED else 0


def _flaky_import(parsed: argparse.Namespace) -> int:
    try:
        request = FlakyImportRequest(
            run_id=parsed.run_id,
            quality_output_dir=Path(parsed.output_dir),
            database_path=Path(parsed.db),
        )
        result = import_flaky_history(request)
    except Exception as error:
        print(f"quality Flaky import failed: {type(error).__name__}: {error}", file=sys.stderr)
        return 2
    print(_model_json(result))
    return (
        0
        if result.status
        in {
            FlakyImportStatus.IMPORTED,
            FlakyImportStatus.NOOP,
            FlakyImportStatus.DEGRADED,
        }
        else 2
    )


def _flaky_history(parsed: argparse.Namespace) -> int:
    try:
        entries = query_flaky_history(
            Path(parsed.db),
            case_id=parsed.case_id,
            param_hash=parsed.param_hash,
            environment=parsed.environment,
            execution_profile=parsed.execution_profile,
            state_epoch=parsed.state_epoch,
        )
    except Exception as error:
        print(f"quality Flaky history query failed: {type(error).__name__}: {error}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "case_id": parsed.case_id,
                "count": len(entries),
                "observations": [entry.model_dump(mode="json") for entry in entries],
            },
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def _flaky_reset_epoch(parsed: argparse.Namespace) -> int:
    try:
        result = reset_flaky_epoch(
            Path(parsed.db),
            EpochResetRequest(
                case_id=parsed.case_id,
                environment=parsed.environment,
                execution_profile=parsed.execution_profile,
                actor=parsed.actor,
                reason=parsed.reason,
            ),
        )
    except Exception as error:
        print(f"quality Flaky epoch reset failed: {type(error).__name__}: {error}", file=sys.stderr)
        return 2
    print(_model_json(result))
    return 0


def _flaky_db_check(parsed: argparse.Namespace) -> int:
    try:
        result = FlakyV3Service(Path(parsed.db)).check_invariants()
    except Exception as error:
        _print_cli_error(error)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["status"] == "OK" else 2


def _flaky_db_migrate(parsed: argparse.Namespace) -> int:
    try:
        result = migrate_store(Path(parsed.db))
    except Exception as error:
        _print_cli_error(error)
        return 2
    print(
        json.dumps(
            {
                "schema_version": "quality.flaky-db-migrate.v1",
                "status": "OK",
                "previous_database_schema_version": result.previous_schema_version,
                "database_schema_version": result.schema_version,
                "migration_applied": result.migration_applied,
                "backup_path": str(result.backup_path) if result.backup_path else None,
                "checksums": result.checksums,
                "check_result": result.quick_check,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def _flaky_recovery(parsed: argparse.Namespace) -> int:
    service = FlakyV3Service(Path(parsed.db))
    now = datetime.now(UTC)
    try:
        if parsed.command == "flaky-recovery-start":
            result = service.recovery_start(
                RecoveryStartRequest(
                    flaky_key=parsed.flaky_key,
                    target_commit_sha=parsed.target_commit_sha,
                    actor=parsed.actor,
                    reason=parsed.reason,
                    request_id=parsed.request_id,
                    expected_row_version=parsed.expected_row_version,
                ),
                now=now,
            )
        elif parsed.command == "flaky-recovery-status":
            result = service.recovery_status(parsed.flaky_key)
        elif parsed.command == "flaky-recovery-close":
            verified_branch_head = parsed.verified_branch_head
            if service.recovery_close_requires_fresh_head(parsed.attempt_id):
                git_remote = validate_git_remote(
                    os.environ.get("QUALITY_FLAKY_GIT_REMOTE", "origin")
                )
                verified_branch_head = GitTargetResolver(
                    Path.cwd(), remote=git_remote
                ).resolve_dev3()
                evidence_key_id, evidence_secret = load_probe_evidence_key(
                    repository_root=Path.cwd()
                )
                service = FlakyV3Service(
                    Path(parsed.db),
                    probe_evidence_keys={evidence_key_id: evidence_secret},
                )
            result = service.recovery_close(
                RecoveryCloseRequest(
                    attempt_id=parsed.attempt_id,
                    actor=parsed.actor,
                    reason=parsed.reason,
                    expected_row_version=parsed.expected_row_version,
                    verified_branch_head=verified_branch_head,
                ),
                now=now,
            )
        else:
            result = service.recovery_cancel(
                RecoveryCancelRequest(
                    attempt_id=parsed.attempt_id,
                    actor=parsed.actor,
                    reason=parsed.reason,
                    expected_row_version=parsed.expected_row_version,
                ),
                now=now,
            )
    except Exception as error:
        _print_cli_error(error)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _flaky_probe(parsed: argparse.Namespace) -> int:
    runtime = load_probe_runtime_config(repository_root=Path.cwd())
    service = ProbeControlService(
        Path(parsed.db),
        runtime,
        target_resolver=GitTargetResolver(
            Path.cwd(), remote=runtime.git_remote
        ).resolve_dev3,
    )
    now = datetime.now(UTC)
    try:
        if parsed.command == "flaky-probe-dispatch-once":
            result = service.dispatch_once(FixedJenkinsClient(runtime), now=now)
        elif parsed.command == "flaky-probe-reconcile-once":
            result = service.reconcile_once(FixedJenkinsClient(runtime), now=now)
        elif parsed.command == "flaky-probe-claim":
            token = os.environ.pop(parsed.dispatch_token_env, None)
            if not token:
                raise FlakyStoreError(
                    "probe_dispatch_token_missing", "dispatch token environment is missing"
                )
            result = service.claim(
                trigger_id=parsed.trigger_id,
                dispatch_token=token,
                plan_digest=parsed.plan_digest,
                job_full_name=parsed.job_full_name,
                build_number=parsed.build_number,
                now=now,
            )
        elif parsed.command == "flaky-probe-plan":
            result = service.get_plan(parsed.attempt_id).model_dump(mode="json")
        elif parsed.command == "flaky-probe-authorize-round":
            result = service.authorize_round(parsed.attempt_id, now=now)
        elif parsed.command == "flaky-probe-start-round":
            result = service.start_round(
                parsed.attempt_id,
                parsed.round_no,
                actual_target_commit_sha=parsed.actual_target_commit_sha,
                now=now,
            )
        else:
            result = service.finalize_build(parsed.trigger_id, now=now)
    except Exception as error:
        _print_cli_error(error)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _flaky_state_evaluate(parsed: argparse.Namespace) -> int:
    try:
        result = evaluate_flaky_state(
            Path(parsed.db),
            run_id=parsed.run_id,
            quality_output_dir=Path(parsed.output_dir),
        )
    except Exception as error:
        print(
            f"quality Flaky state evaluation failed: {type(error).__name__}: {error}",
            file=sys.stderr,
        )
        return 2
    print(_model_json(result))
    return (
        0
        if result.status
        in {
            FlakyEvaluationStatus.EVALUATED,
            FlakyEvaluationStatus.NOOP,
            FlakyEvaluationStatus.DEGRADED,
        }
        else 2
    )


def _flaky_state(parsed: argparse.Namespace) -> int:
    try:
        states = query_flaky_states(
            Path(parsed.db),
            case_id=parsed.case_id,
            param_hash=parsed.param_hash,
            environment=parsed.environment,
            execution_profile=parsed.execution_profile,
            state_epoch=parsed.state_epoch,
        )
    except Exception as error:
        print(
            f"quality Flaky state query failed: {type(error).__name__}: {error}",
            file=sys.stderr,
        )
        return 2
    print(
        json.dumps(
            {
                "case_id": parsed.case_id,
                "count": len(states),
                "states": [state.model_dump(mode="json") for state in states],
            },
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def _flaky_state_rebuild(parsed: argparse.Namespace) -> int:
    if parsed.apply and (not parsed.actor or not parsed.reason):
        print("--apply requires --actor and --reason", file=sys.stderr)
        return 2
    try:
        result = rebuild_flaky_states(Path(parsed.db), apply=parsed.apply)
    except Exception as error:
        print(
            f"quality Flaky state rebuild failed: {type(error).__name__}: {error}",
            file=sys.stderr,
        )
        return 2
    print(
        json.dumps(
            result,
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def _flaky_manual_action(parsed: argparse.Namespace) -> int:
    if parsed.command in {"flaky-confirm", "flaky-mark-not-flaky"}:
        try:
            result = FlakyV3Service(Path(parsed.db)).override_detection(
                flaky_key=parsed.flaky_key,
                detection_generation=parsed.detection_generation,
                fingerprint=parsed.comparability_fingerprint,
                action=(
                    "confirm_flaky"
                    if parsed.command == "flaky-confirm"
                    else "mark_not_flaky"
                ),
                actor=parsed.actor,
                reason=parsed.reason,
                idempotency_key=_manual_idempotency_key(parsed),
                now=datetime.now(UTC),
            )
        except Exception as error:
            _print_cli_error(error)
            return 2
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    request = FlakyManualActionRequest(
        flaky_key=parsed.flaky_key,
        actor=parsed.actor,
        reason=parsed.reason,
    )
    handlers = {
        "flaky-cancel-quarantine": cancel_flaky_quarantine,
    }
    try:
        result = handlers[parsed.command](Path(parsed.db), request)
    except Exception as error:
        print(
            f"quality Flaky governance action failed: {type(error).__name__}: {error}",
            file=sys.stderr,
        )
        return 2
    print(_model_json(result))
    return 0


def _flaky_quarantine(parsed: argparse.Namespace) -> int:
    try:
        expires_at = datetime.fromisoformat(parsed.expires_at.replace("Z", "+00:00"))
        result = quarantine_flaky_state(
            Path(parsed.db),
            FlakyQuarantineRequest(
                flaky_key=parsed.flaky_key,
                owner=parsed.owner,
                actor=parsed.actor,
                reason=parsed.reason,
                expires_at=expires_at,
            ),
        )
    except Exception as error:
        print(
            f"quality Flaky quarantine failed: {type(error).__name__}: {error}",
            file=sys.stderr,
        )
        return 2
    print(_model_json(result))
    return 0


def _flaky_governance_list(parsed: argparse.Namespace) -> int:
    try:
        from quality.config import load_quality_config
        from quality.flaky_read import FlakyReadService

        runtime = load_quality_config()
        page = FlakyReadService(
            Path(parsed.db),
            mode_requested=runtime.flaky_skip_mode_requested,
            mode_effective=runtime.flaky_skip_mode_effective,
        ).governance_page(
            status=parsed.status,
            owner=parsed.owner,
            overdue=True if parsed.overdue else None,
            environment=parsed.environment,
            execution_profile=parsed.execution_profile,
            case_path=parsed.case_path,
            keyword=parsed.keyword,
            cursor=parsed.cursor,
            page_size=parsed.page_size,
        )
    except Exception as error:
        print(
            f"quality Flaky governance query failed: {type(error).__name__}: {error}",
            file=sys.stderr,
        )
        return 2
    print(_model_json(page))
    return 0


def _flaky_read_query(parsed: argparse.Namespace) -> int:
    try:
        from quality.config import load_quality_config
        from quality.flaky_read import FlakyReadService

        runtime = load_quality_config()
        service = FlakyReadService(
            Path(parsed.db),
            mode_requested=runtime.flaky_skip_mode_requested,
            mode_effective=runtime.flaky_skip_mode_effective,
        )
        if parsed.command == "flaky-dashboard-summary":
            result = service.summary()
        elif parsed.command == "flaky-case-detail":
            result = service.case_detail(parsed.flaky_key)
        else:
            result = service.run_decisions(parsed.run_id, Path(parsed.artifact_dir))
    except Exception as error:
        _print_cli_error(error)
        return 2
    print(_model_json(result))
    return 0


def _flaky_dashboard(parsed: argparse.Namespace) -> int:
    try:
        from quality.flaky_dashboard import run_dashboard

        run_dashboard(
            Path(parsed.db),
            artifact_directory=(
                Path(parsed.artifact_dir) if parsed.artifact_dir else None
            ),
            host=parsed.host,
            port=parsed.port,
        )
    except Exception as error:
        _print_cli_error(error)
        return 2
    return 0


def _model_json(model) -> str:
    return json.dumps(
        model.model_dump(mode="json"),
        allow_nan=False,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )


def _print_cli_error(error: Exception) -> None:
    code = error.code if isinstance(error, FlakyStoreError) else "unexpected_error"
    print(
        json.dumps(
            {
                "schema_version": "quality.flaky-cli-error.v1",
                "status": "ERROR",
                "error_code": code,
                "message": str(error),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


def _manual_idempotency_key(parsed: argparse.Namespace) -> str:
    import hashlib

    value = "\0".join(
        (
            parsed.command,
            parsed.flaky_key,
            str(parsed.detection_generation),
            parsed.comparability_fingerprint,
            parsed.actor,
            parsed.reason,
        )
    )
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
