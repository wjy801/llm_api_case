from __future__ import annotations

import argparse
from typing import Sequence

from master_service import DEFAULT_SERIAL_MARKER, DEFAULT_TEST_PATH

from . import runner


def main(argv: Sequence[str] | None = None) -> int:
    parsed_args, pytest_args = parse_args(argv or [])
    return runner.run(
        test_path=parsed_args.test_path,
        extra_pytest_args=pytest_args,
        numprocesses=parsed_args.numprocesses,
        dist=parsed_args.dist,
        serial_marker=parsed_args.serial_marker,
    )


def parse_args(argv: Sequence[str]) -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(description="API test framework runner")
    parser.add_argument(
        "target",
        nargs="?",
        help=f"Test collection path. Defaults to {DEFAULT_TEST_PATH}.",
    )
    parser.add_argument(
        "--test-path",
        dest="test_path",
        help=f"Test collection path. Defaults to {DEFAULT_TEST_PATH}.",
    )
    parser.add_argument(
        "-n",
        "--numprocesses",
        dest="numprocesses",
        help="pytest-xdist worker count, for example auto, 2, 4.",
    )
    parser.add_argument(
        "--dist",
        dest="dist",
        help=(
            "pytest-xdist distribution strategy, for example load, "
            "loadscope, worksteal."
        ),
    )
    parser.add_argument(
        "--serial-marker",
        dest="serial_marker",
        default=DEFAULT_SERIAL_MARKER,
        help=(
            "Marker name for cases that must run serially. "
            f"Defaults to {DEFAULT_SERIAL_MARKER}."
        ),
    )
    parser.add_argument(
        "--parallel-first",
        action="store_true",
        help=(
            "Compatibility flag. Passing -n already enables "
            "parallel-first execution."
        ),
    )
    parsed_args, pytest_args = parser.parse_known_args(list(argv))
    parsed_args.test_path = (
        parsed_args.test_path or parsed_args.target or DEFAULT_TEST_PATH
    )
    return parsed_args, pytest_args
