from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, TypeVar

from pydantic import ValidationError

from quality.flaky_models import (
    FLAKY_EVALUATION_SCHEMA_VERSION,
    FLAKY_IMPORTER_VERSION,
    FLAKY_IMPORT_SCHEMA_VERSION,
    FLAKY_STATE_RULE_VERSION,
    FlakyEvaluationResult,
    FlakyEvaluationStatus,
    FlakyImportResult,
    FlakyImportStatus,
)
from quality.metrics_models import (
    RUN_METRICS_AGGREGATION_VERSION,
    RUN_METRICS_SCHEMA_VERSION,
    RunMetricsResult,
    RunMetricsStatus,
)
from quality.models import IntegrityStatus, RunRecord, RunStatus, SCHEMA_VERSION
from quality.observation_models import (
    P1SourceSummary,
    SourceExpectation,
    SourceStatus,
)
from quality.report import REPORT_VERSION

from .contracts import LoadedSource, P0Value
from .validation import (
    IncompatibleSource,
    validate_expected_hash,
    validate_flaky_evaluation_contract,
    validate_flaky_identity,
    validate_metrics_contract,
    validate_metrics_manifest_identity,
    validate_p0_contract,
)


_T = TypeVar("_T")
_METRICS_ARTIFACT = "metrics/run-metrics.json"


def load_p0(run_id: str, output_dir: Path) -> LoadedSource[P0Value]:
    expectation = SourceExpectation.REQUIRED
    paths = {
        "run.json": output_dir / "run.json",
        "summary.json": output_dir / "summary.json",
        "gate-report.json": output_dir / "gate-report.json",
        "gate-report.md": output_dir / "gate-report.md",
    }
    missing = tuple(name for name, path in paths.items() if not path.is_file())
    if any(name != "gate-report.md" for name in missing):
        return source_result(
            "p0_report",
            expectation,
            SourceStatus.MISSING,
            artifact_path="summary.json",
            issue_codes=tuple(f"p0_{artifact_code_name(name)}_missing" for name in missing),
            evidence_refs=tuple(name for name in paths if name not in missing),
        )
    try:
        run = RunRecord.model_validate_json(paths["run.json"].read_text(encoding="utf-8"))
        summary_payload = read_json_object(paths["summary.json"])
        gate_payload = read_json_object(paths["gate-report.json"])
        summary, gate, categories = validate_p0_contract(
            run_id, run, summary_payload, gate_payload
        )
    except IncompatibleSource as error:
        return source_result(
            "p0_report",
            expectation,
            SourceStatus.INCOMPATIBLE,
            artifact_path="summary.json",
            issue_codes=(error.code,),
            evidence_refs=("run.json", "summary.json", "gate-report.json"),
            hashes=existing_source_hashes(paths),
        )
    except (OSError, ValidationError, ValueError, TypeError):
        return source_result(
            "p0_report",
            expectation,
            SourceStatus.INCOMPATIBLE,
            artifact_path="summary.json",
            issue_codes=("p0_report_invalid",),
            evidence_refs=("run.json", "summary.json", "gate-report.json"),
            hashes=existing_source_hashes(paths),
        )
    issues: list[str] = []
    status = SourceStatus.AVAILABLE
    if run.status is not RunStatus.FINISHED:
        issues.append("p0_run_not_finished")
        status = SourceStatus.DEGRADED
    if summary.integrity_status is not IntegrityStatus.COMPLETE:
        issues.append("p0_integrity_not_complete")
        status = SourceStatus.DEGRADED
    if "gate-report.md" in missing:
        issues.append("p0_gate_markdown_missing")
        status = SourceStatus.DEGRADED
    hashes = existing_source_hashes(paths)
    return source_result(
        "p0_report",
        expectation,
        status,
        artifact_path="summary.json",
        schema_version=SCHEMA_VERSION,
        producer_version=REPORT_VERSION,
        sha256=source_hash_for(hashes, "summary.json"),
        issue_codes=tuple(issues),
        evidence_refs=tuple(name for name in paths if name not in missing),
        value=P0Value(
            run=run,
            summary=summary,
            gate=gate,
            failure_categories=categories,
        ),
        hashes=hashes,
    )


