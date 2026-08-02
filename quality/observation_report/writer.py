from __future__ import annotations

from datetime import datetime
import hashlib
import os
from pathlib import Path
import tempfile

from quality.observation_models import (
    P1_OBSERVATION_MANIFEST_VERSION,
    P1_OBSERVATION_REPORT_VERSION,
    P1_OBSERVATION_SCHEMA_VERSION,
    P1ObservationReport,
    P1ReportStatus,
)
from quality.storage import write_json_atomic


def write_observation_manifest(
    path: Path,
    *,
    run_id: str,
    created_at: datetime,
    write_status: str,
    report_status: P1ReportStatus | None,
    output_hashes: dict[str, str],
    source_hashes: dict[str, str],
    issue_codes: tuple[str, ...],
) -> None:
    write_json_atomic(
        path,
        {
            "manifest_version": P1_OBSERVATION_MANIFEST_VERSION,
            "schema_version": P1_OBSERVATION_SCHEMA_VERSION,
            "report_version": P1_OBSERVATION_REPORT_VERSION,
            "run_id": run_id,
            "write_status": write_status,
            "report_status": report_status.value if report_status is not None else None,
            "created_at": created_at,
            "output_hashes": dict(sorted(output_hashes.items())),
            "source_hashes": dict(sorted(source_hashes.items())),
            "issue_codes": sorted(set(issue_codes)),
        },
    )


def write_observation_artifacts(
    *,
    markdown_path: Path,
    json_path: Path,
    markdown: str,
    report: P1ObservationReport,
) -> dict[str, str]:
    write_text_atomic(markdown_path, markdown)
    write_json_atomic(json_path, report)
    return {
        "json": output_file_sha256(json_path),
        "markdown": output_file_sha256(markdown_path),
    }


def write_text_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(content)
            if not content.endswith("\n"):
                handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def output_file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
