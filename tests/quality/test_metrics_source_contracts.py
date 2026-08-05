from __future__ import annotations

import hashlib
import json

import pytest

from quality.metrics import RunMetricsAggregationRequest, aggregate_run_metrics
from quality.metrics.contracts import MetricsSourceError
from quality.metrics.validation import relative_artifact_path, require_manifest
from quality.storage import read_jsonl, write_json_atomic, write_jsonl_atomic
from tests.quality.test_metrics_sources import _build_sources


def _sha256(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    ("manifest", "expected_code"),
    (
        (
            {
                "run_id": "foreign-run",
                "status": "merging",
                "schema_version": "unsupported",
            },
            "p0_manifest_run_id_mismatch",
        ),
        (
            {
                "run_id": "run-1",
                "status": "merging",
                "schema_version": "unsupported",
            },
            "p0_manifest_not_complete",
        ),
        (
            {
                "run_id": "run-1",
                "status": "complete",
                "schema_version": "unsupported",
            },
            "p0_schema_version_unsupported",
        ),
    ),
)
def test_manifest_exact_field_validation_preserves_error_priority(
    manifest,
    expected_code,
):
    with pytest.raises(MetricsSourceError) as captured:
        require_manifest(
            manifest,
            run_id="run-1",
            status="complete",
            versions={"schema_version": "quality.v1"},
            code_prefix="p0",
        )

    assert captured.value.code == expected_code


def _rewrite_request_metrics(semantic_runtime, mutate) -> None:
    output_dir = semantic_runtime.output_dir
    request_path = output_dir / "merged" / "request-metrics.jsonl"
    records = read_jsonl(request_path)
    mutate(records)
    write_jsonl_atomic(request_path, records)

    p0_manifest_path = output_dir / "merged" / "manifest.json"
    p0_manifest = _read_json(p0_manifest_path)
    request_hash = _sha256(request_path)
    p0_manifest["output_hashes"]["request-metrics"] = request_hash
    write_json_atomic(p0_manifest_path, p0_manifest)

    semantic_manifest_path = output_dir / "semantic" / "merged" / "manifest.json"
    semantic_manifest = _read_json(semantic_manifest_path)
    semantic_manifest["p0_evidence"]["request_metrics_sha256"] = request_hash
    semantic_manifest["p0_evidence"]["manifest_sha256"] = _sha256(
        p0_manifest_path
    )
    write_json_atomic(semantic_manifest_path, semantic_manifest)


def _rewrite_semantic_output(semantic_runtime, name: str, mutate) -> None:
    semantic_dir = semantic_runtime.output_dir / "semantic" / "merged"
    output_path = semantic_dir / f"{name}.jsonl"
    records = read_jsonl(output_path)
    mutate(records)
    write_jsonl_atomic(output_path, records)

    manifest_path = semantic_dir / "manifest.json"
    manifest = _read_json(manifest_path)
    manifest["output_hashes"][name] = _sha256(output_path)
    write_json_atomic(manifest_path, manifest)


def _aggregate_issue_code(semantic_runtime) -> str:
    result = aggregate_run_metrics(
        RunMetricsAggregationRequest(
            run_id=semantic_runtime.run_context.run_id,
            output_dir=semantic_runtime.output_dir,
        )
    )
    assert result.status.value == "failed"
    return result.issues[0].code


def test_metrics_rejects_p0_manifest_from_another_run(semantic_runtime):
    _build_sources(semantic_runtime)
    path = semantic_runtime.output_dir / "merged" / "manifest.json"
    manifest = _read_json(path)
    manifest["run_id"] = "foreign-run"
    write_json_atomic(path, manifest)

    assert _aggregate_issue_code(semantic_runtime) == "p0_manifest_run_id_mismatch"


def test_metrics_rejects_unsupported_semantic_schema(semantic_runtime):
    _build_sources(semantic_runtime)
    path = semantic_runtime.output_dir / "semantic" / "merged" / "manifest.json"
    manifest = _read_json(path)
    manifest["schema_version"] = "quality.semantic.unsupported"
    write_json_atomic(path, manifest)

    assert _aggregate_issue_code(semantic_runtime) == "semantic_schema_version_unsupported"


@pytest.mark.parametrize(
    ("source", "expected_code"),
    (
        ("request", "request_event_duplicate"),
        ("request-groups", "request_group_duplicate"),
        ("operations", "operation_duplicate"),
    ),
)
def test_metrics_rejects_duplicate_source_identities(
    semantic_runtime, source, expected_code
):
    _build_sources(semantic_runtime)

    def duplicate(records):
        records.append(dict(records[0]))

    if source == "request":
        _rewrite_request_metrics(semantic_runtime, duplicate)
    else:
        _rewrite_semantic_output(semantic_runtime, source, duplicate)

    assert _aggregate_issue_code(semantic_runtime) == expected_code


def test_metrics_rejects_group_reference_to_missing_event(semantic_runtime):
    _build_sources(semantic_runtime)

    def replace_event(records):
        records[0]["attempt_event_ids"] = ["missing-event"]
        records[0]["final_request_event_id"] = "missing-event"

    _rewrite_semantic_output(semantic_runtime, "request-groups", replace_event)

    assert _aggregate_issue_code(semantic_runtime) == "request_event_missing"


def test_metrics_rejects_non_continuous_attempt_index(semantic_runtime):
    _build_sources(semantic_runtime)

    def change_attempt(records):
        records[0]["attempt_index"] = 2

    _rewrite_request_metrics(semantic_runtime, change_attempt)

    assert _aggregate_issue_code(semantic_runtime) == "attempt_index_sequence_invalid"


def test_metrics_rejects_group_event_interface_mismatch(semantic_runtime):
    _build_sources(semantic_runtime)

    def change_interface(records):
        records[0]["interface_id"] = "GET /different http"

    _rewrite_request_metrics(semantic_runtime, change_interface)

    assert _aggregate_issue_code(semantic_runtime) == "group_event_interface_mismatch"


def test_metrics_rejects_overlapping_usage_evidence(semantic_runtime):
    _build_sources(semantic_runtime)

    def overlap_usage(records):
        event_id = records[0]["usage"]["source_request_event_ids"][0]
        records[0]["usage"]["completeness"] = "partial"
        records[0]["usage"]["missing_request_event_ids"] = [event_id]

    _rewrite_semantic_output(semantic_runtime, "operations", overlap_usage)

    assert _aggregate_issue_code(semantic_runtime) == "usage_evidence_overlap"


def test_metrics_rejects_usage_evidence_outside_operation(semantic_runtime):
    _build_sources(semantic_runtime)

    def replace_usage_event(records):
        records[0]["usage"]["source_request_event_ids"] = ["foreign-event"]

    _rewrite_semantic_output(semantic_runtime, "operations", replace_usage_event)

    assert _aggregate_issue_code(semantic_runtime) == "usage_event_outside_operation"


def test_metrics_rejects_source_path_outside_output_directory(tmp_path):
    output_dir = tmp_path / "quality"
    outside_path = tmp_path / "outside.json"

    with pytest.raises(MetricsSourceError) as captured:
        relative_artifact_path(outside_path, output_dir)

    assert captured.value.code == "source_path_outside_output"
