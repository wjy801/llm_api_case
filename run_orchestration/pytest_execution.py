from __future__ import annotations

from pathlib import Path
from typing import Sequence

import pytest

from master_service import PYTEST_EXIT_NO_TESTS_COLLECTED
from quality.config import QualityRuntimeConfig

from . import artifacts
from .paths import DEFAULT_ALLURE_RESULTS_DIR


def build_parallel_args(
    pytest_args: Sequence[str],
    *,
    numprocesses: str,
    dist: str | None,
    junit_suffix: str,
) -> list[str]:
    args = replace_junitxml_suffix(list(pytest_args), junit_suffix)
    args.extend(["-n", numprocesses])
    if dist:
        args.extend(["--dist", dist])
    return args


def ensure_quality_junit_args(
    pytest_args: Sequence[str],
    quality_config: QualityRuntimeConfig,
) -> list[str]:
    args = list(pytest_args)
    if not quality_config.enabled or artifacts.extract_junit_path(args) is not None:
        return args
    return args + [
        f"--junitxml={quality_config.output_dir / 'junit' / 'quality.xml'}"
    ]


def has_collect_only(pytest_args: Sequence[str]) -> bool:
    return any(
        arg in {"--collect-only", "--co", "--collectonly"}
        for arg in pytest_args
    )


def build_serial_args(
    pytest_args: Sequence[str], *, junit_suffix: str
) -> list[str]:
    return replace_junitxml_suffix(
        remove_xdist_args(list(pytest_args)), junit_suffix
    )


def remove_xdist_args(args: list[str]) -> list[str]:
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


def replace_junitxml_suffix(args: list[str], suffix: str) -> list[str]:
    replaced: list[str] = []
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--junitxml" and index + 1 < len(args):
            replaced.extend([arg, with_report_suffix(args[index + 1], suffix)])
            index += 2
            continue
        if arg.startswith("--junitxml="):
            report_path = arg.split("=", 1)[1]
            replaced.append(
                f"--junitxml={with_report_suffix(report_path, suffix)}"
            )
            index += 1
            continue
        replaced.append(arg)
        index += 1
    return replaced


def with_report_suffix(report_path: str, suffix: str) -> str:
    path = Path(report_path)
    stem = path.stem
    if stem.endswith(f"-{suffix}"):
        return path.as_posix()
    return path.with_name(f"{stem}-{suffix}{path.suffix}").as_posix()


def extract_junit_path(pytest_args: Sequence[str]) -> Path | None:
    return artifacts.extract_junit_path(pytest_args)


def run_pytest(pytest_args: list[str]) -> int:
    return int(pytest.main(pytest_args))


def run_serial_pool(pytest_args: list[str]) -> int:
    results_dir = DEFAULT_ALLURE_RESULTS_DIR
    preserved_results = artifacts.preserve_allure_results(results_dir)
    try:
        return run_pytest(pytest_args)
    finally:
        artifacts.restore_allure_results(results_dir, preserved_results)


def merge_exit_codes(exit_codes: Sequence[int]) -> int:
    if not exit_codes:
        return 0
    failures = [
        exit_code
        for exit_code in exit_codes
        if exit_code not in (0, PYTEST_EXIT_NO_TESTS_COLLECTED)
    ]
    return 1 if failures else 0
