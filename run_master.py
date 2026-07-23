from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Sequence

import pytest

from governance.flaky_models import FlakyStatus
from governance.nodeid_validator import (
    NodeIdValidationResult,
    read_retry_queue,
    validate_nodeids,
    write_stale_retry_queue,
)
from master_service import DEFAULT_TEST_PATH, collect_test_cases


LATEST_RETRY_QUEUE_PATH = Path("reports/flaky/latest-retry-nodeids.csv")
STALE_RETRY_QUEUE_PATH = Path("reports/flaky/current/stale-retry-nodeids.csv")


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
    if _is_rerun_mode(parsed_args):
        return rerun_flaky(parsed_args=parsed_args, extra_pytest_args=pytest_args)

    if parsed_args.numprocesses is not None:
        pytest_args.extend(["-n", parsed_args.numprocesses])
    if parsed_args.dist is not None:
        pytest_args.extend(["--dist", parsed_args.dist])

    return run(test_path=parsed_args.test_path, extra_pytest_args=pytest_args)


def rerun_flaky(parsed_args: argparse.Namespace, extra_pytest_args: Sequence[str] | None = None) -> int:
    queue_path = _retry_queue_path(parsed_args)
    if not queue_path.exists():
        print(f"Flaky 复测队列不存在: {queue_path}")
        return 1

    try:
        entries = read_retry_queue(queue_path, status_filter=_status_filter(parsed_args.rerun_status))
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

    return pytest.main(pytest_args)


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


def _print_validation_result(queue_path: Path, validation: NodeIdValidationResult) -> None:
    print(f"Flaky 复测队列: {queue_path}")
    print(f"有效 nodeid: {validation.valid_count}")
    print(f"失效 nodeid: {validation.stale_count}")
    if validation.stale_count > 0:
        print(f"失效 nodeid 已写入: {STALE_RETRY_QUEUE_PATH}")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
