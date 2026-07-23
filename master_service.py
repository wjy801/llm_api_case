from __future__ import annotations

from pathlib import Path
import subprocess
import sys


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_TEST_PATH = "module"


def collect_test_cases(test_path: str | Path = DEFAULT_TEST_PATH) -> list[str]:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            str(test_path),
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(_collect_error_message(completed))

    return _parse_pytest_nodeids(completed.stdout)


def _parse_pytest_nodeids(output: str) -> list[str]:
    case_pool: list[str] = []
    for line in output.splitlines():
        pytest_nodeid = line.strip()
        if not pytest_nodeid or "::" not in pytest_nodeid:
            continue
        if pytest_nodeid not in case_pool:
            case_pool.append(pytest_nodeid)
    return case_pool


def _collect_error_message(completed: subprocess.CompletedProcess[str]) -> str:
    lines = ["pytest 用例收集失败。"]
    if completed.stdout.strip():
        lines.extend(["stdout:", completed.stdout.strip()])
    if completed.stderr.strip():
        lines.extend(["stderr:", completed.stderr.strip()])
    return "\n".join(lines)


if __name__ == "__main__":
    case_pool = collect_test_cases()
    for pytest_nodeid in case_pool:
        print(pytest_nodeid)
