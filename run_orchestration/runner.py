from __future__ import annotations

from datetime import UTC, datetime
import sys
from typing import Sequence

from config import settings
from master_service import DEFAULT_SERIAL_MARKER, DEFAULT_TEST_PATH

from . import (
    artifacts,
    pytest_execution,
    quality_lifecycle,
    scheduling,
)


def run(
    test_path: str = DEFAULT_TEST_PATH,
    extra_pytest_args: Sequence[str] | None = None,
    *,
    numprocesses: str | None = None,
    dist: str | None = None,
    serial_marker: str = DEFAULT_SERIAL_MARKER,
) -> int:
    try:
        argument_plan = pytest_execution.partition_pytest_args(
            extra_pytest_args or ()
        )
    except ValueError as error:
        print(f"Invalid pytest arguments: {error}", file=sys.stderr)
        return pytest_execution.PYTEST_EXIT_USAGE_ERROR

    quality_run_lifecycle = quality_lifecycle.create_quality_run_lifecycle()
    quality_start_time = datetime.now(UTC)
    quality_run_lifecycle.prepare(quality_start_time)
    collection_started_at = datetime.now(UTC)

    try:
        collection = pytest_execution.collect_test_case_items(
            test_path,
            argument_plan.collection_args,
        )
    except Exception as error:
        print(
            f"Authoritative pytest collection failed: "
            f"{type(error).__name__}: {error}",
            file=sys.stderr,
        )
        quality_run_lifecycle.record_collection_failure()
        return pytest_execution.PYTEST_EXIT_TESTS_FAILED

    if collection.raw_pytest_exit_code != pytest_execution.PYTEST_EXIT_OK:
        if collection.raw_pytest_exit_code == pytest_execution.PYTEST_EXIT_NO_TESTS_COLLECTED:
            print("No executable test cases collected.")
        else:
            print(
                pytest_execution.format_collection_error(collection),
                file=sys.stderr,
            )
        quality_run_lifecycle.record_collection_failure()
        final_exit_code = collection.raw_pytest_exit_code
        if not argument_plan.collect_only:
            final_exit_code = _write_execution_result(
                test_path=test_path,
                argument_plan=argument_plan,
                collection=collection,
                pool_results=(),
                final_exit_code=final_exit_code,
            )
        return final_exit_code

    cases = collection.cases
    case_nodeids = tuple(case.nodeid for case in cases)
    print(f"Collected test cases: {len(cases)}")
    for nodeid in case_nodeids:
        print(f"- {nodeid}")

    parallel_cases, serial_cases = scheduling.split_test_cases(
        cases, serial_marker=serial_marker
    )
    quality_run_lifecycle.prepare_flaky_decisions(
        cases,
        parallel_nodeids=parallel_cases,
        serial_nodeids=serial_cases,
        collection_started_at=collection_started_at,
        all_serial=not bool(numprocesses),
    )
    if argument_plan.collect_only:
        print(f"Parallel pool cases: {len(parallel_cases)}")
        print(f"Serial pool cases: {len(serial_cases)}")
        print(f"{len(cases)} tests collected")
        quality_run_lifecycle.finalize_flaky_collect_only()
        return pytest_execution.PYTEST_EXIT_OK

    allure_lifecycle = pytest_execution.AllureRunLifecycle(
        results_dir=pytest_execution.extract_allure_results_dir(
            argument_plan.execution_args
        ),
        generate_report=settings.generate_allure_report,
        generate_history=settings.generate_history_report,
        history_keep_limit=settings.history_report_keep_limit,
        pooled=True,
    )
    allure_lifecycle.prepare()

    pool_results: list[pytest_execution.PoolExecutionResult] = []
    final_status = quality_lifecycle.RunLifecycleStatus.FINISHED
    try:
        if not numprocesses:
            print("Parallel test execution disabled. Running all cases serially.")
            serial_args = quality_run_lifecycle.ensure_junit_args(
                argument_plan.execution_args
            )
            with quality_run_lifecycle.stage_environment("serial-pool"):
                pool_results.append(
                    pytest_execution.execute_pool(
                        "serial-pool",
                        case_nodeids,
                        serial_args,
                        allure_lifecycle=allure_lifecycle,
                    )
                )
        else:
            print(
                "Parallel-first execution enabled: "
                f"workers={numprocesses}, parallel_cases={len(parallel_cases)}, "
                f"serial_cases={len(serial_cases)}"
            )
            parallel_args = quality_run_lifecycle.ensure_junit_args(
                argument_plan.execution_args
            )
            parallel_args = pytest_execution.build_parallel_args(
                parallel_args,
                numprocesses=numprocesses,
                dist=dist,
                junit_suffix="parallel",
            )
            if parallel_cases:
                print(f"Running parallel pool: {len(parallel_cases)} cases")
                with quality_run_lifecycle.stage_environment("parallel-pool"):
                    parallel_result = pytest_execution.execute_pool(
                        "parallel-pool",
                        parallel_cases,
                        parallel_args,
                        allure_lifecycle=allure_lifecycle,
                    )
            else:
                print("Parallel pool is empty. Skipping parallel stage.")
                parallel_result = _not_run_pool(
                    "parallel-pool", parallel_cases, parallel_args
                )
            pool_results.append(parallel_result)

            stop_after_parallel = (
                parallel_result.status is pytest_execution.PoolExecutionStatus.ERROR
                or pytest_execution.should_stop_after_exit_code(
                    parallel_result.raw_pytest_exit_code
                )
            )
            serial_args = quality_run_lifecycle.ensure_junit_args(
                argument_plan.execution_args
            )
            serial_args = pytest_execution.build_serial_args(
                serial_args, junit_suffix="serial"
            )
            if stop_after_parallel:
                print(
                    "Skipping serial pool because the parallel pool returned "
                    "a terminating execution result."
                )
                serial_result = _not_run_pool(
                    "serial-pool", serial_cases, serial_args
                )
            elif serial_cases:
                print(f"Running serial pool: {len(serial_cases)} cases")
                with quality_run_lifecycle.stage_environment("serial-pool"):
                    serial_result = pytest_execution.execute_pool(
                        "serial-pool",
                        serial_cases,
                        serial_args,
                        allure_lifecycle=allure_lifecycle,
                    )
            else:
                print("Serial pool is empty. Skipping serial stage.")
                serial_result = _not_run_pool(
                    "serial-pool", serial_cases, serial_args
                )
            pool_results.append(serial_result)

        final_exit_code = _final_exit_code(pool_results)
        if any(
            result.status is pytest_execution.PoolExecutionStatus.ERROR
            for result in pool_results
        ):
            final_status = quality_lifecycle.RunLifecycleStatus.PARTIAL
        final_exit_code = _write_execution_result(
            test_path=test_path,
            argument_plan=argument_plan,
            collection=collection,
            pool_results=tuple(pool_results),
            final_exit_code=final_exit_code,
        )
        return final_exit_code
    except (KeyboardInterrupt, SystemExit):
        final_status = quality_lifecycle.RunLifecycleStatus.INTERRUPTED
        raise
    except Exception as error:
        final_status = quality_lifecycle.RunLifecycleStatus.PARTIAL
        print(
            f"Runner execution failed: {type(error).__name__}: {error}",
            file=sys.stderr,
        )
        return pytest_execution.PYTEST_EXIT_TESTS_FAILED
    finally:
        allure_lifecycle.finalize()
        quality_run_lifecycle.finalize(
            start_time=quality_start_time,
            expected_case_count=len(case_nodeids),
            pool_results=tuple(pool_results),
            status=final_status,
        )


