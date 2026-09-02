from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Callable, Iterable, Literal, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from master_service import CollectedTestCase
from quality.config import QualityRuntimeConfig
from quality.flaky_identity import build_flaky_key, normalize_flaky_environment
from quality.flaky_read import FlakyReadService, SnapshotCandidate
from quality.flaky_store.contracts import FlakyStoreError
from quality.flaky_v3 import DEFAULT_GOVERNANCE_POLICY, GovernancePolicy
from quality.models import SCHEMA_VERSION
from quality.pytest_identity import normalize_case_path, path_is_in_policy_scope


SNAPSHOT_SCHEMA_VERSION = "flaky-skip-snapshot.v1"
DECISION_SCHEMA_VERSION = "flaky-skip-decisions.v1"
RECONCILIATION_SCHEMA_VERSION = "flaky-skip-reconciliation.v1"
SNAPSHOT_FILE_NAME = "flaky-skip-snapshot.json"
DECISION_FILE_NAME = "flaky-skip-decisions.json"
RECONCILIATION_FILE_NAME = "flaky-skip-reconciliation.json"
SUPPORTED_DATABASE_SCHEMA_VERSION = 4


class ShadowModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)


class SnapshotEntry(ShadowModel):
    flaky_key: str
    case_id: str
    param_hash: str
    environment: str
    execution_profile: str
    state_epoch: int = Field(ge=1)
    governance_id: str
    governance_status: str
    normalized_case_path: str
    expires_at: datetime
    governance_overdue: bool

    @field_validator("environment")
    @classmethod
    def _environment(cls, value: str) -> str:
        return normalize_flaky_environment(value)

    @model_validator(mode="after")
    def _governance_is_live(self) -> "SnapshotEntry":
        if self.governance_status not in {"ACTIVE", "RECOVERING"}:
            raise ValueError("snapshot governance must be ACTIVE or RECOVERING")
        expected = build_flaky_key(
            self.case_id,
            self.param_hash,
            self.environment,
            self.execution_profile,
            self.state_epoch,
        )
        if expected != self.flaky_key:
            raise ValueError("snapshot flaky_key does not match its identity")
        return self


class SkipSnapshot(ShadowModel):
    schema_version: Literal["flaky-skip-snapshot.v1"] = SNAPSHOT_SCHEMA_VERSION
    status: str
    snapshot_id: str
    run_id: str
    branch: str
    generated_at: datetime
    valid_until: datetime
    policy_revision: str
    database_schema_version: int
    mode_requested: str
    mode_effective: str
    entries: tuple[SnapshotEntry, ...] = ()
    error_code: str | None = None
    diagnostic_codes: tuple[str, ...] = ()
    content_checksum: str

    @model_validator(mode="after")
    def _envelope(self) -> "SkipSnapshot":
        if self.status not in {"READY", "DISABLED", "UNAVAILABLE"}:
            raise ValueError("unsupported snapshot status")
        if self.valid_until < self.generated_at:
            raise ValueError("snapshot validity interval is invalid")
        if self.status != "READY" and self.entries:
            raise ValueError("non-ready snapshot must not contain entries")
        if self.status == "UNAVAILABLE" and not self.error_code:
            raise ValueError("unavailable snapshot requires an error_code")
        if tuple(sorted(self.entries, key=lambda item: item.flaky_key)) != self.entries:
            raise ValueError("snapshot entries must be ordered by flaky_key")
        return self


class DecisionRecord(ShadowModel):
    decision_id: str
    nodeid: str
    case_id: str | None
    param_hash: str | None
    environment: str | None
    execution_profile: str | None
    state_epoch: int | None = Field(default=None, ge=1)
    flaky_key: str | None
    normalized_case_path: str | None
    decision: str
    primary_reason_code: str
    diagnostic_codes: tuple[str, ...] = ()
    governance_id: str | None = None
    governance_status: str | None = None
    governance_overdue: bool = False
    fail_open: bool = False
    business_marker_present: bool = False

    @model_validator(mode="after")
    def _decision_contract(self) -> "DecisionRecord":
        if self.decision not in {"RUN", "WOULD_SKIP", "SKIP"}:
            raise ValueError("decision must be RUN, WOULD_SKIP, or SKIP")
        if self.decision in {"WOULD_SKIP", "SKIP"} and self.governance_id is None:
            raise ValueError(f"{self.decision} requires governance_id")
        return self


