from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from quality.metrics_models import MetricCompleteness
from quality.observation_models import P1MetricObservation, P1ObservationReport


def test_metric_observation_rejects_value_without_samples():
    with pytest.raises(ValidationError, match="without samples"):
        P1MetricObservation(
            metric_id="run:operation.success_rate",
            grain="run",
            metric_name="operation.success_rate",
            value=0,
            numerator=0,
            sample_size=0,
            completeness=MetricCompleteness.NO_DATA,
            algorithm_version="p1-run-metrics.v1",
            source_artifact="metrics/run-metrics.json",
        )


def test_observation_schema_has_no_money_baseline_or_gate_fields():
    schema = json.dumps(P1ObservationReport.model_json_schema()).lower()

    for forbidden in ("amount", "price", "currency", "quota", "baseline", "p95"):
        assert forbidden not in schema
