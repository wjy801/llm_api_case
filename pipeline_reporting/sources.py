from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import re
import tempfile
import os
from typing import Any, Iterable

from quality.junit import JUnitCaseEvidence, parse_junit_file
from quality.models import CaseStatus

from pipeline_reporting.contracts import (
    CaseDetail,
    CollectSummary,
    FlakyChange,
    FlakySummary,
    InterfaceTiming,
    LoadedPipelineSources,
    RequestHealth,
    RetryHealth,
    StageStatus,
    TestSummary,
)


STAGE_STATUS_SCHEMA_VERSION = "pipeline-stage-status.v1"
STAGE_NAMES = ("framework_tests", "smoke_collect", "real_smoke")
_COLLECT_TOTAL_PATTERNS = (
    re.compile(r"Collected test cases:\s*(\d+)", re.IGNORECASE),
    re.compile(r"(\d+)\s+tests? collected", re.IGNORECASE),
)
_COLLECT_PARALLEL_PATTERN = re.compile(r"Parallel pool cases:\s*(\d+)", re.IGNORECASE)
_COLLECT_SERIAL_PATTERN = re.compile(r"Serial pool cases:\s*(\d+)", re.IGNORECASE)


def initialize_stage_status_file(
    path: str | Path,
    *,
    framework_tests_enabled: bool,
    smoke_collect_enabled: bool,
    real_smoke_enabled: bool,
) -> Path:
    selected = {
        "framework_tests": framework_tests_enabled,
        "smoke_collect": smoke_collect_enabled,
        "real_smoke": real_smoke_enabled,
    }
    return _write_stage_statuses(
        path,
        {
            name: StageStatus.BLOCKED if enabled else StageStatus.NOT_RUN
            for name, enabled in selected.items()
        },
    )


def update_stage_status_file(
    path: str | Path,
    *,
    stage_name: str,
    status: StageStatus | str,
) -> Path:
    if stage_name not in STAGE_NAMES:
        raise ValueError(f"unsupported pipeline stage: {stage_name}")
    statuses = load_stage_statuses(path)
    statuses[stage_name] = StageStatus(status)
    for name in STAGE_NAMES:
        statuses.setdefault(name, StageStatus.NOT_RUN)
    return _write_stage_statuses(path, statuses)


def load_stage_statuses(path: str | Path) -> dict[str, StageStatus]:
    source = Path(path)
    if not source.is_file():
        return {}
    payload = _load_json(source)
    if payload.get("schema_version") != STAGE_STATUS_SCHEMA_VERSION:
        raise ValueError("unsupported pipeline stage status schema")
    stages = payload.get("stages")
    if not isinstance(stages, dict):
        raise ValueError("pipeline stage status payload must contain stages")
    result: dict[str, StageStatus] = {}
    for name, raw_status in stages.items():
        if name in STAGE_NAMES:
            result[name] = StageStatus(str(raw_status))
    return result


def load_pipeline_sources(workspace: str | Path) -> LoadedPipelineSources:
    root = Path(workspace)
    reports = root / "reports"
    quality = reports / "quality"
    warnings: list[str] = []

    try:
        stage_statuses = load_stage_statuses(reports / "pipeline-stage-status.json")
    except (OSError, ValueError, json.JSONDecodeError) as error:
        stage_statuses = {}
        warnings.append(f"阶段状态不可用：{type(error).__name__}")

    unit_tests = load_junit_summary((reports / "unit-tests.xml",), warnings=warnings)
    smoke_paths = tuple(sorted(reports.glob("smoke-tests*.xml")))
    smoke_tests = load_junit_summary(smoke_paths, warnings=warnings)
    smoke_collect = load_collect_summary(reports / "smoke-collect.txt", warnings=warnings)

    run_payload = _load_optional_json(quality / "run.json", warnings, "Quality 运行记录")
    summary_payload = _load_optional_json(quality / "summary.json", warnings, "P0 汇总")
    quality_run_id = _text(run_payload.get("run_id")) if run_payload else None
    quality_available = bool(
        quality_run_id
        and summary_payload
        and _text(summary_payload.get("run_id")) == quality_run_id
    )
    if run_payload and summary_payload and not quality_available:
        warnings.append("Quality run_id 与 P0 汇总不一致")

    request_health = RequestHealth()
    if quality_run_id:
        request_health = load_request_health(
            quality / "merged" / "request-metrics.jsonl",
            run_id=quality_run_id,
            warnings=warnings,
        )

    retry_health = RetryHealth()
    timings: tuple[InterfaceTiming, ...] = ()
    metrics_payload = _load_optional_json(
        quality / "metrics" / "run-metrics.json",
        warnings,
        "单次运行指标",
    )
    if metrics_payload:
        if quality_run_id and _text(metrics_payload.get("run_id")) != quality_run_id:
            warnings.append("Metrics run_id 与本轮 Quality run_id 不一致")
        else:
            retry_health = _retry_health(metrics_payload)
            timings = _interface_timings(metrics_payload)

    flaky = FlakySummary()
    flaky_payload = _load_optional_json(
        quality / "flaky-evaluation.json",
        warnings,
        "Flaky 评估",
    )
    if flaky_payload:
        if quality_run_id and _text(flaky_payload.get("run_id")) != quality_run_id:
            warnings.append("Flaky run_id 与本轮 Quality run_id 不一致")
        else:
            flaky = _flaky_summary(flaky_payload)

    return LoadedPipelineSources(
        stage_statuses=stage_statuses,
        unit_tests=unit_tests,
        smoke_tests=smoke_tests,
        smoke_collect=smoke_collect,
        quality_available=quality_available,
        quality_run_id=quality_run_id,
        request_health=request_health,
        retry_health=retry_health,
        interface_timings=timings,
        flaky=flaky,
        warnings=tuple(warnings),
    )


