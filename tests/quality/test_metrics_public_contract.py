from __future__ import annotations

import ast
from dataclasses import fields
import inspect
from pathlib import Path

import quality
import quality.metrics as metrics


EXPECTED_PUBLIC_SYMBOLS = (
    "RunMetricsAggregationRequest",
    "RunMetricsAggregationResult",
    "aggregate_run_metrics",
    "count_distribution",
    "metric_bucket_id",
    "numeric_aggregate",
    "ratio_aggregate",
)


def test_metrics_is_a_package_with_the_frozen_public_api():
    package_dir = Path(metrics.__file__).resolve().parent

    assert metrics.__spec__.submodule_search_locations is not None
    assert package_dir.name == "metrics"
    assert not (package_dir.parent / "metrics.py").exists()
    assert metrics.__all__ == EXPECTED_PUBLIC_SYMBOLS
    assert all(getattr(quality, name) is getattr(metrics, name) for name in EXPECTED_PUBLIC_SYMBOLS)


def test_metrics_public_dataclass_fields_are_compatible():
    assert tuple(field.name for field in fields(metrics.RunMetricsAggregationRequest)) == (
        "run_id",
        "output_dir",
    )
    assert tuple(field.name for field in fields(metrics.RunMetricsAggregationResult)) == (
        "run_id",
        "output_dir",
        "manifest_path",
        "metrics_path",
        "status",
        "operation_count",
        "request_group_count",
        "request_event_count",
        "issues",
        "metrics",
    )


def test_metrics_public_function_parameters_are_compatible():
    assert tuple(inspect.signature(metrics.numeric_aggregate).parameters) == (
        "values",
        "decimals",
        "not_applicable",
    )
    assert tuple(inspect.signature(metrics.ratio_aggregate).parameters) == ("values",)
    assert tuple(inspect.signature(metrics.count_distribution).parameters) == (
        "values",
        "unknown_count",
    )
    assert tuple(inspect.signature(metrics.metric_bucket_id).parameters) == (
        "run_id",
        "grain",
        "dimension",
    )
    assert tuple(inspect.signature(metrics.aggregate_run_metrics).parameters) == (
        "request",
    )

    numeric = inspect.signature(metrics.numeric_aggregate).parameters
    assert numeric["decimals"].kind is inspect.Parameter.KEYWORD_ONLY
    assert numeric["decimals"].default == 3
    assert numeric["not_applicable"].kind is inspect.Parameter.KEYWORD_ONLY
    assert numeric["not_applicable"].default is False


def test_metrics_package_init_contains_no_business_implementation():
    init_path = Path(metrics.__file__).resolve()
    tree = ast.parse(init_path.read_text(encoding="utf-8"))

    forbidden = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
    assert not any(isinstance(node, forbidden) for node in ast.walk(tree))
