from __future__ import annotations

from pathlib import Path

import pytest

import run_master
from master_service import CollectedTestCase, collect_test_case_items, split_test_cases
from quality.config import QualityRuntimeConfig
from run_orchestration import environment as orchestration_environment
from run_orchestration import artifacts, pytest_execution, quality_pipeline, scheduling


def _set_quality_enabled(monkeypatch) -> None:
    monkeypatch.setattr(
        orchestration_environment,
        "resolve_parent_quality_config",
        lambda: QualityRuntimeConfig(
            enabled=True,
            run_id=None,
            execution_id=None,
            output_dir=Path("reports/quality"),
        ),
    )


def _collection(*cases: CollectedTestCase) -> pytest_execution.CollectionResult:
    return pytest_execution.CollectionResult(0, tuple(cases), "", "")


def _without_generated_junit_arg(args: list[str]) -> list[str]:
    return [
        arg
        for arg in args
        if not arg.startswith("--junitxml=")
        and not arg.startswith("--alluredir=")
        and arg != "--clean-alluredir"
    ]


def _without_allure_args(args: list[str]) -> list[str]:
    return [
        arg
        for arg in args
        if not arg.startswith("--alluredir=")
        and arg != "--clean-alluredir"
    ]


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
    _set_quality_enabled(monkeypatch)
    monkeypatch.setattr(
        pytest_execution,
        "collect_test_case_items",
        lambda test_path, pytest_args=(): _collection(
            CollectedTestCase("test_a.py::test_parallel", frozenset()),
            CollectedTestCase("test_b.py::test_serial", frozenset({"serial"})),
        ),
    )
    calls: list[list[str]] = []
    monkeypatch.setattr(pytest_execution, "run_pytest", lambda args: calls.append(args) or 0)

    exit_code = run_master.run("tests")

    assert exit_code == 0
    assert [_without_generated_junit_arg(args) for args in calls] == [
        ["test_a.py::test_parallel", "test_b.py::test_serial"]
    ]
    assert _generated_junit_name(calls[0]) == "quality.xml"


