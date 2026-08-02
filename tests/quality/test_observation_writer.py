from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json

from quality.observation_report import (
    P1ObservationRequest,
    generate_p1_observation_report,
)
from quality.observation_report import loader, writer
from quality.storage import write_json_atomic as storage_write_json_atomic
from tests.quality.test_observation_refactor_equivalence import (
    copy_observation_sources,
)


def test_text_atomic_write_adds_one_trailing_newline(tmp_path):
    path = tmp_path / "report.md"

    writer.write_text_atomic(path, "line")
    assert path.read_bytes() == b"line\n"
    writer.write_text_atomic(path, "line\n")
    assert path.read_bytes() == b"line\n"


def test_markdown_failure_does_not_commit_json_and_leaves_failed_manifest(
    tmp_path, monkeypatch
):
    output_dir = copy_observation_sources(tmp_path)
    monkeypatch.setattr(
        writer,
        "write_text_atomic",
        lambda path, content: (_ for _ in ()).throw(OSError("markdown unavailable")),
    )

    result = generate_p1_observation_report(
        P1ObservationRequest(run_id="run-semantic", output_dir=output_dir)
    )

    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert result.write_status == "failed"
    assert not result.json_path.exists()
    assert manifest["write_status"] == "failed"
    assert manifest["output_hashes"] == {}


def test_json_failure_keeps_manifest_non_complete(tmp_path, monkeypatch):
    output_dir = copy_observation_sources(tmp_path)

    def fail_report_json(path, value):
        if path.name == "p1-observation.json":
            raise OSError("json unavailable")
        storage_write_json_atomic(path, value)

    monkeypatch.setattr(writer, "write_json_atomic", fail_report_json)

    result = generate_p1_observation_report(
        P1ObservationRequest(run_id="run-semantic", output_dir=output_dir)
    )

    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert result.write_status == "failed"
    assert result.markdown_path.is_file()
    assert not result.json_path.exists()
    assert manifest["write_status"] == "failed"
    assert manifest["output_hashes"] == {}


def test_manifest_sorts_hashes_and_deduplicates_issue_codes(tmp_path):
    path = tmp_path / "manifest.json"

    writer.write_observation_manifest(
        path,
        run_id="run-1",
        created_at=datetime(2026, 8, 2, tzinfo=UTC),
        write_status="failed",
        report_status=None,
        output_hashes={"z": "2", "a": "1"},
        source_hashes={"z": "4", "a": "3"},
        issue_codes=("z", "a", "z"),
    )

    manifest = json.loads(path.read_text(encoding="utf-8"))
    assert list(manifest["output_hashes"]) == ["a", "z"]
    assert list(manifest["source_hashes"]) == ["a", "z"]
    assert manifest["issue_codes"] == ["a", "z"]


def test_source_and_output_hash_boundaries_use_the_same_sha256_contract(tmp_path):
    artifact = tmp_path / "artifact.json"
    artifact.write_bytes(b'{"value":1}\n')
    expected = hashlib.sha256(artifact.read_bytes()).hexdigest()

    assert loader.source_file_sha256(artifact) == expected
    assert writer.output_file_sha256(artifact) == expected
