from __future__ import annotations

from contextlib import contextmanager, redirect_stderr, redirect_stdout
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from io import StringIO
from pathlib import Path
import os
from typing import Sequence

import pytest

from master_service import CollectedTestCase

from . import artifacts
from .allure_lifecycle import (
    RUNNER_MANAGED_ALLURE_ENV,
    AllureRunLifecycle,
    extract_allure_results_dir as _extract_allure_results_dir,
)
from .paths import DEFAULT_ALLURE_RESULTS_DIR


PYTEST_EXIT_OK = 0
PYTEST_EXIT_TESTS_FAILED = 1
PYTEST_EXIT_INTERRUPTED = 2
PYTEST_EXIT_INTERNAL_ERROR = 3
PYTEST_EXIT_USAGE_ERROR = 4
PYTEST_EXIT_NO_TESTS_COLLECTED = 5
PYTEST_TERMINATING_EXIT_CODES = frozenset(
    {
        PYTEST_EXIT_INTERRUPTED,
        PYTEST_EXIT_INTERNAL_ERROR,
        PYTEST_EXIT_USAGE_ERROR,
        PYTEST_EXIT_NO_TESTS_COLLECTED,
    }
)
COLLECT_ONLY_ARGS = frozenset({"--collect-only", "--co", "--collectonly"})
_SELECTION_OPTIONS_WITH_VALUE = frozenset(
    {"-k", "-m", "--ignore", "--ignore-glob", "--deselect"}
)
_SELECTION_OPTION_PREFIXES = (
    "-k=",
    "-m=",
    "--ignore=",
    "--ignore-glob=",
    "--deselect=",
)
_EXECUTION_OPTIONS_WITH_VALUE = frozenset(
    {
        "--junitxml",
        "--junit-xml",
        "--alluredir",
        "-n",
        "--numprocesses",
        "--dist",
    }
)
_EXECUTION_OPTION_PREFIXES = (
    "--junitxml=",
    "--junit-xml=",
    "--alluredir=",
    "--numprocesses=",
    "--dist=",
)
_EXECUTION_ONLY_FLAGS = frozenset({"--clean-alluredir"})


@dataclass(frozen=True)
class PytestArgumentPlan:
    collection_args: tuple[str, ...]
    execution_args: tuple[str, ...]
    selection_args: tuple[str, ...]
    collect_only: bool


@dataclass(frozen=True)
class CollectionResult:
    raw_pytest_exit_code: int
    cases: tuple[CollectedTestCase, ...]
    stdout: str
    stderr: str


class PoolExecutionStatus(str, Enum):
    NOT_RUN = "NOT_RUN"
    COMPLETED = "COMPLETED"
    ERROR = "ERROR"


@dataclass(frozen=True)
class PoolExecutionResult:
    stage_id: str
    planned_nodeids: tuple[str, ...]
    status: PoolExecutionStatus
    raw_pytest_exit_code: int | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    exception_type: str | None = None
    junit_path: Path | None = None


def partition_pytest_args(pytest_args: Sequence[str]) -> PytestArgumentPlan:
    collection_args: list[str] = []
    execution_args: list[str] = []
    selection_args: list[str] = []
    collect_only = False
    args = list(pytest_args)
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in COLLECT_ONLY_ARGS:
            collect_only = True
            index += 1
            continue
        if arg in _SELECTION_OPTIONS_WITH_VALUE:
            if index + 1 >= len(args):
                raise ValueError(f"pytest selection option requires a value: {arg}")
            pair = [arg, args[index + 1]]
            collection_args.extend(pair)
            selection_args.extend(pair)
            index += 2
            continue
        if arg.startswith(_SELECTION_OPTION_PREFIXES):
            collection_args.append(arg)
            selection_args.append(arg)
            index += 1
            continue
        if arg in _EXECUTION_OPTIONS_WITH_VALUE:
            if index + 1 >= len(args):
                raise ValueError(f"pytest execution option requires a value: {arg}")
            execution_args.extend([arg, args[index + 1]])
            index += 2
            continue
        if arg.startswith(_EXECUTION_OPTION_PREFIXES) or arg in _EXECUTION_ONLY_FLAGS:
            execution_args.append(arg)
            index += 1
            continue

        # Unknown/plugin arguments are shared to preserve their established
        # behavior without attempting to reimplement pytest's full parser.
        collection_args.append(arg)
        execution_args.append(arg)
        index += 1

    return PytestArgumentPlan(
        collection_args=tuple(collection_args),
        execution_args=tuple(execution_args),
        selection_args=tuple(selection_args),
        collect_only=collect_only,
    )


def collect_test_case_items(
    test_path: str | Path,
    pytest_args: Sequence[str] = (),
) -> CollectionResult:
    collector = _CaseCollector()
    stdout = StringIO()
    stderr = StringIO()
    args = [
        "--collect-only",
        "-q",
        "-o",
        "addopts=",
        *pytest_args,
        str(test_path),
    ]
    with redirect_stdout(stdout), redirect_stderr(stderr):
        exit_code = int(pytest.main(args, plugins=[collector]))

    if collector.duplicate_nodeids:
        duplicates = ", ".join(sorted(collector.duplicate_nodeids))
        raise RuntimeError(f"pytest collection produced duplicate nodeids: {duplicates}")
    return CollectionResult(
        raw_pytest_exit_code=exit_code,
        cases=tuple(collector.items),
        stdout=stdout.getvalue(),
        stderr=stderr.getvalue(),
    )


