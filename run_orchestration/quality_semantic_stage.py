from quality.config import QualityRuntimeConfig
from quality.semantic_aggregator import (
    SemanticMergeRequest,
    merge_semantic_run,
)


def run_semantic_stage(quality_config: QualityRuntimeConfig) -> None:
    if not quality_config.semantic_enabled:
        return
    try:
        merge_semantic_run(
            SemanticMergeRequest(
                run_id=str(quality_config.run_id),
                output_dir=quality_config.output_dir,
            )
        )
    except Exception as error:
        print(
            "Quality semantic merge failed open: "
            f"{type(error).__name__}: {error}"
        )
