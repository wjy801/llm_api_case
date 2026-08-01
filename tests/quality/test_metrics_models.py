from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from quality.metrics import metric_bucket_id, numeric_aggregate, ratio_aggregate
from quality.metrics_models import (
    MetricCompleteness,
    NumericAggregate,
    RatioAggregate,
    RunMetricsResult,
)


def test_numeric_aggregate_distinguishes_missing_from_known_zero():
    aggregate = numeric_aggregate([0, None])

    assert aggregate.sample_size == 1
    assert aggregate.missing_count == 1
    assert aggregate.total == 0
    assert aggregate.completeness is MetricCompleteness.PARTIAL


def test_numeric_aggregate_without_samples_has_null_values():
    aggregate = numeric_aggregate([None])

    assert aggregate.sample_size == 0
    assert aggregate.total is None
    assert aggregate.mean is None
    assert aggregate.completeness is MetricCompleteness.NO_DATA


def test_numeric_model_rejects_inconsistent_coverage():
    with pytest.raises(ValidationError, match="sample_size.*missing_count"):
        NumericAggregate(
            eligible_count=2,
            sample_size=1,
            missing_count=0,
            total=1,
            mean=1,
            minimum=1,
            maximum=1,
            completeness=MetricCompleteness.COMPLETE,
        )


def test_ratio_keeps_unknown_out_of_denominator():
    aggregate = ratio_aggregate([True, False, None])

    assert aggregate == RatioAggregate(
        numerator=1,
        sample_size=2,
        unknown_count=1,
        value=0.5,
        completeness=MetricCompleteness.PARTIAL,
    )


def test_ratio_with_only_unknown_observations_is_no_data():
    aggregate = ratio_aggregate([None, None])

    assert aggregate.sample_size == 0
    assert aggregate.unknown_count == 2
    assert aggregate.value is None
    assert aggregate.completeness is MetricCompleteness.NO_DATA


def test_bucket_id_is_deterministic_for_dimension_order():
    left = metric_bucket_id("run-1", "operation", {"name": "chat", "kind": "http"})
    right = metric_bucket_id("run-1", "operation", {"kind": "http", "name": "chat"})

    assert left == right


def test_metrics_schema_has_no_money_or_baseline_fields():
    schema = json.dumps(RunMetricsResult.model_json_schema()).lower()

    for forbidden in ("amount", "price", "currency", "quota", "baseline", "p95"):
        assert forbidden not in schema
