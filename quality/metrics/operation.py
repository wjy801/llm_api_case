from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Sequence
from typing import TypeVar

from pydantic import BaseModel

from quality.metrics_models import (
    NumericAggregate,
    OperationDimension,
    OperationMetricBucket,
    OperationStability,
    OperationTimingAggregate,
    OperationUsageAggregate,
    RetryUsageAggregate,
)
from quality.models import RequestMetric
from quality.semantic_models import (
    OperationKind,
    OperationOutcome,
    OperationRecord,
    UsageCompleteness,
)

from .primitives import (
    canonical_partition_key,
    count_distribution,
    evidence_membership,
    metric_bucket_id,
    numeric_aggregate,
    ratio_aggregate,
)
from .request_event import event_known_usage


_T = TypeVar("_T", bound=BaseModel)


def operation_stability(
    operations: Sequence[OperationRecord],
) -> OperationStability:
    outcomes = tuple(item.outcome.value for item in operations)
    completeness = tuple(item.completeness.value for item in operations)
    return OperationStability(
        operation_count=len(operations),
        outcomes=count_distribution(outcomes),
        success_rate=ratio_aggregate(
            item.outcome is OperationOutcome.SUCCESS for item in operations
        ),
        timeout_rate=ratio_aggregate(
            item.outcome is OperationOutcome.TIMEOUT for item in operations
        ),
        incomplete_or_unknown_count=sum(
            item.outcome in {OperationOutcome.INCOMPLETE, OperationOutcome.UNKNOWN}
            for item in operations
        ),
        record_completeness=count_distribution(completeness),
    )


def operation_usage(
    operations: Sequence[OperationRecord],
    events: dict[str, RequestMetric],
) -> OperationUsageAggregate:
    applicable = tuple(
        item
        for item in operations
        if item.usage.completeness is not UsageCompleteness.NOT_APPLICABLE
    )
    return OperationUsageAggregate(
        completeness=count_distribution(
            item.usage.completeness.value for item in operations
        ),
        input_tokens=known_field_aggregate(
            applicable, lambda item: item.usage.input_tokens
        ),
        output_tokens=known_field_aggregate(
            applicable, lambda item: item.usage.output_tokens
        ),
        media_count=known_field_aggregate(
            applicable, lambda item: item.usage.media_count
        ),
        media_duration_ms=known_field_aggregate(
            applicable, lambda item: item.usage.media_duration_ms
        ),
        known_source_event_count=sum(
            len(item.usage.source_request_event_ids) for item in applicable
        ),
        missing_source_event_count=sum(
            len(item.usage.missing_request_event_ids) for item in applicable
        ),
        retry_extra_usage=retry_usage(operations, events),
    )


def known_field_aggregate(
    items: Sequence[_T],
    getter: Callable[[_T], int | float | None],
) -> NumericAggregate:
    known = tuple(value for item in items if (value := getter(item)) is not None)
    return numeric_aggregate(known, not_applicable=not items)


def retry_usage(
    operations: Sequence[OperationRecord],
    events: dict[str, RequestMetric],
    *,
    allowed_event_ids: set[str] | None = None,
) -> RetryUsageAggregate:
    first_events: list[RequestMetric] = []
    retry_events: list[RequestMetric] = []
    retry_missing = 0
    for operation in operations:
        for event_id in operation.usage.source_request_event_ids:
            if allowed_event_ids is not None and event_id not in allowed_event_ids:
                continue
            event = events[event_id]
            (first_events if event.attempt_index == 1 else retry_events).append(event)
        retry_missing += sum(
            events[event_id].attempt_index > 1
            for event_id in operation.usage.missing_request_event_ids
            if allowed_event_ids is None or event_id in allowed_event_ids
        )
    return RetryUsageAggregate(
        first_attempt_input_tokens=event_known_usage(first_events, "input_tokens"),
        first_attempt_output_tokens=event_known_usage(first_events, "output_tokens"),
        first_attempt_media_count=event_known_usage(first_events, "media_count"),
        retry_input_tokens=event_known_usage(retry_events, "input_tokens"),
        retry_output_tokens=event_known_usage(retry_events, "output_tokens"),
        retry_media_count=event_known_usage(retry_events, "media_count"),
        retry_missing_attempt_count=retry_missing,
    )


