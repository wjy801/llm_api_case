from __future__ import annotations

import os
from pathlib import Path

from quality.storage import read_jsonl
from quality.junit import parse_junit_file


pytest_plugins = ("pytester",)
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _prepare_plugin(
    pytester,
    monkeypatch,
    *,
    enabled=True,
    semantic_enabled=False,
    execution_id="manual-pytest",
):
    pytester.makeconftest('pytest_plugins = ("quality.pytest_plugin",)')
    output_dir = pytester.path / "quality-output"
    monkeypatch.setenv("QUALITY_ENABLE", "1" if enabled else "0")
    monkeypatch.setenv("QUALITY_SEMANTIC_ENABLE", "1" if semantic_enabled else "0")
    monkeypatch.setenv("QUALITY_RUN_ID", "run-plugin")
    monkeypatch.setenv("QUALITY_EXECUTION_ID", execution_id)
    monkeypatch.setenv("QUALITY_OUTPUT_DIR", str(output_dir))
    monkeypatch.setenv("USE_CHINA_ENVIRONMENT", "FALSE")
    monkeypatch.setenv("OVERSEAS_TEST_BASE_URL", "https://example.com")
    monkeypatch.setenv("OVERSEAS_API_KEY", "test-key")
    pythonpath = os.environ.get("PYTHONPATH")
    monkeypatch.setenv(
        "PYTHONPATH",
        str(PROJECT_ROOT) if not pythonpath else f"{PROJECT_ROOT}{os.pathsep}{pythonpath}",
    )
    return output_dir


def _run_subprocess(pytester, *args):
    return pytester.runpytest_subprocess("-o", "addopts=", *args)


def test_plugin_records_call_statuses_and_parameter_identity(pytester, monkeypatch):
    output_dir = _prepare_plugin(pytester, monkeypatch)
    pytester.makepyfile(
        test_sample="""
        import pytest

        @pytest.mark.parametrize("value", [1, 2])
        def test_param(value):
            assert value > 0

        def test_failed():
            assert False

        @pytest.mark.skip(reason="sample")
        def test_skipped():
            pass

        @pytest.mark.xfail(reason="sample")
        def test_xfailed():
            assert False

        @pytest.mark.xfail(reason="sample")
        def test_xpassed():
            pass
        """
    )

    result = _run_subprocess(pytester, "-q")

    result.assert_outcomes(passed=2, failed=1, skipped=1, xfailed=1, xpassed=1)
    records = read_jsonl(output_dir / "shards/cases-manual-pytest-master.jsonl")
    call_records = [record for record in records if record["phase"] == "call"]
    assert {record["raw_status"] for record in call_records} == {
        "passed",
        "failed",
        "xfailed",
        "xpassed",
    }
    assert any(record["raw_status"] == "skipped" for record in records)

    params = [record for record in call_records if "test_param" in record["nodeid"]]
    assert len({record["case_id"] for record in params}) == 1
    assert len({record["invocation_id"] for record in params}) == 2
    assert {record["run_id"] for record in records} == {"run-plugin"}
    assert {record["execution_id"] for record in records} == {"manual-pytest"}


def test_setup_failure_is_error_and_does_not_synthesize_call(pytester, monkeypatch):
    output_dir = _prepare_plugin(pytester, monkeypatch)
    pytester.makepyfile(
        test_sample="""
        import pytest

        @pytest.fixture
        def broken():
            raise RuntimeError("broken")

        def test_setup_error(broken):
            pass
        """
    )

    result = _run_subprocess(pytester, "-q")

    result.assert_outcomes(errors=1)
    records = read_jsonl(output_dir / "shards/cases-manual-pytest-master.jsonl")
    assert [(record["phase"], record["raw_status"]) for record in records] == [
        ("setup", "error"),
        ("teardown", "passed"),
    ]


def test_disabled_and_collect_only_do_not_create_quality_output(pytester, monkeypatch):
    output_dir = _prepare_plugin(pytester, monkeypatch, enabled=False)
    pytester.makepyfile(
        """
        import sys

        def test_ok():
            assert "quality.pytest_plugin_runtime" not in sys.modules
        """
    )

    disabled = _run_subprocess(pytester, "-q")

    disabled.assert_outcomes(passed=1)
    assert not output_dir.exists()

    monkeypatch.setenv("QUALITY_ENABLE", "1")
    collected = _run_subprocess(pytester, "--collect-only", "-q")

    assert collected.ret == 0
    assert not output_dir.exists()


def test_plugin_adds_quality_identity_to_junit_properties(pytester, monkeypatch):
    _prepare_plugin(pytester, monkeypatch)
    pytester.makepyfile("def test_ok(): pass")
    junit = pytester.path / "quality.xml"

    result = _run_subprocess(pytester, "-q", f"--junitxml={junit}")

    result.assert_outcomes(passed=1)
    evidence = parse_junit_file(junit)[0]
    assert evidence.case_id == "test_plugin_adds_quality_identity_to_junit_properties.py::test_ok"
    assert evidence.invocation_id is not None