class DecisionPlan(ShadowModel):
    schema_version: Literal["flaky-skip-decisions.v1"] = DECISION_SCHEMA_VERSION
    run_id: str
    snapshot_id: str
    snapshot_checksum: str
    collection_started_at: datetime
    generated_at: datetime
    mode_requested: str
    mode_effective: str
    policy_revision: str
    decisions: tuple[DecisionRecord, ...]
    run_count: int = Field(ge=0)
    would_skip_count: int = Field(ge=0)
    skip_count: int = Field(default=0, ge=0)
    fail_open_count: int = Field(ge=0)
    reason_counts: dict[str, int]
    integrity_status: str
    diagnostic_codes: tuple[str, ...] = ()
    content_checksum: str

    @model_validator(mode="after")
    def _counts(self) -> "DecisionPlan":
        actual = Counter(item.decision for item in self.decisions)
        if actual["RUN"] != self.run_count:
            raise ValueError("decision RUN count mismatch")
        if actual["WOULD_SKIP"] != self.would_skip_count:
            raise ValueError("decision WOULD_SKIP count mismatch")
        if actual["SKIP"] != self.skip_count:
            raise ValueError("decision SKIP count mismatch")
        if self.mode_effective not in {"off", "shadow", "enforce"}:
            raise ValueError("unsupported effective skip mode")
        if (
            self.mode_effective in {"shadow", "enforce"}
            and self.mode_requested != self.mode_effective
        ):
            raise ValueError("active effective mode must match requested mode")
        if self.mode_effective != "shadow" and self.would_skip_count:
            raise ValueError("WOULD_SKIP requires shadow mode")
        if self.mode_effective != "enforce" and self.skip_count:
            raise ValueError("SKIP requires enforce mode")
        if sum(item.fail_open for item in self.decisions) != self.fail_open_count:
            raise ValueError("decision fail-open count mismatch")
        reasons = dict(
            sorted(Counter(item.primary_reason_code for item in self.decisions).items())
        )
        if reasons != self.reason_counts:
            raise ValueError("decision reason counts mismatch")
        if self.integrity_status not in {"OK", "DEGRADED"}:
            raise ValueError("unsupported decision integrity status")
        if len({item.nodeid for item in self.decisions}) != len(self.decisions):
            raise ValueError("decision nodeids must be unique")
        if len({item.decision_id for item in self.decisions}) != len(self.decisions):
            raise ValueError("decision ids must be unique")
        return self


class ReconciliationResult(ShadowModel):
    schema_version: Literal["flaky-skip-reconciliation.v1"] = (
        RECONCILIATION_SCHEMA_VERSION
    )
    run_id: str
    decisions_checksum: str
    generated_at: datetime
    status: str
    planned_count: int = Field(ge=0)
    observed_count: int = Field(ge=0)
    actual_governance_skip_count: int = Field(default=0, ge=0)
    missing_nodeids: tuple[str, ...] = ()
    duplicate_nodeids: tuple[str, ...] = ()
    unexpected_nodeids: tuple[str, ...] = ()
    unexpected_skipped_nodeids: tuple[str, ...] = ()
    diagnostic_codes: tuple[str, ...] = ()
    content_checksum: str

    @model_validator(mode="after")
    def _result_contract(self) -> "ReconciliationResult":
        if self.status not in {"OK", "DEGRADED", "NOT_EXECUTED"}:
            raise ValueError("unsupported reconciliation status")
        return self


