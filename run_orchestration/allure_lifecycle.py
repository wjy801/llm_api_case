from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from .paths import DEFAULT_ALLURE_RESULTS_DIR, PROJECT_ROOT


ALLURE_REPORT_DIR = "allure-report"
HISTORY_REPORT_DIR = "history_report"
HISTORY_LATEST_DIR = "latest"
RUNNER_MANAGED_ALLURE_ENV = "API_CASE_RUNNER_MANAGED_ALLURE"


class AllureRunLifecycle:
    """Single owner of framework Allure raw/report/history artifacts."""

    def __init__(
        self,
        *,
        results_dir: Path,
        project_root: Path = PROJECT_ROOT,
        generate_report: bool,
        generate_history: bool,
        history_keep_limit: int,
        reporter: Callable[[str], None] = print,
        pooled: bool = False,
    ) -> None:
        self.project_root = project_root.resolve()
        self.results_dir = _resolve_path(results_dir, self.project_root)
        self.report_dir = self.project_root / ALLURE_REPORT_DIR
        self.history_root = self.project_root / HISTORY_REPORT_DIR
        self.generate_report = generate_report
        self.generate_history = generate_history
        self.history_keep_limit = history_keep_limit
        self.reporter = reporter
        self.pooled = pooled
        self._prepared = False
        self._finalized = False
        self._pool_dirs: dict[str, Path] = {}
        self._merged_pools: set[str] = set()
        self._has_conflict = False
        self._temp_root: Path | None = None

    def prepare(self) -> None:
        if self._prepared:
            return
        self._prepared = True
        try:
            clean_directory(self.results_dir)
            if self.pooled:
                self._temp_root = Path(
                    tempfile.mkdtemp(
                        prefix=".allure-run-",
                        dir=self.results_dir.parent,
                    )
                )
            self.reporter(f"Allure raw results cleaned: {self.results_dir}")
        except Exception as error:
            self.reporter(
                "Allure lifecycle prepare failed open: "
                f"{type(error).__name__}: {error}"
            )

    def pool_args(self, stage_id: str, pytest_args: Sequence[str]) -> list[str]:
        self.prepare()
        if self._temp_root is None:
            return list(pytest_args)
        pool_dir = self._temp_root / _safe_stage_name(stage_id)
        self._pool_dirs[stage_id] = pool_dir
        return replace_allure_results_args(pytest_args, pool_dir)

    def merge_pool(self, stage_id: str) -> None:
        if stage_id in self._merged_pools:
            return
        self._merged_pools.add(stage_id)
        pool_dir = self._pool_dirs.get(stage_id)
        if pool_dir is None or not pool_dir.exists():
            return
        try:
            self.results_dir.mkdir(parents=True, exist_ok=True)
            for source in pool_dir.iterdir():
                target = self.results_dir / source.name
                if not target.exists():
                    _copy_artifact(source, target)
                    continue
                if source.is_file() and target.is_file() and _same_file(source, target):
                    continue
                self._has_conflict = True
                self.reporter(
                    "Allure artifact conflict preserved in pool directory: "
                    f"stage={stage_id}, name={source.name}"
                )
        except Exception as error:
            self.reporter(
                "Allure pool merge failed open: "
                f"stage={stage_id}, {type(error).__name__}: {error}"
            )

    def finalize(self) -> None:
        if self._finalized:
            return
        self._finalized = True
        try:
            if not self.generate_report:
                self.reporter(
                    "Allure HTML report generation skipped by "
                    "GENERATE_ALLURE_REPORT=FALSE."
                )
                return
            allure_executable = find_allure_executable(self.project_root)
            if allure_executable is None:
                self.reporter(
                    "Allure CLI not found; skipped HTML report generation."
                )
                return
            env = build_allure_env()
            completed = run_allure_generate(
                allure_executable,
                self.results_dir,
                self.report_dir,
                cwd=self.project_root,
                env=env,
            )
            if not self._report_generate_result(
                completed,
                success_message=f"Allure HTML report generated: {self.report_dir}",
                failure_message="Allure HTML report generation failed.",
            ):
                return
            if not self.generate_history:
                self.reporter(
                    "Allure history report generation skipped by "
                    "GENERATE_HISTORY_REPORT=FALSE."
                )
                return
            self._generate_history(allure_executable, env)
        except Exception as error:
            self.reporter(
                "Allure lifecycle finalize failed open: "
                f"{type(error).__name__}: {error}"
            )
        finally:
            if self._temp_root is not None and not self._has_conflict:
                shutil.rmtree(self._temp_root, ignore_errors=True)

    def _generate_history(
        self,
        allure_executable: str,
        env: dict[str, str],
    ) -> None:
        report_dir = self.history_root / history_report_name()
        completed = run_allure_generate(
            allure_executable,
            self.results_dir,
            report_dir,
            cwd=self.project_root,
            env=env,
            single_file=True,
        )
        if not self._report_generate_result(
            completed,
            success_message=f"Allure history report generated: {report_dir}",
            failure_message="Allure history report generation failed.",
        ):
            return
        update_latest_history_report(self.history_root, report_dir)
        cleanup_old_history_reports(
            self.history_root,
            self.history_keep_limit,
        )
        self.reporter(
            "Allure latest history report updated: "
            f"{self.history_root / HISTORY_LATEST_DIR}"
        )

    def _report_generate_result(
        self,
        completed: subprocess.CompletedProcess[str],
        *,
        success_message: str,
        failure_message: str,
    ) -> bool:
        if completed.returncode == 0:
            self.reporter(success_message)
            return True
        self.reporter(failure_message)
        if completed.stdout.strip():
            self.reporter(completed.stdout.strip())
        if completed.stderr.strip():
            self.reporter(completed.stderr.strip())
        return False