def _not_run_pool(
    stage_id: str,
    nodeids: Sequence[str],
    pytest_args: Sequence[str],
) -> pytest_execution.PoolExecutionResult:
    return pytest_execution.PoolExecutionResult(
        stage_id=stage_id,
        planned_nodeids=tuple(nodeids),
        status=pytest_execution.PoolExecutionStatus.NOT_RUN,
        junit_path=pytest_execution.extract_junit_path(pytest_args),
    )


def _final_exit_code(
    pool_results: Sequence[pytest_execution.PoolExecutionResult],
) -> int:
    if any(
        result.status is pytest_execution.PoolExecutionStatus.ERROR
        for result in pool_results
    ):
        return pytest_execution.PYTEST_EXIT_TESTS_FAILED
    return pytest_execution.merge_exit_codes(
        [
            result.raw_pytest_exit_code
            for result in pool_results
            if result.raw_pytest_exit_code is not None
        ]
    )


def _write_execution_result(
    *,
    test_path: str,
    argument_plan: pytest_execution.PytestArgumentPlan,
    collection: pytest_execution.CollectionResult,
    pool_results: tuple[pytest_execution.PoolExecutionResult, ...],
    final_exit_code: int,
) -> int:
    payload = {
        "schema_version": artifacts.RUNNER_EXECUTION_SCHEMA_VERSION,
        "test_target": str(test_path),
        "selection_args": list(argument_plan.selection_args),
        "planned_case_count": len(collection.cases),
        "planned_nodeids": [case.nodeid for case in collection.cases],
        "collection_exit_code": collection.raw_pytest_exit_code,
        "pool_results": [
            {
                "stage_id": result.stage_id,
                "planned_nodeids": list(result.planned_nodeids),
                "status": result.status.value,
                "raw_pytest_exit_code": result.raw_pytest_exit_code,
                "started_at": (
                    result.started_at.isoformat()
                    if result.started_at is not None
                    else None
                ),
                "completed_at": (
                    result.completed_at.isoformat()
                    if result.completed_at is not None
                    else None
                ),
                "exception_type": result.exception_type,
                "junit_path": (
                    result.junit_path.as_posix()
                    if result.junit_path is not None
                    else None
                ),
            }
            for result in pool_results
        ],
        "final_exit_code": final_exit_code,
    }
    try:
        artifacts.write_execution_result_atomic(payload)
    except Exception as error:
        print(
            f"Runner execution result write failed: "
            f"{type(error).__name__}: {error}",
            file=sys.stderr,
        )
        if final_exit_code in pytest_execution.PYTEST_TERMINATING_EXIT_CODES:
            return final_exit_code
        return pytest_execution.PYTEST_EXIT_TESTS_FAILED
    return final_exit_code
