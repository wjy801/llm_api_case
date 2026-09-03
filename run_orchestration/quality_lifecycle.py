"""Neutral lifecycle boundary for the optional Quality extension."""

from __future__ import annotations

from contextlib import AbstractContextManager, nullcontext
from dataclasses import replace
from datetime import datetime
from enum import Enum
import os
from pathlib import Path
import subprocess
from typing import Any, Protocol, Sequence


class RunLifecycleStatus(str, Enum):
    FINISHED = "finished"
    PARTIAL = "partial"
    INTERRUPTED = "interrupted"


class QualityRunLifecycle(Protocol):
    enabled: bool

    def prepare(self, start_time: datetime) -> None: ...

    def prepare_flaky_decisions(
        self,
        cases: Sequence[Any],
        *,
        parallel_nodeids: Sequence[str],
        serial_nodeids: Sequence[str],
        collection_started_at: datetime,
        all_serial: bool,
    ) -> None: ...

    def finalize_flaky_collect_only(self) -> None: ...

    def record_collection_failure(self) -> None: ...

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

    def prepare_flaky_decisions(
        self,
        cases: Sequence[Any],
        *,
        parallel_nodeids: Sequence[str],
        serial_nodeids: Sequence[str],
        collection_started_at: datetime,
        all_serial: bool,
    ) -> None:
        return None

    def finalize_flaky_collect_only(self) -> None:
        return None

    def record_collection_failure(self) -> None:
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
        self._snapshot = None
        self._decision_plan = None
        self._branch: str | None = None

    def prepare(self, start_time: datetime) -> None:
        try:
            from .quality_run_record import write_initial_run_record

            write_initial_run_record(self._config, start_time)
        except Exception as error:
            _warn("Quality initialization failed open", error)
        try:
            from quality.flaky_shadow import generate_snapshot, write_snapshot

            from .paths import PROJECT_ROOT

            self._branch = _controller_branch(PROJECT_ROOT)
            snapshot = generate_snapshot(
                self._config,
                run_id=str(self._config.run_id),
                branch=self._branch,
                repository_root=PROJECT_ROOT,
                now=start_time,
            )
            write_snapshot(snapshot, self._config.output_dir)
            self._snapshot = snapshot
        except Exception as error:
            _warn("Flaky snapshot generation failed open", error)

    def prepare_flaky_decisions(
        self,
        cases: Sequence[Any],
        *,
        parallel_nodeids: Sequence[str],
        serial_nodeids: Sequence[str],
        collection_started_at: datetime,
        all_serial: bool,
    ) -> None:
        if self._snapshot is None:
            return
        try:
            from quality.flaky_shadow import build_decision_plan, write_decision_plan

            from .paths import PROJECT_ROOT
            from .quality_run_record import quality_environment_name

            profiles = (
                {case.nodeid: "serial" for case in cases}
                if all_serial
                else {
                    **{nodeid: "parallel" for nodeid in parallel_nodeids},
                    **{nodeid: "serial" for nodeid in serial_nodeids},
                }
            )
            decision_plan = build_decision_plan(
                self._snapshot,
                cases,
                run_id=str(self._config.run_id),
                branch=self._branch or "unknown",
                environment=quality_environment_name(),
                execution_profiles=profiles,
                collection_started_at=collection_started_at,
            )
            path = write_decision_plan(
                decision_plan,
                self._config.output_dir,
            )
            self._config = replace(
                self._config,
                flaky_decision_plan_path=path.resolve(),
                flaky_decision_checksum=decision_plan.content_checksum,
            )
            self._decision_plan = decision_plan
        except Exception as error:
            _warn("Flaky Shadow decision generation failed open", error)

    def finalize_flaky_collect_only(self) -> None:
        if self._decision_plan is None:
            return
        try:
            from quality.flaky_shadow import (
                reconcile_decision_plan,
                write_reconciliation,
            )

            result = reconcile_decision_plan(
                self._decision_plan,
                (),
                collect_only=True,
            )
            write_reconciliation(result, self._config.output_dir)
        except Exception as error:
            _warn("Flaky Shadow collect-only reconciliation failed open", error)

    def record_collection_failure(self) -> None:
        try:
            from quality.flaky_shadow import (
                collection_failure_reconciliation,
                write_reconciliation,
            )

            result = collection_failure_reconciliation(str(self._config.run_id))
            write_reconciliation(result, self._config.output_dir)
        except Exception as error:
            _warn("Flaky Shadow collection failure audit failed open", error)

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
        self._finalize_flaky_reconciliation()

    def _finalize_flaky_reconciliation(self) -> None:
        if self._decision_plan is None:
            return
        try:
            from quality.flaky_shadow import (
                reconcile_decision_plan,
                write_reconciliation,
            )
            from quality.storage import read_jsonl

            cases_path = self._config.output_dir / "merged" / "case-results.jsonl"
            cases = read_jsonl(cases_path) if cases_path.is_file() else ()
            result = reconcile_decision_plan(self._decision_plan, cases)
            write_reconciliation(result, self._config.output_dir)
        except Exception as error:
            _warn("Flaky Shadow reconciliation failed open", error)


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


def _controller_branch(project_root: Path) -> str:
    configured = os.environ.get("BRANCH_NAME") or os.environ.get("GIT_BRANCH")
    if configured:
        return configured.removeprefix("refs/heads/").removeprefix("origin/")
    try:
        result = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=project_root,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        branch = result.stdout.strip()
        return branch or "unknown"
    except Exception:
        return "unknown"


__all__ = (
    "EnabledQualityRunLifecycle",
    "NoopQualityRunLifecycle",
    "QualityRunLifecycle",
    "RunLifecycleStatus",
    "create_quality_run_lifecycle",
)
