from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from quality.metrics_models import RunMetricsStatus
from quality.observation_models import SourceExpectation, SourceStatus
from quality.observation_report import loader
from quality.storage import write_json_atomic
from tests.quality.test_observation_refactor_equivalence import (
    copy_observation_sources,
)


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, payload: dict) -> None:
    write_json_atomic(path, payload)


def _refresh_metrics_manifest(output_dir: Path, *, status: str | None = None) -> None:
    metrics_path = output_dir / "metrics" / "run-metrics.json"
    manifest_path = output_dir / "metrics" / "manifest.json"
    manifest = _read(manifest_path)
    manifest["output_hashes"]["run_metrics"] = hashlib.sha256(
        metrics_path.read_bytes()
    ).hexdigest()
    if status is not None:
        manifest["metrics_status"] = status
    _write(manifest_path, manifest)


@pytest.mark.parametrize(
    ("artifact", "mutation", "issue_code"),
    [
        (
            "run.json",
            lambda value: value.update(run_id="other-run"),
            "p0_run_id_mismatch",
        ),
        (
            "summary.json",
            lambda value: value.update(schema_version="quality.unsupported"),
            "p0_schema_version_unsupported",
        ),
        (
            "gate-report.json",
            lambda value: value.update(overall="PASS"),
            "p0_gate_envelope_mismatch",
        ),
    ],
)
def test_p0_identity_schema_and_envelope_mismatches_are_incompatible(
    tmp_path, artifact, mutation, issue_code
):
    output_dir = copy_observation_sources(tmp_path)
    path = output_dir / artifact
    payload = _read(path)
    mutation(payload)
    _write(path, payload)

    loaded = loader.load_p0("run-semantic", output_dir)

    assert loaded.summary.status is SourceStatus.INCOMPATIBLE
    assert loaded.summary.issue_codes == (issue_code,)


def test_metrics_manifest_version_hash_count_and_membership_are_validated(tmp_path):
    output_dir = copy_observation_sources(tmp_path)
    manifest_path = output_dir / "metrics" / "manifest.json"
    manifest = _read(manifest_path)
    manifest["manifest_version"] = "unsupported"
    _write(manifest_path, manifest)
    assert loader.load_metrics(
        "run-semantic", output_dir, expectation=SourceExpectation.REQUIRED
    ).summary.issue_codes == ("metrics_version_unsupported",)

    output_dir = copy_observation_sources(tmp_path / "hash")
    metrics_path = output_dir / "metrics" / "run-metrics.json"
    metrics_path.write_text(metrics_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    assert loader.load_metrics(
        "run-semantic", output_dir, expectation=SourceExpectation.REQUIRED
    ).summary.issue_codes == ("metrics_result_hash_mismatch",)

    output_dir = copy_observation_sources(tmp_path / "count")
    manifest_path = output_dir / "metrics" / "manifest.json"
    manifest = _read(manifest_path)
    manifest["output_counts"]["workload_operations"] = 2
    _write(manifest_path, manifest)
    assert loader.load_metrics(
        "run-semantic", output_dir, expectation=SourceExpectation.REQUIRED
    ).summary.issue_codes == ("metrics_result_invalid",)

    output_dir = copy_observation_sources(tmp_path / "membership")
    metrics_path = output_dir / "metrics" / "run-metrics.json"
    metrics = _read(metrics_path)
    metrics["operation_buckets"][0]["evidence"]["member_ids"] = []
    _write(metrics_path, metrics)
    _refresh_metrics_manifest(output_dir)
    assert loader.load_metrics(
        "run-semantic", output_dir, expectation=SourceExpectation.REQUIRED
    ).summary.issue_codes == ("metrics_result_invalid",)


@pytest.mark.parametrize(
    ("metrics_status", "source_status"),
    [
        (RunMetricsStatus.DEGRADED, SourceStatus.DEGRADED),
        (RunMetricsStatus.NO_DATA, SourceStatus.NO_DATA),
    ],
)
def test_metrics_consumable_status_mapping_is_preserved(
    tmp_path, metrics_status, source_status
):
    output_dir = copy_observation_sources(tmp_path)
    metrics_path = output_dir / "metrics" / "run-metrics.json"
    metrics = _read(metrics_path)
    metrics["status"] = metrics_status.value
    _write(metrics_path, metrics)
    _refresh_metrics_manifest(output_dir, status=metrics_status.value)

    loaded = loader.load_metrics(
        "run-semantic", output_dir, expectation=SourceExpectation.REQUIRED
    )

    assert loaded.summary.status is source_status
    assert loaded.value is not None


def test_required_disabled_missing_and_failed_metrics_are_not_conflated(tmp_path):
    output_dir = copy_observation_sources(tmp_path)
    disabled = loader.load_metrics(
        "run-semantic", output_dir, expectation=SourceExpectation.DISABLED
    )
    assert disabled.summary.status is SourceStatus.DISABLED
    assert disabled.summary.issue_codes == ("source_disabled",)

    (output_dir / "metrics" / "manifest.json").unlink()
    missing = loader.load_metrics(
        "run-semantic", output_dir, expectation=SourceExpectation.REQUIRED
    )
    assert missing.summary.status is SourceStatus.MISSING

    output_dir = copy_observation_sources(tmp_path / "failed")
    manifest_path = output_dir / "metrics" / "manifest.json"
    manifest = _read(manifest_path)
    manifest["write_status"] = "failed"
    manifest["metrics_status"] = "failed"
    _write(manifest_path, manifest)
    failed = loader.load_metrics(
        "run-semantic", output_dir, expectation=SourceExpectation.REQUIRED
    )
    assert failed.summary.status is SourceStatus.FAILED


@pytest.mark.parametrize(
    ("filename", "mutation", "loader_name", "issue_code"),
    [
        (
            "flaky-import.json",
            lambda value: value.update(run_id="other-run"),
            "load_flaky_import",
            "flaky_import_run_id_mismatch",
        ),
        (
            "flaky-import.json",
            lambda value: value.update(artifact_ref="C:/absolute/flaky.db"),
            "load_flaky_import",
            "flaky_import_absolute_artifact_ref",
        ),
        (
            "flaky-evaluation.json",
            lambda value: value.update(rule_version="unsupported"),
            "load_flaky_evaluation",
            "flaky_evaluation_contract_incompatible",
        ),
    ],
)
def test_flaky_identity_and_versions_are_validated(
    tmp_path, filename, mutation, loader_name, issue_code
):
    output_dir = copy_observation_sources(tmp_path)
    path = output_dir / filename
    payload = _read(path)
    mutation(payload)
    _write(path, payload)

    loaded = getattr(loader, loader_name)(
        "run-semantic", output_dir, expectation=SourceExpectation.REQUIRED
    )

    assert loaded.summary.status is SourceStatus.INCOMPATIBLE
    assert loaded.summary.issue_codes == (issue_code,)


def test_stale_flaky_projection_degrades_the_source(tmp_path):
    output_dir = copy_observation_sources(tmp_path)
    path = output_dir / "flaky-evaluation.json"
    payload = _read(path)
    payload["stale_count"] = 1
    _write(path, payload)

    loaded = loader.load_flaky_evaluation(
        "run-semantic", output_dir, expectation=SourceExpectation.REQUIRED
    )

    assert loaded.summary.status is SourceStatus.DEGRADED
    assert "flaky_projection_stale" in loaded.summary.issue_codes
