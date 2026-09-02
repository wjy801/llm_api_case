"""Quality artifact adapter loaded only for Quality-enabled reports."""

from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
from typing import Any

from quality.aggregator import MANIFEST_VERSION as QUALITY_MANIFEST_VERSION
from quality.flaky_models import (
    FLAKY_EVALUATION_SCHEMA_VERSION,
    FlakyEvaluationStatus,
)
from quality.flaky_store import FlakyStoreError
from quality.metrics_models import (
    RUN_METRICS_AGGREGATION_VERSION,
    RUN_METRICS_MANIFEST_VERSION,
    RUN_METRICS_SCHEMA_VERSION,
    RunMetricsStatus,
)
from quality.models import SCHEMA_VERSION, IntegrityStatus
from util.artifact_io import (
    ArtifactFormatError,
    ArtifactJsonLineError,
    compare_file_sha256,
    exact_field_mismatches,
    read_json_object as read_artifact_json_object,
    read_jsonl_values,
)

from pipeline_reporting.contracts import (
    FlakyChange,
    FlakySummary,
    InterfaceTiming,
    LoadedQualitySources,
    RequestHealth,
    RetryHealth,
    ShadowDecisionSummary,
)
_USABLE_METRICS_STATUSES = {
    RunMetricsStatus.AGGREGATED.value,
    RunMetricsStatus.DEGRADED.value,
}
_USABLE_FLAKY_STATUSES = {
    FlakyEvaluationStatus.EVALUATED.value,
    FlakyEvaluationStatus.NOOP.value,
    FlakyEvaluationStatus.DEGRADED.value,
}


def load_quality_sources(
    quality_dir: str | Path,
    *,
    warnings: list[str],
) -> LoadedQualitySources:
    quality = Path(quality_dir)
    run_id, manifest, facts_available = _load_quality_facts(quality, warnings)
    request_health = RequestHealth()
    if run_id and manifest:
        request_path = quality / "merged" / "request-metrics.jsonl"
        expected_hash = _mapping(manifest.get("output_hashes")).get(
            "request-metrics"
        )
        if _artifact_hash_matches(
            request_path,
            expected_hash,
            warnings,
            "请求指标",
        ):
            request_health = load_request_health(
                request_path,
                run_id=run_id,
                warnings=warnings,
            )

    metrics_payload = (
        _load_metrics_payload(quality, run_id, warnings) if run_id else None
    )
    retry_health = (
        _retry_health(metrics_payload) if metrics_payload else RetryHealth()
    )
    timings = _interface_timings(metrics_payload) if metrics_payload else ()

    flaky_payload = (
        _load_flaky_payload(quality, run_id, warnings) if run_id else None
    )
    flaky = _flaky_summary(flaky_payload) if flaky_payload else FlakySummary()
    shadow = _load_shadow_decisions(quality, run_id, warnings)
    return LoadedQualitySources(
        facts_available=facts_available,
        run_id=run_id,
        request_health=request_health,
        retry_health=retry_health,
        interface_timings=timings,
        flaky=flaky,
        shadow=shadow,
    )


def _load_shadow_decisions(
    quality_dir: Path,
    run_id: str | None,
    warnings: list[str],
) -> ShadowDecisionSummary:
    path = quality_dir / "flaky-skip-decisions.json"
    if not path.is_file():
        return ShadowDecisionSummary(error_code="decision_artifact_missing")
    try:
        from quality.flaky_shadow import read_decision_plan, read_reconciliation

        plan = read_decision_plan(path, expected_run_id=run_id if run_id else None)
        reconciliation_status = None
        actual_governance_skip_count = None
        reconciliation_error = None
        reconciliation_path = quality_dir / "flaky-skip-reconciliation.json"
        if reconciliation_path.is_file():
            reconciliation = read_reconciliation(reconciliation_path)
            if (
                reconciliation.run_id != plan.run_id
                or reconciliation.decisions_checksum != plan.content_checksum
            ):
                raise FlakyStoreError(
                    "decision_reconciliation_mismatch",
                    "Shadow reconciliation does not reference the plan",
                )
            reconciliation_status = reconciliation.status
            actual_governance_skip_count = (
                reconciliation.actual_governance_skip_count
            )
        else:
            reconciliation_status = "UNKNOWN"
            reconciliation_error = "reconciliation_artifact_missing"
    except FlakyStoreError as error:
        warnings.append(f"Flaky Shadow 决策不可用：{error.code}")
        return ShadowDecisionSummary(
            integrity_status="DEGRADED",
            error_code=error.code,
        )
    except Exception as error:
        warnings.append(f"Flaky Shadow 决策不可用：{type(error).__name__}")
        return ShadowDecisionSummary(
            integrity_status="DEGRADED",
            error_code="decision_artifact_invalid",
        )
    return ShadowDecisionSummary(
        available=True,
        integrity_status=(
            "DEGRADED"
            if reconciliation_status in {"DEGRADED", "UNKNOWN"}
            else plan.integrity_status
        ),
        error_code=reconciliation_error,
        mode_requested=plan.mode_requested,
        mode_effective=plan.mode_effective,
        run_count=plan.run_count,
        would_skip_count=plan.would_skip_count,
        skip_count=plan.skip_count,
        actual_governance_skip_count=actual_governance_skip_count,
        fail_open_count=plan.fail_open_count,
        reason_counts=tuple(sorted(plan.reason_counts.items())),
        reconciliation_status=reconciliation_status,
    )


