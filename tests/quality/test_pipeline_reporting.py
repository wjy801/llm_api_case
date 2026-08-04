from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil

import pytest

from pipeline_reporting import generate_pipeline_summary
from pipeline_reporting.config import load_pipeline_report_config
from pipeline_reporting.sources import (
    initialize_stage_status_file,
    update_stage_status_file,
)
from util.config_validation import ConfigValidationError


BASELINE_FIXTURE = Path(__file__).parent / "fixtures" / "pipeline_report_cleanup"


def test_default_safe_pipeline_generates_summary_without_quality_run(tmp_path):
    reports = tmp_path / "reports"
    reports.mkdir()
    _write_junit(
        reports / "unit-tests.xml",
        (
            ("tests.test_alpha", "test_alpha", "passed", None),
            ("tests.test_beta", "test_beta", "passed", None),
        ),
    )
    (reports / "smoke-collect.txt").write_text(
        "Collected test cases: 41\nParallel pool cases: 15\nSerial pool cases: 26\n",
        encoding="utf-8",
    )
    initialize_stage_status_file(
        reports / "pipeline-stage-status.json",
        framework_tests_enabled=True,
        smoke_collect_enabled=True,
        real_smoke_enabled=False,
    )
    update_stage_status_file(
        reports / "pipeline-stage-status.json",
        stage_name="framework_tests",
        status="PASSED",
    )
    update_stage_status_file(
        reports / "pipeline-stage-status.json",
        stage_name="smoke_collect",
        status="PASSED",
    )
    environment = _environment(
        RUN_FRAMEWORK_TESTS="true",
        RUN_COLLECT_ONLY="true",
        RUN_REAL_SMOKE="false",
    )

    report = generate_pipeline_summary(tmp_path, environment=environment)

    assert report is not None
    assert report.conclusion.value == "PASS"
    assert (reports / "pipeline-summary.md").is_file()
    markdown = (reports / "pipeline-summary.md").read_text(encoding="utf-8")
    assert "本轮按配置执行完成" in markdown
    assert "共收集 41 项；并发池 15 项，串行池 26 项" in markdown
    assert "用例收集 | 通过" in markdown
    assert "接口测试 | 未执行" in markdown
    assert "RUN_COLLECT_ONLY" not in markdown
    assert "RUN_REAL_SMOKE" not in markdown
    assert "SMOKE_TARGET" not in markdown
    assert "Smoke" not in markdown
    assert "## 请求质量" not in markdown


@pytest.mark.parametrize(
    (
        "framework_enabled",
        "collect_enabled",
        "interface_enabled",
        "expected_conclusion",
    ),
    (
        (True, True, False, "PASS"),
        (True, False, False, "PASS"),
        (False, True, False, "PASS"),
        (False, False, True, "WARN"),
        (True, False, True, "WARN"),
        (True, True, True, "WARN"),
        (False, False, False, "WARN"),
    ),
    ids=(
        "framework-collect",
        "framework-only",
        "collect-only",
        "interface-only",
        "framework-interface",
        "all-stages",
        "no-tests-selected",
    ),
)
def test_pipeline_summary_mode_matrix(
    tmp_path,
    framework_enabled,
    collect_enabled,
    interface_enabled,
    expected_conclusion,
):
    reports = _install_mode_artifacts(
        tmp_path,
        framework_enabled=framework_enabled,
        collect_enabled=collect_enabled,
        interface_enabled=interface_enabled,
    )

    report = generate_pipeline_summary(
        tmp_path,
        environment=_environment(
            RUN_FRAMEWORK_TESTS=str(framework_enabled),
            RUN_COLLECT_ONLY=str(collect_enabled),
            RUN_REAL_SMOKE=str(interface_enabled),
        ),
    )

    assert report is not None
    assert report.conclusion.value == expected_conclusion
    stage_statuses = {item.name: item.status.value for item in report.stages}
    assert stage_statuses["框架单测"] == (
        "PASSED" if framework_enabled else "NOT_RUN"
    )
    assert stage_statuses["用例收集"] == (
        "PASSED" if collect_enabled else "NOT_RUN"
    )
    assert stage_statuses["接口测试"] == (
        "PASSED" if interface_enabled else "NOT_RUN"
    )
    assert stage_statuses["质量观测"] == (
        "PASSED" if interface_enabled else "NOT_RUN"
    )
    markdown = (reports / "pipeline-summary.md").read_text(encoding="utf-8")
    metric_sections = (
        "## 请求质量",
        "## 重试效果",
        "## 接口耗时 Top 5",
        "## Flaky 状态迁移",
    )
    for section in metric_sections:
        assert (section in markdown) is interface_enabled
    if not any((framework_enabled, collect_enabled, interface_enabled)):
        assert "本轮未执行测试验证" in markdown


