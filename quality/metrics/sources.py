from __future__ import annotations

import json
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from util.artifact_io import (
    ArtifactFormatError,
    ArtifactJsonLineError,
    file_sha256,
    read_json_object as read_artifact_json_object,
    read_jsonl_values,
)

from quality.aggregator import MANIFEST_VERSION as P0_MANIFEST_VERSION
from quality.metrics_models import ArtifactEvidence, SourceEvidence
from quality.models import SCHEMA_VERSION, IntegrityStatus, RequestMetric, RunRecord
from quality.semantic_models import (
    SEMANTIC_MANIFEST_VERSION,
    SEMANTIC_MERGE_VERSION,
    SEMANTIC_SCHEMA_VERSION,
    OperationRecord,
    PollingSessionRecord,
    RequestGroupRecord,
    SemanticIntegrityIssue,
)

from .contracts import MetricsSources
from .validation import (
    raise_source_error,
    relative_artifact_path,
    require_manifest,
    validate_source_relationships,
    validated_output_hash,
)


_T = TypeVar("_T", bound=BaseModel)
_SEMANTIC_OUTPUT_MODELS: dict[str, type[BaseModel]] = {
    "request-groups": RequestGroupRecord,
    "polling-sessions": PollingSessionRecord,
    "operations": OperationRecord,
    "integrity-issues": SemanticIntegrityIssue,
}


def load_sources(run_id: str, output_dir: Path) -> MetricsSources:
    run_path = output_dir / "run.json"
    p0_manifest_path = output_dir / "merged" / "manifest.json"
    request_metrics_path = output_dir / "merged" / "request-metrics.jsonl"
    semantic_dir = output_dir / "semantic" / "merged"
    semantic_manifest_path = semantic_dir / "manifest.json"

    run = read_model(run_path, RunRecord, "run_record_invalid")
    if run.run_id != run_id:
        raise_source_error(
            "run_id_mismatch", "run.json belongs to a different run", run.run_id
        )

    p0_manifest = read_json_object(p0_manifest_path, "p0_manifest_invalid")
    require_manifest(
        p0_manifest,
        run_id=run_id,
        status="complete",
        versions={
            "manifest_version": P0_MANIFEST_VERSION,
            "schema_version": SCHEMA_VERSION,
        },
        code_prefix="p0",
    )
    p0_integrity_status = str(p0_manifest.get("integrity_status") or "unknown")
    if p0_integrity_status == IntegrityStatus.FAILED.value:
        raise_source_error(
            "p0_integrity_failed", "P0 merged facts failed integrity validation"
        )
    p0_request_hash = validated_source_output_hash(
        request_metrics_path,
        (p0_manifest.get("output_hashes") or {}).get("request-metrics"),
        "p0_request_metrics",
    )
    requests = tuple(
        read_jsonl_models(
            request_metrics_path, RequestMetric, "p0_request_metric_invalid"
        )
    )

    semantic_manifest = read_json_object(
        semantic_manifest_path, "semantic_manifest_invalid"
    )
    require_manifest(
        semantic_manifest,
        run_id=run_id,
        status="complete",
        versions={
            "manifest_version": SEMANTIC_MANIFEST_VERSION,
            "schema_version": SEMANTIC_SCHEMA_VERSION,
            "merge_version": SEMANTIC_MERGE_VERSION,
        },
        code_prefix="semantic",
    )
    semantic_integrity_status = str(
        semantic_manifest.get("integrity_status") or "unknown"
    )
    if semantic_integrity_status == IntegrityStatus.FAILED.value:
        raise_source_error(
            "semantic_integrity_failed",
            "semantic merged facts failed integrity validation",
        )
    p0_manifest_hash = source_file_sha256(p0_manifest_path)
    semantic_p0 = semantic_manifest.get("p0_evidence") or {}
    if semantic_p0.get("manifest_sha256") != p0_manifest_hash:
        raise_source_error(
            "semantic_p0_manifest_evidence_mismatch",
            "semantic facts reference a different P0 manifest",
        )
    if semantic_p0.get("request_metrics_sha256") != p0_request_hash:
        raise_source_error(
            "semantic_p0_request_evidence_mismatch",
            "semantic facts reference different P0 request metrics",
        )

    parsed: dict[str, tuple[BaseModel, ...]] = {}
    semantic_evidence: dict[str, ArtifactEvidence] = {}
    for name, model in _SEMANTIC_OUTPUT_MODELS.items():
        path = semantic_dir / f"{name}.jsonl"
        digest = validated_source_output_hash(
            path,
            (semantic_manifest.get("output_hashes") or {}).get(name),
            f"semantic_{name.replace('-', '_')}",
        )
        parsed[name] = tuple(
            read_jsonl_models(
                path, model, f"semantic_{name.replace('-', '_')}_invalid"
            )
        )
        semantic_evidence[name] = ArtifactEvidence(
            path=relative_artifact_path(path, output_dir),
            sha256=digest,
            schema_version=SEMANTIC_SCHEMA_VERSION,
        )

    sources = MetricsSources(
        run=run,
        requests=tuple(
            item for item in requests if isinstance(item, RequestMetric)
        ),
        groups=tuple(
            item
            for item in parsed["request-groups"]
            if isinstance(item, RequestGroupRecord)
        ),
        sessions=tuple(
            item
            for item in parsed["polling-sessions"]
            if isinstance(item, PollingSessionRecord)
        ),
        operations=tuple(
            item
            for item in parsed["operations"]
            if isinstance(item, OperationRecord)
        ),
        semantic_issues=tuple(
            item
            for item in parsed["integrity-issues"]
            if isinstance(item, SemanticIntegrityIssue)
        ),
        p0_integrity_status=p0_integrity_status,
        semantic_integrity_status=semantic_integrity_status,
        evidence=SourceEvidence(
            p0_manifest=ArtifactEvidence(
                path=relative_artifact_path(p0_manifest_path, output_dir),
                sha256=p0_manifest_hash,
                schema_version=SCHEMA_VERSION,
                manifest_version=P0_MANIFEST_VERSION,
                merge_version=str(p0_manifest.get("merge_version") or "unknown"),
            ),
            p0_request_metrics=ArtifactEvidence(
                path=relative_artifact_path(request_metrics_path, output_dir),
                sha256=p0_request_hash,
                schema_version=SCHEMA_VERSION,
            ),
            semantic_manifest=ArtifactEvidence(
                path=relative_artifact_path(semantic_manifest_path, output_dir),
                sha256=source_file_sha256(semantic_manifest_path),
                schema_version=SEMANTIC_SCHEMA_VERSION,
                manifest_version=SEMANTIC_MANIFEST_VERSION,
                merge_version=SEMANTIC_MERGE_VERSION,
            ),
            semantic_outputs=semantic_evidence,
        ),
    )
    validate_source_relationships(run_id, sources)
    return sources


