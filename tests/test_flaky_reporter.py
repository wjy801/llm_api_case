from __future__ import annotations

import json

import pytest

from governance.flaky_models import AttemptOutcome, AttemptResult, FlakyStatus, FlakyTestResult
from governance.flaky_reporter import build_summary, redact_failure_message, write_flaky_reports


pytestmark = pytest.mark.flaky_governance


class TestBuildSummary:
    def test_counts_statuses_and_rates(self):
        results = [
            FlakyTestResult("test_a", FlakyStatus.PASSED),
            FlakyTestResult("test_b", FlakyStatus.RETRY_PASSED),
            FlakyTestResult("test_c", FlakyStatus.RETRY_FAILED),
            FlakyTestResult("test_d", FlakyStatus.FAILED),
        ]

        summary = build_summary(results)

        assert summary == {
            "total": 4,
            "passed": 1,
            "retry_passed": 1,
            "retry_failed": 1,
            "failed": 1,
            "first_pass_rate": 0.25,
            "final_success_rate": 0.5,
            "retry_recovery_rate": 0.5,
        }

    def test_handles_empty_results(self):
        summary = build_summary([])

        assert summary["total"] == 0
        assert summary["first_pass_rate"] == 0.0
        assert summary["final_success_rate"] == 0.0
        assert summary["retry_recovery_rate"] == 0.0


class TestRedactFailureMessage:
    def test_redacts_sensitive_values(self):
        message = "Authorization: Bearer secret-token api_key=secret password=secret"

        redacted = redact_failure_message(message)

        assert redacted == "Authorization: Bearer <redacted> api_key=<redacted> password=<redacted>"


class TestWriteFlakyReports:
    def test_writes_results_summary_and_text_report(self, tmp_path):
        result = FlakyTestResult(
            nodeid="module/test_demo.py::TestDemo::test_case",
            status=FlakyStatus.FAILED,
            attempts=(
                AttemptResult(
                    index=1,
                    outcome=AttemptOutcome.FAILED,
                    duration=0.1,
                    failure_type="AssertionError",
                    failure_message="assert False",
                ),
            ),
            total_duration=0.1,
        )

        write_flaky_reports(tmp_path, [result])

        results = json.loads((tmp_path / "flaky-results.json").read_text(encoding="utf-8"))
        summary = json.loads((tmp_path / "flaky-summary.json").read_text(encoding="utf-8"))
        summary_text = (tmp_path / "flaky-summary.txt").read_text(encoding="utf-8")

        assert results["results"][0]["nodeid"] == "module/test_demo.py::TestDemo::test_case"
        assert results["results"][0]["attempts"][0]["failure_type"] == "AssertionError"
        assert summary["failed"] == 1
        assert "失败：1" in summary_text

    def test_writes_allure_environment_summary(self, tmp_path):
        allure_results_dir = tmp_path / "allure-results"
        result = FlakyTestResult("module/test_demo.py::test_case", FlakyStatus.RETRY_PASSED)

        write_flaky_reports(tmp_path / "reports", [result], allure_results_dir=allure_results_dir)

        environment_text = (allure_results_dir / "environment.properties").read_text(encoding="utf-8")
        assert "flaky.total=1" in environment_text
        assert "flaky.retry_passed=1" in environment_text
        assert "flaky.final_success_rate=100.00%" in environment_text

    def test_enriches_allure_result_with_flaky_parameters(self, tmp_path):
        allure_results_dir = tmp_path / "allure-results"
        allure_results_dir.mkdir()
        result_path = allure_results_dir / "case-result.json"
        result_path.write_text(
            json.dumps(
                {
                    "name": "test_case",
                    "fullName": "module.test_demo.TestDemo#test_case",
                    "parameters": [{"name": "existing", "value": "kept"}],
                }
            ),
            encoding="utf-8",
        )
        result = FlakyTestResult(
            nodeid="module/test_demo.py::TestDemo::test_case",
            status=FlakyStatus.RETRY_FAILED,
            attempts=(
                AttemptResult(
                    index=1,
                    outcome=AttemptOutcome.FAILED,
                    duration=0.1,
                    failure_type="ReadTimeout",
                ),
                AttemptResult(index=2, outcome=AttemptOutcome.FAILED, duration=0.2),
            ),
            total_duration=0.3,
        )

        write_flaky_reports(tmp_path / "reports", [result], allure_results_dir=allure_results_dir)

        payload = json.loads(result_path.read_text(encoding="utf-8"))
        parameters = {parameter["name"]: parameter["value"] for parameter in payload["parameters"]}
        assert parameters["existing"] == "kept"
        assert parameters["flaky_status"] == "retry_failed"
        assert parameters["flaky_attempt_count"] == "2"
        assert parameters["flaky_first_outcome"] == "failed"
        assert parameters["flaky_first_failure_type"] == "ReadTimeout"
