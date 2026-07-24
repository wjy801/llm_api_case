from __future__ import annotations

from types import SimpleNamespace

import pytest

from governance.flaky_models import AttemptOutcome, FlakyStatus
from governance.flaky_plugin import FlakyAttemptStore, STORE_KEY, pytest_configure, pytest_runtest_logreport


pytestmark = pytest.mark.flaky_governance


def fake_report(
    nodeid: str,
    when: str,
    outcome: str,
    *,
    duration: float = 0.01,
    message: str | None = None,
    rerun: int | None = None,
):
    longrepr = None
    if outcome in {"failed", "rerun"}:
        longrepr = SimpleNamespace(reprcrash=SimpleNamespace(message=message or "AssertionError: assert False"))
        longrepr.__str__ = lambda self: message or "AssertionError: assert False"

    report = SimpleNamespace(
        nodeid=nodeid,
        when=when,
        outcome=outcome,
        duration=duration,
        longrepr=longrepr,
    )
    if rerun is not None:
        report.rerun = rerun
    return report


class TestFlakyAttemptStore:
    def test_all_stages_passed_creates_passed_result(self):
        store = FlakyAttemptStore()
        nodeid = "module/test_demo.py::TestDemo::test_pass"

        store.add_report(fake_report(nodeid, "setup", "passed"))
        store.add_report(fake_report(nodeid, "call", "passed"))
        store.add_report(fake_report(nodeid, "teardown", "passed"))

        results = store.results()

        assert len(results) == 1
        assert results[0].status == FlakyStatus.PASSED
        assert results[0].attempts[0].outcome == AttemptOutcome.PASSED

    @pytest.mark.parametrize("failed_stage", ["setup", "call", "teardown"])
    def test_any_failed_stage_creates_failed_result(self, failed_stage: str):
        store = FlakyAttemptStore()
        nodeid = f"module/test_demo.py::TestDemo::test_{failed_stage}_failed"

        for stage in ("setup", "call", "teardown"):
            outcome = "failed" if stage == failed_stage else "passed"
            store.add_report(fake_report(nodeid, stage, outcome, message="AssertionError: token=secret-token"))

        result = store.results()[0]

        assert result.status == FlakyStatus.FAILED
        assert result.attempts[0].outcome == AttemptOutcome.FAILED
        assert result.attempts[0].failure_message is not None
        assert "token=<redacted>" in result.attempts[0].failure_message

    def test_skipped_reports_are_ignored(self):
        store = FlakyAttemptStore()

        store.add_report(fake_report("module/test_demo.py::test_skipped", "setup", "skipped"))

        assert store.results() == []

    def test_multiple_attempts_are_classified_as_retry_passed(self):
        store = FlakyAttemptStore()
        nodeid = "module/test_demo.py::TestDemo::test_retry"

        store.add_report(fake_report(nodeid, "setup", "passed", rerun=0))
        store.add_report(fake_report(nodeid, "call", "rerun", rerun=0))
        store.add_report(fake_report(nodeid, "setup", "passed", rerun=1))
        store.add_report(fake_report(nodeid, "call", "passed", rerun=1))
        store.add_report(fake_report(nodeid, "teardown", "passed", rerun=1))

        result = store.results()[0]

        assert result.status == FlakyStatus.RETRY_PASSED
        assert result.attempt_count == 2

    def test_rerun_then_failed_is_classified_as_retry_failed(self):
        store = FlakyAttemptStore()
        nodeid = "module/test_demo.py::TestDemo::test_retry_failed"

        store.add_report(fake_report(nodeid, "setup", "passed", rerun=0))
        store.add_report(fake_report(nodeid, "call", "rerun", rerun=0))
        store.add_report(fake_report(nodeid, "setup", "passed", rerun=1))
        store.add_report(fake_report(nodeid, "call", "failed", rerun=1))
        store.add_report(fake_report(nodeid, "teardown", "passed", rerun=1))

        result = store.results()[0]

        assert result.status == FlakyStatus.RETRY_FAILED
        assert result.attempt_count == 2


class TestPytestLogreportHook:
    def test_logreport_hook_adds_reports_to_config_store(self):
        config = SimpleNamespace(stash={})
        nodeid = "module/test_demo.py::TestDemo::test_hook"

        pytest_configure(config)
        pytest_runtest_logreport(fake_report(nodeid, "setup", "passed"))
        pytest_runtest_logreport(fake_report(nodeid, "call", "passed"))
        pytest_runtest_logreport(fake_report(nodeid, "teardown", "passed"))

        results = config.stash[STORE_KEY].results()

        assert results[0].status == FlakyStatus.PASSED
