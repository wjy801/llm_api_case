from __future__ import annotations

from collections import Counter
from pathlib import Path
import json
import re
from typing import Any

from governance.flaky_models import AttemptResult, FlakyStatus, FlakyTestResult
from governance.retry_queue import write_retry_queue


MAX_FAILURE_MESSAGE_LENGTH = 2000
SENSITIVE_VALUE = "<redacted>"
SENSITIVE_PATTERNS = (
    re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;]+"),
    re.compile(r"(?i)(api[_-]?key\s*[:=]\s*)[^\s,;]+"),
    re.compile(r"(?i)(token\s*[:=]\s*)[^\s,;]+"),
    re.compile(r"(?i)(password\s*[:=]\s*)[^\s,;]+"),
)


def write_flaky_reports(
    report_dir: Path,
    results: list[FlakyTestResult],
    *,
    latest_retry_queue_path: Path | None = None,
    allure_results_dir: Path | None = None,
) -> None:
    report_dir.mkdir(parents=True, exist_ok=True)
    summary = build_summary(results)

    (report_dir / "flaky-results.json").write_text(
        json.dumps({"results": [_result_to_dict(result) for result in results]}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (report_dir / "flaky-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (report_dir / "flaky-summary.txt").write_text(_summary_to_text(summary), encoding="utf-8")
    if allure_results_dir is not None:
        write_allure_environment(allure_results_dir, summary)
        enrich_allure_results(allure_results_dir, results)
    if latest_retry_queue_path is not None:
        write_retry_queue(report_dir, latest_retry_queue_path, results)


def build_summary(results: list[FlakyTestResult]) -> dict[str, Any]:
    counter = Counter(result.status.value for result in results)
    total = len(results)
    passed = counter[FlakyStatus.PASSED.value]
    retry_passed = counter[FlakyStatus.RETRY_PASSED.value]
    retry_failed = counter[FlakyStatus.RETRY_FAILED.value]
    failed = counter[FlakyStatus.FAILED.value]
    retried_total = retry_passed + retry_failed

    return {
        "total": total,
        "passed": passed,
        "retry_passed": retry_passed,
        "retry_failed": retry_failed,
        "failed": failed,
        "first_pass_rate": _ratio(passed, total),
        "final_success_rate": _ratio(passed + retry_passed, total),
        "retry_recovery_rate": _ratio(retry_passed, retried_total),
    }


def redact_failure_message(message: str | None) -> str | None:
    if message is None:
        return None

    redacted = message
    for pattern in SENSITIVE_PATTERNS:
        redacted = pattern.sub(rf"\1{SENSITIVE_VALUE}", redacted)

    if len(redacted) > MAX_FAILURE_MESSAGE_LENGTH:
        return redacted[:MAX_FAILURE_MESSAGE_LENGTH] + "\n...<truncated>"
    return redacted


def write_allure_environment(allure_results_dir: Path, summary: dict[str, Any]) -> None:
    allure_results_dir.mkdir(parents=True, exist_ok=True)
    existing_values = _read_properties(allure_results_dir / "environment.properties")
    existing_values.update(
        {
            "flaky.total": str(summary["total"]),
            "flaky.passed": str(summary["passed"]),
            "flaky.retry_passed": str(summary["retry_passed"]),
            "flaky.retry_failed": str(summary["retry_failed"]),
            "flaky.failed": str(summary["failed"]),
            "flaky.first_pass_rate": _format_rate(summary["first_pass_rate"]),
            "flaky.final_success_rate": _format_rate(summary["final_success_rate"]),
            "flaky.retry_recovery_rate": _format_rate(summary["retry_recovery_rate"]),
        }
    )
    _write_properties(allure_results_dir / "environment.properties", existing_values)


def enrich_allure_results(allure_results_dir: Path, results: list[FlakyTestResult]) -> None:
    if not allure_results_dir.exists():
        return

    results_by_key = _allure_result_keys(results)
    for result_file in allure_results_dir.glob("*-result.json"):
        payload = json.loads(result_file.read_text(encoding="utf-8"))
        result = _match_allure_result(payload, results_by_key)
        if result is None:
            continue

        payload["parameters"] = _merge_allure_parameters(
            payload.get("parameters", []),
            _flaky_allure_parameters(result),
        )
        result_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _result_to_dict(result: FlakyTestResult) -> dict[str, Any]:
    return {
        "nodeid": result.nodeid,
        "status": result.status.value,
        "attempt_count": result.attempt_count,
        "attempts": [_attempt_to_dict(attempt) for attempt in result.attempts],
        "total_duration": result.total_duration,
    }


def _attempt_to_dict(attempt: AttemptResult) -> dict[str, Any]:
    return {
        "index": attempt.index,
        "outcome": attempt.outcome.value,
        "duration": attempt.duration,
        "failure_type": attempt.failure_type,
        "failure_message": attempt.failure_message,
    }


def _summary_to_text(summary: dict[str, Any]) -> str:
    return "\n".join(
        [
            "================ Flaky 治理汇总 ================",
            f"实际执行：{summary['total']}",
            f"通过：{summary['passed']}",
            f"重试通过：{summary['retry_passed']}",
            f"重试不通过：{summary['retry_failed']}",
            f"失败：{summary['failed']}",
            "",
            f"首次通过率：{summary['first_pass_rate']:.2%}",
            f"最终成功率：{summary['final_success_rate']:.2%}",
            f"重试恢复率：{summary['retry_recovery_rate']:.2%}",
            "================================================",
            "",
        ]
    )


def _ratio(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 0.0
    return round(numerator / denominator, 4)


def _format_rate(value: float) -> str:
    return f"{value:.2%}"


def _read_properties(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}

    properties: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        properties[key] = value
    return properties


def _write_properties(path: Path, values: dict[str, str]) -> None:
    lines = [f"{key}={_escape_property_value(values[key])}" for key in sorted(values)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _escape_property_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n")


def _allure_result_keys(results: list[FlakyTestResult]) -> dict[str, FlakyTestResult]:
    keys: dict[str, FlakyTestResult] = {}
    for result in results:
        keys[result.nodeid] = result
        keys[_nodeid_to_allure_full_name(result.nodeid)] = result
        keys[_nodeid_to_allure_name(result.nodeid)] = result
    return keys


def _nodeid_to_allure_full_name(nodeid: str) -> str:
    parts = nodeid.split("::")
    module_name = parts[0].replace("\\", "/").removesuffix(".py").replace("/", ".")
    if len(parts) == 1:
        return module_name
    if len(parts) == 2:
        return f"{module_name}#{_strip_param_id(parts[1])}"
    return f"{module_name}.{'.'.join(parts[1:-1])}#{_strip_param_id(parts[-1])}"


def _nodeid_to_allure_name(nodeid: str) -> str:
    return _strip_param_id(nodeid.split("::")[-1])


def _strip_param_id(value: str) -> str:
    return value.split("[", 1)[0]


def _match_allure_result(
    payload: dict[str, Any],
    results_by_key: dict[str, FlakyTestResult],
) -> FlakyTestResult | None:
    for key in (payload.get("fullName"), payload.get("name")):
        if key in results_by_key:
            return results_by_key[key]
    return None


def _merge_allure_parameters(
    existing_parameters: list[dict[str, Any]],
    flaky_parameters: list[dict[str, str]],
) -> list[dict[str, Any]]:
    flaky_names = {parameter["name"] for parameter in flaky_parameters}
    retained_parameters = [
        parameter
        for parameter in existing_parameters
        if parameter.get("name") not in flaky_names
    ]
    return retained_parameters + flaky_parameters


def _flaky_allure_parameters(result: FlakyTestResult) -> list[dict[str, str]]:
    return [
        {"name": "flaky_status", "value": result.status.value},
        {"name": "flaky_attempt_count", "value": str(result.attempt_count)},
        {"name": "flaky_first_outcome", "value": _attempt_outcome(result, 0)},
        {"name": "flaky_final_outcome", "value": _attempt_outcome(result, -1)},
        {"name": "flaky_total_duration", "value": f"{result.total_duration:.6f}"},
        {"name": "flaky_first_failure_type", "value": _result_first_failure_type(result)},
    ]


def _attempt_outcome(result: FlakyTestResult, index: int) -> str:
    if not result.attempts:
        return ""
    return result.attempts[index].outcome.value


def _result_first_failure_type(result: FlakyTestResult) -> str:
    for attempt in result.attempts:
        if attempt.failure_type:
            return attempt.failure_type
    return ""
