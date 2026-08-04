from __future__ import annotations

from pathlib import Path
import subprocess


PROJECT_ROOT = Path(__file__).resolve().parents[2]
GITIGNORE_PATH = PROJECT_ROOT / ".gitignore"
HASH_FIXTURE_PATH = (
    "tests/quality/fixtures/pipeline_report_cleanup/"
    "merged/request-metrics.jsonl"
)
FIXTURE_ROOT = PROJECT_ROOT / "tests" / "quality" / "fixtures"


def test_tests_directory_is_not_controlled_by_an_allowlist() -> None:
    content = GITIGNORE_PATH.read_text(encoding="utf-8")

    assert "/tests/*" not in content
    assert "!/tests/" not in content


def test_new_test_file_is_not_ignored_by_default() -> None:
    result = subprocess.run(
        [
            "git",
            "check-ignore",
            "--quiet",
            "--no-index",
            "tests/test_example.py",
        ],
        cwd=PROJECT_ROOT,
        check=False,
    )

    assert result.returncode == 1


def test_current_test_modules_are_not_ignored() -> None:
    test_paths = sorted(
        path.relative_to(PROJECT_ROOT).as_posix()
        for path in (PROJECT_ROOT / "tests").rglob("test_*.py")
    )
    result = subprocess.run(
        ["git", "check-ignore", "--no-index", "--stdin"],
        cwd=PROJECT_ROOT,
        input="\n".join(test_paths),
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1, result.stdout


def test_hash_protected_quality_fixtures_use_lf_checkout() -> None:
    result = subprocess.run(
        ["git", "check-attr", "eol", "--", HASH_FIXTURE_PATH],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.strip().endswith(": eol: lf")


def test_quality_fixture_worktree_bytes_use_lf() -> None:
    fixture_paths = sorted(path for path in FIXTURE_ROOT.rglob("*") if path.is_file())

    assert fixture_paths
    for path in fixture_paths:
        assert b"\r\n" not in path.read_bytes(), path.relative_to(PROJECT_ROOT)
