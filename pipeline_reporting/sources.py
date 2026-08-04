from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import tempfile
import os
from typing import Any, Iterable

from quality.aggregator import MANIFEST_VERSION as QUALITY_MANIFEST_VERSION
from quality.flaky_models import (
    FLAKY_EVALUATION_SCHEMA_VERSION,
    FlakyEvaluationStatus,
)
from quality.junit import JUnitCaseEvidence, parse_junit_file
from quality.metrics_models import (
    RUN_METRICS_AGGREGATION_VERSION,
    RUN_METRICS_MANIFEST_VERSION,
    RUN_METRICS_SCHEMA_VERSION,
    RunMetricsStatus,
)
from quality.models import SCHEMA_VERSION, CaseStatus, IntegrityStatus

from pipeline_reporting.contracts import (
    CaseDetail,
    CollectSummary,
    ExecutionSummary,
    FlakyChange,
    FlakySummary,
    InterfaceTiming,
    LoadedPipelineSources,
    PoolExecutionSummary,
    RequestHealth,
    RetryHealth,
    StageStatus,
    TestSummary,
    RUNNER_EXECUTION_SCHEMA_VERSION,
)


STAGE_STATUS_SCHEMA_VERSION = "pipeline-stage-status.v1"
STAGE_NAMES = ("framework_tests", "smoke_collect", "real_smoke")
_COLLECT_TOTAL_PATTERNS = (
    re.compile(r"Collected test cases:\s*(\d+)", re.IGNORECASE),
    re.compile(r"(\d+)\s+tests? collected", re.IGNORECASE),
)
_COLLECT_PARALLEL_PATTERN = re.compile(r"Parallel pool cases:\s*(\d+)", re.IGNORECASE)
_COLLECT_SERIAL_PATTERN = re.compile(r"Serial pool cases:\s*(\d+)", re.IGNORECASE)
_USABLE_METRICS_STATUSES = {
    RunMetricsStatus.AGGREGATED.value,
    RunMetricsStatus.DEGRADED.value,
}
_USABLE_FLAKY_STATUSES = {
    FlakyEvaluationStatus.EVALUATED.value,
    FlakyEvaluationStatus.NOOP.value,
    FlakyEvaluationStatus.DEGRADED.value,
}


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


def load_pipeline_sources(
    workspace: str | Path,
    *,
    include_quality: bool = True,
) -> LoadedPipelineSources:
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
    execution = (
        load_execution_summary(
            reports / "execution-result.json", warnings=warnings
        )
        if include_quality
        else ExecutionSummary()
    )

    quality_run_id: str | None = None
    quality_facts_available = False
    request_health = RequestHealth()
    retry_health = RetryHealth()
    timings: tuple[InterfaceTiming, ...] = ()
    flaky = FlakySummary()
    if include_quality:
        quality_run_id, quality_manifest, quality_facts_available = _load_quality_facts(
            quality,
            warnings,
        )
        if quality_run_id and quality_manifest:
            request_path = quality / "merged" / "request-metrics.jsonl"
            expected_hash = _mapping(quality_manifest.get("output_hashes")).get(
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
                    run_id=quality_run_id,
                    warnings=warnings,
                )

        metrics_payload = (
            _load_metrics_payload(quality, quality_run_id, warnings)
            if quality_run_id
            else None
        )
        if metrics_payload:
            retry_health = _retry_health(metrics_payload)
            timings = _interface_timings(metrics_payload)

        flaky_payload = (
            _load_flaky_payload(quality, quality_run_id, warnings)
            if quality_run_id
            else None
        )
        if flaky_payload:
            flaky = _flaky_summary(flaky_payload)

    return LoadedPipelineSources(
        stage_statuses=stage_statuses,
        unit_tests=unit_tests,
        smoke_tests=smoke_tests,
        smoke_collect=smoke_collect,
        execution=execution,
        quality_facts_available=quality_facts_available,
        quality_run_id=quality_run_id,
        request_health=request_health,
        retry_health=retry_health,
        interface_timings=timings,
        flaky=flaky,
        warnings=tuple(warnings),
    )


def load_execution_summary(
    path: str | Path,
    *,
    warnings: list[str] | None = None,
) -> ExecutionSummary:
    warning_list = warnings if warnings is not None else []
    target = Path(path)
    if not target.is_file():
        return ExecutionSummary()
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("execution result must be an object")
        if payload.get("schema_version") != RUNNER_EXECUTION_SCHEMA_VERSION:
            raise ValueError("unsupported runner execution schema")
        planned_nodeids = payload.get("planned_nodeids")
        pool_payloads = payload.get("pool_results")
        if not isinstance(planned_nodeids, list) or not all(
            isinstance(nodeid, str) and nodeid.strip()
            for nodeid in planned_nodeids
        ):
            raise ValueError("planned_nodeids must be a string list")
        if len(set(planned_nodeids)) != len(planned_nodeids):
            raise ValueError("planned_nodeids contains duplicates")
        planned_case_count = _required_nonnegative_int(
            payload.get("planned_case_count"), "planned_case_count"
        )
        if planned_case_count != len(planned_nodeids):
            raise ValueError("planned_case_count differs from planned_nodeids")
        collection_exit_code = _pytest_exit_code(
            payload.get("collection_exit_code"), "collection_exit_code"
        )
        final_exit_code = _pytest_exit_code(
            payload.get("final_exit_code"), "final_exit_code"
        )
        if not isinstance(pool_payloads, list):
            raise ValueError("pool_results must be a list")
        pools = tuple(_pool_execution_summary(item) for item in pool_payloads)
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
        warning_list.append(f"Runner 执行事实不可用：{type(error).__name__}")
        return ExecutionSummary()
    return ExecutionSummary(
        available=True,
        test_target=_text(payload.get("test_target")),
        planned_case_count=planned_case_count,
        collection_exit_code=collection_exit_code,
        final_exit_code=final_exit_code,
        pools=pools,
    )


