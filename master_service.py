from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_TEST_PATH = "module"
DEFAULT_SERIAL_MARKER = "serial"
PYTEST_EXIT_OK = 0
PYTEST_EXIT_NO_TESTS_COLLECTED = 5


@dataclass(frozen=True)
class CollectedTestCase:
    nodeid: str
    markers: frozenset[str]
    case_id: str | None = None
    param_hash: str | None = None
    normalized_case_path: str | None = None

    @property
    def is_serial(self) -> bool:
        return DEFAULT_SERIAL_MARKER in self.markers


def collect_test_cases(test_path: str | Path = DEFAULT_TEST_PATH) -> list[str]:
    return [case.nodeid for case in collect_test_case_items(test_path)]


def collect_test_case_items(test_path: str | Path = DEFAULT_TEST_PATH) -> list[CollectedTestCase]:
    # Keep this module as the stable compatibility facade. The Runner consumes
    # the richer CollectionResult directly from pytest_execution.
    from run_orchestration.pytest_execution import (
        collect_test_case_items as collect_authoritative_test_case_items,
        format_collection_error,
    )

    result = collect_authoritative_test_case_items(test_path)
    if result.raw_pytest_exit_code == PYTEST_EXIT_NO_TESTS_COLLECTED:
        return []
    if result.raw_pytest_exit_code != PYTEST_EXIT_OK:
        raise RuntimeError(format_collection_error(result))
    return list(result.cases)


def split_test_cases(
    cases: Sequence[CollectedTestCase],
    serial_marker: str = DEFAULT_SERIAL_MARKER,
) -> tuple[list[str], list[str]]:
    from run_orchestration.scheduling import split_test_cases as split_plan

    parallel_cases, serial_cases = split_plan(cases, serial_marker=serial_marker)
    return list(parallel_cases), list(serial_cases)


if __name__ == "__main__":
    case_pool = collect_test_cases()
    for pytest_nodeid in case_pool:
        print(pytest_nodeid)
