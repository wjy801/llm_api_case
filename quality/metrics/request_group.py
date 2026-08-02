from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

from quality.metrics_models import (
    RequestGroupDimension,
    RequestGroupMetricBucket,
    RequestGroupStability,
    RequestGroupTimingAggregate,
)
from quality.models import RequestMetric
from quality.semantic_models import (
    AttemptTransportOutcome,
    OperationRecord,
    RequestGroupRecord,
)

from .operation import retry_usage
from .primitives import (
    canonical_partition_key,
    count_distribution,
    evidence_membership,
    metric_bucket_id,
    numeric_aggregate,
    ratio_aggregate,
)
from .request_event import business_success, http_success


def request_group_stability(
    groups: Sequence[RequestGroupRecord],
    events: dict[str, RequestMetric],
) -> RequestGroupStability:
    first_events = tuple(events[item.attempt_event_ids[0]] for item in groups)
    final_events = tuple(events[item.final_request_event_id] for item in groups)
    retried = tuple(item for item in groups if item.attempt_count > 1)
    first_business = tuple(business_success(item) for item in first_events)
    final_business = tuple(business_success(item) for item in final_events)
    business_pairs = tuple(
        (
            business_success(events[item.attempt_event_ids[0]]),
            business_success(events[item.final_request_event_id]),
        )
        for item in retried
    )
    return RequestGroupStability(
        group_count=len(groups),
        attempt_count=numeric_aggregate(tuple(item.attempt_count for item in groups)),
        retried_group_count=len(retried),
        retry_rate=ratio_aggregate(item.attempt_count > 1 for item in groups),
        first_transport=count_distribution(
            item.first_transport_outcome.value for item in groups
        ),
        final_transport=count_distribution(
            item.final_transport_outcome.value for item in groups
        ),
        first_transport_response_rate=ratio_aggregate(
            item.first_transport_outcome is AttemptTransportOutcome.RESPONSE
            for item in groups
        ),
        final_transport_response_rate=ratio_aggregate(
            item.final_transport_outcome is AttemptTransportOutcome.RESPONSE
            for item in groups
        ),
        first_http_success_rate=ratio_aggregate(
            http_success(item) for item in first_events
        ),
        final_http_success_rate=ratio_aggregate(
            http_success(item) for item in final_events
        ),
        first_business_success_rate=ratio_aggregate(first_business),
        final_business_success_rate=ratio_aggregate(final_business),
        http_retry_rescue_rate=ratio_aggregate(
            (not http_success(events[item.attempt_event_ids[0]]))
            and http_success(events[item.final_request_event_id])
            for item in retried
        ),
        business_retry_rescue_rate=ratio_aggregate(
            None if first is None or final is None else (not first and final)
            for first, final in business_pairs
        ),
    )


def request_group_timing(
    groups: Sequence[RequestGroupRecord],
    events: dict[str, RequestMetric],
) -> RequestGroupTimingAggregate:
    retry_attempt_durations = tuple(
        events[event_id].duration_ms
        for group in groups
        for event_id in group.attempt_event_ids[1:]
    )
    return RequestGroupTimingAggregate(
        total_duration_ms=numeric_aggregate(
            tuple(item.total_duration_ms for item in groups),
            not_applicable=not groups,
        ),
        retry_wait_ms=numeric_aggregate(
            tuple(item.retry_wait_ms for item in groups),
            not_applicable=not groups,
        ),
        first_attempt_duration_ms=numeric_aggregate(
            tuple(events[item.attempt_event_ids[0]].duration_ms for item in groups),
            not_applicable=not groups,
        ),
        retry_attempt_duration_ms=numeric_aggregate(
            retry_attempt_durations,
            not_applicable=not retry_attempt_durations,
        ),
    )


def request_group_buckets(
    run_id: str,
    groups: Sequence[RequestGroupRecord],
    events: dict[str, RequestMetric],
    operations: Sequence[OperationRecord],
) -> tuple[RequestGroupMetricBucket, ...]:
    partitions: dict[tuple[str, str, str], list[RequestGroupRecord]] = defaultdict(list)
    for item in groups:
        partitions[
            (item.interface_id, item.protocol.value, item.traffic_role.value)
        ].append(item)
    operation_by_group = {
        group_id: operation
        for operation in operations
        for group_id in operation.request_group_ids
    }
    buckets: list[RequestGroupMetricBucket] = []
    for key, members in sorted(
        partitions.items(), key=lambda pair: canonical_partition_key(pair[0])
    ):
        interface_id, protocol, role = key
        dimension = RequestGroupDimension(
            interface_id=interface_id,
            protocol=protocol,
            traffic_role=role,
        )
        bucket_id = metric_bucket_id(
            run_id, "request_group", dimension.model_dump(mode="json")
        )
        ordered = tuple(sorted(members, key=lambda item: item.request_group_id))
        member_operations = tuple(
            {
                operation.operation_id: operation
                for group in ordered
                if (
                    operation := operation_by_group.get(group.request_group_id)
                )
                is not None
            }.values()
        )
        buckets.append(
            RequestGroupMetricBucket(
                dimension=dimension,
                stability=request_group_stability(ordered, events),
                timing=request_group_timing(ordered, events),
                retry_usage=retry_usage(
                    member_operations,
                    events,
                    allowed_event_ids={
                        event_id
                        for group in ordered
                        for event_id in group.attempt_event_ids
                    },
                ),
                evidence=evidence_membership(
                    bucket_id,
                    tuple(item.request_group_id for item in ordered),
                    (
                        "semantic/merged/request-groups.jsonl",
                        "merged/request-metrics.jsonl",
                    ),
                ),
            )
        )
    return tuple(buckets)
