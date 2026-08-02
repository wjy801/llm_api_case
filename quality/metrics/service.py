from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from quality.metrics_models import MetricsIssue, RunMetricsStatus
from quality.models import IssueSeverity

from .builder import build_run_metrics
from .contracts import (
    MetricsSourceError,
    RunMetricsAggregationRequest,
    RunMetricsAggregationResult,
)
from .sources import load_sources
from .writer import write_metrics_manifest, write_run_metrics


def aggregate_run_metrics(
    request: RunMetricsAggregationRequest,
) -> RunMetricsAggregationResult:
    run_id = request.run_id.strip()
    if not run_id:
        raise ValueError("run_id must not be empty")
    output_dir = Path(request.output_dir)
    metrics_dir = output_dir / "metrics"
    manifest_path = metrics_dir / "manifest.json"
    metrics_path = metrics_dir / "run-metrics.json"
    created_at = datetime.now(UTC)
    write_metrics_manifest(
        manifest_path,
        run_id=run_id,
        write_status="aggregating",
        metrics_status=None,
        created_at=created_at,
        source_evidence=None,
        output_hashes={},
        output_counts={},
        issues=(),
    )
    try:
        sources = load_sources(run_id, output_dir)
        metrics = build_run_metrics(
            run_id, sources, generated_at=datetime.now(UTC)
        )
        output_hash = write_run_metrics(metrics_path, metrics)
        counts = {
            "case_invocations": len(metrics.case_invocations),
            "operation_buckets": len(metrics.operation_buckets),
            "request_group_buckets": len(metrics.request_group_buckets),
            "request_event_buckets": len(metrics.request_event_buckets),
            "workload_operations": (
                metrics.run_metrics.operation.operation_count
                if metrics.run_metrics is not None
                else 0
            ),
            "workload_request_groups": (
                metrics.run_metrics.request_groups.group_count
                if metrics.run_metrics is not None
                else 0
            ),
            "workload_request_events": (
                metrics.run_metrics.request_events.event_count
                if metrics.run_metrics is not None
                else 0
            ),
            "issues": len(metrics.issues),
        }
        write_metrics_manifest(
            manifest_path,
            run_id=run_id,
            write_status="complete",
            metrics_status=metrics.status,
            created_at=created_at,
            source_evidence=metrics.source_evidence,
            output_hashes={"run_metrics": output_hash},
            output_counts=counts,
            issues=metrics.issues,
        )
        return RunMetricsAggregationResult(
            run_id=run_id,
            output_dir=output_dir,
            manifest_path=manifest_path,
            metrics_path=metrics_path,
            status=metrics.status,
            operation_count=counts["workload_operations"],
            request_group_count=counts["workload_request_groups"],
            request_event_count=counts["workload_request_events"],
            issues=metrics.issues,
            metrics=metrics,
        )
    except Exception as error:
        issue = failure_issue(error)
        try:
            write_metrics_manifest(
                manifest_path,
                run_id=run_id,
                write_status="failed",
                metrics_status=RunMetricsStatus.FAILED,
                created_at=created_at,
                source_evidence=None,
                output_hashes={},
                output_counts={},
                issues=(issue,),
            )
        except Exception:
            pass
        return RunMetricsAggregationResult(
            run_id=run_id,
            output_dir=output_dir,
            manifest_path=manifest_path,
            metrics_path=metrics_path,
            status=RunMetricsStatus.FAILED,
            operation_count=0,
            request_group_count=0,
            request_event_count=0,
            issues=(issue,),
        )


def failure_issue(error: Exception) -> MetricsIssue:
    if isinstance(error, MetricsSourceError):
        return MetricsIssue(
            severity=IssueSeverity.ERROR,
            code=error.code,
            summary=error.summary,
            related_id=error.related_id,
        )
    return MetricsIssue(
        severity=IssueSeverity.ERROR,
        code="metrics_aggregation_failed",
        summary=f"run metrics aggregation failed: {type(error).__name__}",
    )