def test_run_with_numprocesses_runs_parallel_pool_before_serial_pool(monkeypatch):
    monkeypatch.setattr(
        pytest_execution,
        "collect_test_case_items",
        lambda test_path, pytest_args=(): _collection(
            CollectedTestCase("test_a.py::test_parallel", frozenset()),
            CollectedTestCase("test_b.py::test_serial", frozenset({"serial"})),
        ),
    )
    calls: list[list[str]] = []
    monkeypatch.setattr(pytest_execution, "run_pytest", lambda args: calls.append(args) or 0)

    exit_code = run_master.run(
        "tests",
        extra_pytest_args=["-q", "--junitxml=reports/smoke-tests.xml"],
        numprocesses="2",
    )

    assert exit_code == 0
    assert [_without_allure_args(args) for args in calls] == [
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
    _set_quality_enabled(monkeypatch)
    monkeypatch.setattr(
        pytest_execution,
        "collect_test_case_items",
        lambda test_path, pytest_args=(): _collection(
            CollectedTestCase("test_a.py::test_parallel", frozenset())
        ),
    )
    calls: list[list[str]] = []
    monkeypatch.setattr(pytest_execution, "run_pytest", lambda args: calls.append(args) or 0)

    exit_code = run_master.run("tests", numprocesses="auto")

    assert exit_code == 0
    assert [_without_generated_junit_arg(args) for args in calls] == [
        ["test_a.py::test_parallel", "-n", "auto"]
    ]
    assert _generated_junit_name(calls[0]) == "quality-parallel.xml"


def test_run_with_empty_parallel_pool_runs_serial_only(monkeypatch):
    _set_quality_enabled(monkeypatch)
    monkeypatch.setattr(
        pytest_execution,
        "collect_test_case_items",
        lambda test_path, pytest_args=(): _collection(
            CollectedTestCase("test_b.py::test_serial", frozenset({"serial"}))
        ),
    )
    calls: list[list[str]] = []
    monkeypatch.setattr(pytest_execution, "run_pytest", lambda args: calls.append(args) or 0)

    exit_code = run_master.run("tests", numprocesses="2")

    assert exit_code == 0
    assert [_without_generated_junit_arg(args) for args in calls] == [["test_b.py::test_serial"]]
    assert _generated_junit_name(calls[0]) == "quality-serial.xml"


def test_run_collect_only_prints_pool_counts_without_execution(monkeypatch, capsys):
    monkeypatch.setattr(
        pytest_execution,
        "collect_test_case_items",
        lambda test_path, pytest_args=(): _collection(
            CollectedTestCase("test_a.py::test_parallel", frozenset()),
            CollectedTestCase("test_b.py::test_serial", frozenset({"serial"})),
        ),
    )
    monkeypatch.setattr(
        pytest_execution,
        "run_pytest",
        lambda args: pytest.fail("collect-only should not execute pytest"),
    )

    exit_code = run_master.run("tests", extra_pytest_args=["--collect-only", "-q"], numprocesses="2")

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Parallel pool cases: 1" in captured.out
    assert "Serial pool cases: 1" in captured.out


def test_run_continues_serial_pool_after_parallel_failure(monkeypatch):
    _set_quality_enabled(monkeypatch)
    monkeypatch.setattr(
        pytest_execution,
        "collect_test_case_items",
        lambda test_path, pytest_args=(): _collection(
            CollectedTestCase("test_a.py::test_parallel", frozenset()),
            CollectedTestCase("test_b.py::test_serial", frozenset({"serial"})),
        ),
    )
    calls: list[list[str]] = []

    def fake_run_pytest(args: list[str]) -> int:
        calls.append(args)
        return 1 if "test_a.py::test_parallel" in args else 0

    monkeypatch.setattr(pytest_execution, "run_pytest", fake_run_pytest)

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


def test_run_with_quality_disabled_does_not_add_default_junit(monkeypatch):
    monkeypatch.setattr(
        orchestration_environment,
        "resolve_parent_quality_config",
        lambda: QualityRuntimeConfig(
            enabled=False,
            run_id=None,
            execution_id=None,
            output_dir=Path("reports/quality"),
        ),
    )
    monkeypatch.setattr(
        pytest_execution,
        "collect_test_case_items",
        lambda test_path, pytest_args=(): _collection(
            CollectedTestCase("test_a.py::test_case", frozenset())
        ),
    )
    calls: list[list[str]] = []
    monkeypatch.setattr(pytest_execution, "run_pytest", lambda args: calls.append(args) or 0)

    assert run_master.run("tests") == 0
    assert [_without_generated_junit_arg(args) for args in calls] == [
        ["test_a.py::test_case"]
    ]


def test_runner_merges_pool_raw_results_into_custom_alluredir(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(
        pytest_execution,
        "collect_test_case_items",
        lambda test_path, pytest_args=(): _collection(
            CollectedTestCase("test_a.py::test_case", frozenset())
        ),
    )
    pool_dirs = []

    def fake_run_pytest(args):
        pool_dir = Path(
            next(
                arg.split("=", 1)[1]
                for arg in args
                if arg.startswith("--alluredir=")
            )
        )
        pool_dirs.append(pool_dir)
        pool_dir.mkdir(parents=True, exist_ok=True)
        (pool_dir / "case-result.json").write_text("{}", encoding="utf-8")
        return 0

    monkeypatch.setattr(pytest_execution, "run_pytest", fake_run_pytest)
    final_dir = tmp_path / "custom-allure"

    assert run_master.run(
        "tests",
        extra_pytest_args=[f"--alluredir={final_dir}"],
    ) == 0

    assert pool_dirs and pool_dirs[0] != final_dir
    assert (final_dir / "case-result.json").exists()


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (["--junitxml=reports/smoke.xml"], ["--junitxml=reports/smoke-parallel.xml"]),
        (["--junitxml", "reports/smoke.xml"], ["--junitxml", "reports/smoke-serial.xml"]),
    ],
)
def test_replace_junitxml_suffix(args: list[str], expected: list[str]):
    suffix = "parallel" if "parallel" in expected[-1] else "serial"

    assert pytest_execution.replace_junitxml_suffix(args, suffix) == expected


def test_pytest_arguments_are_partitioned_by_execution_phase():
    plan = pytest_execution.partition_pytest_args(
        [
            "-q",
            "-k",
            "selected",
            "--ignore=tests/ignored",
            "--junitxml=reports/result.xml",
            "--alluredir",
            "custom-allure",
            "--collect-only",
        ]
    )

    assert plan.collect_only is True
    assert plan.selection_args == (
        "-k",
        "selected",
        "--ignore=tests/ignored",
    )
    assert plan.collection_args == (
        "-q",
        "-k",
        "selected",
        "--ignore=tests/ignored",
    )
    assert plan.execution_args == (
        "-q",
        "--junitxml=reports/result.xml",
        "--alluredir",
        "custom-allure",
    )