def test_report_switch_false_removes_stale_summary_and_skips_generation(tmp_path):
    reports = tmp_path / "reports"
    reports.mkdir()
    target = reports / "pipeline-summary.md"
    target.write_text("stale", encoding="utf-8")

    report = generate_pipeline_summary(
        tmp_path,
        environment=_environment(
            GENERATE_PIPELINE_SUMMARY="FALSE",
            RUN_FRAMEWORK_TESTS="true",
            RUN_REAL_SMOKE="true",
        ),
    )

    assert report is None
    assert not target.exists()


def test_report_config_prefers_process_environment_over_dotenv(tmp_path):
    dotenv = tmp_path / ".env"
    dotenv.write_text("GENERATE_PIPELINE_SUMMARY=FALSE\n", encoding="utf-8")

    config = load_pipeline_report_config(
        {"GENERATE_PIPELINE_SUMMARY": "TRUE"},
        dotenv_path=dotenv,
    )

    assert config.enabled is True


def test_invalid_report_switch_is_rejected(tmp_path):
    with pytest.raises(ConfigValidationError, match="GENERATE_PIPELINE_SUMMARY"):
        load_pipeline_report_config(
            {"GENERATE_PIPELINE_SUMMARY": "sometimes"},
            dotenv_path=tmp_path / ".env",
        )


def test_real_smoke_summary_uses_direct_request_retry_timing_and_flaky_metrics(tmp_path):
    reports = tmp_path / "reports"
    quality = reports / "quality"
    (quality / "merged").mkdir(parents=True)
    (quality / "metrics").mkdir()
    _write_junit(
        reports / "smoke-tests.xml",
        (("module.smoke.test_demo", "test_demo", "passed", None),),
        quality_identity=True,
    )
    initialize_stage_status_file(
        reports / "pipeline-stage-status.json",
        framework_tests_enabled=False,
        smoke_collect_enabled=False,
        real_smoke_enabled=True,
    )
    update_stage_status_file(
        reports / "pipeline-stage-status.json",
        stage_name="real_smoke",
        status="PASSED",
    )
    run_id = "job-77-run"
    request_records = (
        {"run_id": run_id, "status_code": 200, "timeout": False, "error_type": None},
        {"run_id": run_id, "status_code": 400, "timeout": False, "error_type": None},
        {"run_id": run_id, "status_code": 429, "timeout": False, "error_type": None},
        {"run_id": run_id, "status_code": 503, "timeout": False, "error_type": None},
        {"run_id": run_id, "status_code": None, "timeout": True, "error_type": "Timeout"},
    )
    (quality / "merged" / "request-metrics.jsonl").write_text(
        "".join(json.dumps(item) + "\n" for item in request_records),
        encoding="utf-8",
    )
    _write_quality_identity(quality, run_id)
    _write_metrics_artifacts(
        quality,
        run_id,
        [
            _timing_bucket("POST /v1/images/generations http", "workload", "http", 2, 5000, 7000),
            _timing_bucket("GET /v1/account/balance http", "control", "http", 5, 100, 200),
            _timing_bucket("GET /v1/tasks/{id} polling", "workload", "polling", 20, 250, 500),
            _timing_bucket("POST /v1/chat/completions http", "workload", "http", 3, 2000, 3000),
        ],
    )
    _write_json(
        quality / "flaky-evaluation.json",
        {
            "schema_version": "quality.flaky-evaluation.v1",
            "run_id": run_id,
            "status": "EVALUATED",
            "newly_suspected": [{"case_id": "module/test_demo.py::test_demo"}],
            "newly_confirmed": [],
            "recovered": [],
            "overdue": [],
            "transitions": [
                {
                    "case_id": "module/test_demo.py::test_demo",
                    "from_state": "STABLE",
                    "to_state": "SUSPECTED",
                }
            ],
        },
    )

    report = generate_pipeline_summary(
        tmp_path,
        environment=_environment(
            RUN_FRAMEWORK_TESTS="false",
            RUN_COLLECT_ONLY="false",
            RUN_REAL_SMOKE="true",
        ),
    )

    assert report is not None
    assert report.conclusion.value == "WARN"
    assert report.request_health.total == 5
    assert report.request_health.success_count == 2
    assert report.retry_health.retried_group_count == 2
    assert report.retry_health.rescued_group_count == 1
    assert [item.interface_id for item in report.interface_timings] == [
        "POST /v1/images/generations http",
        "POST /v1/chat/completions http",
    ]
    markdown = (reports / "pipeline-summary.md").read_text(encoding="utf-8")
    assert "40.00%" in markdown
    assert "50.00%" in markdown
    assert "GET /v1/account/balance" not in markdown
    assert "GET /v1/tasks/{id}" not in markdown
    assert "STABLE -> SUSPECTED" in markdown