def generate_snapshot(
    config: QualityRuntimeConfig,
    *,
    run_id: str,
    branch: str,
    repository_root: str | Path,
    now: datetime | None = None,
    policy: GovernancePolicy = DEFAULT_GOVERNANCE_POLICY,
    read_service_factory: Callable[[Path], FlakyReadService] = FlakyReadService,
) -> SkipSnapshot:
    generated_at = _aware_utc(now or datetime.now(UTC))
    valid_until = generated_at + timedelta(
        minutes=config.flaky_snapshot_max_age_minutes
    )
    common = {
        "run_id": _required(run_id, "run_id"),
        "branch": _required(branch, "branch"),
        "generated_at": generated_at,
        "valid_until": valid_until,
        "policy_revision": policy.revision,
        "mode_requested": config.flaky_skip_mode_requested,
        "mode_effective": config.flaky_skip_mode_effective,
    }
    if not config.flaky_auto_skip_enabled or config.flaky_skip_mode_effective == "off":
        diagnostics = _diagnostics(config.flaky_skip_warning)
        return _snapshot(
            **common,
            database_schema_version=SUPPORTED_DATABASE_SCHEMA_VERSION,
            status="DISABLED",
            entries=(),
            error_code=None,
            diagnostic_codes=diagnostics,
        )
    try:
        if config.flaky_database_path is None:
            raise FlakyStoreError(
                "snapshot_database_unavailable",
                "Flaky database path is not configured",
            )
        source = read_service_factory(config.flaky_database_path).snapshot_source()
        if source.database_schema_version != SUPPORTED_DATABASE_SCHEMA_VERSION:
            raise FlakyStoreError(
                "snapshot_version_incompatible",
                "Flaky database schema is not supported",
            )
        entries = _snapshot_entries(
            source.candidates,
            repository_root=repository_root,
            generated_at=generated_at,
        )
        return _snapshot(
            **common,
            database_schema_version=source.database_schema_version,
            status="READY",
            entries=entries,
            error_code=None,
            diagnostic_codes=_diagnostics(config.flaky_skip_warning),
        )
    except Exception as error:
        code = (
            error.code
            if isinstance(error, FlakyStoreError)
            else "snapshot_generation_failed"
        )
        return _snapshot(
            **common,
            database_schema_version=SUPPORTED_DATABASE_SCHEMA_VERSION,
            status="UNAVAILABLE",
            entries=(),
            error_code=code,
            diagnostic_codes=tuple(sorted({code, type(error).__name__})),
        )


