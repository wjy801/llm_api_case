from __future__ import annotations

import ast
from pathlib import Path

import pipeline_reporting


def test_quality_adapter_is_the_only_top_level_quality_dependency():
    package_dir = Path(pipeline_reporting.__file__).resolve().parent
    top_level_quality_consumers = {
        path.stem
        for path in package_dir.glob("*.py")
        if _top_level_imports_package(path, "quality")
    }
    assert top_level_quality_consumers == {"quality_sources"}


def test_core_sources_load_quality_dependencies_only_inside_enabled_paths():
    package_dir = Path(pipeline_reporting.__file__).resolve().parent
    sources = package_dir / "sources.py"

    assert _imports_package(sources, "quality")
    assert not _top_level_imports_package(sources, "quality")
    assert not _top_level_imports_package(sources, "pipeline_reporting.quality_sources")


def _imports_package(path: Path, package: str) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name == package or alias.name.startswith(f"{package}.") for alias in node.names):
                return True
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            if node.module == package or node.module.startswith(f"{package}."):
                return True
    return False


def _top_level_imports_package(path: Path, package: str) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Import):
            if any(
                alias.name == package or alias.name.startswith(f"{package}.")
                for alias in node.names
            ):
                return True
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            if node.module == package or node.module.startswith(f"{package}."):
                return True
    return False