def _pool_execution_summary(value: Any) -> PoolExecutionSummary:
    if not isinstance(value, dict):
        raise ValueError("pool execution result must be an object")
    stage_id = _text(value.get("stage_id"))
    status = _text(value.get("status"))
    planned_nodeids = value.get("planned_nodeids")
    if not stage_id:
        raise ValueError("pool stage_id is required")
    if status not in {"NOT_RUN", "COMPLETED", "ERROR"}:
        raise ValueError("pool status is unsupported")
    if not isinstance(planned_nodeids, list) or not all(
        isinstance(nodeid, str) and nodeid.strip()
        for nodeid in planned_nodeids
    ):
        raise ValueError("pool planned_nodeids must be a string list")
    raw_exit = value.get("raw_pytest_exit_code")
    if raw_exit is not None:
        raw_exit = _pytest_exit_code(raw_exit, "raw_pytest_exit_code")
    return PoolExecutionSummary(
        stage_id=stage_id,
        status=status,
        planned_case_count=len(planned_nodeids),
        raw_pytest_exit_code=raw_exit,
        exception_type=_text(value.get("exception_type")),
        junit_path=_text(value.get("junit_path")),
    )


def _required_nonnegative_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a nonnegative integer")
    return value


def _pytest_exit_code(value: Any, name: str) -> int:
    result = _required_nonnegative_int(value, name)
    if result > 5:
        raise ValueError(f"{name} must be between 0 and 5")
    return result


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
    if run_payload.get("schema_version") != SCHEMA_VERSION:
        warnings.append("质量运行记录 Schema 不受支持")
        return run_id, manifest_payload, False
    if not run_id or _text(manifest_payload.get("run_id")) != run_id:
        warnings.append("质量运行记录与事实清单 run_id 不一致")
        return run_id, manifest_payload, False
    if manifest_payload.get("manifest_version") != QUALITY_MANIFEST_VERSION:
        warnings.append("质量事实清单版本不受支持")
        return run_id, manifest_payload, False
    if manifest_payload.get("schema_version") != SCHEMA_VERSION:
        warnings.append("质量事实清单 Schema 不受支持")
        return run_id, manifest_payload, False
    if manifest_payload.get("status") != "complete":
        warnings.append("质量事实清单尚未完整提交")
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
    if _text(manifest.get("run_id")) != run_id:
        warnings.append("Metrics 清单 run_id 与本轮不一致")
        return None
    if manifest.get("manifest_version") != RUN_METRICS_MANIFEST_VERSION:
        warnings.append("Metrics 清单版本不受支持")
        return None
    if manifest.get("schema_version") != RUN_METRICS_SCHEMA_VERSION:
        warnings.append("Metrics 清单 Schema 不受支持")
        return None
    if manifest.get("aggregation_version") != RUN_METRICS_AGGREGATION_VERSION:
        warnings.append("Metrics 聚合版本不受支持")
        return None
    if manifest.get("write_status") != "complete":
        warnings.append("Metrics 产物尚未完整写入")
        return None
    metrics_status = _text(manifest.get("metrics_status"))
    if metrics_status not in _USABLE_METRICS_STATUSES:
        warnings.append("Metrics 本轮没有可用聚合结果")
        return None
    if _text(payload.get("run_id")) != run_id:
        warnings.append("单次运行指标 run_id 与本轮不一致")
        return None
    if payload.get("schema_version") != RUN_METRICS_SCHEMA_VERSION:
        warnings.append("单次运行指标 Schema 不受支持")
        return None
    if payload.get("aggregation_version") != RUN_METRICS_AGGREGATION_VERSION:
        warnings.append("单次运行指标聚合版本不受支持")
        return None
    if _text(payload.get("status")) != metrics_status:
        warnings.append("Metrics 清单与指标状态不一致")
        return None
    expected_hash = _mapping(manifest.get("output_hashes")).get("run_metrics")
    if not _artifact_hash_matches(metrics_path, expected_hash, warnings, "单次运行指标"):
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
    if _text(payload.get("run_id")) != run_id:
        warnings.append("Flaky 评估 run_id 与本轮不一致")
        return None
    if payload.get("schema_version") != FLAKY_EVALUATION_SCHEMA_VERSION:
        warnings.append("Flaky 评估 Schema 不受支持")
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
        actual = _file_sha256(path)
    except OSError as error:
        warnings.append(f"{label}不可读：{type(error).__name__}")
        return False
    if actual != expected:
        warnings.append(f"{label}哈希与清单不一致")
        return False
    return True


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
            warnings.append(f"用例收集清单不可读：{type(error).__name__}")
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
