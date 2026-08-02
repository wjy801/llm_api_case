from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from quality.observation_models import SourceExpectation

from . import builder, loader, renderer, writer
from .contracts import P1ObservationGenerationResult, P1ObservationRequest
from .validation import required_text


_OUTPUT_JSON = "p1-observation.json"
_OUTPUT_MARKDOWN = "p1-observation.md"
_OUTPUT_MANIFEST = "p1-observation-manifest.json"


def generate_p1_observation_report(
    request: P1ObservationRequest,
) -> P1ObservationGenerationResult:
    run_id = required_text(request.run_id, "run_id")
    output_dir = Path(request.output_dir)
    manifest_path = output_dir / _OUTPUT_MANIFEST
    json_path = output_dir / _OUTPUT_JSON
    markdown_path = output_dir / _OUTPUT_MARKDOWN
    created_at = datetime.now(UTC)
    metrics_expectation = SourceExpectation(request.metrics_expectation)
    flaky_import_expectation = SourceExpectation(request.flaky_import_expectation)
    flaky_evaluation_expectation = SourceExpectation(
        request.flaky_evaluation_expectation
    )
    writer.write_observation_manifest(
        manifest_path,
        run_id=run_id,
        created_at=created_at,
        write_status="building",
        report_status=None,
        output_hashes={},
        source_hashes={},
        issue_codes=(),
    )
    try:
        p0 = loader.load_p0(run_id, output_dir)
        metrics = loader.load_metrics(
            run_id, output_dir, expectation=metrics_expectation
        )
        flaky_import = loader.load_flaky_import(
            run_id, output_dir, expectation=flaky_import_expectation
        )
        flaky_evaluation = loader.load_flaky_evaluation(
            run_id,
            output_dir,
            expectation=flaky_evaluation_expectation,
        )
        loaded = (p0, metrics, flaky_import, flaky_evaluation)
        report = builder.build_report(
            run_id,
            created_at=created_at,
            p0=p0,
            metrics=metrics,
            flaky_import=flaky_import,
            flaky_evaluation=flaky_evaluation,
        )
        markdown = renderer.render_p1_observation_markdown(report)
        output_hashes = writer.write_observation_artifacts(
            markdown_path=markdown_path,
            json_path=json_path,
            markdown=markdown,
            report=report,
        )
        source_hashes = {
            name: digest
            for item in loaded
            for name, digest in item.hashes
        }
        issue_codes = report.integrity.issue_codes
        writer.write_observation_manifest(
            manifest_path,
            run_id=run_id,
            created_at=created_at,
            write_status="complete",
            report_status=report.report_status,
            output_hashes=output_hashes,
            source_hashes=source_hashes,
            issue_codes=issue_codes,
        )
        return P1ObservationGenerationResult(
            run_id=run_id,
            output_dir=output_dir,
            manifest_path=manifest_path,
            json_path=json_path,
            markdown_path=markdown_path,
            write_status="complete",
            report_status=report.report_status,
            issue_codes=issue_codes,
            report=report,
        )
    except Exception as error:
        code = "p1_observation_generation_failed"
        try:
            writer.write_observation_manifest(
                manifest_path,
                run_id=run_id,
                created_at=created_at,
                write_status="failed",
                report_status=None,
                output_hashes={},
                source_hashes={},
                issue_codes=(code, type(error).__name__),
            )
        except Exception:
            pass
        return P1ObservationGenerationResult(
            run_id=run_id,
            output_dir=output_dir,
            manifest_path=manifest_path,
            json_path=json_path,
            markdown_path=markdown_path,
            write_status="failed",
            report_status=None,
            issue_codes=(code, type(error).__name__),
        )
