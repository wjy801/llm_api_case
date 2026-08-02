from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from quality.metrics_models import (
    MetricsIssue,
    RunMetricsResult,
    RunMetricsStatus,
    SourceEvidence,
)
from quality.models import RequestMetric, RunRecord
from quality.semantic_models import (
    OperationRecord,
    PollingSessionRecord,
    RequestGroupRecord,
    SemanticIntegrityIssue,
)


@dataclass(frozen=True)
class RunMetricsAggregationRequest:
    run_id: str
    output_dir: Path


@dataclass(frozen=True)
class RunMetricsAggregationResult:
    run_id: str
    output_dir: Path
    manifest_path: Path
    metrics_path: Path
    status: RunMetricsStatus
    operation_count: int
    request_group_count: int
    request_event_count: int
    issues: tuple[MetricsIssue, ...]
    metrics: RunMetricsResult | None = None


@dataclass(frozen=True)
class MetricsSources:
    run: RunRecord
    requests: tuple[RequestMetric, ...]
    groups: tuple[RequestGroupRecord, ...]
    sessions: tuple[PollingSessionRecord, ...]
    operations: tuple[OperationRecord, ...]
    semantic_issues: tuple[SemanticIntegrityIssue, ...]
    p0_integrity_status: str
    semantic_integrity_status: str
    evidence: SourceEvidence


class MetricsSourceError(ValueError):
    def __init__(self, code: str, summary: str, related_id: str | None = None) -> None:
        super().__init__(summary)
        self.code = code
        self.summary = summary
        self.related_id = related_id
