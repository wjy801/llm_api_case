from __future__ import annotations

import os

import pytest

import run_master
from master_service import CollectedTestCase


QUALITY_ENV_NAMES = (
    "QUALITY_ENABLE",
    "QUALITY_RUN_ID",
    "QUALITY_EXECUTION_ID",
    "QUALITY_OUTPUT_DIR",
    "QUALITY_SHADOW_GATE",
    "QUALITY_MIN_REQUEST_SAMPLES",
    "QUALITY_HTTP_5XX_WARN_RATE",
    "QUALITY_TIMEOUT_WARN_RATE",
)


@pytest.fixture(autouse=True)
def clear_quality_environment(monkeypatch):
    for name in QUALITY_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("JOB_NAME", raising=False)
    monkeypatch.delenv("BUILD_NUMBER", raising=False)


def _case(nodeid, *markers):
    return CollectedTestCase(nodeid=nodeid, markers=frozenset(markers))


def _capture_pytest_environment(monkeypatch):
    calls = []

    def fake_main(args):
        calls.append(
            {
                "args": list(args),
                "quality": {name: os.environ.get(name) for name in QUALITY_ENV_NAMES},
            }
        )
        return 0

    monkeypatch.setattr(run_master.pytest, "main", fake_main)
    return calls


def _capture_quality_finalization(monkeypatch):
    merges = []
    writes = []
    reports = []

    def fake_merge(request):
        merges.append(request)
        return type(
            "MergeResult",
            (),
            {
                "integrity_status": run_master.IntegrityStatus.COMPLETE,
                "integrity_issues": (),
            },
        )()

    monkeypatch.setattr(run_master, "merge_quality_run", fake_merge)
    monkeypatch.setattr(run_master, "write_json_atomic", lambda path, data: writes.append((path, data)) or path)
    monkeypatch.setattr(run_master, "generate_quality_report", lambda request: reports.append(request))
    return merges, writes, reports


def test_disabled_quality_preserves_existing_environment(monkeypatch):
    monkeypatch.setenv("QUALITY_ENABLE", "0")
    monkeypatch.setenv("QUALITY_RUN_ID", "outside-run")
    monkeypatch.setenv("QUALITY_EXECUTION_ID", "outside-execution")
    monkeypatch.setattr(
        run_master,
        "collect_test_case_items",
        lambda path: [_case("module/test_sample.py::test_ok")],
    )
    calls = _capture_pytest_environment(monkeypatch)

    assert run_master.run() == 0

    assert calls[0]["quality"]["QUALITY_ENABLE"] == "0"
    assert calls[0]["quality"]["QUALITY_RUN_ID"] == "outside-run"
    assert calls[0]["quality"]["QUALITY_EXECUTION_ID"] == "outside-execution"


def test_serial_run_uses_semantic_execution_id_and_restores_environment(monkeypatch):
    monkeypatch.setenv("QUALITY_ENABLE", "1")
    monkeypatch.setenv("QUALITY_RUN_ID", "parent-run")
    monkeypatch.setenv("QUALITY_EXECUTION_ID", "outside-execution")
    monkeypatch.setenv("QUALITY_OUTPUT_DIR", "quality-output")
    monkeypatch.setattr(
        run_master,
        "collect_test_case_items",
        lambda path: [_case("module/test_sample.py::test_ok")],
    )
    calls = _capture_pytest_environment(monkeypatch)
    _capture_quality_finalization(monkeypatch)

    assert run_master.run() == 0

    quality = calls[0]["quality"]
    assert quality["QUALITY_RUN_ID"] == "parent-run"
    assert quality["QUALITY_EXECUTION_ID"] == "serial-pool"
    assert quality["QUALITY_OUTPUT_DIR"] == str(run_master.PROJECT_ROOT / "quality-output")
    assert os.environ["QUALITY_EXECUTION_ID"] == "outside-execution"
    assert os.environ["QUALITY_OUTPUT_DIR"] == "quality-output"


