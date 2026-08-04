from __future__ import annotations

from collections.abc import Mapping
import json
import os
from pathlib import Path
import tempfile
from typing import Sequence

from .paths import PROJECT_ROOT


RUNNER_EXECUTION_SCHEMA_VERSION = "runner-execution.v1"
DEFAULT_EXECUTION_RESULT_PATH = PROJECT_ROOT / "reports" / "execution-result.json"


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


def write_execution_result_atomic(
    payload: Mapping[str, object],
    path: str | Path = DEFAULT_EXECUTION_RESULT_PATH,
) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix=f".{target.name}.",
            suffix=".tmp",
            dir=target.parent,
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            json.dump(
                dict(payload),
                temporary_file,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            temporary_file.write("\n")
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_path, target)
        temporary_path = None
        return target
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
