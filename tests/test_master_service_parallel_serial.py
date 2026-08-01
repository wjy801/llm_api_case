from __future__ import annotations

from pathlib import Path

import pytest

import run_master
from master_service import CollectedTestCase, collect_test_case_items, split_test_cases


def _without_generated_junit_arg(args: list[str]) -> list[str]:
    return [arg for arg in args if not arg.startswith("--junitxml=")]


def _generated_junit_name(args: list[str]) -> str:
    junit_args = [arg for arg in args if arg.startswith("--junitxml=")]
    assert len(junit_args) == 1
    return Path(junit_args[0].split("=", 1)[1]).name


def test_collect_test_case_items_reads_function_class_and_file_markers(tmp_path: Path):
    test_file = tmp_path / "test_marker_collection.py"
    test_file.write_text(
        "\n".join(
            [
                "import pytest",
                "",
                "pytestmark = pytest.mark.file_marker",
                "",
                "@pytest.mark.class_marker",
                "class TestMarkedClass:",
                "    @pytest.mark.serial",
                "    def test_serial_case(self):",
                "        pass",
                "",
                "def test_file_marker_only():",
                "    pass",
            ]
        ),
        encoding="utf-8",
    )

    cases = collect_test_case_items(test_file)
    markers_by_name = {case.nodeid.split("::")[-1]: case.markers for case in cases}

    assert "serial" in markers_by_name["test_serial_case"]
    assert "class_marker" in markers_by_name["test_serial_case"]
    assert "file_marker" in markers_by_name["test_serial_case"]
    assert "file_marker" in markers_by_name["test_file_marker_only"]
    assert "serial" not in markers_by_name["test_file_marker_only"]


def test_split_test_cases_separates_serial_pool():
    cases = [
        CollectedTestCase("test_a.py::test_parallel", frozenset()),
        CollectedTestCase("test_b.py::test_serial", frozenset({"serial"})),
        CollectedTestCase("test_c.py::test_parallel", frozenset({"smoke"})),
    ]

    parallel_cases, serial_cases = split_test_cases(cases)

    assert parallel_cases == ["test_a.py::test_parallel", "test_c.py::test_parallel"]
    assert serial_cases == ["test_b.py::test_serial"]


def test_run_without_numprocesses_runs_all_cases_once(monkeypatch):
    monkeypatch.setattr(
        run_master,
        "collect_test_case_items",
        lambda test_path: [
            CollectedTestCase("test_a.py::test_parallel", frozenset()),
            CollectedTestCase("test_b.py::test_serial", frozenset({"serial"})),
        ],
    )
    calls: list[list[str]] = []
    monkeypatch.setattr(run_master, "_run_pytest", lambda args: calls.append(args) or 0)

    exit_code = run_master.run("tests")

    assert exit_code == 0
    assert [_without_generated_junit_arg(args) for args in calls] == [
        ["test_a.py::test_parallel", "test_b.py::test_serial"]
    ]
    assert _generated_junit_name(calls[0]) == "quality.xml"


def test_run_with_numprocesses_runs_parallel_pool_before_serial_pool(monkeypatch):
    monkeypatch.setattr(
        run_master,
        "collect_test_case_items",
        lambda test_path: [
            CollectedTestCase("test_a.py::test_parallel", frozenset()),
            CollectedTestCase("test_b.py::test_serial", frozenset({"serial"})),
        ],
    )
    calls: list[list[str]] = []
    monkeypatch.setattr(run_master, "_run_pytest", lambda args: calls.append(args) or 0)
    monkeypatch.setattr(run_master, "_preserve_allure_results", lambda results_dir: None)
    monkeypatch.setattr(run_master, "_restore_allure_results", lambda results_dir, preserved_results: None)

    exit_code = run_master.run(
        "tests",
        extra_pytest_args=["-q", "--junitxml=reports/smoke-tests.xml"],
        numprocesses="2",
    )

    assert exit_code == 0
    assert calls == [
        [
            "test_a.py::test_parallel",
            "-q",
            "--junitxml=reports/smoke-tests-parallel.xml",
            "-n",
            "2",
        ],
        [
            "test_b.py::test_serial",
            "-q",
            "--junitxml=reports/smoke-tests-serial.xml",
        ],
    ]


