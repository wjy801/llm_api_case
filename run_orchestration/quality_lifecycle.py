"""Neutral lifecycle boundary for the optional Quality extension."""

from __future__ import annotations

from contextlib import AbstractContextManager, nullcontext
from datetime import datetime
from enum import Enum
from typing import Any, Protocol, Sequence


class RunLifecycleStatus(str, Enum):
    FINISHED = "finished"
    PARTIAL = "partial"
    INTERRUPTED = "interrupted"


class QualityRunLifecycle(Protocol):
    enabled: bool

    def prepare(self, start_time: datetime) -> None: ...

    def ensure_junit_args(self, pytest_args: Sequence[str]) -> list[str]: ...

    def stage_environment(self, execution_id: str) -> AbstractContextManager: ...

    def finalize(
        self,
        *,
        start_time: datetime,
        expected_case_count: int,
        pool_results: Sequence[Any],
        status: RunLifecycleStatus,
    ) -> None: ...


class NoopQualityRunLifecycle:
    enabled = False

    def prepare(self, start_time: datetime) -> None:
        return None

    def ensure_junit_args(self, pytest_args: Sequence[str]) -> list[str]:
        return list(pytest_args)

    def stage_environment(self, execution_id: str) -> AbstractContextManager:
        return nullcontext()

    def finalize(
        self,
        *,
        start_time: datetime,
        expected_case_count: int,
        pool_results: Sequence[Any],
        status: RunLifecycleStatus,
    ) -> None:
        return None


class EnabledQualityRunLifecycle:
    enabled = True

    def __init__(self, runtime_config: Any) -> None:
        self._config = runtime_config

    def prepare(self, start_time: datetime) -> None:
        try:
            from .quality_run_record import write_initial_run_record

            write_initial_run_record(self._config, start_time)
        except Exception as error:
            _warn("Quality initialization failed open", error)

    def ensure_junit_args(self, pytest_args: Sequence[str]) -> list[str]:
        from .artifacts import extract_junit_path

        args = list(pytest_args)
        if extract_junit_path(args) is not None:
            return args
        return args + [
            f"--junitxml={self._config.output_dir / 'junit' / 'quality.xml'}"
        ]

    def stage_environment(self, execution_id: str) -> AbstractContextManager:
        try:
            from .environment import quality_stage_environment

            return quality_stage_environment(self._config, execution_id)
        except Exception as error:
            _warn("Quality stage environment failed open", error)
            return nullcontext()

    def finalize(
        self,
        *,
        start_time: datetime,
        expected_case_count: int,
        pool_results: Sequence[Any],
        status: RunLifecycleStatus,
    ) -> None:
        executed = tuple(
            result
            for result in pool_results
            if getattr(getattr(result, "status", None), "value", None) != "NOT_RUN"
        )
        try:
            from quality.models import RunStatus

            from .quality_pipeline import finalize_quality_run

            finalize_quality_run(
                self._config,
                start_time=start_time,
                expected_execution_ids=tuple(
                    result.stage_id for result in executed
                ),
                expected_case_count=expected_case_count,
                junit_files=tuple(result.junit_path for result in executed),
                status=RunStatus(status.value),
            )
        except Exception as error:
            _warn("Quality finalization failed open", error)


def create_quality_run_lifecycle() -> QualityRunLifecycle:
    try:
        from quality.config import load_quality_config

        preview = load_quality_config()
    except Exception as error:
        _warn("Quality collection disabled", error)
        return NoopQualityRunLifecycle()
    if not preview.enabled:
        return NoopQualityRunLifecycle()

    try:
        from .environment import resolve_parent_quality_config

        runtime_config = resolve_parent_quality_config()
    except Exception as error:
        _warn("Quality collection disabled", error)
        return NoopQualityRunLifecycle()
    if not runtime_config.enabled:
        return NoopQualityRunLifecycle()
    return EnabledQualityRunLifecycle(runtime_config)


def _warn(prefix: str, error: Exception) -> None:
    print(f"{prefix}: {type(error).__name__}: {error}")


__all__ = (
    "EnabledQualityRunLifecycle",
    "NoopQualityRunLifecycle",
    "QualityRunLifecycle",
    "RunLifecycleStatus",
    "create_quality_run_lifecycle",
)
