from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Generic, TypeVar

from quality.models import GateDecision, QualitySummary, RunRecord
from quality.observation_models import (
    P1ObservationReport,
    P1ReportStatus,
    P1SourceSummary,
    SourceExpectation,
)


_T = TypeVar("_T")


@dataclass(frozen=True)
class P1ObservationRequest:
    run_id: str
    output_dir: Path
    metrics_expectation: SourceExpectation = SourceExpectation.REQUIRED
    flaky_import_expectation: SourceExpectation = SourceExpectation.REQUIRED
    flaky_evaluation_expectation: SourceExpectation = SourceExpectation.REQUIRED


@dataclass(frozen=True)
class P1ObservationGenerationResult:
    run_id: str
    output_dir: Path
    manifest_path: Path
    json_path: Path
    markdown_path: Path
    write_status: str
    report_status: P1ReportStatus | None
    issue_codes: tuple[str, ...]
    report: P1ObservationReport | None = None


@dataclass(frozen=True)
class LoadedSource(Generic[_T]):
    summary: P1SourceSummary
    value: _T | None = None
    hashes: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class P0Value:
    run: RunRecord
    summary: QualitySummary
    gate: GateDecision
    failure_categories: dict[str, int]
