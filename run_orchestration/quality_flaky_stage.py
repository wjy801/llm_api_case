from __future__ import annotations

from quality.config import QualityRuntimeConfig
from quality.flaky_importer import (
    evaluate_flaky_state,
    import_flaky_history,
    write_flaky_no_data_report,
    write_flaky_state_no_data_report,
)
from quality.flaky_models import (
    FlakyImportRequest,
    FlakyImportResult,
    FlakyImportStatus,
)
from quality.models import RunStatus


def run_flaky_history_stage(
    quality_config: QualityRuntimeConfig,
    *,
    status: RunStatus,
) -> FlakyImportResult | None:
    if (
        quality_config.flaky_history_warning
        and not quality_config.flaky_history_enabled
    ):
        print(
            "Quality Flaky history configuration warning: "
            f"{quality_config.flaky_history_warning}"
        )
    if not quality_config.flaky_history_enabled:
        return None
    try:
        if status is not RunStatus.FINISHED:
            result = write_flaky_no_data_report(
                run_id=str(quality_config.run_id),
                quality_output_dir=quality_config.output_dir,
                code="run_not_finished",
                summary=f"run status {status.value!r} is not importable",
            )
        elif quality_config.flaky_history_warning:
            print(
                "Quality Flaky history configuration warning: "
                f"{quality_config.flaky_history_warning}"
            )
            result = write_flaky_no_data_report(
                run_id=str(quality_config.run_id),
                quality_output_dir=quality_config.output_dir,
                code="invalid_flaky_history_configuration",
                summary=quality_config.flaky_history_warning,
            )
        elif quality_config.flaky_database_path is None:
            result = write_flaky_no_data_report(
                run_id=str(quality_config.run_id),
                quality_output_dir=quality_config.output_dir,
                code="flaky_database_path_missing",
                summary="Flaky history database path is missing",
            )
        else:
            result = import_flaky_history(
                FlakyImportRequest(
                    run_id=str(quality_config.run_id),
                    quality_output_dir=quality_config.output_dir,
                    database_path=quality_config.flaky_database_path,
                )
            )
        print(
            "Quality Flaky history import completed: "
            f"status={result.status.value}, inserted={result.inserted_count}"
        )
        return result
    except Exception as error:
        print(
            "Quality Flaky history import failed open: "
            f"{type(error).__name__}: {error}"
        )
        return None


def run_flaky_state_stage(
    quality_config: QualityRuntimeConfig,
    flaky_import_result: FlakyImportResult | None,
) -> None:
    if (
        quality_config.flaky_state_warning
        and not quality_config.flaky_state_enabled
    ):
        print(
            "Quality Flaky state configuration warning: "
            f"{quality_config.flaky_state_warning}"
        )
    if not quality_config.flaky_state_enabled:
        return
    try:
        if quality_config.flaky_database_path is None:
            result = write_flaky_state_no_data_report(
                run_id=str(quality_config.run_id),
                quality_output_dir=quality_config.output_dir,
                code="flaky_database_path_missing",
                summary="Flaky state database path is missing",
            )
        elif (
            flaky_import_result is None
            or flaky_import_result.status
            not in {
                FlakyImportStatus.IMPORTED,
                FlakyImportStatus.NOOP,
                FlakyImportStatus.DEGRADED,
            }
        ):
            result = write_flaky_state_no_data_report(
                run_id=str(quality_config.run_id),
                quality_output_dir=quality_config.output_dir,
                code="flaky_history_import_not_ready",
                summary=(
                    "Flaky history import did not produce an evaluable run"
                ),
            )
        else:
            result = evaluate_flaky_state(
                quality_config.flaky_database_path,
                run_id=str(quality_config.run_id),
                quality_output_dir=quality_config.output_dir,
            )
        print(
            "Quality Flaky state evaluation completed: "
            f"status={result.status.value}, "
            f"affected={result.affected_count}, "
            f"transitioned={result.transitioned_count}"
        )
    except Exception as error:
        print(
            "Quality Flaky state evaluation failed open: "
            f"{type(error).__name__}: {error}"
        )