def _load_quality_facts(
    quality_dir: Path,
    warnings: list[str],
) -> tuple[str | None, dict[str, Any] | None, bool]:
    run_payload = _load_optional_json(
        quality_dir / "run.json",
        warnings,
        "质量运行记录",
    )
    manifest_payload = _load_optional_json(
        quality_dir / "merged" / "manifest.json",
        warnings,
        "质量事实清单",
    )
    if run_payload is None or manifest_payload is None:
        return None, manifest_payload, False

    run_id = _text(run_payload.get("run_id"))
    run_schema = run_payload.get("schema_version")
    if run_schema not in {"quality.v1", SCHEMA_VERSION}:
        warnings.append("质量运行记录 Schema 不受支持")
        return run_id, manifest_payload, False
    if not run_id:
        warnings.append("质量运行记录与事实清单 run_id 不一致")
        return run_id, manifest_payload, False
    if _append_exact_field_warning(
        {
            "run_id": _text(manifest_payload.get("run_id")),
            "manifest_version": manifest_payload.get("manifest_version"),
            "schema_version": manifest_payload.get("schema_version"),
            "status": manifest_payload.get("status"),
        },
        {
            "run_id": run_id,
            "manifest_version": (
                "quality.merge.v1"
                if run_schema == "quality.v1"
                else QUALITY_MANIFEST_VERSION
            ),
            "schema_version": run_schema,
            "status": "complete",
        },
        {
            "run_id": "质量运行记录与事实清单 run_id 不一致",
            "manifest_version": "质量事实清单版本不受支持",
            "schema_version": "质量事实清单 Schema 不受支持",
            "status": "质量事实清单尚未完整提交",
        },
        warnings,
    ):
        return run_id, manifest_payload, False
    if manifest_payload.get("integrity_status") == IntegrityStatus.FAILED.value:
        warnings.append("质量事实完整性校验失败")
        return run_id, manifest_payload, False
    if not isinstance(manifest_payload.get("output_hashes"), dict):
        warnings.append("质量事实清单缺少产物哈希")
        return run_id, manifest_payload, False
    return run_id, manifest_payload, True


def _load_metrics_payload(
    quality_dir: Path,
    run_id: str,
    warnings: list[str],
) -> dict[str, Any] | None:
    metrics_dir = quality_dir / "metrics"
    manifest_path = metrics_dir / "manifest.json"
    metrics_path = metrics_dir / "run-metrics.json"
    if not manifest_path.is_file() and not metrics_path.is_file():
        return None
    manifest = _load_optional_json(manifest_path, warnings, "Metrics 清单")
    payload = _load_optional_json(metrics_path, warnings, "单次运行指标")
    if manifest is None or payload is None:
        return None
    if _append_exact_field_warning(
        {
            "run_id": _text(manifest.get("run_id")),
            "manifest_version": manifest.get("manifest_version"),
            "schema_version": manifest.get("schema_version"),
            "aggregation_version": manifest.get("aggregation_version"),
            "write_status": manifest.get("write_status"),
        },
        {
            "run_id": run_id,
            "manifest_version": RUN_METRICS_MANIFEST_VERSION,
            "schema_version": RUN_METRICS_SCHEMA_VERSION,
            "aggregation_version": RUN_METRICS_AGGREGATION_VERSION,
            "write_status": "complete",
        },
        {
            "run_id": "Metrics 清单 run_id 与本轮不一致",
            "manifest_version": "Metrics 清单版本不受支持",
            "schema_version": "Metrics 清单 Schema 不受支持",
            "aggregation_version": "Metrics 聚合版本不受支持",
            "write_status": "Metrics 产物尚未完整写入",
        },
        warnings,
    ):
        return None
    metrics_status = _text(manifest.get("metrics_status"))
    if metrics_status not in _USABLE_METRICS_STATUSES:
        warnings.append("Metrics 本轮没有可用聚合结果")
        return None
    if _append_exact_field_warning(
        {
            "run_id": _text(payload.get("run_id")),
            "schema_version": payload.get("schema_version"),
            "aggregation_version": payload.get("aggregation_version"),
            "status": _text(payload.get("status")),
        },
        {
            "run_id": run_id,
            "schema_version": RUN_METRICS_SCHEMA_VERSION,
            "aggregation_version": RUN_METRICS_AGGREGATION_VERSION,
            "status": metrics_status,
        },
        {
            "run_id": "单次运行指标 run_id 与本轮不一致",
            "schema_version": "单次运行指标 Schema 不受支持",
            "aggregation_version": "单次运行指标聚合版本不受支持",
            "status": "Metrics 清单与指标状态不一致",
        },
        warnings,
    ):
        return None
    expected_hash = _mapping(manifest.get("output_hashes")).get("run_metrics")
    if not _artifact_hash_matches(
        metrics_path,
        expected_hash,
        warnings,
        "单次运行指标",
    ):
        return None
    return payload


