from __future__ import annotations

import hashlib
import json

import pytest

from util.artifact_io import (
    ArtifactFormatError,
    ArtifactJsonLineError,
    compare_file_sha256,
    exact_field_mismatches,
    file_sha256,
    read_json_object,
    read_jsonl_values,
)


def test_file_sha256_and_comparison_use_original_bytes(tmp_path):
    artifact = tmp_path / "artifact.json"
    content = b'{"value": 1}\r\n'
    artifact.write_bytes(content)
    expected = hashlib.sha256(content).hexdigest()

    assert file_sha256(artifact) == expected
    assert compare_file_sha256(artifact, expected).matches is True
    assert compare_file_sha256(artifact, "0" * 64).matches is False
    assert compare_file_sha256(artifact, None).matches is False


def test_read_json_object_requires_an_object(tmp_path):
    object_path = tmp_path / "object.json"
    object_path.write_text('{"run_id": "run-1"}', encoding="utf-8")
    list_path = tmp_path / "list.json"
    list_path.write_text("[]", encoding="utf-8")

    assert read_json_object(object_path) == {"run_id": "run-1"}
    with pytest.raises(ArtifactFormatError, match="JSON object required"):
        read_json_object(list_path)


def test_read_jsonl_values_preserves_source_line_numbers(tmp_path):
    path = tmp_path / "records.jsonl"
    path.write_text('{"id": 1}\n\n{"id": 2}\n', encoding="utf-8")

    values = read_jsonl_values(path)

    assert [(item.number, item.value) for item in values] == [
        (1, {"id": 1}),
        (3, {"id": 2}),
    ]


def test_read_jsonl_values_reports_the_invalid_line(tmp_path):
    path = tmp_path / "records.jsonl"
    path.write_text(json.dumps({"id": 1}) + "\nnot-json\n", encoding="utf-8")

    with pytest.raises(ArtifactJsonLineError) as exc_info:
        tuple(read_jsonl_values(path))

    assert exc_info.value.line_number == 2


def test_exact_field_mismatches_is_domain_neutral():
    assert exact_field_mismatches(
        {"run_id": "run-1", "schema_version": "v2", "status": "complete"},
        {"run_id": "run-1", "schema_version": "v1", "status": "complete"},
    ) == ("schema_version",)


def test_exact_field_mismatches_preserves_expected_field_order():
    assert exact_field_mismatches(
        {"status": "merging", "schema_version": "v2", "run_id": "foreign"},
        {"run_id": "run-1", "status": "complete", "schema_version": "v1"},
    ) == ("run_id", "status", "schema_version")