def format_collection_error(result: CollectionResult) -> str:
    lines = [
        f"pytest collection failed with exit code {result.raw_pytest_exit_code}."
    ]
    if result.stdout.strip():
        lines.extend(["stdout:", result.stdout.strip()])
    if result.stderr.strip():
        lines.extend(["stderr:", result.stderr.strip()])
    return "\n".join(lines)


class _CaseCollector:
    def __init__(self) -> None:
        self.items: list[CollectedTestCase] = []
        self.duplicate_nodeids: set[str] = set()

    def pytest_collection_finish(self, session: pytest.Session) -> None:
        seen: set[str] = set()
        for item in session.items:
            if item.nodeid in seen:
                self.duplicate_nodeids.add(item.nodeid)
                continue
            seen.add(item.nodeid)
            self.items.append(
                CollectedTestCase(
                    nodeid=item.nodeid,
                    markers=frozenset(marker.name for marker in item.iter_markers()),
                )
            )


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
    quality_config,
) -> list[str]:
    args = list(pytest_args)
    if not quality_config.enabled or artifacts.extract_junit_path(args) is not None:
        return args
    return args + [
        f"--junitxml={quality_config.output_dir / 'junit' / 'quality.xml'}"
    ]


def has_collect_only(pytest_args: Sequence[str]) -> bool:
    return any(arg in COLLECT_ONLY_ARGS for arg in pytest_args)


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


def extract_allure_results_dir(pytest_args: Sequence[str]) -> Path:
    if any(
        argument == "--alluredir" or argument.startswith("--alluredir=")
        for argument in pytest_args
    ):
        return _extract_allure_results_dir(pytest_args)
    return DEFAULT_ALLURE_RESULTS_DIR


def run_pytest(pytest_args: list[str]) -> int:
    return int(pytest.main(pytest_args))


def run_serial_pool(pytest_args: list[str]) -> int:
    return run_pytest(pytest_args)


def execute_pool(
    stage_id: str,
    planned_nodeids: Sequence[str],
    pytest_args: Sequence[str],
    *,
    allure_lifecycle: AllureRunLifecycle | None = None,
) -> PoolExecutionResult:
    nodeids = tuple(planned_nodeids)
    junit_path = extract_junit_path(pytest_args)
    if not nodeids:
        return PoolExecutionResult(
            stage_id=stage_id,
            planned_nodeids=(),
            status=PoolExecutionStatus.NOT_RUN,
            junit_path=junit_path,
        )

    started_at = datetime.now(UTC)
    effective_args = (
        allure_lifecycle.pool_args(stage_id, pytest_args)
        if allure_lifecycle is not None
        else list(pytest_args)
    )
    try:
        args = [*nodeids, *effective_args]
        with _runner_managed_allure_environment():
            exit_code = run_pytest(args)
    except Exception as error:
        return PoolExecutionResult(
            stage_id=stage_id,
            planned_nodeids=nodeids,
            status=PoolExecutionStatus.ERROR,
            started_at=started_at,
            completed_at=datetime.now(UTC),
            exception_type=type(error).__name__,
            junit_path=junit_path,
        )
    finally:
        if allure_lifecycle is not None:
            allure_lifecycle.merge_pool(stage_id)
    return PoolExecutionResult(
        stage_id=stage_id,
        planned_nodeids=nodeids,
        status=PoolExecutionStatus.COMPLETED,
        raw_pytest_exit_code=exit_code,
        started_at=started_at,
        completed_at=datetime.now(UTC),
        junit_path=junit_path,
    )


def merge_exit_codes(exit_codes: Sequence[int]) -> int:
    if not exit_codes:
        return 0
    for exit_code in exit_codes:
        if exit_code in PYTEST_TERMINATING_EXIT_CODES:
            return exit_code
    if any(exit_code == PYTEST_EXIT_TESTS_FAILED for exit_code in exit_codes):
        return PYTEST_EXIT_TESTS_FAILED
    if any(exit_code != PYTEST_EXIT_OK for exit_code in exit_codes):
        return PYTEST_EXIT_TESTS_FAILED
    return PYTEST_EXIT_OK


def should_stop_after_exit_code(exit_code: int | None) -> bool:
    return exit_code in PYTEST_TERMINATING_EXIT_CODES


@contextmanager
def _runner_managed_allure_environment():
    previous = os.environ.get(RUNNER_MANAGED_ALLURE_ENV)
    os.environ[RUNNER_MANAGED_ALLURE_ENV] = "1"
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(RUNNER_MANAGED_ALLURE_ENV, None)
        else:
            os.environ[RUNNER_MANAGED_ALLURE_ENV] = previous
