from quality.config import QualityRuntimeConfig
from quality.metrics import RunMetricsAggregationRequest, aggregate_run_metrics


def run_metrics_stage(quality_config: QualityRuntimeConfig) -> None:
    if quality_config.metrics_warning and not quality_config.metrics_enabled:
        print(
            "Quality run metrics configuration warning: "
            f"{quality_config.metrics_warning}"
        )
    if not quality_config.metrics_enabled:
        return
    try:
        result = aggregate_run_metrics(
            RunMetricsAggregationRequest(
                run_id=str(quality_config.run_id),
                output_dir=quality_config.output_dir,
            )
        )
        print(
            "Quality run metrics completed: "
            f"status={result.status.value}, "
            f"operations={result.operation_count}, "
            f"request_groups={result.request_group_count}, "
            f"request_events={result.request_event_count}"
        )
    except Exception as error:
        print(
            "Quality run metrics failed open: "
            f"{type(error).__name__}: {error}"
        )
