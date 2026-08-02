from __future__ import annotations

import ast
from pathlib import Path

import quality.flaky_store as flaky_store


EXPECTED_FILES = {
    "__init__.py",
    "backup.py",
    "contracts.py",
    "epoch.py",
    "facade.py",
    "governance.py",
    "import_service.py",
    "migration.py",
    "projection.py",
    "repository.py",
}

ALLOWED_INTERNAL_IMPORTS = {
    "__init__": {"contracts", "facade", "migration"},
    "backup": {"contracts", "repository"},
    "contracts": set(),
    "epoch": {"contracts", "repository"},
    "facade": {
        "contracts",
        "epoch",
        "governance",
        "import_service",
        "migration",
        "projection",
        "repository",
    },
    "governance": {"contracts", "projection", "repository"},
    "import_service": {"contracts", "repository"},
    "migration": {"backup", "contracts", "repository"},
    "projection": {"contracts", "repository"},
    "repository": {"contracts"},
}


def _package_dir() -> Path:
    return Path(flaky_store.__file__).resolve().parent


def _tree(module: str) -> ast.Module:
    return ast.parse((_package_dir() / f"{module}.py").read_text(encoding="utf-8"))


def _internal_imports(module: str) -> set[str]:
    result: set[str] = set()
    for node in ast.walk(_tree(module)):
        if not isinstance(node, ast.ImportFrom) or node.level == 0:
            continue
        if node.module is not None:
            result.add(node.module.split(".", maxsplit=1)[0])
        else:
            result.update(alias.name.split(".", maxsplit=1)[0] for alias in node.names)
    return result


def _string_literals(module: str) -> tuple[str, ...]:
    return tuple(
        node.value
        for node in ast.walk(_tree(module))
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    )


def test_flaky_store_directory_uses_only_the_planned_python_files():
    actual = {path.name for path in _package_dir().glob("*.py")}
    assert actual == EXPECTED_FILES
    assert not {"utils.py", "helpers.py", "common.py", "dao.py"} & actual


def test_flaky_store_internal_imports_follow_the_dependency_dag():
    for module, allowed in ALLOWED_INTERNAL_IMPORTS.items():
        assert _internal_imports(module) <= allowed


def test_facade_contains_no_sql_or_sqlite_connection_details():
    facade = _tree("facade")
    sql_tokens = (
        "SELECT ",
        "INSERT ",
        "UPDATE ",
        "DELETE ",
        "PRAGMA ",
        "BEGIN ",
        "COMMIT",
        "ROLLBACK",
    )

    assert not any(
        token in value.upper()
        for value in _string_literals("facade")
        for token in sql_tokens
    )
    assert not any(
        isinstance(node, (ast.Import, ast.ImportFrom))
        and (
            any(alias.name == "sqlite3" for alias in node.names)
            or node.module == "sqlite3"
        )
        for node in ast.walk(facade)
    )


def test_domain_services_do_not_control_transactions_or_create_connections():
    forbidden = {
        "BEGIN",
        "COMMIT",
        "DELETE",
        "INSERT",
        "PRAGMA",
        "ROLLBACK",
        "SELECT",
        "UPDATE",
    }
    for module in ("import_service", "projection", "governance", "epoch"):
        assert not any(
            value.strip().upper().split(maxsplit=1)[0].rstrip(";") in forbidden
            for value in _string_literals(module)
            if value.strip()
        )
        assert "connect" not in {
            node.func.attr
            for node in ast.walk(_tree(module))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        assert "execute" not in {
            node.func.attr
            for node in ast.walk(_tree(module))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }


def test_only_repository_creates_the_main_sqlite_connection():
    modules_with_connect = set()
    for filename in EXPECTED_FILES - {"__init__.py"}:
        module = filename.removesuffix(".py")
        if any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "connect"
            for node in ast.walk(_tree(module))
        ):
            modules_with_connect.add(module)

    assert modules_with_connect == {"backup", "repository"}
    assert "BEGIN IMMEDIATE" in "\n".join(_string_literals("repository"))
    assert "BEGIN IMMEDIATE;" in "\n".join(_string_literals("migration"))
