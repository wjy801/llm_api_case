from __future__ import annotations

import ast
from pathlib import Path

import pipeline_reporting


EXPECTED_FILES = {
    "__init__.py",
    "__main__.py",
    "builder.py",
    "config.py",
    "contracts.py",
    "renderer.py",
    "service.py",
    "sources.py",
}

ALLOWED_INTERNAL_IMPORTS = {
    "__init__": {"config", "contracts", "service"},
    "__main__": {"config", "contracts", "service", "sources"},
    "builder": {"contracts"},
    "config": set(),
    "contracts": set(),
    "renderer": {"contracts"},
    "service": {"builder", "config", "contracts", "renderer", "sources"},
    "sources": {"contracts"},
}


def test_pipeline_reporting_follows_the_planned_dependency_dag():
    package_dir = Path(pipeline_reporting.__file__).resolve().parent
    actual_files = {path.name for path in package_dir.glob("*.py")}

    assert actual_files == EXPECTED_FILES
    for module, allowed in ALLOWED_INTERNAL_IMPORTS.items():
        path = package_dir / f"{module}.py"
        assert _pipeline_reporting_imports(path) <= allowed

    quality_consumers = {
        path.stem
        for path in package_dir.glob("*.py")
        if _imports_package(path, "quality")
    }
    assert quality_consumers == {"sources"}


def _pipeline_reporting_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.module is None:
            continue
        if node.module == "pipeline_reporting":
            modules.update(alias.name.split(".", maxsplit=1)[0] for alias in node.names)
        elif node.module.startswith("pipeline_reporting."):
            modules.add(node.module.split(".", maxsplit=1)[1])
    return modules


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