def test_run_with_empty_serial_pool_skips_serial_stage(monkeypatch):
    monkeypatch.setattr(
        run_master,
        "collect_test_case_items",
        lambda test_path: [CollectedTestCase("test_a.py::test_parallel", frozenset())],
    )
    calls: list[list[str]] = []
    monkeypatch.setattr(run_master, "_run_pytest", lambda args: calls.append(args) or 0)

    exit_code = run_master.run("tests", numprocesses="auto")

    assert exit_code == 0
    assert [_without_generated_junit_arg(args) for args in calls] == [
        ["test_a.py::test_parallel", "-n", "auto"]
    ]
    assert _generated_junit_name(calls[0]) == "quality-parallel.xml"


def test_run_with_empty_parallel_pool_runs_serial_only(monkeypatch):
    monkeypatch.setattr(
        run_master,
        "collect_test_case_items",
        lambda test_path: [CollectedTestCase("test_b.py::test_serial", frozenset({"serial"}))],
    )
    calls: list[list[str]] = []
    monkeypatch.setattr(run_master, "_run_pytest", lambda args: calls.append(args) or 0)
    monkeypatch.setattr(run_master, "_preserve_allure_results", lambda results_dir: None)
    monkeypatch.setattr(run_master, "_restore_allure_results", lambda results_dir, preserved_results: None)

    exit_code = run_master.run("tests", numprocesses="2")

    assert exit_code == 0
    assert [_without_generated_junit_arg(args) for args in calls] == [["test_b.py::test_serial"]]
    assert _generated_junit_name(calls[0]) == "quality-serial.xml"


def test_run_collect_only_prints_pool_counts_without_execution(monkeypatch, capsys):
    monkeypatch.setattr(
        run_master,
        "collect_test_case_items",
        lambda test_path: [
            CollectedTestCase("test_a.py::test_parallel", frozenset()),
            CollectedTestCase("test_b.py::test_serial", frozenset({"serial"})),
        ],
    )
    monkeypatch.setattr(
        run_master,
        "_run_pytest",
        lambda args: pytest.fail("collect-only should not execute pytest"),
    )

    exit_code = run_master.run("tests", extra_pytest_args=["--collect-only", "-q"], numprocesses="2")

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Parallel pool cases: 1" in captured.out
    assert "Serial pool cases: 1" in captured.out


def test_run_continues_serial_pool_after_parallel_failure(monkeypatch):
    monkeypatch.setattr(
        run_master,
        "collect_test_case_items",
        lambda test_path: [
            CollectedTestCase("test_a.py::test_parallel", frozenset()),
            CollectedTestCase("test_b.py::test_serial", frozenset({"serial"})),
        ],
    )
    calls: list[list[str]] = []

    def fake_run_pytest(args: list[str]) -> int:
        calls.append(args)
        return 1 if "test_a.py::test_parallel" in args else 0

    monkeypatch.setattr(run_master, "_run_pytest", fake_run_pytest)
    monkeypatch.setattr(run_master, "_preserve_allure_results", lambda results_dir: None)
    monkeypatch.setattr(run_master, "_restore_allure_results", lambda results_dir, preserved_results: None)

    exit_code = run_master.run("tests", numprocesses="2")

    assert exit_code == 1
    assert [_without_generated_junit_arg(args) for args in calls] == [
        ["test_a.py::test_parallel", "-n", "2"],
        ["test_b.py::test_serial"],
    ]
    assert [_generated_junit_name(args) for args in calls] == [
        "quality-parallel.xml",
        "quality-serial.xml",
    ]


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (["--junitxml=reports/smoke.xml"], ["--junitxml=reports/smoke-parallel.xml"]),
        (["--junitxml", "reports/smoke.xml"], ["--junitxml", "reports/smoke-serial.xml"]),
    ],
)
def test_replace_junitxml_suffix(args: list[str], expected: list[str]):
    suffix = "parallel" if "parallel" in expected[-1] else "serial"

    assert run_master._replace_junitxml_suffix(args, suffix) == expected
