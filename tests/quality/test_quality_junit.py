from __future__ import annotations

from quality.junit import parse_junit_file
from quality.models import CaseStatus


def test_parse_junit_file_extracts_quality_identity_and_failure_evidence(tmp_path):
    junit = tmp_path / "quality.xml"
    junit.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
        <testsuite>
          <testcase classname="module.test_demo" name="test_failed" time="0.1">
            <properties>
              <property name="quality_case_id" value="module/test_demo.py::test_failed" />
              <property name="quality_invocation_id" value="inv-1" />
            </properties>
            <failure type="AssertionError" message="AssertionError: expected 200 got 500">
              module/test_demo.py:12: AssertionError token=secret
            </failure>
          </testcase>
        </testsuite>
        """,
        encoding="utf-8",
    )

    evidence = parse_junit_file(junit)[0]

    assert evidence.status is CaseStatus.FAILED
    assert evidence.case_id == "module/test_demo.py::test_failed"
    assert evidence.invocation_id == "inv-1"
    assert evidence.error_type == "AssertionError"
    assert evidence.assert_location == "module/test_demo.py:12"
    assert "secret" not in (evidence.message or "")


def test_parse_junit_file_marks_missing_identity_as_none(tmp_path):
    junit = tmp_path / "quality.xml"
    junit.write_text("<testsuite><testcase classname='c' name='n' /></testsuite>", encoding="utf-8")

    evidence = parse_junit_file(junit)[0]

    assert evidence.status is CaseStatus.PASSED
    assert evidence.case_id is None
    assert evidence.invocation_id is None