def _load_flaky_payload(
    quality_dir: Path,
    run_id: str,
    warnings: list[str],
) -> dict[str, Any] | None:
    path = quality_dir / "flaky-evaluation.json"
    if not path.is_file():
        return None
    payload = _load_optional_json(path, warnings, "Flaky 评估")
    if payload is None:
        return None
    if _append_exact_field_warning(
        {
            "run_id": _text(payload.get("run_id")),
            "schema_version": payload.get("schema_version"),
        },
        {
            "run_id": run_id,
            "schema_version": FLAKY_EVALUATION_SCHEMA_VERSION,
        },
        {
            "run_id": "Flaky 评估 run_id 与本轮不一致",
            "schema_version": "Flaky 评估 Schema 不受支持",
        },
        warnings,
    ):
        return None
    status = _text(payload.get("status"))
    if status not in _USABLE_FLAKY_STATUSES:
        warnings.append("Flaky 本轮没有可用评估结果")
        return None
    for name in (
        "newly_suspected",
        "newly_confirmed",
        "recovered",
        "overdue",
        "transitions",
    ):
        if not isinstance(payload.get(name), list):
            warnings.append(f"Flaky 评估字段 {name} 不可用")
            return None
    return payload


def _append_exact_field_warning(
    actual_fields: dict[str, Any],
    expected_fields: dict[str, Any],
    warning_by_field: dict[str, str],
    warnings: list[str],
) -> bool:
    mismatches = exact_field_mismatches(actual_fields, expected_fields)
    if not mismatches:
        return False
    warnings.append(warning_by_field[mismatches[0]])
    return True


def _artifact_hash_matches(
    path: Path,
    expected_hash: Any,
    warnings: list[str],
    label: str,
) -> bool:
    if not path.is_file():
        warnings.append(f"{label}文件缺失")
        return False
    expected = _text(expected_hash)
    if expected is None:
        warnings.append(f"{label}缺少可信哈希")
        return False
    try:
        actual = compare_file_sha256(path, expected).actual
    except OSError as error:
        warnings.append(f"{label}不可读：{type(error).__name__}")
        return False
    if actual != expected:
        warnings.append(f"{label}哈希与清单不一致")
        return False
    return True


def load_request_health(
    path: str | Path,
    *,
    run_id: str,
    warnings: list[str] | None = None,
) -> RequestHealth:
    source = Path(path)
    if not source.is_file():
        return RequestHealth()
    records: list[dict[str, Any]] = []
    foreign_count = 0
    try:
        for item in read_jsonl_values(source):
            value = item.value
            if _text(value.get("run_id")) != run_id:
                foreign_count += 1
                continue
            records.append(value)
    except (OSError, ArtifactJsonLineError) as error:
        if warnings is not None:
            error_type = (
                type(error.error).__name__
                if isinstance(error, ArtifactJsonLineError)
                else type(error).__name__
            )
            warnings.append(f"请求指标不可用：{error_type}")
        return RequestHealth()
    if foreign_count and warnings is not None:
        warnings.append(f"请求指标忽略了 {foreign_count} 条其他 run_id 记录")
    success_count = sum(_technical_request_success(item) for item in records)
    return RequestHealth(
        available=True,
        total=len(records),
        success_count=success_count,
        http_5xx_count=sum(
            1
            for item in records
            if (status := _integer(item.get("status_code"))) is not None
            and 500 <= status <= 599
        ),
        timeout_count=sum(bool(item.get("timeout")) for item in records),
    )