def load_metrics(
    run_id: str,
    output_dir: Path,
    *,
    expectation: SourceExpectation,
) -> LoadedSource[RunMetricsResult]:
    if expectation is SourceExpectation.DISABLED:
        return disabled_source("run_metrics")
    manifest_path = output_dir / "metrics" / "manifest.json"
    metrics_path = output_dir / _METRICS_ARTIFACT
    if not manifest_path.is_file():
        return source_result(
            "run_metrics",
            expectation,
            SourceStatus.MISSING,
            artifact_path="metrics/manifest.json",
            issue_codes=("metrics_manifest_missing",),
        )
    manifest_hash = source_file_sha256(manifest_path)
    hashes: tuple[tuple[str, str], ...] = (("metrics/manifest.json", manifest_hash),)
    try:
        manifest = read_json_object(manifest_path)
    except (OSError, ValueError):
        return source_result(
            "run_metrics",
            expectation,
            SourceStatus.INCOMPATIBLE,
            artifact_path="metrics/manifest.json",
            sha256=manifest_hash,
            issue_codes=("metrics_manifest_invalid",),
            evidence_refs=("metrics/manifest.json",),
            hashes=hashes,
        )
    issue_codes = manifest_issue_codes(manifest)
    base = {
        "artifact_path": "metrics/manifest.json",
        "schema_version": optional_manifest_text(manifest, "schema_version"),
        "producer_version": optional_manifest_text(manifest, "aggregation_version"),
        "sha256": manifest_hash,
        "evidence_refs": ("metrics/manifest.json",),
        "hashes": hashes,
    }
    try:
        validate_metrics_manifest_identity(manifest, run_id)
    except IncompatibleSource as error:
        return source_result(
            "run_metrics",
            expectation,
            SourceStatus.INCOMPATIBLE,
            issue_codes=(error.code,),
            **base,
        )
    write_status = manifest.get("write_status")
    if write_status == "failed" or manifest.get("metrics_status") == RunMetricsStatus.FAILED.value:
        return source_result(
            "run_metrics",
            expectation,
            SourceStatus.FAILED,
            issue_codes=issue_codes or ("metrics_upstream_failed",),
            **base,
        )
    if write_status != "complete":
        return source_result(
            "run_metrics",
            expectation,
            SourceStatus.FAILED,
            issue_codes=("metrics_manifest_not_complete",),
            **base,
        )
    if not metrics_path.is_file():
        return source_result(
            "run_metrics",
            expectation,
            SourceStatus.MISSING,
            issue_codes=("metrics_result_missing",),
            **base,
        )
    metrics_hash = source_file_sha256(metrics_path)
    hashes = (*hashes, (_METRICS_ARTIFACT, metrics_hash))
    expected_hash = (manifest.get("output_hashes") or {}).get("run_metrics")
    try:
        validate_expected_hash(
            expected_hash,
            metrics_hash,
            code="metrics_result_hash_mismatch",
        )
    except IncompatibleSource as error:
        return source_result(
            "run_metrics",
            expectation,
            SourceStatus.INCOMPATIBLE,
            artifact_path=_METRICS_ARTIFACT,
            schema_version=RUN_METRICS_SCHEMA_VERSION,
            producer_version=RUN_METRICS_AGGREGATION_VERSION,
            sha256=metrics_hash,
            issue_codes=(error.code,),
            evidence_refs=("metrics/manifest.json", _METRICS_ARTIFACT),
            hashes=hashes,
        )
    try:
        metrics = RunMetricsResult.model_validate_json(metrics_path.read_text(encoding="utf-8"))
        validate_metrics_contract(metrics, manifest, run_id)
    except (OSError, ValidationError, ValueError, TypeError):
        return source_result(
            "run_metrics",
            expectation,
            SourceStatus.INCOMPATIBLE,
            artifact_path=_METRICS_ARTIFACT,
            schema_version=RUN_METRICS_SCHEMA_VERSION,
            producer_version=RUN_METRICS_AGGREGATION_VERSION,
            sha256=metrics_hash,
            issue_codes=("metrics_result_invalid",),
            evidence_refs=("metrics/manifest.json", _METRICS_ARTIFACT),
            hashes=hashes,
        )
    status = {
        RunMetricsStatus.AGGREGATED: SourceStatus.AVAILABLE,
        RunMetricsStatus.DEGRADED: SourceStatus.DEGRADED,
        RunMetricsStatus.NO_DATA: SourceStatus.NO_DATA,
        RunMetricsStatus.FAILED: SourceStatus.FAILED,
    }[metrics.status]
    return source_result(
        "run_metrics",
        expectation,
        status,
        artifact_path=_METRICS_ARTIFACT,
        schema_version=RUN_METRICS_SCHEMA_VERSION,
        producer_version=RUN_METRICS_AGGREGATION_VERSION,
        sha256=metrics_hash,
        issue_codes=tuple(sorted({*issue_codes, *(item.code for item in metrics.issues)})),
        evidence_refs=("metrics/manifest.json", _METRICS_ARTIFACT),
        value=metrics,
        hashes=hashes,
    )

