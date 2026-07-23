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
