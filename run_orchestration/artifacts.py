from __future__ import annotations

from pathlib import Path
import shutil
from typing import Sequence
import uuid

from .paths import PROJECT_ROOT


def extract_junit_path(pytest_args: Sequence[str]) -> Path | None:
    index = 0
    while index < len(pytest_args):
        arg = pytest_args[index]
        if arg == "--junitxml" and index + 1 < len(pytest_args):
            return resolve_report_path(pytest_args[index + 1])
        if arg.startswith("--junitxml="):
            value = arg.split("=", 1)[1]
            return resolve_report_path(value)
        index += 1
    return None


def resolve_report_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (PROJECT_ROOT / path).resolve()


def preserve_allure_results(results_dir: Path) -> Path | None:
    if not results_dir.exists():
        return None

    temp_root = (
        results_dir.parent / f".allure-results-preserve-{uuid.uuid4().hex}"
    )
    shutil.copytree(results_dir, temp_root)
    return temp_root


def restore_allure_results(
    results_dir: Path, preserved_results: Path | None
) -> None:
    if preserved_results is None:
        return

    try:
        results_dir.mkdir(parents=True, exist_ok=True)
        for item in preserved_results.iterdir():
            target = results_dir / item.name
            if target.exists():
                target = (
                    results_dir
                    / f"{item.stem}-{uuid.uuid4().hex}{item.suffix}"
                )

            if item.is_dir() and not item.is_symlink():
                shutil.copytree(item, target)
            else:
                shutil.copy2(item, target)
    finally:
        shutil.rmtree(preserved_results, ignore_errors=True)
