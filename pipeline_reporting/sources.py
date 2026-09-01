from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import re
import tempfile
import os
from typing import Any, Iterable

from pipeline_reporting.contracts import (
    CaseDetail,
    CollectSummary,
    ExecutionSummary,
    LoadedPipelineSources,
    LoadedQualitySources,
    PoolExecutionSummary,
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
    execution = load_execution_summary(
        reports / "execution-result.json", warnings=warnings
    )

    quality_sources = LoadedQualitySources()
    if include_quality:
        try:
            from pipeline_reporting.quality_sources import load_quality_sources

            quality_sources = load_quality_sources(
                reports / "quality",
                warnings=warnings,
            )
        except Exception as error:
            warnings.append(f"质量观测数据源不可用：{type(error).__name__}")

    return LoadedPipelineSources(
        stage_statuses=stage_statuses,
        unit_tests=unit_tests,
        smoke_tests=smoke_tests,
        smoke_collect=smoke_collect,
        execution=execution,
        quality_facts_available=quality_sources.facts_available,
        quality_run_id=quality_sources.run_id,
        request_health=quality_sources.request_health,
        retry_health=quality_sources.retry_health,
        interface_timings=quality_sources.interface_timings,
        flaky=quality_sources.flaky,
        shadow=quality_sources.shadow,
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


def load_junit_summary(
    paths: Iterable[str | Path],
    *,
    warnings: list[str] | None = None,
) -> TestSummary:
    warning_sink = warnings if warnings is not None else []
    cases: dict[str, Any] = {}
    parser = None
    for raw_path in paths:
        path = Path(raw_path)
        if not path.is_file():
            continue
        try:
            if parser is None:
                from quality.junit import parse_junit_file

                parser = parse_junit_file
            parsed = parser(path)
        except (OSError, ValueError) as error:
            warning_sink.append(f"JUnit 无法解析：{path.name}（{type(error).__name__}）")
            continue
        for item in parsed:
            key = item.invocation_id or item.case_id or f"{item.classname}::{item.name}"
            cases.setdefault(key, item)
    if not cases:
        return TestSummary()

    values = tuple(cases.values())
    status_counts = Counter(item.status.value for item in values)
    failed_cases = tuple(
        _case_detail(item)
        for item in values
        if item.status.value in {"failed", "error"}
    )
    skipped_cases = tuple(
        _case_detail(item) for item in values if item.status.value == "skipped"
    )
    return TestSummary(
        available=True,
        total=len(values),
        passed=status_counts["passed"],
        failed=status_counts["failed"],
        errors=status_counts["error"],
        skipped=status_counts["skipped"],
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


def _case_detail(item: Any) -> CaseDetail:
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
