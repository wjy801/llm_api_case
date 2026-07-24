from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Sequence

import pytest

from governance.flaky_gate import evaluate_flaky_gate
from governance.flaky_models import AttemptOutcome, AttemptResult, FlakyStatus, FlakyTestResult
from governance.nodeid_validator import (
    NodeIdValidationResult,
    RetryQueueEntry,
    read_retry_queue,
    validate_nodeids,
    write_stale_retry_queue,
)
from governance.retry_queue import update_retry_queue_after_rerun
from master_service import DEFAULT_TEST_PATH, collect_test_cases


LATEST_RETRY_QUEUE_PATH = Path("reports/flaky/latest-retry-nodeids.csv")
STALE_RETRY_QUEUE_PATH = Path("reports/flaky/current/stale-retry-nodeids.csv")
DEFAULT_FLAKY_REPORT_DIR = Path("reports/flaky/current")


def run(test_path: str = DEFAULT_TEST_PATH, extra_pytest_args: Sequence[str] | None = None) -> int:
    case_pool = collect_test_cases(test_path)
    if len(case_pool) == 0:
        print("未收集到可执行用例。")
        return 1

    pytest_args = list(case_pool)
    if extra_pytest_args:
        pytest_args.extend(extra_pytest_args)

    print(f"已收集用例数: {len(case_pool)}")
    for nodeid in case_pool:
        print(f"- {nodeid}")

    return pytest.main(pytest_args)


def main(argv: Sequence[str] | None = None) -> int:
    parsed_args, pytest_args = _parse_args(argv or [])
    _append_flaky_report_dir(pytest_args, parsed_args.flaky_report_dir)
    if _is_rerun_mode(parsed_args):
        return rerun_flaky(parsed_args=parsed_args, extra_pytest_args=pytest_args)

    if parsed_args.numprocesses is not None:
        pytest_args.extend(["-n", parsed_args.numprocesses])
    if parsed_args.dist is not None:
        pytest_args.extend(["--dist", parsed_args.dist])

    if not _contains_collect_only(pytest_args):
        _remove_flaky_report_files(parsed_args.flaky_report_dir)
    exit_code = run(test_path=parsed_args.test_path, extra_pytest_args=pytest_args)
    return _apply_flaky_gate(
        exit_code,
        parsed_args.flaky_report_dir,
        fail_on_retry_passed=parsed_args.fail_on_retry_passed,
        skip_gate=_contains_collect_only(pytest_args),
    )


def rerun_flaky(parsed_args: argparse.Namespace, extra_pytest_args: Sequence[str] | None = None) -> int:
    queue_path = _retry_queue_path(parsed_args)
    if not queue_path.exists():
        print(f"Flaky 复测队列不存在: {queue_path}")
        return 1

    try:
        all_entries = read_retry_queue(queue_path)
        entries = _filter_entries_by_status(all_entries, _status_filter(parsed_args.rerun_status))
    except ValueError as error:
        print(str(error))
        return 1

    collected_nodeids = collect_test_cases(parsed_args.test_path)
    validation = validate_nodeids(entries, collected_nodeids)
    write_stale_retry_queue(STALE_RETRY_QUEUE_PATH, validation.stale_entries)
    _print_validation_result(queue_path, validation)

    if parsed_args.strict_nodeids and validation.stale_count > 0:
        print("--strict-nodeids 模式下存在失效 nodeid，已阻断复测。")
        return 1

    if validation.valid_count == 0:
        print("没有有效 Flaky 复测 nodeid。")
        return 1

    if parsed_args.list_rerun_targets:
        for nodeid in validation.valid_nodeids:
            print(f"- {nodeid}")
        return 0

    pytest_args = validation.valid_nodeids
    if extra_pytest_args:
        pytest_args.extend(extra_pytest_args)
    if parsed_args.numprocesses is not None:
        pytest_args.extend(["-n", parsed_args.numprocesses])
    if parsed_args.dist is not None:
        pytest_args.extend(["--dist", parsed_args.dist])

    update_base_entries = _retry_queue_entries_for_update(all_entries)
    _remove_flaky_report_files(parsed_args.flaky_report_dir)
    exit_code = pytest.main(pytest_args)
    _update_latest_retry_queue_after_rerun(update_base_entries, parsed_args.flaky_report_dir)
    return _apply_flaky_gate(
        exit_code,
        parsed_args.flaky_report_dir,
        fail_on_retry_passed=parsed_args.fail_on_retry_passed,
        skip_gate=_contains_collect_only(pytest_args),
    )


