from __future__ import annotations

import ast
from pathlib import Path

import run_orchestration


def _package_dir() -> Path:
    return Path(run_orchestration.__file__).resolve().parent


def _tree(name: str) -> ast.Module:
    path = _package_dir() / f"{name}.py"
    return ast.parse(path.read_text(encoding="utf-8"))


def test_pytest_main_and_allure_filesystem_lifecycle_have_single_owners():
    pytest_owners = set()
    allure_filesystem_owners = set()
    for path in _package_dir().glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if isinstance(node.func.value, ast.Name) and node.func.value.id == "pytest" and node.func.attr == "main":
                pytest_owners.add(path.name)
            if (
                isinstance(node.func.value, ast.Name)
                and node.func.value.id == "shutil"
                and node.func.attr in {"copy2", "copytree", "move", "rmtree"}
            ):
                allure_filesystem_owners.add(path.name)

    assert pytest_owners == {"pytest_execution.py"}
    assert allure_filesystem_owners == {"allure_lifecycle.py"}


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


def test_runner_and_neutral_lifecycle_have_no_top_level_quality_imports():
    for name in ("runner", "quality_lifecycle"):
        tree = _tree(name)
        quality_imports = {
            node.module
            for node in tree.body
            if isinstance(node, ast.ImportFrom)
            and node.module
            and node.module.startswith("quality")
        }
        assert not quality_imports
