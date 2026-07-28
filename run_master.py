from __future__ import annotations

import argparse
import os
import shutil
import sys
import uuid
from pathlib import Path
from typing import Sequence

import pytest

from master_service import (
    DEFAULT_SERIAL_MARKER,
    DEFAULT_TEST_PATH,
    PYTEST_EXIT_NO_TESTS_COLLECTED,
    collect_test_case_items,
    split_test_cases,
)


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_ALLURE_RESULTS_DIR = PROJECT_ROOT / "allure-results"


def run(
    test_path: str = DEFAULT_TEST_PATH,
    extra_pytest_args: Sequence[str] | None = None,
    *,
    numprocesses: str | None = None,
    dist: str | None = None,
    serial_marker: str = DEFAULT_SERIAL_MARKER,
) -> int:
    cases = collect_test_case_items(test_path)
    if len(cases) == 0:
        print("No executable test cases collected.")
        return 1

    case_nodeids = [case.nodeid for case in cases]
    print(f"Collected test cases: {len(cases)}")
    for nodeid in case_nodeids:
        print(f"- {nodeid}")

    pytest_args = list(extra_pytest_args or [])
    if _has_collect_only(pytest_args):
        parallel_cases, serial_cases = split_test_cases(cases, serial_marker=serial_marker)
        print(f"Parallel pool cases: {len(parallel_cases)}")
        print(f"Serial pool cases: {len(serial_cases)}")
        print(f"{len(cases)} tests collected")
        return 0

    if not numprocesses:
        print("Parallel test execution disabled. Running all cases serially.")
        return _run_pytest(case_nodeids + pytest_args)

    parallel_cases, serial_cases = split_test_cases(cases, serial_marker=serial_marker)
    print(
        "Parallel-first execution enabled: "
        f"workers={numprocesses}, parallel_cases={len(parallel_cases)}, serial_cases={len(serial_cases)}"
    )

    results: list[int] = []
    if parallel_cases:
        parallel_args = _build_parallel_args(
            pytest_args,
            numprocesses=numprocesses,
            dist=dist,
            junit_suffix="parallel",
        )
        print(f"Running parallel pool: {len(parallel_cases)} cases")
        results.append(_run_pytest(parallel_cases + parallel_args))
    else:
        print("Parallel pool is empty. Skipping parallel stage.")

    if serial_cases:
        serial_args = _build_serial_args(pytest_args, junit_suffix="serial")
        print(f"Running serial pool: {len(serial_cases)} cases")
        results.append(_run_serial_pool(serial_cases + serial_args))
    else:
        print("Serial pool is empty. Skipping serial stage.")

    return _merge_exit_codes(results)


def main(argv: Sequence[str] | None = None) -> int:
    parsed_args, pytest_args = _parse_args(argv or [])
    return run(
        test_path=parsed_args.test_path,
        extra_pytest_args=pytest_args,
        numprocesses=parsed_args.numprocesses,
        dist=parsed_args.dist,
        serial_marker=parsed_args.serial_marker,
    )


def _parse_args(argv: Sequence[str]) -> tuple[argparse.Namespace, list[str]]:
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
        help="pytest-xdist distribution strategy, for example load, loadscope, worksteal.",
    )
    parser.add_argument(
        "--serial-marker",
        dest="serial_marker",
        default=DEFAULT_SERIAL_MARKER,
        help=f"Marker name for cases that must run serially. Defaults to {DEFAULT_SERIAL_MARKER}.",
    )
    parser.add_argument(
        "--parallel-first",
        action="store_true",
        help="Compatibility flag. Passing -n already enables parallel-first execution.",
    )

    parsed_args, pytest_args = parser.parse_known_args(list(argv))
    parsed_args.test_path = parsed_args.test_path or parsed_args.target or DEFAULT_TEST_PATH
    return parsed_args, pytest_args


def _build_parallel_args(
    pytest_args: Sequence[str],
    *,
    numprocesses: str,
    dist: str | None,
    junit_suffix: str,
) -> list[str]:
    args = _replace_junitxml_suffix(list(pytest_args), junit_suffix)
    args.extend(["-n", numprocesses])
    if dist:
        args.extend(["--dist", dist])
    return args


def _has_collect_only(pytest_args: Sequence[str]) -> bool:
    return any(arg in {"--collect-only", "--co", "--collectonly"} for arg in pytest_args)


def _build_serial_args(pytest_args: Sequence[str], *, junit_suffix: str) -> list[str]:
    return _replace_junitxml_suffix(_remove_xdist_args(list(pytest_args)), junit_suffix)


def _remove_xdist_args(args: list[str]) -> list[str]:
    cleaned: list[str] = []
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in {"-n", "--numprocesses", "--dist"}:
            index += 2
            continue

        if arg.startswith("--numprocesses=") or arg.startswith("--dist="):
            index += 1
            continue

        cleaned.append(arg)
        index += 1

    return cleaned


def _replace_junitxml_suffix(args: list[str], suffix: str) -> list[str]:
    replaced: list[str] = []
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--junitxml" and index + 1 < len(args):
            replaced.extend([arg, _with_report_suffix(args[index + 1], suffix)])
            index += 2
            continue

        if arg.startswith("--junitxml="):
            report_path = arg.split("=", 1)[1]
            replaced.append(f"--junitxml={_with_report_suffix(report_path, suffix)}")
            index += 1
            continue

        replaced.append(arg)
        index += 1

    return replaced


def _with_report_suffix(report_path: str, suffix: str) -> str:
    path = Path(report_path)
    stem = path.stem
    if stem.endswith(f"-{suffix}"):
        return path.as_posix()
    return path.with_name(f"{stem}-{suffix}{path.suffix}").as_posix()


def _run_serial_pool(pytest_args: list[str]) -> int:
    preserved_results = _preserve_allure_results(DEFAULT_ALLURE_RESULTS_DIR)
    try:
        return _run_pytest(pytest_args)
    finally:
        _restore_allure_results(DEFAULT_ALLURE_RESULTS_DIR, preserved_results)


def _run_pytest(pytest_args: list[str]) -> int:
    exit_code = pytest.main(pytest_args)
    return int(exit_code)


def _merge_exit_codes(exit_codes: Sequence[int]) -> int:
    if not exit_codes:
        return 0

    failures = [exit_code for exit_code in exit_codes if exit_code not in (0, PYTEST_EXIT_NO_TESTS_COLLECTED)]
    return 1 if failures else 0


def _preserve_allure_results(results_dir: Path) -> Path | None:
    if not results_dir.exists():
        return None

    temp_root = results_dir.parent / f".allure-results-preserve-{uuid.uuid4().hex}"
    shutil.copytree(results_dir, temp_root)
    return temp_root


def _restore_allure_results(results_dir: Path, preserved_results: Path | None) -> None:
    if preserved_results is None:
        return

    try:
        results_dir.mkdir(parents=True, exist_ok=True)
        for item in preserved_results.iterdir():
            target = results_dir / item.name
            if target.exists():
                target = results_dir / f"{item.stem}-{uuid.uuid4().hex}{item.suffix}"

            if item.is_dir() and not item.is_symlink():
                shutil.copytree(item, target)
            else:
                shutil.copy2(item, target)
    finally:
        shutil.rmtree(preserved_results, ignore_errors=True)


if __name__ == "__main__":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    os.environ.setdefault("PYTHONUTF8", "1")
    raise SystemExit(main(sys.argv[1:]))
