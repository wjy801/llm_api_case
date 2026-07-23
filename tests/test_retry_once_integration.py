from __future__ import annotations

import json

import pytest


pytestmark = pytest.mark.flaky_governance


class TestRetryOnceIntegration:
    def test_retry_once_records_retry_passed(self, pytester: pytest.Pytester):
        pytester.makeconftest(
            """
            pytest_plugins = ["governance.flaky_plugin"]
            """
        )
        pytester.makepyfile(
            test_retry="""
            from common.markers import retry_once

            state = {"count": 0}

            @retry_once
            def test_passes_on_retry():
                state["count"] += 1
                assert state["count"] == 2
            """
        )

        result = pytester.runpytest("-q")

        result.assert_outcomes(passed=1)
        result.stdout.fnmatch_lines(["*1 passed, 1 rerun*"])
        summary = json.loads((pytester.path / "reports" / "flaky" / "current" / "flaky-summary.json").read_text(encoding="utf-8"))
        assert summary["retry_passed"] == 1
        assert summary["passed"] == 0

    def test_retry_once_records_retry_failed(self, pytester: pytest.Pytester):
        pytester.makeconftest(
            """
            pytest_plugins = ["governance.flaky_plugin"]
            """
        )
        pytester.makepyfile(
            test_retry="""
            from common.markers import retry_once

            @retry_once
            def test_still_fails_after_retry():
                assert False
            """
        )

        result = pytester.runpytest("-q")

        result.assert_outcomes(failed=1)
        result.stdout.fnmatch_lines(["*1 failed, 1 rerun*"])
        summary = json.loads((pytester.path / "reports" / "flaky" / "current" / "flaky-summary.json").read_text(encoding="utf-8"))
        assert summary["retry_failed"] == 1
        assert summary["failed"] == 0