def _parse_args(argv: Sequence[str]) -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(description="API test framework runner")
    parser.add_argument(
        "target",
        nargs="?",
        help=f"测试收集路径，默认 {DEFAULT_TEST_PATH}",
    )
    parser.add_argument(
        "--test-path",
        dest="test_path",
        help=f"测试收集路径，默认 {DEFAULT_TEST_PATH}",
    )
    parser.add_argument(
        "-n",
        "--numprocesses",
        dest="numprocesses",
        help="pytest-xdist 并发 worker 数，例如 auto、2",
    )
    parser.add_argument(
        "--dist",
        dest="dist",
        help="pytest-xdist 分发策略，例如 load、loadscope、worksteal",
    )
    parser.add_argument(
        "--rerun-latest-flaky",
        action="store_true",
        help="读取 reports/flaky/latest-retry-nodeids.csv 并复测有效 nodeid",
    )
    parser.add_argument(
        "--rerun-from",
        dest="rerun_from",
        help="从指定 retry-nodeids.csv 读取 Flaky 复测 nodeid",
    )
    parser.add_argument(
        "--rerun-status",
        choices=[FlakyStatus.RETRY_FAILED.value, FlakyStatus.RETRY_PASSED.value],
        help="只复测指定状态的 Flaky nodeid",
    )
    parser.add_argument(
        "--list-rerun-targets",
        action="store_true",
        help="只展示校验后的复测目标，不发起实际执行",
    )
    parser.add_argument(
        "--strict-nodeids",
        action="store_true",
        help="存在失效 nodeid 时直接阻断复测",
    )
    parser.add_argument(
        "--fail-on-retry-passed",
        action="store_true",
        help="严格模式：存在重试通过时也返回失败退出码",
    )
    parser.add_argument(
        "--flaky-report-dir",
        type=Path,
        default=DEFAULT_FLAKY_REPORT_DIR,
        help="Flaky 治理报告目录，默认 reports/flaky/current",
    )

    parsed_args, pytest_args = parser.parse_known_args(list(argv))
    parsed_args.test_path = parsed_args.test_path or parsed_args.target or DEFAULT_TEST_PATH
    return parsed_args, pytest_args


def _is_rerun_mode(parsed_args: argparse.Namespace) -> bool:
    return bool(parsed_args.rerun_latest_flaky or parsed_args.rerun_from)


def _retry_queue_path(parsed_args: argparse.Namespace) -> Path:
    if parsed_args.rerun_from:
        return Path(parsed_args.rerun_from)
    return LATEST_RETRY_QUEUE_PATH


def _status_filter(status: str | None) -> FlakyStatus | None:
    if status is None:
        return None
    return FlakyStatus(status)


def _filter_entries_by_status(
    entries: list[RetryQueueEntry],
    status_filter: FlakyStatus | None,
) -> list[RetryQueueEntry]:
    if status_filter is None:
        return entries
    return [entry for entry in entries if entry.status == status_filter.value]


def _append_flaky_report_dir(pytest_args: list[str], report_dir: Path) -> None:
    pytest_args.extend(["--flaky-governance-report-dir", str(report_dir)])


def _contains_collect_only(pytest_args: Sequence[str]) -> bool:
    return "--collect-only" in pytest_args or "--co" in pytest_args


def _update_latest_retry_queue_after_rerun(all_entries: list[RetryQueueEntry], report_dir: Path) -> None:
    results_path = report_dir / "flaky-results.json"
    results = _read_current_flaky_results(results_path)
    if results is None:
        print(f"未找到本次 Flaky 结果，跳过 latest 队列更新: {results_path}")
        return

    update_retry_queue_after_rerun(
        LATEST_RETRY_QUEUE_PATH,
        [entry.row for entry in all_entries],
        results,
    )
    print(f"Flaky latest 队列已按本次复测结果更新: {LATEST_RETRY_QUEUE_PATH}")


def _retry_queue_entries_for_update(fallback_entries: list[RetryQueueEntry]) -> list[RetryQueueEntry]:
    if not LATEST_RETRY_QUEUE_PATH.exists():
        return fallback_entries

    try:
        return read_retry_queue(LATEST_RETRY_QUEUE_PATH)
    except ValueError:
        return fallback_entries


def _remove_flaky_report_files(report_dir: Path) -> None:
    for filename in ("flaky-results.json", "flaky-summary.json", "flaky-summary.txt"):
        path = report_dir / filename
        if path.exists():
            path.unlink()


def _apply_flaky_gate(
    exit_code: int,
    report_dir: Path,
    *,
    fail_on_retry_passed: bool,
    skip_gate: bool = False,
) -> int:
    if skip_gate:
        return exit_code

    summary = _read_flaky_summary(report_dir / "flaky-summary.json")
    if summary is None:
        return exit_code

    decision = evaluate_flaky_gate(summary, fail_on_retry_passed=fail_on_retry_passed)
    for message in decision.messages:
        print(message)

    if decision.should_fail:
        return 1
    return exit_code


def _read_flaky_summary(path: Path) -> dict | None:
    if not path.exists():
        print(f"未找到 Flaky 汇总，跳过门禁判定: {path}")
        return None

    return json.loads(path.read_text(encoding="utf-8"))


def _read_current_flaky_results(path: Path) -> list[FlakyTestResult] | None:
    if not path.exists():
        return None

    payload = json.loads(path.read_text(encoding="utf-8"))
    return [_flaky_result_from_dict(result) for result in payload.get("results", [])]


def _flaky_result_from_dict(result: dict) -> FlakyTestResult:
    return FlakyTestResult(
        nodeid=result["nodeid"],
        status=FlakyStatus(result["status"]),
        attempts=tuple(_attempt_from_dict(attempt) for attempt in result.get("attempts", [])),
        total_duration=float(result.get("total_duration", 0.0)),
    )


def _attempt_from_dict(attempt: dict) -> AttemptResult:
    return AttemptResult(
        index=int(attempt["index"]),
        outcome=AttemptOutcome(attempt["outcome"]),
        duration=float(attempt.get("duration", 0.0)),
        failure_type=attempt.get("failure_type"),
        failure_message=attempt.get("failure_message"),
    )


def _print_validation_result(queue_path: Path, validation: NodeIdValidationResult) -> None:
    print(f"Flaky 复测队列: {queue_path}")
    print(f"有效 nodeid: {validation.valid_count}")
    print(f"失效 nodeid: {validation.stale_count}")
    if validation.stale_count > 0:
        print(f"失效 nodeid 已写入: {STALE_RETRY_QUEUE_PATH}")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