def test_collection_failure_writes_integrity_without_case(pytester, monkeypatch):
    output_dir = _prepare_plugin(pytester, monkeypatch)
    pytester.makepyfile(test_broken="raise RuntimeError('collect failed')")

    result = _run_subprocess(pytester, "-q")

    assert result.ret != 0
    issues = read_jsonl(output_dir / "shards/integrity-manual-pytest-master.jsonl")
    assert any(issue["code"] == "collection_failed" for issue in issues)
    assert read_jsonl(output_dir / "shards/cases-manual-pytest-master.jsonl") == []


def test_xdist_workers_write_separate_shards_without_controller_duplicates(pytester, monkeypatch):
    output_dir = _prepare_plugin(pytester, monkeypatch)
    pytester.makepyfile(
        test_sample="""
        def test_one(): pass
        def test_two(): pass
        def test_three(): pass
        def test_four(): pass
        """
    )

    result = _run_subprocess(pytester, "-n", "2", "-q")

    result.assert_outcomes(passed=4)
    case_files = sorted((output_dir / "shards").glob("cases-manual-pytest-gw*.jsonl"))
    assert case_files
    assert not (output_dir / "shards/cases-manual-pytest-master.jsonl").exists()
    call_records = [
        record
        for path in case_files
        for record in read_jsonl(path)
        if record["phase"] == "call"
    ]
    assert len(call_records) == 4
    assert len({record["nodeid"] for record in call_records}) == 4
    assert {record["run_id"] for record in call_records} == {"run-plugin"}


def test_collector_failure_does_not_change_pytest_outcome(pytester, monkeypatch):
    _prepare_plugin(pytester, monkeypatch)
    pytester.makeconftest(
        """
        pytest_plugins = ("quality.pytest_plugin",)

        def pytest_sessionstart():
            import quality.collector
            def fail(*args, **kwargs):
                raise OSError("disk full")
            quality.collector.append_jsonl = fail
        """
    )
    pytester.makepyfile("def test_ok(): pass")

    result = _run_subprocess(pytester, "-q")

    result.assert_outcomes(passed=1)


def test_semantic_plugin_writes_independent_http_operation_shards(pytester, monkeypatch):
    output_dir = _prepare_plugin(pytester, monkeypatch, semantic_enabled=True)
    pytester.makepyfile(
        test_sample="""
        import json
        import requests

        from common.base_request import BaseRequest

        class Config:
            base_url = "https://example.com"
            api_key = "secret"
            timeout = 5

        def test_http_operation():
            response = requests.Response()
            response.status_code = 200
            response.url = "https://example.com/v1/items"
            response._content = json.dumps({"usage": {"prompt_tokens": 1}}).encode()
            response.headers["Content-Type"] = "application/json"
            client = BaseRequest(config=Config())
            client.session.request = lambda method, url, **kwargs: response
            assert client.get("/v1/items", _attach_log=False).status_code == 200
        """
    )

    result = _run_subprocess(pytester, "-q")

    result.assert_outcomes(passed=1)
    semantic_shards = output_dir / "semantic" / "shards"
    groups = read_jsonl(
        semantic_shards / "request-groups-manual-pytest-master.jsonl"
    )
    operations = read_jsonl(
        semantic_shards / "operations-manual-pytest-master.jsonl"
    )
    assert len(groups) == len(operations) == 1
    assert groups[0]["operation_id"] == operations[0]["operation_id"]


def test_semantic_xdist_workers_write_separate_operation_shards(pytester, monkeypatch):
    output_dir = _prepare_plugin(pytester, monkeypatch, semantic_enabled=True)
    pytester.makepyfile(
        test_sample="""
        import json
        import requests

        from common.base_request import BaseRequest

        class Config:
            base_url = "https://example.com"
            api_key = "secret"
            timeout = 5

        def call_api():
            response = requests.Response()
            response.status_code = 200
            response.url = "https://example.com/v1/items"
            response._content = json.dumps({"usage": {"prompt_tokens": 1}}).encode()
            response.headers["Content-Type"] = "application/json"
            client = BaseRequest(config=Config())
            client.session.request = lambda method, url, **kwargs: response
            assert client.get("/v1/items", _attach_log=False).status_code == 200

        def test_one(): call_api()
        def test_two(): call_api()
        def test_three(): call_api()
        def test_four(): call_api()
        """
    )

    result = _run_subprocess(pytester, "-n", "2", "-q")

    result.assert_outcomes(passed=4)
    files = sorted(
        (output_dir / "semantic" / "shards").glob(
            "operations-manual-pytest-gw*.jsonl"
        )
    )
    assert files
    assert not (
        output_dir / "semantic" / "shards" / "operations-manual-pytest-master.jsonl"
    ).exists()
    operations = [record for path in files for record in read_jsonl(path)]
    assert len(operations) == 4
