from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
import hashlib
import json
import math
from typing import Any

from quality.metrics_models import (
    RUN_METRICS_AGGREGATION_VERSION,
    RUN_METRICS_SCHEMA_VERSION,
    CountDistribution,
    EvidenceMembership,
    MetricCompleteness,
    NumericAggregate,
    RatioAggregate,
)


def numeric_aggregate(
    values: Iterable[int | float | None],
    *,
    decimals: int = 3,
    not_applicable: bool = False,
) -> NumericAggregate:
    observations = tuple(values)
    known = tuple(value for value in observations if value is not None)
    for value in known:
        if isinstance(value, bool) or not math.isfinite(float(value)) or value < 0:
            raise ValueError("numeric observations must be finite and nonnegative")
    eligible_count = len(observations)
    sample_size = len(known)
    missing_count = eligible_count - sample_size
    if sample_size == 0:
        completeness = (
            MetricCompleteness.NOT_APPLICABLE
            if not_applicable and eligible_count == 0
            else MetricCompleteness.NO_DATA
        )
        return NumericAggregate(
            eligible_count=eligible_count,
            sample_size=0,
            missing_count=missing_count,
            total=None,
            mean=None,
            minimum=None,
            maximum=None,
            completeness=completeness,
        )
    ordered = tuple(sorted(known, key=float))
    all_integers = all(
        isinstance(value, int) and not isinstance(value, bool) for value in ordered
    )
    total: int | float
    if all_integers:
        total = sum(int(value) for value in ordered)
        mean = round(float(total) / sample_size, decimals)
        minimum: int | float = min(ordered)
        maximum: int | float = max(ordered)
    else:
        raw_total = math.fsum(float(value) for value in ordered)
        total = round(raw_total, decimals)
        mean = round(raw_total / sample_size, decimals)
        minimum = round(float(min(ordered)), decimals)
        maximum = round(float(max(ordered)), decimals)
    return NumericAggregate(
        eligible_count=eligible_count,
        sample_size=sample_size,
        missing_count=missing_count,
        total=total,
        mean=mean,
        minimum=minimum,
        maximum=maximum,
        completeness=(
            MetricCompleteness.COMPLETE
            if missing_count == 0
            else MetricCompleteness.PARTIAL
        ),
    )


def ratio_aggregate(values: Iterable[bool | None]) -> RatioAggregate:
    observations = tuple(values)
    known = tuple(value for value in observations if value is not None)
    numerator = sum(value is True for value in known)
    sample_size = len(known)
    unknown_count = len(observations) - sample_size
    if sample_size == 0:
        value = None
        completeness = MetricCompleteness.NO_DATA
    else:
        value = round(numerator / sample_size, 6)
        completeness = (
            MetricCompleteness.PARTIAL
            if unknown_count
            else MetricCompleteness.COMPLETE
        )
    return RatioAggregate(
        numerator=numerator,
        sample_size=sample_size,
        unknown_count=unknown_count,
        value=value,
        completeness=completeness,
    )


def count_distribution(
    values: Iterable[str], *, unknown_count: int = 0
) -> CountDistribution:
    counter = Counter(values)
    return CountDistribution(
        sample_size=sum(counter.values()),
        counts=dict(sorted(counter.items())),
        unknown_count=unknown_count,
    )


def metric_bucket_id(run_id: str, grain: str, dimension: dict[str, Any]) -> str:
    payload = {
        "aggregation_version": RUN_METRICS_AGGREGATION_VERSION,
        "dimension": dimension,
        "grain": grain,
        "run_id": run_id,
        "schema_version": RUN_METRICS_SCHEMA_VERSION,
    }
    canonical = json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def evidence_membership(
    bucket_id: str,
    member_ids: tuple[str, ...],
    source_refs: tuple[str, ...],
) -> EvidenceMembership:
    members = tuple(sorted(member_ids))
    return EvidenceMembership(
        metric_bucket_id=bucket_id,
        member_count=len(members),
        member_ids=members,
        source_artifact_refs=source_refs,
    )


def canonical_partition_key(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