def build_decision_plan(
    snapshot: SkipSnapshot,
    cases: Sequence[CollectedTestCase],
    *,
    run_id: str,
    branch: str,
    environment: str,
    execution_profiles: Mapping[str, str],
    collection_started_at: datetime,
    now: datetime | None = None,
    policy: GovernancePolicy = DEFAULT_GOVERNANCE_POLICY,
) -> DecisionPlan:
    collection_time = _aware_utc(collection_started_at)
    generated_at = _aware_utc(now or datetime.now(UTC))
    global_diagnostics = _snapshot_validation_diagnostics(
        snapshot,
        run_id=run_id,
        branch=branch,
        collection_started_at=collection_time,
        policy=policy,
    )
    disabled_contract_diagnostics = (
        _snapshot_contract_diagnostics(
            snapshot,
            run_id=run_id,
            branch=branch,
            collection_started_at=collection_time,
            policy=policy,
        )
        if snapshot.status == "DISABLED"
        else ()
    )
    if snapshot.status == "READY":
        try:
            environment = normalize_flaky_environment(environment)
        except ValueError:
            global_diagnostics = tuple(
                sorted({*global_diagnostics, "collection_environment_invalid"})
            )
    entries: dict[tuple[str, str, str, str], list[SnapshotEntry]] = defaultdict(list)
    for entry in snapshot.entries:
        entries[
            (
                entry.case_id,
                entry.param_hash,
                entry.environment,
                entry.execution_profile,
            )
        ].append(entry)

    provisional: list[DecisionRecord] = []
    for case in cases:
        profile = execution_profiles.get(case.nodeid)
        markers = {marker.casefold() for marker in case.markers}
        business_marker = bool(markers & {"skip", "skipif", "xfail"})
        if snapshot.status == "DISABLED":
            if disabled_contract_diagnostics:
                provisional.append(
                    _decision(
                        run_id,
                        case,
                        environment=environment,
                        execution_profile=profile,
                        decision="RUN",
                        reason="snapshot_invalid",
                        diagnostics=tuple(
                            sorted(
                                {
                                    *snapshot.diagnostic_codes,
                                    *disabled_contract_diagnostics,
                                }
                            )
                        ),
                        fail_open=True,
                        business_marker_present=business_marker,
                    )
                )
                continue
            disabled_reason = (
                "auto_skip_disabled"
                if snapshot.mode_requested in {"shadow", "enforce"}
                else "mode_off"
            )
            config_failed_open = bool(snapshot.diagnostic_codes)
            provisional.append(
                _decision(
                    run_id,
                    case,
                    environment=environment,
                    execution_profile=profile,
                    decision="RUN",
                    reason=disabled_reason,
                    diagnostics=snapshot.diagnostic_codes,
                    fail_open=config_failed_open,
                    business_marker_present=business_marker,
                )
            )
            continue
        if global_diagnostics:
            provisional.append(
                _decision(
                    run_id,
                    case,
                    environment=environment,
                    execution_profile=profile,
                    decision="RUN",
                    reason="snapshot_invalid",
                    diagnostics=global_diagnostics,
                    fail_open=True,
                    business_marker_present=business_marker,
                )
            )
            continue
        if not case.case_id or not case.param_hash or not case.normalized_case_path or not profile:
            provisional.append(
                _decision(
                    run_id,
                    case,
                    environment=environment,
                    execution_profile=profile,
                    decision="RUN",
                    reason="snapshot_invalid",
                    diagnostics=("collection_identity_incomplete",),
                    fail_open=True,
                    business_marker_present=business_marker,
                )
            )
            continue
        matches = entries.get(
            (case.case_id, case.param_hash, environment, profile), ()
        )
        if not matches:
            provisional.append(
                _decision(
                    run_id,
                    case,
                    environment=environment,
                    execution_profile=profile,
                    decision="RUN",
                    reason="governance_not_matched",
                    business_marker_present=business_marker,
                )
            )
            continue
        if len(matches) != 1:
            provisional.append(
                _decision(
                    run_id,
                    case,
                    environment=environment,
                    execution_profile=profile,
                    decision="RUN",
                    reason="snapshot_invalid",
                    diagnostics=("snapshot_identity_ambiguous",),
                    fail_open=True,
                    business_marker_present=business_marker,
                )
            )
            continue
        entry = matches[0]
        diagnostics: list[str] = []
        if entry.normalized_case_path != case.normalized_case_path:
            diagnostics.append("collection_path_mismatch")
        if not path_is_in_policy_scope(
            case.normalized_case_path,
            include_prefixes=policy.include_path_prefixes,
            exclude_prefixes=policy.exclude_path_prefixes,
        ):
            diagnostics.append("path_out_of_scope")
        if diagnostics:
            provisional.append(
                _decision(
                    run_id,
                    case,
                    environment=environment,
                    execution_profile=profile,
                    decision="RUN",
                    reason="snapshot_invalid",
                    diagnostics=tuple(diagnostics),
                    entry=entry,
                    fail_open=True,
                    business_marker_present=business_marker,
                )
            )
            continue
        entry_diagnostics = (
            ("governance_overdue",) if entry.governance_overdue else ()
        )
        enforce = snapshot.mode_effective == "enforce"
        provisional.append(
            _decision(
                run_id,
                case,
                environment=environment,
                execution_profile=profile,
                decision="SKIP" if enforce else "WOULD_SKIP",
                reason=(
                    "governance_enforce_match"
                    if enforce
                    else "governance_shadow_match"
                ),
                diagnostics=entry_diagnostics,
                entry=entry,
                business_marker_present=business_marker,
            )
        )

    conflicts = _conflicting_flaky_keys(provisional)
    decisions = tuple(
        _replace_with_conflict(item) if item.flaky_key in conflicts else item
        for item in provisional
    )
    diagnostics = tuple(
        sorted(
            {
                code
                for item in decisions
                for code in item.diagnostic_codes
                if item.fail_open
            }
        )
    )
    return _decision_plan(
        run_id=run_id,
        snapshot_id=snapshot.snapshot_id,
        snapshot_checksum=snapshot.content_checksum,
        collection_started_at=collection_time,
        generated_at=generated_at,
        mode_requested=snapshot.mode_requested,
        mode_effective=snapshot.mode_effective,
        policy_revision=policy.revision,
        decisions=decisions,
        integrity_status="DEGRADED" if any(item.fail_open for item in decisions) else "OK",
        diagnostic_codes=diagnostics,
    )