def test_pipeline_report_cleanup_fixture_freezes_current_summary(tmp_path):
    reports = tmp_path / "reports"
    quality = reports / "quality"
    quality.mkdir(parents=True)
    for name in ("run.json", "flaky-evaluation.json"):
        shutil.copy2(BASELINE_FIXTURE / name, quality / name)
    shutil.copytree(BASELINE_FIXTURE / "merged", quality / "merged")
    shutil.copytree(BASELINE_FIXTURE / "metrics", quality / "metrics")
    shutil.copy2(BASELINE_FIXTURE / "interface-tests.xml", reports / "smoke-tests.xml")
    shutil.copy2(
        BASELINE_FIXTURE / "pipeline-stage-status.json",
        reports / "pipeline-stage-status.json",
    )

    report = generate_pipeline_summary(
        tmp_path,
        environment=_environment(
            BUILD_NUMBER="88",
            PIPELINE_DURATION_MS="120000",
            GIT_COMMIT="baseline1234567890",
            RUN_FRAMEWORK_TESTS="false",
            RUN_COLLECT_ONLY="false",
            RUN_REAL_SMOKE="true",
            SMOKE_TARGET="module/interface",
            TEST_PARALLEL_WORKERS="2",
        ),
    )

    assert report is not None
    markdown = (reports / "pipeline-summary.md").read_text(encoding="utf-8")
    assert "运行身份与归并事实完整" in markdown
    assert "P0 运行身份与汇总完整" not in markdown
    assert "gate-report" not in markdown
    assert "P0 影子门禁" not in markdown
    assert "## 详细证据" not in markdown
    assert report.request_health.success_count == 2
    assert report.retry_health.rescued_group_count == 1
    assert [item.interface_id for item in report.interface_timings] == [
        "POST /v1/images/generations http",
        "POST /v1/chat/completions http",
    ]
    assert "STABLE -> SUSPECTED" in markdown


@pytest.mark.parametrize(
    ("field", "value", "warning"),
    (
        ("run_id", "foreign-run", "run_id 不一致"),
        ("status", "merging", "尚未完整提交"),
        ("schema_version", "quality.v999", "Schema 不受支持"),
    ),
)
def test_pipeline_summary_rejects_invalid_quality_manifest(
    tmp_path,
    field,
    value,
    warning,
):
    reports = _install_cleanup_fixture(tmp_path)
    manifest_path = reports / "quality" / "merged" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest[field] = value
    _write_json(manifest_path, manifest)

    report = generate_pipeline_summary(
        tmp_path,
        environment=_environment(RUN_REAL_SMOKE="true"),
    )

    assert report is not None
    assert any(warning in item for item in report.warnings)
    stage_status = {item.name: item.status.value for item in report.stages}
    assert stage_status["质量观测"] == "NO_DATA"


