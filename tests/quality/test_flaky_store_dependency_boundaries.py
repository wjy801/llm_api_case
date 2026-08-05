from __future__ import annotations

import ast
from pathlib import Path

import quality.flaky_store as flaky_store


def _package_dir() -> Path:
    return Path(flaky_store.__file__).resolve().parent


def _tree(module: str) -> ast.Module:
    return ast.parse((_package_dir() / f"{module}.py").read_text(encoding="utf-8"))


def _string_literals(module: str) -> tuple[str, ...]:
    return tuple(
        node.value
        for node in ast.walk(_tree(module))
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    )


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


def test_only_storage_infrastructure_creates_sqlite_connections():
    modules_with_connect = set()
    for path in _package_dir().glob("*.py"):
        if path.name == "__init__.py":
            continue
        module = path.stem
        if any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "connect"
            for node in ast.walk(_tree(module))
        ):
            modules_with_connect.add(module)

    assert "repository" in modules_with_connect
    assert modules_with_connect <= {"backup", "repository"}
    assert "BEGIN IMMEDIATE" in "\n".join(_string_literals("repository"))
    assert "BEGIN IMMEDIATE;" in "\n".join(_string_literals("migration"))