def reconcile_decision_plan(
    plan: DecisionPlan,
    case_results: Iterable[Mapping[str, object]],
    *,
    collect_only: bool = False,
    now: datetime | None = None,
) -> ReconciliationResult:
    if collect_only:
        return _reconciliation(
            run_id=plan.run_id,
            decisions_checksum=plan.content_checksum,
            generated_at=_aware_utc(now or datetime.now(UTC)),
            status="NOT_EXECUTED",
            planned_count=len(plan.decisions),
            observed_count=0,
        )
    planned = {item.nodeid: item for item in plan.decisions}
    invocations: dict[str, set[str]] = defaultdict(set)
    observed: set[str] = set()
    skipped: set[str] = set()
    for raw in case_results:
        nodeid = str(raw.get("nodeid") or "").strip()
        if not nodeid:
            continue
        phase = str(raw.get("phase") or "").casefold()
        status = str(raw.get("final_status") or raw.get("raw_status") or "").casefold()
        if phase == "call" or (phase == "setup" and status in {"error", "skipped", "xfailed"}):
            observed.add(nodeid)
            invocation = str(raw.get("invocation_id") or nodeid)
            invocations[nodeid].add(invocation)
        if status in {"skipped", "xfailed"}:
            skipped.add(nodeid)
    missing = tuple(sorted(set(planned) - observed))
    duplicates = tuple(
        sorted(nodeid for nodeid, values in invocations.items() if len(values) > 1)
    )
    unexpected = tuple(sorted(observed - set(planned)))
    planned_governance_skips = {
        nodeid
        for nodeid, decision in planned.items()
        if decision.decision == "SKIP" and not decision.business_marker_present
    }
    actual_governance_skips = skipped & planned_governance_skips
    unexpected_skipped = tuple(
        sorted(
            nodeid
            for nodeid in skipped & set(planned)
            if (
                not planned[nodeid].business_marker_present
                and planned[nodeid].decision != "SKIP"
            )
        )
    )
    governance_skip_not_observed = tuple(sorted(planned_governance_skips - skipped))
    diagnostics: list[str] = []
    if missing:
        diagnostics.append("execution_result_missing")
    if duplicates:
        diagnostics.append("execution_result_duplicate")
    if unexpected:
        diagnostics.append("execution_result_unplanned")
    if unexpected_skipped:
        diagnostics.append("unexpected_skip_observed")
    if governance_skip_not_observed:
        diagnostics.append("governance_skip_not_observed")
    if plan.integrity_status == "DEGRADED":
        diagnostics.append("decision_plan_degraded")
    return _reconciliation(
        run_id=plan.run_id,
        decisions_checksum=plan.content_checksum,
        generated_at=_aware_utc(now or datetime.now(UTC)),
        status="DEGRADED" if diagnostics else "OK",
        planned_count=len(plan.decisions),
        observed_count=len(observed),
        actual_governance_skip_count=len(actual_governance_skips),
        missing_nodeids=missing,
        duplicate_nodeids=duplicates,
        unexpected_nodeids=unexpected,
        unexpected_skipped_nodeids=unexpected_skipped,
        diagnostic_codes=tuple(sorted(set(diagnostics))),
    )


