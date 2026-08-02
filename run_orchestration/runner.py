from __future__ import annotations

from datetime import UTC, datetime
from typing import Sequence

from master_service import DEFAULT_SERIAL_MARKER, DEFAULT_TEST_PATH
from quality.models import RunStatus

from . import (
    environment,
    pytest_execution,
    quality_pipeline,
    quality_run_record,
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
    cases = scheduling.collect_test_case_items(test_path)
    if len(cases) == 0:
        print("No executable test cases collected.")
        return 1

    case_nodeids = [case.nodeid for case in cases]
    print(f"Collected test cases: {len(cases)}")
    for nodeid in case_nodeids:
        print(f"- {nodeid}")

    pytest_args = list(extra_pytest_args or [])
    if pytest_execution.has_collect_only(pytest_args):
        parallel_cases, serial_cases = scheduling.split_test_cases(
            cases, serial_marker=serial_marker
        )
        print(f"Parallel pool cases: {len(parallel_cases)}")
        print(f"Serial pool cases: {len(serial_cases)}")
        print(f"{len(cases)} tests collected")
        return 0

    quality_config = environment.resolve_parent_quality_config()
    quality_start_time = datetime.now(UTC)
    if quality_config.enabled:
        quality_run_record.write_initial_run_record(
            quality_config, quality_start_time
        )

    if not numprocesses:
        print("Parallel test execution disabled. Running all cases serially.")
        stage_id = "serial-pool"
        serial_args = pytest_execution.ensure_quality_junit_args(
            pytest_args, quality_config
        )
        junit_files = (pytest_execution.extract_junit_path(serial_args),)
        final_status = RunStatus.FINISHED
        try:
            with environment.quality_stage_environment(
                quality_config, stage_id
            ):
                return pytest_execution.run_pytest(case_nodeids + serial_args)
        except (KeyboardInterrupt, SystemExit):
            final_status = RunStatus.INTERRUPTED
            raise
        except Exception:
            final_status = RunStatus.PARTIAL
            raise
        finally:
            quality_pipeline.finalize_quality_run(
                quality_config,
                start_time=quality_start_time,
                expected_execution_ids=(stage_id,),
                expected_case_count=len(case_nodeids),
                junit_files=junit_files,
                status=final_status,
            )

    parallel_cases, serial_cases = scheduling.split_test_cases(
        cases, serial_marker=serial_marker
    )
    print(
        "Parallel-first execution enabled: "
        f"workers={numprocesses}, parallel_cases={len(parallel_cases)}, "
        f"serial_cases={len(serial_cases)}"
    )

    results: list[int] = []
    executed_stage_ids: list[str] = []
    junit_files = []
    final_status = RunStatus.FINISHED
    try:
        if parallel_cases:
            stage_id = "parallel-pool"
            pytest_args = pytest_execution.ensure_quality_junit_args(
                pytest_args, quality_config
            )
            parallel_args = pytest_execution.build_parallel_args(
                pytest_args,
                numprocesses=numprocesses,
                dist=dist,
                junit_suffix="parallel",
            )
            junit_files.append(
                pytest_execution.extract_junit_path(parallel_args)
            )
            executed_stage_ids.append(stage_id)
            print(f"Running parallel pool: {len(parallel_cases)} cases")
            with environment.quality_stage_environment(
                quality_config, stage_id
            ):
                results.append(
                    pytest_execution.run_pytest(
                        parallel_cases + parallel_args
                    )
                )
        else:
            print("Parallel pool is empty. Skipping parallel stage.")

        if serial_cases:
            stage_id = "serial-pool"
            pytest_args = pytest_execution.ensure_quality_junit_args(
                pytest_args, quality_config
            )
            serial_args = pytest_execution.build_serial_args(
                pytest_args, junit_suffix="serial"
            )
            junit_files.append(
                pytest_execution.extract_junit_path(serial_args)
            )
            executed_stage_ids.append(stage_id)
            print(f"Running serial pool: {len(serial_cases)} cases")
            with environment.quality_stage_environment(
                quality_config, stage_id
            ):
                results.append(
                    pytest_execution.run_serial_pool(
                        serial_cases + serial_args
                    )
                )
        else:
            print("Serial pool is empty. Skipping serial stage.")

        return pytest_execution.merge_exit_codes(results)
    except (KeyboardInterrupt, SystemExit):
        final_status = RunStatus.INTERRUPTED
        raise
    except Exception:
        final_status = RunStatus.PARTIAL
        raise
    finally:
        quality_pipeline.finalize_quality_run(
            quality_config,
            start_time=quality_start_time,
            expected_execution_ids=tuple(executed_stage_ids),
            expected_case_count=len(case_nodeids),
            junit_files=tuple(junit_files),
            status=final_status,
        )