@pytest.mark.parametrize(
    "relative_path",
    ("run.json", "merged/manifest.json"),
    ids=("run-missing", "manifest-missing"),
)
def test_pipeline_summary_rejects_missing_core_quality_facts(tmp_path, relative_path):
    reports = _install_cleanup_fixture(tmp_path)
    (reports / "quality" / relative_path).unlink()

    report = generate_pipeline_summary(
        tmp_path,
        environment=_environment(RUN_REAL_SMOKE="true"),
    )

    assert report is not None
    stage_status = {item.name: item.status.value for item in report.stages}
    assert stage_status["质量观测"] == "NO_DATA"
    assert report.request_health.available is False
    assert report.retry_health.available is False
    assert report.flaky.available is False


def test_pipeline_summary_degrades_only_request_section_on_hash_mismatch(tmp_path):
    reports = _install_cleanup_fixture(tmp_path)
    request_path = reports / "quality" / "merged" / "request-metrics.jsonl"
    request_path.write_text(
        request_path.read_text(encoding="utf-8") + "{}\n",
        encoding="utf-8",
    )

    report = generate_pipeline_summary(
        tmp_path,
        environment=_environment(RUN_REAL_SMOKE="true"),
    )

    assert report is not None
    assert report.request_health.available is False
    assert report.retry_health.available is True
    assert report.flaky.available is True
    assert any("请求指标哈希与清单不一致" in item for item in report.warnings)
    stage_status = {item.name: item.status.value for item in report.stages}
    assert stage_status["质量观测"] == "PASSED"


@pytest.mark.parametrize("mutation", ("run_id", "hash"))
def test_pipeline_summary_rejects_untrusted_metrics(tmp_path, mutation):
    reports = _install_cleanup_fixture(tmp_path)
    metrics_dir = reports / "quality" / "metrics"
    if mutation == "run_id":
        manifest_path = metrics_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["run_id"] = "foreign-run"
        _write_json(manifest_path, manifest)
    else:
        metrics_path = metrics_dir / "run-metrics.json"
        metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
        metrics["extra"] = "tampered"
        _write_json(metrics_path, metrics)

    report = generate_pipeline_summary(
        tmp_path,
        environment=_environment(RUN_REAL_SMOKE="true"),
    )

    assert report is not None
    assert report.request_health.available is True
    assert report.retry_health.available is False
    assert not report.interface_timings
    assert report.flaky.available is True


def test_pipeline_summary_degrades_only_metrics_when_manifest_is_missing(tmp_path):
    reports = _install_cleanup_fixture(tmp_path)
    (reports / "quality" / "metrics" / "manifest.json").unlink()

    report = generate_pipeline_summary(
        tmp_path,
        environment=_environment(RUN_REAL_SMOKE="true"),
    )

    assert report is not None
    assert report.request_health.available is True
    assert report.retry_health.available is False
    assert not report.interface_timings
    assert report.flaky.available is True


@pytest.mark.parametrize("mutation", ("run_id", "schema_version", "status"))
def test_pipeline_summary_rejects_untrusted_flaky_evaluation(tmp_path, mutation):
    reports = _install_cleanup_fixture(tmp_path)
    flaky_path = reports / "quality" / "flaky-evaluation.json"
    flaky = json.loads(flaky_path.read_text(encoding="utf-8"))
    flaky[mutation] = {
        "run_id": "foreign-run",
        "schema_version": "quality.flaky-evaluation.v999",
        "status": "FAILED",
    }[mutation]
    _write_json(flaky_path, flaky)

    report = generate_pipeline_summary(
        tmp_path,
        environment=_environment(RUN_REAL_SMOKE="true"),
    )

    assert report is not None
    assert report.request_health.available is True
    assert report.retry_health.available is True
    assert report.flaky.available is False