def test_parallel_and_serial_stages_share_one_run_id(monkeypatch, tmp_path):
    monkeypatch.setenv("QUALITY_ENABLE", "1")
    monkeypatch.setattr(run_master, "DEFAULT_ALLURE_RESULTS_DIR", tmp_path / "allure-results")
    monkeypatch.setattr(
        run_master,
        "collect_test_case_items",
        lambda path: [
            _case("module/test_sample.py::test_parallel"),
            _case("module/test_sample.py::test_serial", "serial"),
        ],
    )
    generated = []
    monkeypatch.setattr(
        run_master,
        "build_run_id",
        lambda **kwargs: generated.append(kwargs) or "generated-run",
    )
    calls = _capture_pytest_environment(monkeypatch)
    merges, _writes, reports = _capture_quality_finalization(monkeypatch)

    assert run_master.run(numprocesses="2") == 0

    assert len(generated) == 1
    assert [call["quality"]["QUALITY_EXECUTION_ID"] for call in calls] == [
        "parallel-pool",
        "serial-pool",
    ]
    assert {call["quality"]["QUALITY_RUN_ID"] for call in calls} == {"generated-run"}
    assert all("-pool-1" not in call["quality"]["QUALITY_EXECUTION_ID"] for call in calls)
    assert merges[0].expected_execution_ids == ("parallel-pool", "serial-pool")
    assert reports[0].run_id == "generated-run"


def test_jenkins_identity_is_used_only_when_job_and_build_are_present(monkeypatch):
    monkeypatch.setenv("QUALITY_ENABLE", "1")
    monkeypatch.setenv("JOB_NAME", "api-case")
    monkeypatch.setenv("BUILD_NUMBER", "42")
    monkeypatch.setattr(
        run_master,
        "collect_test_case_items",
        lambda path: [_case("module/test_sample.py::test_ok")],
    )
    captured = []
    monkeypatch.setattr(
        run_master,
        "build_run_id",
        lambda **kwargs: captured.append(kwargs) or "jenkins-run",
    )
    calls = _capture_pytest_environment(monkeypatch)
    _capture_quality_finalization(monkeypatch)

    assert run_master.run() == 0

    assert captured == [{"job_name": "api-case", "build_number": "42"}]
    assert calls[0]["quality"]["QUALITY_RUN_ID"] == "jenkins-run"


def test_collect_only_does_not_generate_or_inject_quality_identity(monkeypatch):
    monkeypatch.setenv("QUALITY_ENABLE", "1")
    monkeypatch.setattr(
        run_master,
        "collect_test_case_items",
        lambda path: [_case("module/test_sample.py::test_ok")],
    )
    monkeypatch.setattr(
        run_master,
        "build_run_id",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("must not generate")),
    )
    monkeypatch.setattr(
        run_master.pytest,
        "main",
        lambda args: (_ for _ in ()).throw(AssertionError("must not execute")),
    )

    assert run_master.run(extra_pytest_args=["--collect-only"]) == 0


def test_invalid_quality_enable_fails_open(monkeypatch, capsys):
    monkeypatch.setenv("QUALITY_ENABLE", "invalid")
    monkeypatch.setattr(
        run_master,
        "collect_test_case_items",
        lambda path: [_case("module/test_sample.py::test_ok")],
    )
    calls = _capture_pytest_environment(monkeypatch)

    assert run_master.run() == 0

    assert calls[0]["quality"]["QUALITY_ENABLE"] == "invalid"
    assert "Quality collection disabled" in capsys.readouterr().out


def test_quality_enabled_adds_default_junit_path_and_finalizes(monkeypatch, tmp_path):
    output_dir = tmp_path / "quality"
    monkeypatch.setenv("QUALITY_ENABLE", "1")
    monkeypatch.setenv("QUALITY_RUN_ID", "run-1")
    monkeypatch.setenv("QUALITY_OUTPUT_DIR", str(output_dir))
    monkeypatch.setattr(
        run_master,
        "collect_test_case_items",
        lambda path: [_case("module/test_sample.py::test_ok")],
    )
    calls = _capture_pytest_environment(monkeypatch)
    merges, writes, reports = _capture_quality_finalization(monkeypatch)

    assert run_master.run() == 0

    assert any(str(output_dir / "junit" / "quality.xml") in arg for arg in calls[0]["args"])
    assert merges[0].run_id == "run-1"
    assert merges[0].expected_case_count == 1
    assert merges[0].junit_files == (output_dir / "junit" / "quality.xml",)
    assert writes
    assert reports[0].output_dir == output_dir