def test_authoritative_empty_selection_returns_pytest_exit_5(monkeypatch):
    monkeypatch.setattr(
        pytest_execution,
        "collect_test_case_items",
        lambda path, pytest_args=(): pytest_execution.CollectionResult(5, (), "", ""),
    )
    monkeypatch.setattr(
        pytest_execution,
        "run_pytest",
        lambda args: pytest.fail("empty selection must not execute pytest"),
    )
    payloads = []
    monkeypatch.setattr(
        artifacts,
        "write_execution_result_atomic",
        lambda payload: payloads.append(payload),
    )

    assert run_master.run("tests") == 5
    assert payloads[0]["collection_exit_code"] == 5
    assert payloads[0]["final_exit_code"] == 5
    assert payloads[0]["pool_results"] == []


@pytest.mark.parametrize("terminating_exit", [2, 3, 4, 5])
def test_terminating_parallel_exit_stops_serial_pool(monkeypatch, terminating_exit):
    monkeypatch.setattr(
        pytest_execution,
        "collect_test_case_items",
        lambda path, pytest_args=(): _collection(
            CollectedTestCase("test_a.py::test_parallel", frozenset()),
            CollectedTestCase("test_b.py::test_serial", frozenset({"serial"})),
        ),
    )
    calls = []
    monkeypatch.setattr(
        pytest_execution,
        "run_pytest",
        lambda args: calls.append(list(args)) or terminating_exit,
    )

    assert run_master.run("tests", numprocesses="2") == terminating_exit
    assert len(calls) == 1
    assert "test_a.py::test_parallel" in calls[0]


def test_runner_writes_pool_level_execution_facts(monkeypatch):
    monkeypatch.setattr(
        pytest_execution,
        "collect_test_case_items",
        lambda path, pytest_args=(): _collection(
            CollectedTestCase("test_a.py::test_case", frozenset())
        ),
    )
    monkeypatch.setattr(pytest_execution, "run_pytest", lambda args: 0)
    payloads = []
    monkeypatch.setattr(
        artifacts,
        "write_execution_result_atomic",
        lambda payload: payloads.append(payload),
    )

    assert run_master.run("tests") == 0
    payload = payloads[0]
    assert payload["schema_version"] == "runner-execution.v1"
    assert payload["planned_case_count"] == 1
    assert payload["final_exit_code"] == 0
    assert payload["pool_results"][0]["stage_id"] == "serial-pool"
    assert payload["pool_results"][0]["raw_pytest_exit_code"] == 0


def test_quality_finalization_failure_does_not_override_pytest_exit(monkeypatch):
    monkeypatch.setattr(
        pytest_execution,
        "collect_test_case_items",
        lambda path, pytest_args=(): _collection(
            CollectedTestCase("test_a.py::test_case", frozenset())
        ),
    )
    monkeypatch.setattr(pytest_execution, "run_pytest", lambda args: 0)
    monkeypatch.setattr(
        quality_pipeline,
        "finalize_quality_run",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("quality unavailable")),
    )

    assert run_master.run("tests") == 0


@pytest.mark.parametrize(("pytest_exit", "expected"), [(0, 1), (4, 4)])
def test_execution_result_write_failure_never_creates_false_success(
    monkeypatch, pytest_exit, expected
):
    monkeypatch.setattr(
        pytest_execution,
        "collect_test_case_items",
        lambda path, pytest_args=(): _collection(
            CollectedTestCase("test_a.py::test_case", frozenset())
        ),
    )
    monkeypatch.setattr(
        pytest_execution, "run_pytest", lambda args: pytest_exit
    )
    monkeypatch.setattr(
        artifacts,
        "write_execution_result_atomic",
        lambda payload: (_ for _ in ()).throw(OSError("disk unavailable")),
    )

    assert run_master.run("tests") == expected


def test_runner_exception_stops_following_pool_and_returns_nonzero(monkeypatch):
    monkeypatch.setattr(
        pytest_execution,
        "collect_test_case_items",
        lambda path, pytest_args=(): _collection(
            CollectedTestCase("test_a.py::test_parallel", frozenset()),
            CollectedTestCase("test_b.py::test_serial", frozenset({"serial"})),
        ),
    )
    calls = []

    def fail_parallel(args):
        calls.append(list(args))
        raise OSError("pytest infrastructure unavailable")

    monkeypatch.setattr(pytest_execution, "run_pytest", fail_parallel)

    assert run_master.run("tests", numprocesses="2") == 1
    assert len(calls) == 1