def test_pipeline_summary_treats_missing_flaky_as_not_enabled(tmp_path):
    reports = _install_cleanup_fixture(tmp_path)
    (reports / "quality" / "flaky-evaluation.json").unlink()

    report = generate_pipeline_summary(
        tmp_path,
        environment=_environment(RUN_REAL_SMOKE="true"),
    )

    assert report is not None
    assert report.request_health.available is True
    assert report.retry_health.available is True
    assert report.flaky.available is False
    markdown = (reports / "pipeline-summary.md").read_text(encoding="utf-8")
    assert "本轮未启用或未生成 Flaky 评估" in markdown


def test_pipeline_summary_reports_enabled_flaky_without_transitions(tmp_path):
    reports = _install_cleanup_fixture(tmp_path)
    flaky_path = reports / "quality" / "flaky-evaluation.json"
    flaky = json.loads(flaky_path.read_text(encoding="utf-8"))
    for field in (
        "newly_suspected",
        "newly_confirmed",
        "recovered",
        "newly_quarantined",
        "overdue",
        "transitions",
    ):
        flaky[field] = []
    _write_json(flaky_path, flaky)

    report = generate_pipeline_summary(
        tmp_path,
        environment=_environment(RUN_REAL_SMOKE="true"),
    )

    assert report is not None
    assert report.flaky.available is True
    assert report.flaky.transition_count == 0
    assert report.flaky.actionable_count == 0
    markdown = (reports / "pipeline-summary.md").read_text(encoding="utf-8")
    assert "本轮无 Flaky 状态迁移" in markdown


def test_pipeline_summary_does_not_read_stale_quality_when_interface_tests_disabled(tmp_path):
    reports = tmp_path / "reports"
    quality = reports / "quality"
    quality.mkdir(parents=True)
    (quality / "run.json").write_text("not-json", encoding="utf-8")

    report = generate_pipeline_summary(
        tmp_path,
        environment=_environment(RUN_REAL_SMOKE="false"),
    )

    assert report is not None
    assert not report.warnings


def test_selected_stage_without_artifact_is_not_reported_as_zero_tests(tmp_path):
    reports = tmp_path / "reports"
    reports.mkdir()
    initialize_stage_status_file(
        reports / "pipeline-stage-status.json",
        framework_tests_enabled=True,
        smoke_collect_enabled=False,
        real_smoke_enabled=True,
    )
    update_stage_status_file(
        reports / "pipeline-stage-status.json",
        stage_name="framework_tests",
        status="FAILED",
    )

    report = generate_pipeline_summary(
        tmp_path,
        environment=_environment(
            PIPELINE_BUILD_RESULT="FAILURE",
            RUN_FRAMEWORK_TESTS="true",
            RUN_COLLECT_ONLY="false",
            RUN_REAL_SMOKE="true",
        ),
    )

    assert report is not None
    assert report.conclusion.value == "FAIL"
    stage_status = {item.name: item.status.value for item in report.stages}
    assert stage_status["框架单测"] == "FAILED"
    assert stage_status["接口测试"] == "BLOCKED"
    markdown = (reports / "pipeline-summary.md").read_text(encoding="utf-8")
    assert "0 总计" not in markdown


def test_explicit_stage_failure_has_priority_over_passing_junit(tmp_path):
    reports = tmp_path / "reports"
    reports.mkdir()
    _write_junit(
        reports / "smoke-tests.xml",
        [("module.test_demo", "test_ok", "passed", None)],
    )
    initialize_stage_status_file(
        reports / "pipeline-stage-status.json",
        framework_tests_enabled=False,
        smoke_collect_enabled=False,
        real_smoke_enabled=True,
    )
    update_stage_status_file(
        reports / "pipeline-stage-status.json",
        stage_name="real_smoke",
        status="FAILED",
    )

    report = generate_pipeline_summary(
        tmp_path,
        environment=_environment(RUN_REAL_SMOKE="true"),
    )

    assert report is not None
    interface_stage = next(item for item in report.stages if item.name == "接口测试")
    assert interface_stage.status.value == "FAILED"
    assert report.conclusion.value == "FAIL"


