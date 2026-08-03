from __future__ import annotations

from datetime import UTC, datetime, timedelta
import importlib.util
import json
from pathlib import Path
import shutil

import quality
import requests
from common.base_request import BaseRequest
from pipeline_reporting import generate_pipeline_summary, renderer, sources
from quality.cli import main
from quality.config import QualityRuntimeConfig
from quality.models import CasePhase, CaseResult, CaseStatus, RunStatus
from quality.semantic_models import TrafficRole
from run_orchestration import quality_pipeline


PROJECT_ROOT = Path(__file__).resolve().parents[2]

REMOVED_MODULES = (
    "quality.gate",
    "quality.report",
    "quality.observation_models",
    "quality.observation_report",
    "run_orchestration.quality_observation_stage",
)
REMOVED_PUBLIC_APIS = (
    "GATE_RULESET_VERSION",
    "GateDecision",
    "GateMode",
    "GateResult",
    "GateRuleDecision",
    "QualityReportConfig",
    "QualityReportRequest",
    "QualityReportResult",
    "QualitySummary",
    "REPORT_VERSION",
    "ShadowGateConfig",
    "ShadowGateContext",
    "evaluate_shadow_gate",
    "generate_quality_report",
    "load_quality_report_config",
)
REMOVED_ARTIFACTS = (
    "summary.json",
    "gate-report.json",
    "gate-report.md",
    "p1-observation.json",
    "p1-observation.md",
    "p1-observation-manifest.json",
)


class _OfflineConfig:
    base_url = "https://example.com"
    api_key = "synthetic"
    timeout = 1


def _build_machine_fact_shards(semantic_runtime) -> None:
    response = requests.Response()
    response.status_code = 200
    response.url = "https://example.com/v1/items"
    response._content = json.dumps(
        {"usage": {"prompt_tokens": 2, "completion_tokens": 3}}
    ).encode("utf-8")
    response.headers["Content-Type"] = "application/json"
    client = BaseRequest(config=_OfflineConfig())
    client.session.request = lambda method, url, **kwargs: response  # type: ignore[method-assign]
    client.get(
        "/v1/items",
        _attach_log=False,
        _quality_traffic_role=TrafficRole.WORKLOAD,
    )
    now = datetime(2026, 8, 3, tzinfo=UTC)
    semantic_runtime.p0.record_case(
        CaseResult(
            run_id=semantic_runtime.run_context.run_id,
            execution_id=semantic_runtime.run_context.execution_id,
            worker_id=semantic_runtime.run_context.worker_id,
            case_id=semantic_runtime.case_context.case_id,
            invocation_id=semantic_runtime.case_context.invocation_id,
            nodeid=semantic_runtime.case_context.nodeid,
            param_hash=semantic_runtime.case_context.param_hash,
            phase=CasePhase.CALL,
            raw_status=CaseStatus.PASSED,
            final_status=CaseStatus.PASSED,
            duration_ms=1,
            start_time=now,
            end_time=now + timedelta(milliseconds=1),
        )
    )


def test_removed_report_modules_and_public_apis_stay_absent():
    for module_name in REMOVED_MODULES:
        spec = importlib.util.find_spec(module_name)
        assert spec is None or spec.loader is None
    for api_name in REMOVED_PUBLIC_APIS:
        assert not hasattr(quality, api_name)


def test_removed_report_cli_and_orchestration_entries_stay_absent():
    assert main(["report"]) == 2
    assert main(["p1-report"]) == 2
    assert hasattr(quality_pipeline, "quality_fact_merge_stage")
    assert not hasattr(quality_pipeline, "quality_p0_stage")


def test_jenkins_and_pipeline_summary_do_not_reference_removed_reports():
    jenkins = (PROJECT_ROOT / "Jenkinsfile").read_text(encoding="utf-8")
    renderer_source = Path(renderer.__file__).read_text(encoding="utf-8")
    source_loader = Path(sources.__file__).read_text(encoding="utf-8")
    removed_tokens = (
        "QUALITY_SHADOW_GATE",
        "QUALITY_MIN_REQUEST_SAMPLES",
        "QUALITY_HTTP_5XX_WARN_RATE",
        "QUALITY_TIMEOUT_WARN_RATE",
        "gate-report",
        "p1-observation",
    )

    for token in removed_tokens:
        assert token not in jenkins
        assert token not in renderer_source
    for token in (
        "summary.json",
        "gate-report",
        "p1-observation",
        "quality.report",
        "quality.gate",
        "observation_report",
        "observation_models",
    ):
        assert token not in source_loader


def test_active_docs_only_recommend_the_pipeline_summary():
    active_docs = (
        PROJECT_ROOT / "README.md",
        PROJECT_ROOT / "FRAMEWORK_TEST_SPEC.md",
        PROJECT_ROOT / "JENKINS_MIGRATION_TEMPLATE.md",
        PROJECT_ROOT / ".env.example",
    )
    forbidden = (
        "gate-report",
        "p1-observation",
        "P0 质量门禁报告",
        "P1 观察报告",
        "QUALITY_P1_REPORT_ENABLE",
        "QUALITY_SHADOW_GATE",
        "QUALITY_MIN_REQUEST_SAMPLES",
        "QUALITY_HTTP_5XX_WARN_RATE",
        "QUALITY_TIMEOUT_WARN_RATE",
    )

    for path in active_docs:
        content = path.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in content
    for path in active_docs[:3]:
        assert "pipeline-summary.md" in path.read_text(encoding="utf-8")


def test_offline_finalization_keeps_machine_facts_without_legacy_reports(
    semantic_runtime,
):
    _build_machine_fact_shards(semantic_runtime)
    output_dir = semantic_runtime.output_dir
    quality_pipeline.finalize_quality_run(
        QualityRuntimeConfig(
            enabled=True,
            run_id=semantic_runtime.run_context.run_id,
            execution_id=None,
            output_dir=output_dir,
            semantic_enabled=True,
            metrics_enabled=True,
            flaky_history_enabled=True,
            flaky_database_path=output_dir.parent / "flaky.sqlite3",
            flaky_state_enabled=True,
        ),
        start_time=datetime(2026, 8, 3, tzinfo=UTC),
        expected_execution_ids=(semantic_runtime.run_context.execution_id,),
        expected_case_count=1,
        junit_files=(),
        status=RunStatus.FINISHED,
    )

    for relative_path in REMOVED_ARTIFACTS:
        assert not (output_dir / relative_path).exists()

    retained_artifacts = (
        "run.json",
        "merged/manifest.json",
        "merged/request-metrics.jsonl",
        "semantic/merged/manifest.json",
        "metrics/manifest.json",
        "metrics/run-metrics.json",
        "flaky-import.json",
        "flaky-evaluation.json",
    )
    for relative_path in retained_artifacts:
        assert (output_dir / relative_path).is_file()

    reports = output_dir.parent / "reports"
    shutil.copytree(output_dir, reports / "quality")
    report = generate_pipeline_summary(
        output_dir.parent,
        environment={
            "GENERATE_PIPELINE_SUMMARY": "TRUE",
            "RUN_FRAMEWORK_TESTS": "false",
            "RUN_COLLECT_ONLY": "false",
            "RUN_REAL_SMOKE": "true",
        },
    )
    assert report is not None
    assert (reports / "pipeline-summary.md").is_file()
    for relative_path in REMOVED_ARTIFACTS:
        assert not (reports / "quality" / relative_path).exists()