def operation_timing(
    operations: Sequence[OperationRecord],
) -> OperationTimingAggregate:
    total = tuple(item.timing.total_duration_ms for item in operations)
    successful = tuple(
        item.timing.total_duration_ms
        for item in operations
        if item.outcome is OperationOutcome.SUCCESS
    )
    unsuccessful = tuple(
        item.timing.total_duration_ms
        for item in operations
        if item.outcome is not OperationOutcome.SUCCESS
    )
    header_eligible = tuple(
        item
        for item in operations
        if item.operation_kind in {OperationKind.HTTP, OperationKind.SSE}
    )
    sse = tuple(item for item in operations if item.operation_kind is OperationKind.SSE)
    async_items = tuple(
        item for item in operations if item.operation_kind is OperationKind.ASYNC_TASK
    )
    return OperationTimingAggregate(
        total_duration_ms=numeric_aggregate(total, not_applicable=not total),
        success_total_duration_ms=numeric_aggregate(
            successful, not_applicable=not successful
        ),
        unsuccessful_total_duration_ms=numeric_aggregate(
            unsuccessful, not_applicable=not unsuccessful
        ),
        response_headers_ms=numeric_aggregate(
            (item.timing.response_headers_ms for item in header_eligible),
            not_applicable=not header_eligible,
        ),
        first_data_ms=numeric_aggregate(
            (item.timing.first_data_ms for item in sse),
            not_applicable=not sse,
        ),
        first_content_ms=numeric_aggregate(
            (item.timing.first_content_ms for item in sse),
            not_applicable=not sse,
        ),
        stream_duration_ms=numeric_aggregate(
            (item.timing.stream_duration_ms for item in sse),
            not_applicable=not sse,
        ),
        create_request_ms=numeric_aggregate(
            (item.timing.create_request_ms for item in async_items),
            not_applicable=not async_items,
        ),
        polling_total_ms=numeric_aggregate(
            (item.timing.polling_total_ms for item in async_items),
            not_applicable=not async_items,
        ),
        polling_sleep_ms=numeric_aggregate(
            (item.timing.polling_sleep_ms for item in async_items),
            not_applicable=not async_items,
        ),
        timing_completeness=count_distribution(
            item.timing.timing_completeness.value for item in operations
        ),
    )


def operation_buckets(
    run_id: str,
    operations: Sequence[OperationRecord],
    events: dict[str, RequestMetric],
) -> tuple[OperationMetricBucket, ...]:
    partitions: dict[
        tuple[str, str, str, str | None], list[OperationRecord]
    ] = defaultdict(list)
    for item in operations:
        partitions[
            (
                item.operation_kind.value,
                item.operation_name,
                item.traffic_role.value,
                item.model_id,
            )
        ].append(item)
    buckets: list[OperationMetricBucket] = []
    for key, members in sorted(
        partitions.items(), key=lambda pair: canonical_partition_key(pair[0])
    ):
        kind, name, role, model_id = key
        dimension = OperationDimension(
            operation_kind=kind,
            operation_name=name,
            traffic_role=role,
            model_id=model_id,
        )
        bucket_id = metric_bucket_id(
            run_id, "operation", dimension.model_dump(mode="json")
        )
        ordered = tuple(sorted(members, key=lambda item: item.operation_id))
        buckets.append(
            OperationMetricBucket(
                dimension=dimension,
                stability=operation_stability(ordered),
                usage=operation_usage(ordered, events),
                timing=operation_timing(ordered),
                evidence=evidence_membership(
                    bucket_id,
                    tuple(item.operation_id for item in ordered),
                    ("semantic/merged/operations.jsonl",),
                ),
            )
        )
    return tuple(buckets)