def collection_failure_reconciliation(
    run_id: str,
    *,
    diagnostic_code: str = "authoritative_collection_failed",
    now: datetime | None = None,
) -> ReconciliationResult:
    return _reconciliation(
        run_id=_required(run_id, "run_id"),
        decisions_checksum="unavailable",
        generated_at=_aware_utc(now or datetime.now(UTC)),
        status="DEGRADED",
        planned_count=0,
        observed_count=0,
        diagnostic_codes=(diagnostic_code,),
    )


def write_snapshot(snapshot: SkipSnapshot, directory: str | Path) -> Path:
    return write_immutable_json(Path(directory) / SNAPSHOT_FILE_NAME, snapshot)


def write_decision_plan(plan: DecisionPlan, directory: str | Path) -> Path:
    return write_immutable_json(Path(directory) / DECISION_FILE_NAME, plan)


def write_reconciliation(
    reconciliation: ReconciliationResult,
    directory: str | Path,
) -> Path:
    return write_immutable_json(
        Path(directory) / RECONCILIATION_FILE_NAME,
        reconciliation,
    )


def write_immutable_json(path: str | Path, model: ShadowModel) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise FileExistsError(f"immutable artifact already exists: {target.name}")
    data = json.dumps(
        model.model_dump(mode="json"),
        allow_nan=False,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ).encode("utf-8") + b"\n"
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{target.name}.",
            suffix=".tmp",
            dir=target.parent,
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, target)
        return target
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def read_snapshot(path: str | Path, *, expected_run_id: str | None = None) -> SkipSnapshot:
    model = SkipSnapshot.model_validate(_read_object(path))
    _verify_snapshot(model)
    if expected_run_id is not None and model.run_id != expected_run_id:
        raise FlakyStoreError("snapshot_run_mismatch", "snapshot run_id mismatch")
    return model


def read_decision_plan(
    path: str | Path,
    *,
    expected_run_id: str | None = None,
    expected_checksum: str | None = None,
) -> DecisionPlan:
    model = DecisionPlan.model_validate(_read_object(path))
    _verify_checksum(model)
    if expected_run_id is not None and model.run_id != expected_run_id:
        raise FlakyStoreError("decision_run_mismatch", "decision plan run_id mismatch")
    if expected_checksum is not None and model.content_checksum != expected_checksum:
        raise FlakyStoreError(
            "decision_checksum_mismatch", "decision plan checksum mismatch"
        )
    return model


def read_reconciliation(path: str | Path) -> ReconciliationResult:
    model = ReconciliationResult.model_validate(_read_object(path))
    _verify_checksum(model)
    return model


def _snapshot_entries(
    candidates: Sequence[SnapshotCandidate],
    *,
    repository_root: str | Path,
    generated_at: datetime,
) -> tuple[SnapshotEntry, ...]:
    seen: set[str] = set()
    entries: list[SnapshotEntry] = []
    for candidate in sorted(candidates, key=lambda item: item.flaky_key):
        if candidate.flaky_key in seen:
            raise FlakyStoreError(
                "snapshot_candidate_duplicate", "snapshot contains duplicate flaky_key"
            )
        seen.add(candidate.flaky_key)
        entries.append(
            SnapshotEntry(
                **candidate.model_dump(),
                normalized_case_path=normalize_case_path(
                    candidate.case_id,
                    repository_root,
                ),
                governance_overdue=candidate.expires_at <= generated_at,
            )
        )
    return tuple(entries)


def _snapshot(**payload: object) -> SkipSnapshot:
    content = {"schema_version": SNAPSHOT_SCHEMA_VERSION, **payload}
    digest = _checksum(content)
    return SkipSnapshot(
        **content,
        snapshot_id=f"snapshot-v1-{digest.removeprefix('sha256:')}",
        content_checksum=digest,
    )


def _decision_plan(**payload: object) -> DecisionPlan:
    decisions = tuple(payload["decisions"])
    content = {
        "schema_version": DECISION_SCHEMA_VERSION,
        **payload,
        "decisions": decisions,
        "run_count": sum(item.decision == "RUN" for item in decisions),
        "would_skip_count": sum(item.decision == "WOULD_SKIP" for item in decisions),
        "skip_count": sum(item.decision == "SKIP" for item in decisions),
        "fail_open_count": sum(item.fail_open for item in decisions),
        "reason_counts": dict(
            sorted(Counter(item.primary_reason_code for item in decisions).items())
        ),
    }
    return DecisionPlan(**content, content_checksum=_checksum(content))


