from __future__ import annotations

import ast
from pathlib import Path

import quality.observation_report as observation_report


EXPECTED_FILES = {
    "__init__.py",
    "attention.py",
    "builder.py",
    "contracts.py",
    "loader.py",
    "renderer.py",
    "service.py",
    "validation.py",
    "writer.py",
}

ALLOWED_INTERNAL_IMPORTS = {
    "__init__": {"contracts", "renderer", "service"},
    "attention": set(),
    "builder": {"attention", "contracts"},
    "contracts": set(),
    "loader": {"contracts", "validation"},
    "renderer": set(),
    "service": {"builder", "contracts", "loader", "renderer", "validation", "writer"},
    "validation": {"contracts"},
    "writer": set(),
}


def _package_dir() -> Path:
    return Path(observation_report.__file__).resolve().parent


def _internal_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.level == 0:
            continue
        if node.module is not None:
            modules.add(node.module.split(".", maxsplit=1)[0])
        else:
            modules.update(alias.name.split(".", maxsplit=1)[0] for alias in node.names)
    return modules


def _called_attributes(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }


def test_observation_directory_uses_only_the_planned_files():
    package_dir = _package_dir()
    actual = {path.name for path in package_dir.glob("*.py")}

    assert actual == EXPECTED_FILES
    assert not {"utils.py", "helpers.py", "common.py"} & actual


def test_observation_internal_imports_follow_the_dependency_dag():
    package_dir = _package_dir()

    for module, allowed in ALLOWED_INTERNAL_IMPORTS.items():
        assert _internal_imports(package_dir / f"{module}.py") <= allowed


def test_loader_builder_attention_and_renderer_do_not_perform_forbidden_io():
    package_dir = _package_dir()
    write_calls = {"write", "write_bytes", "write_text", "unlink"}
    all_file_calls = write_calls | {"open", "read_bytes", "read_text"}

    assert not (_called_attributes(package_dir / "loader.py") & write_calls)
    for module in ("builder", "attention", "renderer"):
        assert not (_called_attributes(package_dir / f"{module}.py") & all_file_calls)


def test_writer_has_no_dependency_on_business_decision_modules():
    forbidden = {"attention", "builder", "loader", "renderer", "service", "validation"}

    assert not (_internal_imports(_package_dir() / "writer.py") & forbidden)
