from __future__ import annotations

import ast
import inspect
from pathlib import Path

import run_master
import run_orchestration


def test_root_entrypoint_reexports_the_public_orchestration_api():
    assert run_master.run is run_orchestration.run
    assert run_master.main is run_orchestration.main
    assert run_master.PROJECT_ROOT == Path(__file__).resolve().parents[1]
    assert run_master.DEFAULT_ALLURE_RESULTS_DIR == (
        run_master.PROJECT_ROOT / "allure-results"
    )
    assert tuple(inspect.signature(run_master.run).parameters) == (
        "test_path",
        "extra_pytest_args",
        "numprocesses",
        "dist",
        "serial_marker",
    )
    assert tuple(inspect.signature(run_master.main).parameters) == ("argv",)


def test_root_entrypoint_does_not_import_pytest_or_quality_implementations():
    path = run_master.PROJECT_ROOT / "run_master.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported_modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.add(node.module)

    assert not {
        module
        for module in imported_modules
        if module == "pytest"
        or module.startswith("pytest.")
        or module == "quality"
        or module.startswith("quality.")
    }


def test_orchestration_package_exports_only_the_stable_root_symbols():
    assert run_orchestration.__all__ == (
        "DEFAULT_ALLURE_RESULTS_DIR",
        "PROJECT_ROOT",
        "main",
        "run",
    )