def read_model(path: Path, model: type[_T], code: str) -> _T:
    try:
        return model.model_validate_json(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise_source_error(code, f"required source {path.name} is missing", path.name)
    except (OSError, ValidationError, ValueError):
        raise_source_error(code, f"required source {path.name} is invalid", path.name)


def read_json_object(path: Path, code: str) -> dict[str, Any]:
    try:
        return read_artifact_json_object(path)
    except FileNotFoundError:
        raise_source_error(
            code, f"required manifest {path.name} is missing", path.name
        )
    except ArtifactFormatError:
        raise_source_error(
            code, f"required manifest {path.name} is not an object", path.name
        )
    except (OSError, json.JSONDecodeError):
        raise_source_error(
            code, f"required manifest {path.name} is invalid", path.name
        )
def read_jsonl_models(path: Path, model: type[_T], code: str) -> list[_T]:
    records: list[_T] = []
    try:
        for item in read_jsonl_values(path):
            try:
                records.append(model.model_validate(item.value))
            except (ValidationError, ValueError):
                raise_source_error(
                    code,
                    f"{path.name} contains an invalid record",
                    f"{path.name}:{item.number}",
                )
    except ArtifactJsonLineError as error:
        raise_source_error(
            code,
            f"{path.name} contains an invalid record",
            f"{path.name}:{error.line_number}",
        )
    except FileNotFoundError:
        raise_source_error(code, f"required source {path.name} is missing", path.name)
    except OSError:
        raise_source_error(
            code, f"required source {path.name} cannot be read", path.name
        )
    return records


def validated_source_output_hash(
    path: Path, expected: object, code_prefix: str
) -> str:
    if not path.exists() or not path.is_file():
        raise_source_error(
            f"{code_prefix}_missing",
            f"required source {path.name} is missing",
            path.name,
        )
    actual = source_file_sha256(path)
    return validated_output_hash(path, expected, actual, code_prefix)


def source_file_sha256(path: Path) -> str:
    return file_sha256(path)
