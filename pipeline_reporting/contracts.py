from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


RUNNER_EXECUTION_SCHEMA_VERSION = "runner-execution.v1"


class StageStatus(str, Enum):
    PASSED = "PASSED"
    FAILED = "FAILED"
    NOT_RUN = "NOT_RUN"
    BLOCKED = "BLOCKED"
    NO_DATA = "NO_DATA"


class PipelineConclusion(str, Enum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"
    NO_DATA = "NO_DATA"


@dataclass(frozen=True)
class PipelineContext:
    job_name: str = "local"
    build_number: str = "-"
    build_url: str = ""
    build_result: str = "SUCCESS"
    branch: str = "-"
    commit_sha: str = "-"
    environment_name: str = "unknown"
    duration_ms: int = 0
    framework_tests_enabled: bool = False
    smoke_collect_enabled: bool = False
    real_smoke_enabled: bool = False
    smoke_target: str = "module/smoke"
    parallel_workers: str = "off"


@dataclass(frozen=True)
class CaseDetail:
    name: str
    status: str
    message: str | None = None


@dataclass(frozen=True)
class TestSummary:
    available: bool = False
    total: int = 0
    passed: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0
    duration_seconds: float = 0.0
    failed_cases: tuple[CaseDetail, ...] = ()
    skipped_cases: tuple[CaseDetail, ...] = ()


@dataclass(frozen=True)
class CollectSummary:
    available: bool = False
    total: int | None = None
    parallel: int | None = None
    serial: int | None = None


@dataclass(frozen=True)
class PoolExecutionSummary:
    stage_id: str
    status: str
    planned_case_count: int
    raw_pytest_exit_code: int | None = None
    exception_type: str | None = None
    junit_path: str | None = None


@dataclass(frozen=True)
class ExecutionSummary:
    available: bool = False
    test_target: str | None = None
    planned_case_count: int = 0
    collection_exit_code: int | None = None
    final_exit_code: int | None = None
    pools: tuple[PoolExecutionSummary, ...] = ()


@dataclass(frozen=True)
class RequestHealth:
    available: bool = False
    total: int = 0
    success_count: int = 0
    http_5xx_count: int = 0
    timeout_count: int = 0

    @property
    def success_rate(self) -> float | None:
        return None if self.total == 0 else self.success_count / self.total


@dataclass(frozen=True)
class RetryHealth:
    available: bool = False
    retried_group_count: int = 0
    rescued_group_count: int = 0

    @property
    def rescue_rate(self) -> float | None:
        if self.retried_group_count == 0:
            return None
        return self.rescued_group_count / self.retried_group_count


@dataclass(frozen=True)
class InterfaceTiming:
    interface_id: str
    request_group_count: int
    mean_ms: float
    maximum_ms: float


@dataclass(frozen=True)
class FlakyChange:
    case_id: str
    change: str


@dataclass(frozen=True)
class FlakySummary:
    available: bool = False
    newly_suspected_count: int = 0
    newly_confirmed_count: int = 0
    recovered_count: int = 0
    newly_quarantined_count: int = 0
    overdue_count: int = 0
    transition_count: int = 0
    transition_directions: tuple[tuple[str, int], ...] = ()
    actionable_changes: tuple[FlakyChange, ...] = ()

    @property
    def actionable_count(self) -> int:
        return (
            self.newly_suspected_count
            + self.newly_confirmed_count
            + self.newly_quarantined_count
            + self.overdue_count
        )


@dataclass(frozen=True)
class LoadedPipelineSources:
    stage_statuses: dict[str, StageStatus] = field(default_factory=dict)
    unit_tests: TestSummary = TestSummary()
    smoke_tests: TestSummary = TestSummary()
    smoke_collect: CollectSummary = CollectSummary()
    execution: ExecutionSummary = ExecutionSummary()
    quality_facts_available: bool = False
    quality_run_id: str | None = None
    request_health: RequestHealth = RequestHealth()
    retry_health: RetryHealth = RetryHealth()
    interface_timings: tuple[InterfaceTiming, ...] = ()
    flaky: FlakySummary = FlakySummary()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class StageResult:
    name: str
    status: StageStatus
    summary: str


@dataclass(frozen=True)
class PipelineReport:
    context: PipelineContext
    conclusion: PipelineConclusion
    conclusion_text: str
    stages: tuple[StageResult, ...]
    unit_tests: TestSummary
    smoke_tests: TestSummary
    smoke_collect: CollectSummary
    execution: ExecutionSummary
    request_health: RequestHealth
    retry_health: RetryHealth
    interface_timings: tuple[InterfaceTiming, ...]
    flaky: FlakySummary
    actions: tuple[str, ...]
    warnings: tuple[str, ...] = ()