def create_runner_allure_lifecycle(
    pytest_args: Sequence[str],
    *,
    generate_report: bool,
    generate_history: bool,
    history_keep_limit: int,
    reporter: Callable[[str], None] = print,
) -> AllureRunLifecycle:
    return AllureRunLifecycle(
        results_dir=extract_allure_results_dir(pytest_args),
        generate_report=generate_report,
        generate_history=generate_history,
        history_keep_limit=history_keep_limit,
        reporter=reporter,
        pooled=True,
    )


def extract_allure_results_dir(pytest_args: Sequence[str]) -> Path:
    index = 0
    while index < len(pytest_args):
        argument = pytest_args[index]
        if argument == "--alluredir" and index + 1 < len(pytest_args):
            return _resolve_path(Path(pytest_args[index + 1]), PROJECT_ROOT)
        if argument.startswith("--alluredir="):
            return _resolve_path(Path(argument.split("=", 1)[1]), PROJECT_ROOT)
        index += 1
    return DEFAULT_ALLURE_RESULTS_DIR


def replace_allure_results_args(
    pytest_args: Sequence[str],
    results_dir: Path,
) -> list[str]:
    cleaned: list[str] = []
    args = list(pytest_args)
    index = 0
    while index < len(args):
        argument = args[index]
        if argument == "--alluredir":
            index += 2
            continue
        if argument.startswith("--alluredir=") or argument == "--clean-alluredir":
            index += 1
            continue
        cleaned.append(argument)
        index += 1
    cleaned.extend([f"--alluredir={results_dir}", "--clean-alluredir"])
    return cleaned


def run_allure_generate(
    allure_executable: str,
    results_dir: Path,
    report_dir: Path,
    *,
    cwd: Path,
    env: dict[str, str],
    single_file: bool = False,
) -> subprocess.CompletedProcess[str]:
    command = [
        allure_executable,
        "generate",
        str(results_dir),
        "-o",
        str(report_dir),
        "--clean",
    ]
    if single_file:
        command.append("--single-file")
    return subprocess.run(
        command,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def history_report_name() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def update_latest_history_report(history_root: Path, report_dir: Path) -> None:
    latest_dir = history_root / HISTORY_LATEST_DIR
    if latest_dir.exists():
        if latest_dir.is_dir() and not latest_dir.is_symlink():
            shutil.rmtree(latest_dir)
        else:
            latest_dir.unlink()
    shutil.copytree(report_dir, latest_dir)


def cleanup_old_history_reports(history_root: Path, keep_limit: int) -> None:
    if keep_limit < 1 or not history_root.exists():
        return
    report_dirs = [
        path
        for path in history_root.iterdir()
        if path.is_dir()
        and not path.is_symlink()
        and path.name != HISTORY_LATEST_DIR
    ]
    report_dirs.sort(key=lambda path: path.name, reverse=True)
    for old_report_dir in report_dirs[keep_limit:]:
        shutil.rmtree(old_report_dir)


def clean_directory(directory: Path) -> None:
    if directory.exists() and not directory.is_dir():
        directory.unlink()
    directory.mkdir(parents=True, exist_ok=True)
    for item in directory.iterdir():
        if item.is_dir() and not item.is_symlink():
            shutil.rmtree(item)
        else:
            item.unlink()


def find_allure_executable(rootpath: Path) -> str | None:
    for executable in (
        rootpath / "node_modules" / ".bin" / "allure.cmd",
        rootpath / "node_modules" / ".bin" / "allure",
    ):
        if executable.exists():
            return str(executable)
    return shutil.which("allure")


def build_allure_env() -> dict[str, str]:
    env = os.environ.copy()
    if shutil.which("java", path=env.get("PATH")):
        return env
    java_executable = find_bundled_java()
    if java_executable is None:
        return env
    env["PATH"] = str(java_executable.parent) + os.pathsep + env.get("PATH", "")
    return env


def find_bundled_java() -> Path | None:
    for root in (Path("D:/app"), Path("C:/Program Files"), Path("C:/Program Files (x86)")):
        if not root.exists():
            continue
        for pattern in ("*/jbr/bin/java.exe", "*/jre/bin/java.exe", "*/bin/java.exe"):
            for java_executable in root.glob(pattern):
                if java_executable.is_file():
                    return java_executable
    return None


def _resolve_path(path: Path, root: Path) -> Path:
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def _safe_stage_name(stage_id: str) -> str:
    return "".join(
        character if character.isalnum() or character in {"-", "_"} else "_"
        for character in stage_id
    )


def _copy_artifact(source: Path, target: Path) -> None:
    if source.is_dir() and not source.is_symlink():
        shutil.copytree(source, target)
    else:
        shutil.copy2(source, target)


def _same_file(left: Path, right: Path) -> bool:
    if left.stat().st_size != right.stat().st_size:
        return False
    return _sha256(left) == _sha256(right)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