def load_junit_summary(
    paths: Iterable[str | Path],
    *,
    warnings: list[str] | None = None,
) -> TestSummary:
    warning_sink = warnings if warnings is not None else []
    cases: dict[str, JUnitCaseEvidence] = {}
    for raw_path in paths:
        path = Path(raw_path)
        if not path.is_file():
            continue
        try:
            parsed = parse_junit_file(path)
        except (OSError, ValueError) as error:
            warning_sink.append(f"JUnit 无法解析：{path.name}（{type(error).__name__}）")
            continue
        for item in parsed:
            key = item.invocation_id or item.case_id or f"{item.classname}::{item.name}"
            cases.setdefault(key, item)
    if not cases:
        return TestSummary()

    values = tuple(cases.values())
    status_counts = Counter(item.status for item in values)
    failed_cases = tuple(
        _case_detail(item)
        for item in values
        if item.status in {CaseStatus.FAILED, CaseStatus.ERROR}
    )
    skipped_cases = tuple(
        _case_detail(item) for item in values if item.status is CaseStatus.SKIPPED
    )
    return TestSummary(
        available=True,
        total=len(values),
        passed=status_counts[CaseStatus.PASSED],
        failed=status_counts[CaseStatus.FAILED],
        errors=status_counts[CaseStatus.ERROR],
        skipped=status_counts[CaseStatus.SKIPPED],
        duration_seconds=sum(item.duration_seconds for item in values),
        failed_cases=failed_cases,
        skipped_cases=skipped_cases,
    )


def load_collect_summary(
    path: str | Path,
    *,
    warnings: list[str] | None = None,
) -> CollectSummary:
    source = Path(path)
    if not source.is_file():
        return CollectSummary()
    try:
        text = source.read_text(encoding="utf-8-sig")
    except OSError as error:
        if warnings is not None:
            warnings.append(f"Smoke 收集清单不可读：{type(error).__name__}")
        return CollectSummary()
    total = _first_match(text, _COLLECT_TOTAL_PATTERNS)
    parallel = _first_match(text, (_COLLECT_PARALLEL_PATTERN,))
    serial = _first_match(text, (_COLLECT_SERIAL_PATTERN,))
    return CollectSummary(
        available=total is not None,
        total=total,
        parallel=parallel,
        serial=serial,
    )


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
        with source.open("r", encoding="utf-8") as file_handle:
            for line in file_handle:
                if not line.strip():
                    continue
                value = json.loads(line)
                if _text(value.get("run_id")) != run_id:
                    foreign_count += 1
                    continue
                records.append(value)
    except (OSError, json.JSONDecodeError) as error:
        if warnings is not None:
            warnings.append(f"请求指标不可用：{type(error).__name__}")
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


def _case_detail(item: JUnitCaseEvidence) -> CaseDetail:
    name = item.case_id or (
        f"{item.classname}::{item.name}" if item.classname else item.name
    )
    return CaseDetail(name=name or "unknown case", status=item.status.value, message=item.message)


def _write_stage_statuses(
    path: str | Path,
    statuses: dict[str, StageStatus],
) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": STAGE_STATUS_SCHEMA_VERSION,
        "stages": {name: status.value for name, status in sorted(statuses.items())},
    }
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary_name, target)
    except Exception:
        Path(temporary_name).unlink(missing_ok=True)
        raise
    return target


def _load_optional_json(
    path: Path,
    warnings: list[str],
    label: str,
) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        return _load_json(path)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        warnings.append(f"{label}不可用：{type(error).__name__}")
        return None


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def _first_match(text: str, patterns: Iterable[re.Pattern[str]]) -> int | None:
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            return int(match.group(1))
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