def load_flaky_import(
    run_id: str,
    output_dir: Path,
    *,
    expectation: SourceExpectation,
) -> LoadedSource[FlakyImportResult]:
    if expectation is SourceExpectation.DISABLED:
        return disabled_source("flaky_import")
    path = output_dir / "flaky-import.json"
    return load_flaky_model(
        source_name="flaky_import",
        path=path,
        artifact_path="flaky-import.json",
        run_id=run_id,
        expectation=expectation,
        model=FlakyImportResult,
        schema_version=FLAKY_IMPORT_SCHEMA_VERSION,
        producer_version=FLAKY_IMPORTER_VERSION,
        status_mapper=flaky_import_source_status,
    )


def load_flaky_evaluation(
    run_id: str,
    output_dir: Path,
    *,
    expectation: SourceExpectation,
) -> LoadedSource[FlakyEvaluationResult]:
    if expectation is SourceExpectation.DISABLED:
        return disabled_source("flaky_evaluation")
    path = output_dir / "flaky-evaluation.json"
    loaded = load_flaky_model(
        source_name="flaky_evaluation",
        path=path,
        artifact_path="flaky-evaluation.json",
        run_id=run_id,
        expectation=expectation,
        model=FlakyEvaluationResult,
        schema_version=FLAKY_EVALUATION_SCHEMA_VERSION,
        producer_version=FLAKY_STATE_RULE_VERSION,
        status_mapper=flaky_evaluation_source_status,
    )
    if loaded.value is None:
        return loaded
    value = loaded.value
    try:
        validate_flaky_evaluation_contract(value)
    except IncompatibleSource as error:
        return source_result(
            "flaky_evaluation",
            expectation,
            SourceStatus.INCOMPATIBLE,
            artifact_path="flaky-evaluation.json",
            schema_version=FLAKY_EVALUATION_SCHEMA_VERSION,
            producer_version=FLAKY_STATE_RULE_VERSION,
            sha256=loaded.summary.sha256,
            issue_codes=(error.code,),
            evidence_refs=("flaky-evaluation.json",),
            hashes=loaded.hashes,
        )
    if value.stale_count > 0 and loaded.summary.status is SourceStatus.AVAILABLE:
        loaded = source_result(
            "flaky_evaluation",
            expectation,
            SourceStatus.DEGRADED,
            artifact_path="flaky-evaluation.json",
            schema_version=FLAKY_EVALUATION_SCHEMA_VERSION,
            producer_version=FLAKY_STATE_RULE_VERSION,
            sha256=loaded.summary.sha256,
            issue_codes=tuple(sorted({*loaded.summary.issue_codes, "flaky_projection_stale"})),
            evidence_refs=("flaky-evaluation.json",),
            value=value,
            hashes=loaded.hashes,
        )
    return loaded


