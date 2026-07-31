from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import UTC, datetime
import os
import shutil
import sys
import uuid
from pathlib import Path
from typing import Sequence

import pytest

from quality.aggregator import QualityMergeRequest, merge_quality_run
from quality.config import (
    QUALITY_ENABLE_ENV,
    QUALITY_EXECUTION_ID_ENV,
    QUALITY_OUTPUT_DIR_ENV,
    QUALITY_RUN_ID_ENV,
    QualityRuntimeConfig,
    load_quality_config,
)
from quality.identifiers import build_run_id
from quality.models import IntegrityStatus, RunRecord, RunStatus
from quality.storage import write_json_atomic
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

    quality_config = _resolve_parent_quality_config()
    quality_start_time = datetime.now(UTC)
    if quality_config.enabled:
        _write_initial_run_record(quality_config, quality_start_time)

    if not numprocesses:
        print("Parallel test execution disabled. Running all cases serially.")
        stage_id = "serial-pool"
        serial_args = _ensure_quality_junit_args(pytest_args, quality_config)
        junit_files = (_extract_junit_path(serial_args),)
        final_status = RunStatus.FINISHED
        try:
            with _quality_stage_environment(quality_config, stage_id):
                return _run_pytest(case_nodeids + serial_args)
        except (KeyboardInterrupt, SystemExit):
            final_status = RunStatus.INTERRUPTED
            raise
        except Exception:
            final_status = RunStatus.PARTIAL
            raise
        finally:
            _finalize_quality_run(
                quality_config,
                start_time=quality_start_time,
                expected_execution_ids=(stage_id,),
                expected_case_count=len(case_nodeids),
                junit_files=junit_files,
                status=final_status,
            )

    parallel_cases, serial_cases = split_test_cases(cases, serial_marker=serial_marker)
    print(
        "Parallel-first execution enabled: "
        f"workers={numprocesses}, parallel_cases={len(parallel_cases)}, serial_cases={len(serial_cases)}"
    )

    results: list[int] = []
    executed_stage_ids: list[str] = []
    junit_files: list[Path] = []
    final_status = RunStatus.FINISHED
    try:
        if parallel_cases:
            stage_id = "parallel-pool"
            pytest_args = _ensure_quality_junit_args(pytest_args, quality_config)
            parallel_args = _build_parallel_args(
                pytest_args,
                numprocesses=numprocesses,
                dist=dist,
                junit_suffix="parallel",
            )
            junit_files.append(_extract_junit_path(parallel_args))
            executed_stage_ids.append(stage_id)
            print(f"Running parallel pool: {len(parallel_cases)} cases")
            with _quality_stage_environment(quality_config, stage_id):
                results.append(_run_pytest(parallel_cases + parallel_args))
        else:
            print("Parallel pool is empty. Skipping parallel stage.")

        if serial_cases:
            stage_id = "serial-pool"
            pytest_args = _ensure_quality_junit_args(pytest_args, quality_config)
            serial_args = _build_serial_args(pytest_args, junit_suffix="serial")
            junit_files.append(_extract_junit_path(serial_args))
            executed_stage_ids.append(stage_id)
            print(f"Running serial pool: {len(serial_cases)} cases")
            with _quality_stage_environment(quality_config, stage_id):
                results.append(_run_serial_pool(serial_cases + serial_args))
        else:
            print("Serial pool is empty. Skipping serial stage.")

        return _merge_exit_codes(results)
    except (KeyboardInterrupt, SystemExit):
        final_status = RunStatus.INTERRUPTED
        raise
    except Exception:
        final_status = RunStatus.PARTIAL
        raise
    finally:
        _finalize_quality_run(
            quality_config,
            start_time=quality_start_time,
            expected_execution_ids=tuple(executed_stage_ids),
            expected_case_count=len(case_nodeids),
            junit_files=tuple(junit_files),
            status=final_status,
        )


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


def _ensure_quality_junit_args(
    pytest_args: Sequence[str],
    quality_config: QualityRuntimeConfig,
) -> list[str]:
    args = list(pytest_args)
    if not quality_config.enabled or _extract_junit_path(args) is not None:
        return args
    return args + [f"--junitxml={quality_config.output_dir / 'junit' / 'quality.xml'}"]


def _extract_junit_path(pytest_args: Sequence[str]) -> Path | None:
    index = 0
    while index < len(pytest_args):
        arg = pytest_args[index]
        if arg == "--junitxml" and index + 1 < len(pytest_args):
            return _resolve_report_path(pytest_args[index + 1])
        if arg.startswith("--junitxml="):
            value = arg.split("=", 1)[1]
            return _resolve_report_path(value)
        index += 1
    return None


def _resolve_report_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (PROJECT_ROOT / path).resolve()


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


