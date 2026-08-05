from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from quality.metrics_models import (
    RUN_METRICS_AGGREGATION_VERSION,
    RUN_METRICS_MANIFEST_VERSION,
    RUN_METRICS_SCHEMA_VERSION,
    MetricsIssue,
    RunMetricsResult,
    RunMetricsStatus,
    SourceEvidence,
)
from quality.storage import write_json_atomic
from util.artifact_io import file_sha256


def write_run_metrics(path: Path, metrics: RunMetricsResult) -> str:
    write_json_atomic(path, metrics)
    return output_file_sha256(path)


def write_metrics_manifest(
    path: Path,
    *,
    run_id: str,
    write_status: str,
    metrics_status: RunMetricsStatus | None,
    created_at: datetime,
    source_evidence: SourceEvidence | None,
    output_hashes: dict[str, str],
    output_counts: dict[str, int],
    issues: Sequence[MetricsIssue],
) -> None:
    write_json_atomic(
        path,
        {
            "manifest_version": RUN_METRICS_MANIFEST_VERSION,
            "schema_version": RUN_METRICS_SCHEMA_VERSION,
            "aggregation_version": RUN_METRICS_AGGREGATION_VERSION,
            "run_id": run_id,
            "write_status": write_status,
            "metrics_status": (
                metrics_status.value if metrics_status is not None else None
            ),
            "created_at": created_at,
            "source_evidence": source_evidence,
            "output_hashes": output_hashes,
            "output_counts": output_counts,
            "issues": tuple(issues),
        },
    )


def output_file_sha256(path: Path) -> str:
    return file_sha256(path)
