from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

from quality.metrics_models import (
    NumericAggregate,
    RequestEventDimension,
    RequestEventMetricBucket,
    RequestEventStability,
    RequestEventTimingAggregate,
    RequestEventUsageCoverage,
)
from quality.models import BusinessStatus, RequestMetric
from quality.semantic_models import AttemptTransportOutcome, RequestGroupRecord

from .primitives import (
    canonical_partition_key,
    count_distribution,
    evidence_membership,
    metric_bucket_id,
    numeric_aggregate,
    ratio_aggregate,
)


def event_known_usage(
    events: Sequence[RequestMetric], field: str
) -> NumericAggregate:
    values = tuple(
        value
        for item in events
        if (value := getattr(item.usage, field)) is not None
    )
    return numeric_aggregate(values, not_applicable=not events)


def request_event_stability(
    events: Sequence[RequestMetric],
) -> RequestEventStability:
    transports = tuple(transport_category(item) for item in events)
    return RequestEventStability(
        event_count=len(events),
        transport=count_distribution(transports),
        business_status=count_distribution(item.business_status.value for item in events),
        timeout_rate=ratio_aggregate(item.timeout for item in events),
        http_5xx_rate=ratio_aggregate(
            item.status_code is not None and 500 <= item.status_code < 600
            for item in events
        ),
        http_429_rate=ratio_aggregate(item.status_code == 429 for item in events),
        business_success_rate=ratio_aggregate(business_success(item) for item in events),
        http_429_count=sum(item.status_code == 429 for item in events),
    )


def request_event_timing(
    events: Sequence[RequestMetric],
) -> RequestEventTimingAggregate:
    by_transport: dict[str, list[float]] = defaultdict(list)
    for event in events:
        by_transport[transport_category(event)].append(event.duration_ms)
    aggregate = lambda category: numeric_aggregate(  # noqa: E731
        tuple(by_transport[category]), not_applicable=not by_transport[category]
    )
    return RequestEventTimingAggregate(
        all_duration_ms=numeric_aggregate(
            tuple(item.duration_ms for item in events), not_applicable=not events
        ),
        timeout_duration_ms=aggregate("timeout"),
        transport_error_duration_ms=aggregate("transport_error"),
        http_2xx_duration_ms=aggregate("http_2xx"),
        http_3xx_duration_ms=aggregate("http_3xx"),
        http_4xx_duration_ms=aggregate("http_4xx"),
        http_5xx_duration_ms=aggregate("http_5xx"),
    )


def request_event_usage_coverage(
    events: Sequence[RequestMetric],
) -> RequestEventUsageCoverage:
    known = tuple(item for item in events if event_has_usage(item))
    return RequestEventUsageCoverage(
        known_event_count=len(known),
        missing_event_count=len(events) - len(known),
        input_tokens=event_known_usage(events, "input_tokens"),
        output_tokens=event_known_usage(events, "output_tokens"),
        media_count=event_known_usage(events, "media_count"),
    )


def http_success(event: RequestMetric) -> bool:
    return event.status_code is not None and 200 <= event.status_code < 300


def business_success(event: RequestMetric) -> bool | None:
    if event.business_status is BusinessStatus.UNKNOWN:
        return None
    return event.business_status is BusinessStatus.SUCCESS


def transport_category(event: RequestMetric) -> str:
    if event.timeout:
        return "timeout"
    if event.status_code is None:
        return "transport_error"
    if 200 <= event.status_code < 300:
        return "http_2xx"
    if 300 <= event.status_code < 400:
        return "http_3xx"
    if 400 <= event.status_code < 500:
        return "http_4xx"
    if 500 <= event.status_code < 600:
        return "http_5xx"
    return "http_other"


def event_transport_outcome(event: RequestMetric) -> AttemptTransportOutcome:
    if event.timeout:
        return AttemptTransportOutcome.TIMEOUT
    if event.status_code is None:
        return AttemptTransportOutcome.ERROR
    return AttemptTransportOutcome.RESPONSE


def event_has_usage(event: RequestMetric) -> bool:
    return any(
        value is not None
        for value in (
            event.usage.input_tokens,
            event.usage.output_tokens,
            event.usage.media_count,
        )
    )


def request_event_buckets(
    run_id: str,
    events: Sequence[RequestMetric],
    owners: dict[str, RequestGroupRecord],
) -> tuple[RequestEventMetricBucket, ...]:
    partitions: dict[tuple[str, str, str], list[RequestMetric]] = defaultdict(list)
    for item in events:
        owner = owners[item.request_event_id]
        partitions[
            (item.interface_id, item.protocol.value, owner.traffic_role.value)
        ].append(item)
    buckets: list[RequestEventMetricBucket] = []
    for key, members in sorted(
        partitions.items(), key=lambda pair: canonical_partition_key(pair[0])
    ):
        interface_id, protocol, role = key
        dimension = RequestEventDimension(
            interface_id=interface_id,
            protocol=protocol,
            traffic_role=role,
        )
        bucket_id = metric_bucket_id(
            run_id, "request_event", dimension.model_dump(mode="json")
        )
        ordered = tuple(sorted(members, key=lambda item: item.request_event_id))
        buckets.append(
            RequestEventMetricBucket(
                dimension=dimension,
                stability=request_event_stability(ordered),
                timing=request_event_timing(ordered),
                usage_coverage=request_event_usage_coverage(ordered),
                evidence=evidence_membership(
                    bucket_id,
                    tuple(item.request_event_id for item in ordered),
                    ("merged/request-metrics.jsonl",),
                ),
            )
        )
    return tuple(buckets)
