from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from governance.flaky_classifier import classify_attempts
from governance.flaky_models import AttemptOutcome, AttemptResult, FlakyTestResult
from governance.flaky_reporter import redact_failure_message, write_flaky_reports


FLAKY_REPORT_DIR_NAME = "reports/flaky/current"
LATEST_RETRY_QUEUE_PATH = "reports/flaky/latest-retry-nodeids.csv"
FLAKY_REPORT_DIR_OPTION = "--flaky-governance-report-dir"
STORE_KEY = pytest.StashKey["FlakyAttemptStore"]()
_CONFIG: pytest.Config | None = None


@dataclass
class StageReport:
    outcome: str
    duration: float
    failure_type: str | None = None
    failure_message: str | None = None


@dataclass
class AttemptBuilder:
    index: int
    stages: dict[str, StageReport] = field(default_factory=dict)

    def add_stage(self, stage: str, report: StageReport) -> None:
        self.stages[stage] = report

    def to_attempt_result(self) -> AttemptResult:
        failed_stage = next(
            (stage_report for stage_report in self.stages.values() if stage_report.outcome == "failed"),
            None,
        )
        duration = round(sum(stage_report.duration for stage_report in self.stages.values()), 6)
        if failed_stage is None and self._has_required_passed_stages():
            return AttemptResult(index=self.index, outcome=AttemptOutcome.PASSED, duration=duration)

        return AttemptResult(
            index=self.index,
            outcome=AttemptOutcome.FAILED,
            duration=duration,
            failure_type=failed_stage.failure_type if failed_stage is not None else None,
            failure_message=failed_stage.failure_message if failed_stage is not None else None,
        )

    def _has_required_passed_stages(self) -> bool:
        return all(self.stages.get(stage, StageReport("missing", 0.0)).outcome == "passed" for stage in ("setup", "call", "teardown"))


class FlakyAttemptStore:
    def __init__(self) -> None:
        self._attempts_by_nodeid: dict[str, list[AttemptBuilder]] = defaultdict(list)

    def add_report(self, report: pytest.TestReport) -> None:
        if report.outcome == "skipped":
            return

        attempts = self._attempts_by_nodeid[report.nodeid]
        attempt_index = _attempt_index(report)
        while len(attempts) < attempt_index:
            attempts.append(AttemptBuilder(index=len(attempts) + 1))

        attempts[attempt_index - 1].add_stage(report.when, _stage_report_from_pytest_report(report))

    def results(self) -> list[FlakyTestResult]:
        results: list[FlakyTestResult] = []
        for nodeid in sorted(self._attempts_by_nodeid):
            attempts = tuple(builder.to_attempt_result() for builder in self._attempts_by_nodeid[nodeid])
            status = classify_attempts(list(attempts))
            total_duration = round(sum(attempt.duration for attempt in attempts), 6)
            results.append(
                FlakyTestResult(
                    nodeid=nodeid,
                    status=status,
                    attempts=attempts,
                    total_duration=total_duration,
                )
            )
        return results


def pytest_configure(config: pytest.Config) -> None:
    global _CONFIG
    _CONFIG = config
    config.stash[STORE_KEY] = FlakyAttemptStore()


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        FLAKY_REPORT_DIR_OPTION,
        action="store",
        default=FLAKY_REPORT_DIR_NAME,
        help="Flaky governance report output directory.",
    )


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    if _CONFIG is None:
        return
    _CONFIG.stash[STORE_KEY].add_report(report)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    if hasattr(session.config, "workerinput"):
        return

    if getattr(session.config.option, "collectonly", False):
        return

    results = session.config.stash[STORE_KEY].results()
    write_flaky_reports(
        _flaky_report_dir(session.config),
        results,
        latest_retry_queue_path=_latest_retry_queue_path(session.config),
        allure_results_dir=_allure_results_dir(session.config),
    )


def _stage_report_from_pytest_report(report: pytest.TestReport) -> StageReport:
    return StageReport(
        outcome=_normalized_report_outcome(report),
        duration=round(report.duration, 6),
        failure_type=_failure_type(report),
        failure_message=redact_failure_message(_failure_message(report)),
    )


def _failure_type(report: pytest.TestReport) -> str | None:
    if _normalized_report_outcome(report) != "failed" or report.longrepr is None:
        return None

    reprcrash = getattr(report.longrepr, "reprcrash", None)
    if reprcrash is None:
        return "Failure"

    message = str(getattr(reprcrash, "message", "")).strip()
    if ":" in message:
        return message.split(":", 1)[0].strip() or "Failure"
    return message or "Failure"


def _failure_message(report: pytest.TestReport) -> str | None:
    if _normalized_report_outcome(report) != "failed" or report.longrepr is None:
        return None
    return str(report.longrepr)


def _flaky_report_dir(config: pytest.Config) -> Path:
    report_dir = Path(config.getoption(FLAKY_REPORT_DIR_OPTION))
    if report_dir.is_absolute():
        return report_dir
    return config.rootpath / report_dir


def _latest_retry_queue_path(config: pytest.Config) -> Path:
    return config.rootpath / LATEST_RETRY_QUEUE_PATH


def _allure_results_dir(config: pytest.Config) -> Path | None:
    alluredir = config.getoption("--alluredir", default=None)
    if not alluredir:
        return None

    results_dir = Path(alluredir)
    if results_dir.is_absolute():
        return results_dir
    return config.rootpath / results_dir


def _attempt_index(report: pytest.TestReport) -> int:
    rerun_index = getattr(report, "rerun", None)
    if isinstance(rerun_index, int):
        return rerun_index + 1
    return 1


def _normalized_report_outcome(report: pytest.TestReport) -> str:
    if report.outcome == "rerun":
        return "failed"
    return report.outcome
