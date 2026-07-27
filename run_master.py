from __future__ import annotations

import argparse
import sys
from typing import Sequence

import pytest

from master_service import DEFAULT_TEST_PATH, collect_test_cases


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
    if parsed_args.numprocesses is not None:
        pytest_args.extend(["-n", parsed_args.numprocesses])
    if parsed_args.dist is not None:
        pytest_args.extend(["--dist", parsed_args.dist])

    return run(test_path=parsed_args.test_path, extra_pytest_args=pytest_args)


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
        help="pytest-xdist 并发 worker 数，例如 auto、2、4",
    )
    parser.add_argument(
        "--dist",
        dest="dist",
        help="pytest-xdist 分发策略，例如 load、loadscope、worksteal",
    )

    parsed_args, pytest_args = parser.parse_known_args(list(argv))
    parsed_args.test_path = parsed_args.test_path or parsed_args.target or DEFAULT_TEST_PATH
    return parsed_args, pytest_args


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
