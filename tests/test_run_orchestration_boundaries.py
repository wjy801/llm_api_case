from __future__ import annotations

import ast
from pathlib import Path

import run_orchestration


EXPECTED_FILES = {
    "__init__.py",
    "artifacts.py",
    "cli.py",
    "environment.py",
    "paths.py",
    "pytest_execution.py",
    "quality_fact_merge_stage.py",
    "quality_flaky_stage.py",
    "quality_metrics_stage.py",
    "quality_pipeline.py",
    "quality_run_record.py",
    "quality_semantic_stage.py",
    "runner.py",
    "scheduling.py",
}


def _package_dir() -> Path:
    return Path(run_orchestration.__file__).resolve().parent


def _tree(name: str) -> ast.Module:
    path = _package_dir() / f"{name}.py"
    return ast.parse(path.read_text(encoding="utf-8"))


def test_run_orchestration_uses_only_the_planned_files():
    actual = {path.name for path in _package_dir().glob("*.py")}

    assert actual == EXPECTED_FILES
    assert not {"utils.py", "helpers.py", "common.py"} & actual


def test_pytest_main_and_shutil_have_single_owners():
    pytest_owners = []
    shutil_owners = []
    for path in _package_dir().glob("*.py"):
        source = path.read_text(encoding="utf-8")
        if "pytest.main" in source:
            pytest_owners.append(path.name)
        if "import shutil" in source:
            shutil_owners.append(path.name)

    assert pytest_owners == ["pytest_execution.py"]
    assert shutil_owners == ["artifacts.py"]


def test_quality_stage_modules_do_not_import_each_other():
    stage_names = {
        "quality_fact_merge_stage",
        "quality_semantic_stage",
        "quality_metrics_stage",
        "quality_flaky_stage",
    }
    for name in stage_names:
        relative_modules = {
            node.module.split(".", 1)[0]
            for node in ast.walk(_tree(name))
            if isinstance(node, ast.ImportFrom)
            and node.level > 0
            and node.module
        }
        assert not (relative_modules & stage_names)


def test_runner_does_not_import_quality_business_implementations_directly():
    forbidden = {
        "quality.aggregator",
        "quality.flaky_importer",
        "quality.gate",
        "quality.metrics",
        "quality.observation_report",
        "quality.report",
        "quality.semantic_aggregator",
    }
    imported = {
        node.module
        for node in ast.walk(_tree("runner"))
        if isinstance(node, ast.ImportFrom) and node.module
    }

    assert not (imported & forbidden)
