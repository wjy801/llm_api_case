from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from io import StringIO
import os
from pathlib import Path
from typing import Sequence

import pytest


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_TEST_PATH = "module"
DEFAULT_SERIAL_MARKER = "serial"
PYTEST_EXIT_OK = 0
PYTEST_EXIT_NO_TESTS_COLLECTED = 5


@dataclass(frozen=True)
class CollectedTestCase:
    nodeid: str
    markers: frozenset[str]

    @property
    def is_serial(self) -> bool:
        return DEFAULT_SERIAL_MARKER in self.markers


def collect_test_cases(test_path: str | Path = DEFAULT_TEST_PATH) -> list[str]:
    return [case.nodeid for case in collect_test_case_items(test_path)]


def collect_test_case_items(test_path: str | Path = DEFAULT_TEST_PATH) -> list[CollectedTestCase]:
    collector = _CaseCollector()
    stdout = StringIO()
    stderr = StringIO()
    previous_plugin_autoload = os.environ.get("PYTEST_DISABLE_PLUGIN_AUTOLOAD")
    os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"

    try:
        with redirect_stdout(stdout), redirect_stderr(stderr):
            exit_code = pytest.main(
                [
                    "--collect-only",
                    "-q",
                    "-o",
                    "addopts=",
                    str(test_path),
                ],
                plugins=[collector],
            )
    finally:
        if previous_plugin_autoload is None:
            os.environ.pop("PYTEST_DISABLE_PLUGIN_AUTOLOAD", None)
        else:
            os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = previous_plugin_autoload

    if exit_code == PYTEST_EXIT_NO_TESTS_COLLECTED:
        return []

    if exit_code != PYTEST_EXIT_OK:
        raise RuntimeError(_collect_error_message(exit_code, stdout.getvalue(), stderr.getvalue()))

    return collector.items


def split_test_cases(
    cases: Sequence[CollectedTestCase],
    serial_marker: str = DEFAULT_SERIAL_MARKER,
) -> tuple[list[str], list[str]]:
    parallel_cases: list[str] = []
    serial_cases: list[str] = []

    for case in cases:
        if serial_marker in case.markers:
            serial_cases.append(case.nodeid)
            continue

        parallel_cases.append(case.nodeid)

    return parallel_cases, serial_cases


class _CaseCollector:
    def __init__(self) -> None:
        self.items: list[CollectedTestCase] = []

    def pytest_collection_finish(self, session: pytest.Session) -> None:
        seen: set[str] = set()
        for item in session.items:
            if item.nodeid in seen:
                continue

            seen.add(item.nodeid)
            self.items.append(
                CollectedTestCase(
                    nodeid=item.nodeid,
                    markers=frozenset(marker.name for marker in item.iter_markers()),
                )
            )


def _collect_error_message(exit_code: int, stdout: str, stderr: str) -> str:
    lines = [f"pytest collection failed with exit code {exit_code}."]
    if stdout.strip():
        lines.extend(["stdout:", stdout.strip()])
    if stderr.strip():
        lines.extend(["stderr:", stderr.strip()])
    return "\n".join(lines)


if __name__ == "__main__":
    case_pool = collect_test_cases()
    for pytest_nodeid in case_pool:
        print(pytest_nodeid)
