from __future__ import annotations

from contextlib import contextmanager
import os

from quality.config import (
    QUALITY_ENABLE_ENV,
    QUALITY_EXECUTION_ID_ENV,
    QUALITY_OUTPUT_DIR_ENV,
    QUALITY_RUN_ID_ENV,
    QualityRuntimeConfig,
    load_quality_config,
)
from quality.identifiers import build_run_id

from .paths import PROJECT_ROOT


def resolve_parent_quality_config() -> QualityRuntimeConfig:
    try:
        configured = load_quality_config()
    except ValueError as error:
        print(f"Quality collection disabled: {error}")
        return QualityRuntimeConfig(
            enabled=False,
            run_id=None,
            execution_id=None,
            output_dir=PROJECT_ROOT / "reports/quality",
        )

    output_dir = configured.output_dir
    if not output_dir.is_absolute():
        output_dir = PROJECT_ROOT / output_dir
    if not configured.enabled:
        return QualityRuntimeConfig(
            enabled=False,
            run_id=configured.run_id,
            execution_id=None,
            output_dir=output_dir,
            semantic_enabled=False,
            semantic_warning=configured.semantic_warning,
            metrics_enabled=False,
            metrics_warning=configured.metrics_warning,
            p1_report_enabled=False,
            p1_report_warning=configured.p1_report_warning,
            flaky_history_enabled=False,
            flaky_database_path=configured.flaky_database_path,
            flaky_history_warning=configured.flaky_history_warning,
            flaky_state_enabled=False,
            flaky_state_warning=configured.flaky_state_warning,
        )

    return QualityRuntimeConfig(
        enabled=True,
        run_id=configured.run_id or new_parent_run_id(),
        execution_id=None,
        output_dir=output_dir,
        semantic_enabled=configured.semantic_enabled,
        semantic_warning=configured.semantic_warning,
        metrics_enabled=configured.metrics_enabled,
        metrics_warning=configured.metrics_warning,
        p1_report_enabled=configured.p1_report_enabled,
        p1_report_warning=configured.p1_report_warning,
        flaky_history_enabled=configured.flaky_history_enabled,
        flaky_database_path=configured.flaky_database_path,
        flaky_history_warning=configured.flaky_history_warning,
        flaky_state_enabled=configured.flaky_state_enabled,
        flaky_state_warning=configured.flaky_state_warning,
    )


def new_parent_run_id() -> str:
    job_name = os.environ.get("JOB_NAME")
    build_number = os.environ.get("BUILD_NUMBER")
    if job_name and build_number:
        return build_run_id(job_name=job_name, build_number=build_number)
    return build_run_id()


@contextmanager
def quality_stage_environment(
    quality_config: QualityRuntimeConfig, execution_id: str
):
    if not quality_config.enabled:
        yield
        return
    values = {
        QUALITY_ENABLE_ENV: "1",
        QUALITY_RUN_ID_ENV: str(quality_config.run_id),
        QUALITY_EXECUTION_ID_ENV: execution_id,
        QUALITY_OUTPUT_DIR_ENV: str(quality_config.output_dir),
    }
    previous = {name: os.environ.get(name) for name in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
