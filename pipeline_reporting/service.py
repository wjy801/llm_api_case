from __future__ import annotations

from collections.abc import Mapping
import os
from pathlib import Path
import tempfile

from pipeline_reporting.builder import build_pipeline_report
from pipeline_reporting.config import load_pipeline_report_config
from pipeline_reporting.contracts import PipelineContext, PipelineReport
from pipeline_reporting.renderer import render_pipeline_summary
from pipeline_reporting.sources import load_pipeline_sources


def context_from_environment(
    environment: Mapping[str, str] | None = None,
) -> PipelineContext:
    values = environment if environment is not None else os.environ
    return PipelineContext(
        job_name=_value(values, "JOB_NAME", "local"),
        build_number=_value(values, "BUILD_NUMBER", "-"),
        build_url=_value(values, "BUILD_URL", ""),
        build_result=_value(values, "PIPELINE_BUILD_RESULT", "SUCCESS").upper(),
        branch=_value(values, "BRANCH_NAME", _value(values, "GIT_BRANCH", "-")),
        commit_sha=_value(values, "GIT_COMMIT", "-"),
        environment_name=(
            "china"
            if _bool_value(values.get("USE_CHINA_ENVIRONMENT"), default=True)
            else "overseas"
        ),
        duration_ms=_nonnegative_int(values.get("PIPELINE_DURATION_MS")),
        framework_tests_enabled=_bool_value(values.get("RUN_FRAMEWORK_TESTS")),
        smoke_collect_enabled=_bool_value(values.get("RUN_COLLECT_ONLY")),
        real_smoke_enabled=_bool_value(values.get("RUN_REAL_SMOKE")),
        smoke_target=_value(values, "SMOKE_TARGET", "module/smoke"),
        parallel_workers=_value(values, "TEST_PARALLEL_WORKERS", "off"),
    )


def generate_pipeline_summary(
    workspace: str | Path,
    output_path: str | Path = "reports/pipeline-summary.md",
    *,
    environment: Mapping[str, str] | None = None,
    dotenv_path: str | Path | None = None,
) -> PipelineReport | None:
    root = Path(workspace)
    config = load_pipeline_report_config(
        environment,
        dotenv_path=dotenv_path or root / ".env",
    )
    target = root / output_path if not Path(output_path).is_absolute() else Path(output_path)
    if not config.enabled:
        target.unlink(missing_ok=True)
        return None
    report = build_pipeline_report(
        context_from_environment(environment),
        load_pipeline_sources(root),
    )
    _write_text_atomic(target, render_pipeline_summary(report))
    return report


def _write_text_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
        os.replace(temporary_name, path)
    except Exception:
        Path(temporary_name).unlink(missing_ok=True)
        raise


def _value(values: Mapping[str, str], name: str, default: str) -> str:
    value = values.get(name)
    text = str(value).strip() if value is not None else ""
    return text or default


def _bool_value(value: str | None, *, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().casefold() in {"true", "1", "yes", "on"}


def _nonnegative_int(value: str | None) -> int:
    try:
        return max(int(str(value)), 0) if value is not None else 0
    except ValueError:
        return 0
