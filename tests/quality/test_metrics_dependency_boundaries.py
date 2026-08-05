from __future__ import annotations

import ast
from pathlib import Path

import quality.metrics as metrics


def _package_dir() -> Path:
    return Path(metrics.__file__).resolve().parent


def _internal_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        node.module.split(".", maxsplit=1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.level > 0
        and node.module is not None
    }


def test_metrics_grain_modules_do_not_depend_on_io_orchestration():
    package_dir = _package_dir()
    forbidden = {"builder", "service", "sources", "validation", "writer"}

    for module in ("case", "operation", "request_event", "request_group"):
        assert not (_internal_imports(package_dir / f"{module}.py") & forbidden)


def test_writer_and_sources_do_not_import_each_other():
    package_dir = _package_dir()

    assert "sources" not in _internal_imports(package_dir / "writer.py")
    assert "writer" not in _internal_imports(package_dir / "sources.py")


def test_metrics_foundation_does_not_depend_on_build_or_io_layers():
    package_dir = _package_dir()
    forbidden = {"builder", "service", "sources", "validation", "writer"}

    for module in ("contracts", "primitives"):
        assert not (_internal_imports(package_dir / f"{module}.py") & forbidden)