def test_runner_exit_fact_has_priority_over_passing_junit(tmp_path):
    reports = tmp_path / "reports"
    reports.mkdir()
    _write_junit(
        reports / "smoke-tests.xml",
        [("module.test_demo", "test_ok", "passed", None)],
    )
    _write_json(
        reports / "execution-result.json",
        {
            "schema_version": "runner-execution.v1",
            "test_target": "module/smoke",
            "selection_args": [],
            "planned_case_count": 1,
            "planned_nodeids": ["module/test_demo.py::test_ok"],
            "collection_exit_code": 0,
            "pool_results": [
                {
                    "stage_id": "serial-pool",
                    "planned_nodeids": ["module/test_demo.py::test_ok"],
                    "status": "COMPLETED",
                    "raw_pytest_exit_code": 4,
                    "started_at": "2026-08-04T00:00:00+00:00",
                    "completed_at": "2026-08-04T00:00:01+00:00",
                    "exception_type": None,
                    "junit_path": "reports/smoke-tests.xml",
                }
            ],
            "final_exit_code": 4,
        },
    )

    report = generate_pipeline_summary(
        tmp_path,
        environment=_environment(RUN_REAL_SMOKE="true"),
    )

    assert report is not None
    assert report.execution.available is True
    interface_stage = next(item for item in report.stages if item.name == "接口测试")
    assert interface_stage.status.value == "FAILED"
    assert "退出码 4" in interface_stage.summary


def test_markdown_machine_summary_and_email_share_one_report(tmp_path):
    reports = tmp_path / "reports"
    reports.mkdir()
    _write_junit(
        reports / "unit-tests.xml",
        [("tests.test_demo", "test_ok", "passed", None)],
    )

    report = generate_pipeline_summary(
        tmp_path,
        environment=_environment(RUN_FRAMEWORK_TESTS="true"),
        machine_output_path="reports/pipeline-summary.json",
        email_subject_path="reports/pipeline-email-subject.txt",
        email_html_path="reports/pipeline-email.html",
    )

    assert report is not None
    payload = json.loads(
        (reports / "pipeline-summary.json").read_text(encoding="utf-8")
    )
    assert payload["conclusion"] == report.conclusion.value
    assert payload["unit_tests"]["total"] == report.unit_tests.total == 1
    assert "0 失败 / 1 项" in (
        reports / "pipeline-email-subject.txt"
    ).read_text(encoding="utf-8")
    assert "1 总计 / 1 通过 / 0 失败 / 0 跳过" in (
        reports / "pipeline-email.html"
    ).read_text(encoding="utf-8")


def _environment(**updates: str) -> dict[str, str]:
    values = {
        "GENERATE_PIPELINE_SUMMARY": "TRUE",
        "JOB_NAME": "llm-api-case",
        "BUILD_NUMBER": "77",
        "PIPELINE_BUILD_RESULT": "SUCCESS",
        "PIPELINE_DURATION_MS": "975000",
        "GIT_BRANCH": "origin/dev3",
        "GIT_COMMIT": "3361c4dff5a9c1ed",
        "USE_CHINA_ENVIRONMENT": "TRUE",
        "RUN_FRAMEWORK_TESTS": "false",
        "RUN_COLLECT_ONLY": "false",
        "RUN_REAL_SMOKE": "false",
        "SMOKE_TARGET": "module/smoke",
        "TEST_PARALLEL_WORKERS": "off",
    }
    values.update(updates)
    return values


def _write_junit(path, cases, *, quality_identity: bool = False):
    testcase_xml = []
    for index, (classname, name, status, message) in enumerate(cases, start=1):
        properties = ""
        if quality_identity:
            properties = (
                "<properties>"
                f"<property name='quality_case_id' value='{classname}::{name}' />"
                f"<property name='quality_invocation_id' value='inv-{index}' />"
                "</properties>"
            )
        evidence = ""
        if status == "failed":
            evidence = f"<failure message='{message or 'failed'}' />"
        elif status == "error":
            evidence = f"<error message='{message or 'error'}' />"
        elif status == "skipped":
            evidence = f"<skipped message='{message or 'skipped'}' />"
        testcase_xml.append(
            f"<testcase classname='{classname}' name='{name}' time='0.1'>{properties}{evidence}</testcase>"
        )
    path.write_text(
        "<testsuites><testsuite>" + "".join(testcase_xml) + "</testsuite></testsuites>",
        encoding="utf-8",
    )