def test_quality_finalize_runs_and_original_exception_is_preserved(monkeypatch, tmp_path):
    output_dir = tmp_path / "quality"
    monkeypatch.setenv("QUALITY_ENABLE", "1")
    monkeypatch.setenv("QUALITY_RUN_ID", "run-1")
    monkeypatch.setenv("QUALITY_OUTPUT_DIR", str(output_dir))
    monkeypatch.setattr(
        run_master,
        "collect_test_case_items",
        lambda path: [_case("module/test_sample.py::test_ok")],
    )
    merges, writes, reports = _capture_quality_finalization(monkeypatch)

    def raise_keyboard_interrupt(args):
        raise KeyboardInterrupt

    monkeypatch.setattr(run_master.pytest, "main", raise_keyboard_interrupt)

    with pytest.raises(KeyboardInterrupt):
        run_master.run()

    assert merges
    assert writes[-1][1].status is run_master.RunStatus.INTERRUPTED
    assert reports


def test_quality_report_failure_is_fail_open(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("QUALITY_ENABLE", "1")
    monkeypatch.setenv("QUALITY_RUN_ID", "run-1")
    monkeypatch.setenv("QUALITY_OUTPUT_DIR", str(tmp_path / "quality"))
    monkeypatch.setattr(
        run_master,
        "collect_test_case_items",
        lambda path: [_case("module/test_sample.py::test_ok")],
    )
    _capture_pytest_environment(monkeypatch)
    _capture_quality_finalization(monkeypatch)
    monkeypatch.setattr(
        run_master,
        "generate_quality_report",
        lambda request: (_ for _ in ()).throw(OSError("disk unavailable")),
    )

    assert run_master.run() == 0

    assert "Quality report failed open: OSError: disk unavailable" in capsys.readouterr().out


def test_quality_report_environment_is_applied(monkeypatch, tmp_path):
    monkeypatch.setenv("QUALITY_ENABLE", "1")
    monkeypatch.setenv("QUALITY_RUN_ID", "run-1")
    monkeypatch.setenv("QUALITY_OUTPUT_DIR", str(tmp_path / "quality"))
    monkeypatch.setenv("QUALITY_SHADOW_GATE", "0")
    monkeypatch.setenv("QUALITY_MIN_REQUEST_SAMPLES", "8")
    monkeypatch.setenv("QUALITY_HTTP_5XX_WARN_RATE", "0.1")
    monkeypatch.setenv("QUALITY_TIMEOUT_WARN_RATE", "0.2")
    monkeypatch.setattr(
        run_master,
        "collect_test_case_items",
        lambda path: [_case("module/test_sample.py::test_ok")],
    )
    _capture_pytest_environment(monkeypatch)
    _merges, _writes, reports = _capture_quality_finalization(monkeypatch)

    assert run_master.run() == 0

    assert reports[0].shadow_gate is False
    assert reports[0].min_request_samples == 8
    assert reports[0].http_5xx_warn_rate == 0.1
    assert reports[0].timeout_warn_rate == 0.2


def test_invalid_quality_report_environment_uses_defaults(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("QUALITY_ENABLE", "1")
    monkeypatch.setenv("QUALITY_RUN_ID", "run-1")
    monkeypatch.setenv("QUALITY_OUTPUT_DIR", str(tmp_path / "quality"))
    monkeypatch.setenv("QUALITY_MIN_REQUEST_SAMPLES", "invalid")
    monkeypatch.setattr(
        run_master,
        "collect_test_case_items",
        lambda path: [_case("module/test_sample.py::test_ok")],
    )
    _capture_pytest_environment(monkeypatch)
    _merges, _writes, reports = _capture_quality_finalization(monkeypatch)

    assert run_master.run() == 0

    assert reports[0].min_request_samples == 20
    assert "Quality report configuration warning" in capsys.readouterr().out
