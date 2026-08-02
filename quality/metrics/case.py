from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

from quality.metrics_models import CaseInvocationMetric
from quality.models import RequestMetric
from quality.semantic_models import OperationOutcome, OperationRecord

from .operation import operation_usage
from .primitives import (
    count_distribution,
    evidence_membership,
    metric_bucket_id,
    numeric_aggregate,
    ratio_aggregate,
)


def case_metrics(
    run_id: str,
    operations: Sequence[OperationRecord],
    events: dict[str, RequestMetric],
) -> tuple[CaseInvocationMetric, ...]:
    partitions: dict[tuple[str, str], list[OperationRecord]] = defaultdict(list)
    for operation in operations:
        partitions[(operation.case_id, operation.invocation_id)].append(operation)
    metrics: list[CaseInvocationMetric] = []
    for (case_id, invocation_id), members in sorted(partitions.items()):
        ordered = tuple(sorted(members, key=lambda item: item.operation_id))
        dimension = {"case_id": case_id, "invocation_id": invocation_id}
        bucket_id = metric_bucket_id(run_id, "case_invocation", dimension)
        metrics.append(
            CaseInvocationMetric(
                case_id=case_id,
                invocation_id=invocation_id,
                operation_count=len(ordered),
                outcomes=count_distribution(item.outcome.value for item in ordered),
                operation_success_rate=ratio_aggregate(
                    item.outcome is OperationOutcome.SUCCESS for item in ordered
                ),
                usage=operation_usage(ordered, events),
                operation_duration_ms=numeric_aggregate(
                    tuple(item.timing.total_duration_ms for item in ordered)
                ),
                model_ids=count_distribution(
                    item.model_id if item.model_id is not None else "(none)"
                    for item in ordered
                ),
                operation_kinds=count_distribution(
                    item.operation_kind.value for item in ordered
                ),
                evidence=evidence_membership(
                    bucket_id,
                    tuple(item.operation_id for item in ordered),
                    ("semantic/merged/operations.jsonl",),
                ),
            )
        )
    return tuple(metrics)