def _reconciliation(**payload: object) -> ReconciliationResult:
    content = {
        "schema_version": RECONCILIATION_SCHEMA_VERSION,
        "actual_governance_skip_count": 0,
        "missing_nodeids": (),
        "duplicate_nodeids": (),
        "unexpected_nodeids": (),
        "unexpected_skipped_nodeids": (),
        "diagnostic_codes": (),
        **payload,
    }
    return ReconciliationResult(**content, content_checksum=_checksum(content))


def _decision(
    run_id: str,
    case: CollectedTestCase,
    *,
    environment: str,
    execution_profile: str | None,
    decision: str,
    reason: str,
    diagnostics: Sequence[str] = (),
    entry: SnapshotEntry | None = None,
    fail_open: bool = False,
    business_marker_present: bool = False,
) -> DecisionRecord:
    flaky_key = entry.flaky_key if entry is not None else None
    decision_id = "decision-v1-" + hashlib.sha256(
        "\0".join((run_id, case.nodeid, flaky_key or "")).encode("utf-8")
    ).hexdigest()
    return DecisionRecord(
        decision_id=decision_id,
        nodeid=case.nodeid,
        case_id=case.case_id,
        param_hash=case.param_hash,
        environment=environment,
        execution_profile=execution_profile,
        state_epoch=entry.state_epoch if entry is not None else None,
        flaky_key=flaky_key,
        normalized_case_path=case.normalized_case_path,
        decision=decision,
        primary_reason_code=reason,
        diagnostic_codes=tuple(sorted(set(diagnostics))),
        governance_id=entry.governance_id if entry is not None else None,
        governance_status=entry.governance_status if entry is not None else None,
        governance_overdue=entry.governance_overdue if entry is not None else False,
        fail_open=fail_open,
        business_marker_present=business_marker_present,
    )


def _replace_with_conflict(item: DecisionRecord) -> DecisionRecord:
    payload = item.model_dump()
    payload.update(
        {
            "decision": "RUN",
            "primary_reason_code": "snapshot_invalid",
            "diagnostic_codes": tuple(
                sorted({*item.diagnostic_codes, "collection_identity_conflict"})
            ),
            "fail_open": True,
        }
    )
    return DecisionRecord(**payload)


def _conflicting_flaky_keys(decisions: Sequence[DecisionRecord]) -> set[str]:
    nodeids: dict[str, set[str]] = defaultdict(set)
    for item in decisions:
        if item.flaky_key is not None:
            nodeids[item.flaky_key].add(item.nodeid)
    return {key for key, values in nodeids.items() if len(values) > 1}


def _snapshot_validation_diagnostics(
    snapshot: SkipSnapshot,
    *,
    run_id: str,
    branch: str,
    collection_started_at: datetime,
    policy: GovernancePolicy,
) -> tuple[str, ...]:
    values = [
        *snapshot.diagnostic_codes,
        *_snapshot_contract_diagnostics(
            snapshot,
            run_id=run_id,
            branch=branch,
            collection_started_at=collection_started_at,
            policy=policy,
        ),
    ]
    if snapshot.status != "READY":
        values.append(snapshot.error_code or "snapshot_not_ready")
    if snapshot.mode_effective not in {"shadow", "enforce"}:
        values.append("snapshot_mode_not_active")
    elif snapshot.mode_requested != snapshot.mode_effective:
        values.append("snapshot_mode_mismatch")
    return tuple(sorted(set(values)))


