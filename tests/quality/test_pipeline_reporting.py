from __future__ import annotations

import json

import pytest

from pipeline_reporting import generate_pipeline_summary
from pipeline_reporting.config import load_pipeline_report_config
from pipeline_reporting.sources import (
    initialize_stage_status_file,
    update_stage_status_file,
)
from util.config_validation import ConfigValidationError


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
    assert "真实 Smoke | 未执行" in markdown
    assert "## 请求质量" not in markdown


def test_report_switch_false_removes_stale_summary_and_skips_generation(tmp_path):
    reports = tmp_path / "reports"
    reports.mkdir()
    target = reports / "pipeline-summary.md"
    target.write_text("stale", encoding="utf-8")

    report = generate_pipeline_summary(
        tmp_path,
        environment=_environment(GENERATE_PIPELINE_SUMMARY="FALSE"),
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
    _write_json(quality / "run.json", {"run_id": run_id})
    _write_json(quality / "summary.json", {"run_id": run_id})
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
    _write_json(
        quality / "metrics" / "run-metrics.json",
        {
            "run_id": run_id,
            "run_metrics": {
                "request_groups": {
                    "retried_group_count": 2,
                    "http_retry_rescue_rate": {"numerator": 1, "sample_size": 2},
                }
            },
            "request_group_buckets": [
                _timing_bucket("POST /v1/images/generations http", "workload", "http", 2, 5000, 7000),
                _timing_bucket("GET /v1/account/balance http", "control", "http", 5, 100, 200),
                _timing_bucket("GET /v1/tasks/{id} polling", "workload", "polling", 20, 250, 500),
                _timing_bucket("POST /v1/chat/completions http", "workload", "http", 3, 2000, 3000),
            ],
        },
    )
    _write_json(
        quality / "flaky-evaluation.json",
        {
            "run_id": run_id,
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
    assert stage_status["真实 Smoke"] == "BLOCKED"
    markdown = (reports / "pipeline-summary.md").read_text(encoding="utf-8")
    assert "0 总计" not in markdown


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
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


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