def _write_json(path, value):
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _write_quality_identity(quality, run_id):
    _write_json(
        quality / "run.json",
        {
            "schema_version": "quality.v1",
            "run_id": run_id,
        },
    )
    request_path = quality / "merged" / "request-metrics.jsonl"
    _write_json(
        quality / "merged" / "manifest.json",
        {
            "manifest_version": "quality.merge.v1",
            "schema_version": "quality.v1",
            "run_id": run_id,
            "status": "complete",
            "integrity_status": "complete",
            "output_hashes": {"request-metrics": _sha256(request_path)},
        },
    )


def _write_metrics_artifacts(quality, run_id, buckets):
    metrics_path = quality / "metrics" / "run-metrics.json"
    _write_json(
        metrics_path,
        {
            "schema_version": "quality.run-metrics.v1",
            "aggregation_version": "p1-run-metrics.v1",
            "run_id": run_id,
            "status": "aggregated",
            "run_metrics": {
                "request_groups": {
                    "retried_group_count": 2,
                    "http_retry_rescue_rate": {"numerator": 1, "sample_size": 2},
                }
            },
            "request_group_buckets": buckets,
        },
    )
    _write_json(
        quality / "metrics" / "manifest.json",
        {
            "manifest_version": "quality.run-metrics-manifest.v1",
            "schema_version": "quality.run-metrics.v1",
            "aggregation_version": "p1-run-metrics.v1",
            "run_id": run_id,
            "write_status": "complete",
            "metrics_status": "aggregated",
            "output_hashes": {"run_metrics": _sha256(metrics_path)},
        },
    )


def _install_cleanup_fixture(tmp_path):
    reports = tmp_path / "reports"
    quality = reports / "quality"
    quality.mkdir(parents=True)
    for name in ("run.json", "flaky-evaluation.json"):
        shutil.copy2(BASELINE_FIXTURE / name, quality / name)
    shutil.copytree(BASELINE_FIXTURE / "merged", quality / "merged")
    shutil.copytree(BASELINE_FIXTURE / "metrics", quality / "metrics")
    shutil.copy2(BASELINE_FIXTURE / "interface-tests.xml", reports / "smoke-tests.xml")
    shutil.copy2(
        BASELINE_FIXTURE / "pipeline-stage-status.json",
        reports / "pipeline-stage-status.json",
    )
    return reports


def _install_mode_artifacts(
    tmp_path,
    *,
    framework_enabled,
    collect_enabled,
    interface_enabled,
):
    if interface_enabled:
        reports = _install_cleanup_fixture(tmp_path)
    else:
        reports = tmp_path / "reports"
        reports.mkdir()
    if framework_enabled:
        _write_junit(
            reports / "unit-tests.xml",
            (("tests.test_pipeline_mode", "test_framework", "passed", None),),
        )
    if collect_enabled:
        (reports / "smoke-collect.txt").write_text(
            "Collected test cases: 41\nParallel pool cases: 15\nSerial pool cases: 26\n",
            encoding="utf-8",
        )
    stage_path = reports / "pipeline-stage-status.json"
    initialize_stage_status_file(
        stage_path,
        framework_tests_enabled=framework_enabled,
        smoke_collect_enabled=collect_enabled,
        real_smoke_enabled=interface_enabled,
    )
    for stage_name, enabled in (
        ("framework_tests", framework_enabled),
        ("smoke_collect", collect_enabled),
        ("real_smoke", interface_enabled),
    ):
        if enabled:
            update_stage_status_file(
                stage_path,
                stage_name=stage_name,
                status="PASSED",
            )
    return reports


def _sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _timing_bucket(interface_id, role, protocol, count, mean, maximum):
    return {
        "dimension": {
            "interface_id": interface_id,
            "traffic_role": role,
            "protocol": protocol,
        },
        "stability": {"group_count": count},
        "timing": {
            "total_duration_ms": {
                "mean": mean,
                "maximum": maximum,
            }
        },
    }
