from __future__ import annotations

import ast
from dataclasses import fields
import inspect
from pathlib import Path

import quality.flaky_store as flaky_store
from quality.flaky_store import (
    DEFAULT_BUSY_TIMEOUT_MS,
    FlakyStore,
    FlakyStoreError,
    MIGRATIONS_DIRECTORY,
    StoreImportOutcome,
    StoreInitialization,
)


def test_flaky_store_is_a_package_with_the_stable_root_contract():
    package_dir = Path(flaky_store.__file__).resolve().parent

    assert flaky_store.__spec__.submodule_search_locations is not None
    assert package_dir.name == "flaky_store"
    assert not (package_dir.parent / "flaky_store.py").exists()
    assert "FlakyStore" in flaky_store.__all__
    assert "FlakyV3Service" in flaky_store.__all__
    assert "migrate_store" in flaky_store.__all__
    assert DEFAULT_BUSY_TIMEOUT_MS == 5000
    assert MIGRATIONS_DIRECTORY == package_dir / "migrations"


def test_flaky_store_constructor_and_public_method_signatures_are_compatible():
    expected = {
        "migrate": ("self",),
        "__init__": (
            "self",
            "database_path",
            "busy_timeout_ms",
            "migrations_directory",
        ),
        "import_run": ("self", "metadata", "candidates"),
        "evaluate_run": ("self", "run_id", "config"),
        "states": (
            "self",
            "case_id",
            "param_hash",
            "environment",
            "execution_profile",
            "state_epoch",
        ),
        "confirm_flaky": ("self", "request"),
        "mark_not_flaky": ("self", "request"),
        "quarantine": ("self", "request"),
        "start_recovery": ("self", "request"),
        "cancel_quarantine": ("self", "request"),
        "governance": ("self", "status", "overdue", "query_time"),
        "rebuild_states": ("self", "apply", "config"),
        "reset_epoch": ("self", "request", "epoch_scope_key"),
        "history": (
            "self",
            "case_id",
            "param_hash",
            "environment",
            "execution_profile",
            "state_epoch",
        ),
        "check_database": ("self",),
        "import_normal": ("self", "request", "now"),
        "import_probe": ("self", "request", "now"),
        "recovery_start": ("self", "request", "now"),
        "recovery_status": ("self", "flaky_key"),
        "recovery_close": ("self", "request", "now"),
        "recovery_cancel": ("self", "request", "now"),
    }
    for name, parameters in expected.items():
        assert tuple(inspect.signature(getattr(FlakyStore, name)).parameters) == parameters

    constructor = inspect.signature(FlakyStore.__init__).parameters
    assert constructor["busy_timeout_ms"].default == 5000
    assert constructor["migrations_directory"].default == MIGRATIONS_DIRECTORY
    assert constructor["busy_timeout_ms"].kind is inspect.Parameter.KEYWORD_ONLY
    assert constructor["migrations_directory"].kind is inspect.Parameter.KEYWORD_ONLY


def test_flaky_store_compatibility_dataclasses_and_error_code_are_stable():
    assert tuple(field.name for field in fields(StoreInitialization)) == (
        "schema_version",
        "quick_check",
        "migration_applied",
        "backup_created",
    )
    assert tuple(field.name for field in fields(StoreImportOutcome)) == (
        "imported",
        "inserted_count",
        "initialization",
    )
    assert FlakyStoreError("sample_code", "message").code == "sample_code"


def test_flaky_store_package_init_contains_no_business_implementation():
    tree = ast.parse(Path(flaky_store.__file__).read_text(encoding="utf-8"))
    forbidden = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
    assert not any(isinstance(node, forbidden) for node in ast.walk(tree))