def _snapshot_contract_diagnostics(
    snapshot: SkipSnapshot,
    *,
    run_id: str,
    branch: str,
    collection_started_at: datetime,
    policy: GovernancePolicy,
) -> tuple[str, ...]:
    values: list[str] = []
    try:
        _verify_snapshot(snapshot)
    except Exception:
        values.append("snapshot_checksum_invalid")
    if snapshot.run_id != run_id:
        values.append("snapshot_run_mismatch")
    if branch != "dev3" or snapshot.branch != branch:
        values.append("snapshot_branch_mismatch")
    if not snapshot.generated_at <= collection_started_at <= snapshot.valid_until:
        values.append("snapshot_time_window_invalid")
    if snapshot.policy_revision != policy.revision:
        values.append("snapshot_policy_mismatch")
    if snapshot.database_schema_version != SUPPORTED_DATABASE_SCHEMA_VERSION:
        values.append("snapshot_database_schema_mismatch")
    return tuple(sorted(set(values)))


def _verify_snapshot(model: SkipSnapshot) -> None:
    payload = model.model_dump(
        mode="json",
        exclude={"snapshot_id", "content_checksum"},
    )
    if model.content_checksum != _checksum(payload):
        raise FlakyStoreError(
            "artifact_checksum_mismatch", "artifact checksum mismatch"
        )
    expected_id = f"snapshot-v1-{model.content_checksum.removeprefix('sha256:')}"
    if model.snapshot_id != expected_id:
        raise FlakyStoreError("snapshot_id_mismatch", "snapshot_id mismatch")


def _verify_checksum(model: ShadowModel) -> None:
    payload = model.model_dump(mode="json", exclude={"content_checksum"})
    expected = _checksum(payload)
    actual = str(getattr(model, "content_checksum"))
    if actual != expected:
        raise FlakyStoreError("artifact_checksum_mismatch", "artifact checksum mismatch")


def _checksum(payload: Mapping[str, object]) -> str:
    canonical = json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        default=_json_default,
    )
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _json_default(value: object) -> object:
    if isinstance(value, datetime):
        normalized = _aware_utc(value).isoformat()
        return normalized.replace("+00:00", "Z")
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    raise TypeError(f"unsupported canonical JSON value: {type(value).__name__}")


def _read_object(path: str | Path) -> dict[str, object]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise FlakyStoreError(
            "artifact_unavailable",
            f"artifact cannot be read: {type(error).__name__}",
        ) from error
    if not isinstance(value, dict):
        raise FlakyStoreError("artifact_invalid", "artifact root must be an object")
    return value


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("time must include timezone information")
    return value.astimezone(UTC)


def _required(value: object, name: str) -> str:
    text = str(value).strip()
    if not text:
        raise ValueError(f"{name} must not be empty")
    return text


def _diagnostics(warning: str | None) -> tuple[str, ...]:
    if not warning:
        return ()
    codes: list[str] = []
    if "QUALITY_FLAKY_AUTO_SKIP_ENABLE" in warning:
        codes.append("config_auto_skip_invalid")
    if "QUALITY_FLAKY_SKIP_MODE" in warning:
        codes.append("config_skip_mode_invalid")
    if "QUALITY_FLAKY_SNAPSHOT_MAX_AGE_MINUTES" in warning:
        codes.append("config_snapshot_age_invalid")
    if "skip_enforce_not_available" in warning:
        codes.append("skip_enforce_not_available")
    return tuple(sorted(set(codes or ("config_invalid",))))


__all__ = (
    "DECISION_FILE_NAME",
    "DECISION_SCHEMA_VERSION",
    "DecisionPlan",
    "DecisionRecord",
    "RECONCILIATION_FILE_NAME",
    "RECONCILIATION_SCHEMA_VERSION",
    "ReconciliationResult",
    "SNAPSHOT_FILE_NAME",
    "SNAPSHOT_SCHEMA_VERSION",
    "SkipSnapshot",
    "SnapshotEntry",
    "build_decision_plan",
    "collection_failure_reconciliation",
    "generate_snapshot",
    "read_decision_plan",
    "read_reconciliation",
    "read_snapshot",
    "reconcile_decision_plan",
    "write_decision_plan",
    "write_immutable_json",
    "write_reconciliation",
    "write_snapshot",
)