def load_flaky_model(
    *,
    source_name: str,
    path: Path,
    artifact_path: str,
    run_id: str,
    expectation: SourceExpectation,
    model: type[_T],
    schema_version: str,
    producer_version: str,
    status_mapper: Any,
) -> LoadedSource[_T]:
    if not path.is_file():
        return source_result(
            source_name,
            expectation,
            SourceStatus.MISSING,
            artifact_path=artifact_path,
            issue_codes=(f"{source_name}_missing",),
        )
    digest = source_file_sha256(path)
    hashes = ((artifact_path, digest),)
    try:
        value = model.model_validate_json(path.read_text(encoding="utf-8"))
        validate_flaky_identity(
            value,
            source_name=source_name,
            run_id=run_id,
        )
    except IncompatibleSource as error:
        return source_result(
            source_name,
            expectation,
            SourceStatus.INCOMPATIBLE,
            artifact_path=artifact_path,
            sha256=digest,
            issue_codes=(error.code,),
            evidence_refs=(artifact_path,),
            hashes=hashes,
        )
    except (OSError, ValidationError, ValueError):
        return source_result(
            source_name,
            expectation,
            SourceStatus.INCOMPATIBLE,
            artifact_path=artifact_path,
            sha256=digest,
            issue_codes=(f"{source_name}_invalid",),
            evidence_refs=(artifact_path,),
            hashes=hashes,
        )
    source_status = status_mapper(getattr(value, "status"))
    return source_result(
        source_name,
        expectation,
        source_status,
        artifact_path=artifact_path,
        schema_version=schema_version,
        producer_version=producer_version,
        sha256=digest,
        issue_codes=tuple(sorted(item.code for item in getattr(value, "issues", ()))),
        evidence_refs=(artifact_path,),
        value=value if source_status is not SourceStatus.FAILED else None,
        hashes=hashes,
    )

def source_result(
    source_name: str,
    expectation: SourceExpectation,
    status: SourceStatus,
    *,
    artifact_path: str | None = None,
    schema_version: str | None = None,
    producer_version: str | None = None,
    sha256: str | None = None,
    issue_codes: tuple[str, ...] = (),
    evidence_refs: tuple[str, ...] = (),
    value: _T | None = None,
    hashes: tuple[tuple[str, str], ...] = (),
) -> LoadedSource[_T]:
    return LoadedSource(
        summary=P1SourceSummary(
            source_name=source_name,
            expectation=expectation,
            status=status,
            artifact_path=artifact_path,
            schema_version=schema_version,
            producer_version=producer_version,
            sha256=sha256,
            issue_codes=issue_codes,
            evidence_refs=evidence_refs,
        ),
        value=value,
        hashes=hashes,
    )


def disabled_source(source_name: str) -> LoadedSource[Any]:
    return source_result(
        source_name,
        SourceExpectation.DISABLED,
        SourceStatus.DISABLED,
        issue_codes=("source_disabled",),
    )


def flaky_import_source_status(status: FlakyImportStatus) -> SourceStatus:
    return {
        FlakyImportStatus.IMPORTED: SourceStatus.AVAILABLE,
        FlakyImportStatus.NOOP: SourceStatus.AVAILABLE,
        FlakyImportStatus.DEGRADED: SourceStatus.DEGRADED,
        FlakyImportStatus.FAILED: SourceStatus.FAILED,
        FlakyImportStatus.NO_DATA: SourceStatus.NO_DATA,
    }[status]


def flaky_evaluation_source_status(status: FlakyEvaluationStatus) -> SourceStatus:
    return {
        FlakyEvaluationStatus.EVALUATED: SourceStatus.AVAILABLE,
        FlakyEvaluationStatus.NOOP: SourceStatus.AVAILABLE,
        FlakyEvaluationStatus.DEGRADED: SourceStatus.DEGRADED,
        FlakyEvaluationStatus.FAILED: SourceStatus.FAILED,
        FlakyEvaluationStatus.NO_DATA: SourceStatus.NO_DATA,
    }[status]


def manifest_issue_codes(manifest: dict[str, Any]) -> tuple[str, ...]:
    issues = manifest.get("issues")
    if not isinstance(issues, list):
        return ()
    return tuple(
        sorted(
            {
                str(item.get("code")).strip()
                for item in issues
                if isinstance(item, dict) and str(item.get("code") or "").strip()
            }
        )
    )


def optional_manifest_text(manifest: dict[str, Any], name: str) -> str | None:
    value = manifest.get(name)
    return value.strip() if isinstance(value, str) and value.strip() else None


def existing_source_hashes(paths: dict[str, Path]) -> tuple[tuple[str, str], ...]:
    return tuple(
        (name, source_file_sha256(path))
        for name, path in sorted(paths.items())
        if path.is_file()
    )


def source_hash_for(hashes: tuple[tuple[str, str], ...], name: str) -> str | None:
    return dict(hashes).get(name)


def read_json_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return value

def artifact_code_name(filename: str) -> str:
    return filename.replace(".", "_").replace("-", "_")


def source_file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
