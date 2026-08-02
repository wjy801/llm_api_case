from __future__ import annotations

import hashlib
import json

from quality.metrics import RunMetricsAggregationRequest, aggregate_run_metrics
from quality.metrics.sources import source_file_sha256
from quality.metrics.writer import output_file_sha256
from tests.quality.test_metrics_sources import _build_sources


def _normalized_metrics(result):
    value = result.metrics.model_dump(mode="json")
    value["generated_at"] = "<dynamic>"
    return value


def test_repeated_aggregation_preserves_business_metrics_and_bucket_ids(
    semantic_runtime,
):
    _build_sources(semantic_runtime)
    request = RunMetricsAggregationRequest(
        run_id=semantic_runtime.run_context.run_id,
        output_dir=semantic_runtime.output_dir,
    )

    first = aggregate_run_metrics(request)
    first_metrics = _normalized_metrics(first)
    second = aggregate_run_metrics(request)
    second_metrics = _normalized_metrics(second)

    assert first_metrics == second_metrics
    assert first.metrics.run_metrics.operation.operation_count == 1
    assert first.metrics.run_metrics.request_groups.group_count == 1
    assert first.metrics.run_metrics.request_events.event_count == 1
    assert first.metrics.run_metrics.usage.input_tokens.total == 2
    assert first.metrics.run_metrics.usage.output_tokens.total == 3
    assert [
        bucket.evidence.metric_bucket_id for bucket in first.metrics.operation_buckets
    ] == [
        bucket.evidence.metric_bucket_id for bucket in second.metrics.operation_buckets
    ]


def test_metrics_manifest_hash_and_counts_match_committed_artifact(semantic_runtime):
    _build_sources(semantic_runtime)
    result = aggregate_run_metrics(
        RunMetricsAggregationRequest(
            run_id=semantic_runtime.run_context.run_id,
            output_dir=semantic_runtime.output_dir,
        )
    )

    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["output_hashes"]["run_metrics"] == hashlib.sha256(
        result.metrics_path.read_bytes()
    ).hexdigest()
    assert manifest["output_counts"] == {
        "case_invocations": 1,
        "operation_buckets": 1,
        "request_group_buckets": 1,
        "request_event_buckets": 1,
        "workload_operations": 1,
        "workload_request_groups": 1,
        "workload_request_events": 1,
        "issues": 0,
    }


def test_source_and_output_hash_boundaries_use_the_same_sha256_contract(tmp_path):
    artifact = tmp_path / "artifact.json"
    artifact.write_bytes(b'{"value":1}\n')
    expected = hashlib.sha256(artifact.read_bytes()).hexdigest()

    assert source_file_sha256(artifact) == expected
    assert output_file_sha256(artifact) == expected