def _retry_health(payload: dict[str, Any]) -> RetryHealth:
    groups = _mapping(_mapping(payload.get("run_metrics")).get("request_groups"))
    ratio = _mapping(groups.get("http_retry_rescue_rate"))
    retried = _integer(groups.get("retried_group_count")) or 0
    return RetryHealth(
        available=bool(groups),
        retried_group_count=retried,
        rescued_group_count=_integer(ratio.get("numerator")) or 0,
    )


def _interface_timings(payload: dict[str, Any]) -> tuple[InterfaceTiming, ...]:
    values: list[InterfaceTiming] = []
    raw_buckets = payload.get("request_group_buckets")
    if not isinstance(raw_buckets, list):
        return ()
    for raw_bucket in raw_buckets:
        bucket = _mapping(raw_bucket)
        dimension = _mapping(bucket.get("dimension"))
        if _text(dimension.get("traffic_role")) != "workload":
            continue
        if _text(dimension.get("protocol")) == "polling":
            continue
        timing = _mapping(_mapping(bucket.get("timing")).get("total_duration_ms"))
        mean = _number(timing.get("mean"))
        maximum = _number(timing.get("maximum"))
        if mean is None or maximum is None:
            continue
        stability = _mapping(bucket.get("stability"))
        values.append(
            InterfaceTiming(
                interface_id=_text(dimension.get("interface_id")) or "unknown interface",
                request_group_count=_integer(stability.get("group_count")) or 0,
                mean_ms=mean,
                maximum_ms=maximum,
            )
        )
    return tuple(
        sorted(
            values,
            key=lambda item: (-item.mean_ms, -item.maximum_ms, item.interface_id),
        )[:5]
    )


def _flaky_summary(payload: dict[str, Any]) -> FlakySummary:
    newly_suspected = _list_of_mappings(payload.get("newly_suspected"))
    newly_confirmed = _list_of_mappings(payload.get("newly_confirmed"))
    recovered = _list_of_mappings(payload.get("recovered"))
    overdue = _list_of_mappings(payload.get("overdue"))
    transitions = _list_of_mappings(payload.get("transitions"))
    directions: Counter[str] = Counter()
    newly_quarantined = 0
    for transition in transitions:
        source = _text(transition.get("from_state")) or "-"
        target = _text(transition.get("to_state")) or "-"
        directions[f"{source} -> {target}"] += 1
        if target == "QUARANTINED":
            newly_quarantined += 1

    actionable: list[FlakyChange] = []
    for label, items in (
        ("新增疑似", newly_suspected),
        ("新增确认", newly_confirmed),
        ("恢复稳定", recovered),
        ("超期治理", overdue),
    ):
        for item in items:
            actionable.append(
                FlakyChange(
                    case_id=_text(item.get("case_id")) or "unknown case",
                    change=label,
                )
            )
    for transition in transitions:
        if _text(transition.get("to_state")) == "QUARANTINED":
            actionable.append(
                FlakyChange(
                    case_id=_text(transition.get("case_id")) or "unknown case",
                    change="进入隔离",
                )
            )
    return FlakySummary(
        available=True,
        newly_suspected_count=len(newly_suspected),
        newly_confirmed_count=len(newly_confirmed),
        recovered_count=len(recovered),
        newly_quarantined_count=newly_quarantined,
        overdue_count=len(overdue),
        transition_count=len(transitions),
        transition_directions=tuple(sorted(directions.items())),
        actionable_changes=tuple(actionable[:5]),
    )


def _technical_request_success(record: dict[str, Any]) -> bool:
    status = _integer(record.get("status_code"))
    return bool(
        status is not None
        and status != 429
        and status < 500
        and not bool(record.get("timeout"))
        and not _text(record.get("error_type"))
    )


def _load_optional_json(
    path: Path,
    warnings: list[str],
    label: str,
) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        return read_artifact_json_object(path)
    except (OSError, ArtifactFormatError, json.JSONDecodeError) as error:
        error_type = "ValueError" if isinstance(error, ArtifactFormatError) else type(error).__name__
        warnings.append(f"{label}不可用：{error_type}")
        return None


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list_of_mappings(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _integer(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


__all__ = ("load_quality_sources", "load_request_health")
