from __future__ import annotations

from pathlib import Path
import subprocess

from run_orchestration import allure_lifecycle


def _allure_dir_from_args(args: list[str]) -> Path:
    value = next(arg.split("=", 1)[1] for arg in args if arg.startswith("--alluredir="))
    return Path(value)


def test_pooled_lifecycle_uses_distinct_dirs_and_generates_once(
    monkeypatch,
    tmp_path: Path,
) -> None:
    calls: list[tuple[Path, Path, bool]] = []

    def fake_generate(
        _executable,
        results_dir,
        report_dir,
        *,
        cwd,
        env,
        single_file=False,
    ):
        calls.append((results_dir, report_dir, single_file))
        report_dir.mkdir(parents=True, exist_ok=True)
        (report_dir / "index.html").write_text("ok", encoding="utf-8")
        return subprocess.CompletedProcess([], 0, "", "")

    monkeypatch.setattr(allure_lifecycle, "find_allure_executable", lambda _root: "allure")
    monkeypatch.setattr(allure_lifecycle, "run_allure_generate", fake_generate)
    lifecycle = allure_lifecycle.AllureRunLifecycle(
        results_dir=tmp_path / "custom-results",
        project_root=tmp_path,
        generate_report=True,
        generate_history=True,
        history_keep_limit=2,
        reporter=lambda _message: None,
        pooled=True,
    )

    lifecycle.prepare()
    parallel_dir = _allure_dir_from_args(lifecycle.pool_args("parallel-pool", ["-q"]))
    serial_dir = _allure_dir_from_args(lifecycle.pool_args("serial-pool", ["-q"]))
    assert parallel_dir != serial_dir
    parallel_dir.mkdir(parents=True)
    serial_dir.mkdir(parents=True)
    (parallel_dir / "parallel-result.json").write_text("{}", encoding="utf-8")
    (serial_dir / "serial-attachment.txt").write_text("evidence", encoding="utf-8")

    lifecycle.merge_pool("parallel-pool")
    lifecycle.merge_pool("serial-pool")
    lifecycle.finalize()

    assert (tmp_path / "custom-results" / "parallel-result.json").exists()
    assert (tmp_path / "custom-results" / "serial-attachment.txt").exists()
    assert len(calls) == 2
    assert calls[0] == (
        tmp_path / "custom-results",
        tmp_path / "allure-report",
        False,
    )
    assert calls[1][2] is True
    assert (tmp_path / "history_report" / "latest" / "index.html").exists()


def test_replace_allure_args_preserves_custom_final_path_only_outside_pool(tmp_path: Path) -> None:
    pool_dir = tmp_path / "pool"

    args = allure_lifecycle.replace_allure_results_args(
        ["-q", "--alluredir", "custom-results", "--clean-alluredir"],
        pool_dir,
    )

    assert args == ["-q", f"--alluredir={pool_dir}", "--clean-alluredir"]


def test_collect_path_resolution_supports_both_alluredir_syntaxes(tmp_path: Path) -> None:
    first = allure_lifecycle.extract_allure_results_dir(
        ["--alluredir", str(tmp_path / "one")]
    )
    second = allure_lifecycle.extract_allure_results_dir(
        [f"--alluredir={tmp_path / 'two'}"]
    )

    assert first == (tmp_path / "one").resolve()
    assert second == (tmp_path / "two").resolve()