def _write_initial_run_record(
    quality_config: QualityRuntimeConfig,
    start_time: datetime,
) -> None:
    if not quality_config.enabled or not quality_config.run_id:
        return
    try:
        write_json_atomic(
            quality_config.output_dir / "run.json",
            _build_run_record(
                quality_config,
                start_time=start_time,
                end_time=None,
                status=RunStatus.PARTIAL,
                integrity_status=IntegrityStatus.DEGRADED,
                integrity_issues=(),
            ),
        )
    except Exception as error:
        print(f"Quality run initialization failed: {type(error).__name__}: {error}")


def _finalize_quality_run(
    quality_config: QualityRuntimeConfig,
    *,
    start_time: datetime,
    expected_execution_ids: tuple[str, ...],
    expected_case_count: int,
    junit_files: tuple[Path | None, ...],
    status: RunStatus,
) -> None:
    if not quality_config.enabled or not quality_config.run_id:
        return
    try:
        merge_result = merge_quality_run(
            QualityMergeRequest(
                run_id=quality_config.run_id,
                output_dir=quality_config.output_dir,
                expected_execution_ids=expected_execution_ids,
                expected_case_count=expected_case_count,
                junit_files=tuple(path for path in junit_files if path is not None),
                run_start_time=start_time,
            )
        )
        write_json_atomic(
            quality_config.output_dir / "run.json",
            _build_run_record(
                quality_config,
                start_time=start_time,
                end_time=datetime.now(UTC),
                status=status,
                integrity_status=merge_result.integrity_status,
                integrity_issues=merge_result.integrity_issues,
            ),
        )
    except Exception as error:
        print(f"Quality merge failed open: {type(error).__name__}: {error}")


def _build_run_record(
    quality_config: QualityRuntimeConfig,
    *,
    start_time: datetime,
    end_time: datetime | None,
    status: RunStatus,
    integrity_status: IntegrityStatus,
    integrity_issues,
) -> RunRecord:
    return RunRecord(
        run_id=str(quality_config.run_id),
        job_name=os.environ.get("JOB_NAME") or None,
        build_number=os.environ.get("BUILD_NUMBER") or None,
        branch=os.environ.get("BRANCH_NAME") or os.environ.get("GIT_BRANCH") or None,
        commit_sha=os.environ.get("GIT_COMMIT") or None,
        trigger="jenkins" if os.environ.get("JOB_NAME") and os.environ.get("BUILD_NUMBER") else "local",
        environment=_quality_environment_name(),
        start_time=start_time,
        end_time=end_time,
        status=status,
        integrity_status=integrity_status,
        integrity_issues=tuple(integrity_issues),
    )


def _quality_environment_name() -> str:
    value = os.environ.get("USE_CHINA_ENVIRONMENT")
    if value is None:
        return "unknown"
    return "china" if value.strip().upper() == "TRUE" else "overseas"


def _run_serial_pool(pytest_args: list[str]) -> int:
    preserved_results = _preserve_allure_results(DEFAULT_ALLURE_RESULTS_DIR)
    try:
        return _run_pytest(pytest_args)
    finally:
        _restore_allure_results(DEFAULT_ALLURE_RESULTS_DIR, preserved_results)


def _run_pytest(pytest_args: list[str]) -> int:
    exit_code = pytest.main(pytest_args)
    return int(exit_code)


def _resolve_parent_quality_config() -> QualityRuntimeConfig:
    try:
        configured = load_quality_config()
    except ValueError as error:
        print(f"Quality collection disabled: {error}")
        return QualityRuntimeConfig(
            enabled=False,
            run_id=None,
            execution_id=None,
            output_dir=PROJECT_ROOT / "reports/quality",
        )

    output_dir = configured.output_dir
    if not output_dir.is_absolute():
        output_dir = PROJECT_ROOT / output_dir
    if not configured.enabled:
        return QualityRuntimeConfig(
            enabled=False,
            run_id=configured.run_id,
            execution_id=None,
            output_dir=output_dir,
        )

    return QualityRuntimeConfig(
        enabled=True,
        run_id=configured.run_id or _new_parent_run_id(),
        execution_id=None,
        output_dir=output_dir,
    )


def _new_parent_run_id() -> str:
    job_name = os.environ.get("JOB_NAME")
    build_number = os.environ.get("BUILD_NUMBER")
    if job_name and build_number:
        return build_run_id(job_name=job_name, build_number=build_number)
    return build_run_id()


@contextmanager
def _quality_stage_environment(
    quality_config: QualityRuntimeConfig,
    execution_id: str,
):
    if not quality_config.enabled:
        yield
        return
    values = {
        QUALITY_ENABLE_ENV: "1",
        QUALITY_RUN_ID_ENV: str(quality_config.run_id),
        QUALITY_EXECUTION_ID_ENV: execution_id,
        QUALITY_OUTPUT_DIR_ENV: str(quality_config.output_dir),
    }
    previous = {name: os.environ.get(name) for name in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


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
