from quality.config import QualityRuntimeConfig
from quality.observation_models import SourceExpectation
from quality.observation_report import (
    P1ObservationRequest,
    generate_p1_observation_report,
)


def run_observation_stage(quality_config: QualityRuntimeConfig) -> None:
    if quality_config.p1_report_warning and not quality_config.p1_report_enabled:
        print(
            "Quality P1 observation report configuration warning: "
            f"{quality_config.p1_report_warning}"
        )
    if not quality_config.p1_report_enabled:
        return
    try:
        result = generate_p1_observation_report(
            P1ObservationRequest(
                run_id=str(quality_config.run_id),
                output_dir=quality_config.output_dir,
                metrics_expectation=(
                    SourceExpectation.REQUIRED
                    if quality_config.metrics_enabled
                    else SourceExpectation.DISABLED
                ),
                flaky_import_expectation=(
                    SourceExpectation.REQUIRED
                    if quality_config.flaky_history_enabled
                    else SourceExpectation.DISABLED
                ),
                flaky_evaluation_expectation=(
                    SourceExpectation.REQUIRED
                    if quality_config.flaky_state_enabled
                    else SourceExpectation.DISABLED
                ),
            )
        )
        message = (
            f"write_status={result.write_status}, "
            "report_status="
            f"{result.report_status.value if result.report_status else '-'}"
        )
        if result.write_status == "complete":
            print(f"Quality P1 observation report completed: {message}")
        else:
            print(f"Quality P1 observation report failed open: {message}")
    except Exception as error:
        print(
            "Quality P1 observation report failed open: "
            f"{type(error).__name__}: {error}"
        )
