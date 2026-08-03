from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
from datetime import datetime

import allure
import pytest

from config import settings
from util import (
    attach_media_download_steps,
    start_media_download_collection,
    stop_media_download_collection,
)
from common import (
    BaseRequest,
    attach_model_result_file,
    start_model_result_collection,
    stop_model_result_collection,
)


ALLURE_REPORT_DIR = "allure-report"
HISTORY_REPORT_DIR = "history_report"
HISTORY_LATEST_DIR = "latest"
TEST_RESOURCE_STATE_ATTR = "_api_case_resource_state"


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("protocol-testing")
    group.addoption(
        "--protocol-model-csv",
        action="store",
        default=None,
        help="指定协议测试通用模型 CSV 文件路径，相对路径基于 module/protocol_testing。",
    )
    group.addoption(
        "--text-model-id",
        action="store",
        default=None,
        help="指定用于快速探测 OpenAI 和 Anthropic 协议的文本模型 model_id。",
    )


@pytest.fixture(scope="function", autouse=True)
def collect_test_resources(request: pytest.FixtureRequest):
    setattr(
        request.node,
        TEST_RESOURCE_STATE_ATTR,
        {
            "media_download_token": start_media_download_collection(),
            "model_result_token": start_model_result_collection(),
        },
    )
    yield


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_teardown(item: pytest.Item):
    outcome = yield
    _attach_collected_test_resources(item)
    outcome.get_result()


def _attach_collected_test_resources(item: pytest.Item) -> None:
    state = getattr(item, TEST_RESOURCE_STATE_ATTR, None)
    if not state:
        return

    media_download_token = state.pop("media_download_token", None)
    if media_download_token is not None:
        media_download_tasks = stop_media_download_collection(media_download_token)
        attach_media_download_steps(media_download_tasks)

    model_result_token = state.pop("model_result_token", None)
    if model_result_token is not None:
        model_result_files = stop_model_result_collection(model_result_token)
        if model_result_files:
            with allure.step("模型响应结果"):
                for file_path in model_result_files:
                    attach_model_result_file(file_path)


@pytest.fixture
def api() -> BaseRequest:
    client = BaseRequest()
    try:
        yield client
    finally:
        client.close()


def pytest_sessionstart(session: pytest.Session) -> None:
    if hasattr(session.config, "workerinput"):
        return

    if getattr(session.config.option, "collectonly", False):
        return

    results_dir = _get_allure_results_dir(session.config)
    if results_dir is None:
        return

    _clean_directory(results_dir)
    _write_terminal_line(session.config, f"Allure raw results cleaned: {results_dir}")


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    if hasattr(session.config, "workerinput"):
        return

    if getattr(session.config.option, "collectonly", False):
        return

    if not settings.generate_allure_report:
        _write_terminal_line(
            session.config,
            "Allure HTML report generation skipped by GENERATE_ALLURE_REPORT=FALSE.",
        )
        return

    results_dir = _get_allure_results_dir(session.config)
    if results_dir is None:
        return

    allure_executable = _find_allure_executable(session.config.rootpath)
    if allure_executable is None:
        _write_terminal_line(
            session.config,
            "Allure CLI not found; skipped HTML report generation.",
        )
        return

    env = _build_allure_env()
    report_dir = session.config.rootpath / ALLURE_REPORT_DIR
    completed = _run_allure_generate(
        allure_executable,
        results_dir,
        report_dir,
        cwd=session.config.rootpath,
        env=env,
    )

    if not _report_allure_generate_result(
        session.config,
        completed,
        success_message=f"Allure HTML report generated: {report_dir}",
        failure_message="Allure HTML report generation failed.",
    ):
        return

    if not settings.generate_history_report:
        _write_terminal_line(
            session.config,
            "Allure history report generation skipped by GENERATE_HISTORY_REPORT=FALSE.",
        )
        return

    _generate_history_report(
        session.config,
        allure_executable,
        results_dir,
        env,
    )


def _run_allure_generate(
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


def _report_allure_generate_result(
    config: pytest.Config,
    completed: subprocess.CompletedProcess[str],
    *,
    success_message: str,
    failure_message: str,
) -> bool:
    if completed.returncode == 0:
        _write_terminal_line(config, success_message)
        return True

    _write_terminal_line(config, failure_message)
    if completed.stdout.strip():
        _write_terminal_line(config, completed.stdout.strip())
    if completed.stderr.strip():
        _write_terminal_line(config, completed.stderr.strip())
    return False


def _generate_history_report(
    config: pytest.Config,
    allure_executable: str,
    results_dir: Path,
    env: dict[str, str],
) -> None:
    history_root = config.rootpath / HISTORY_REPORT_DIR
    report_dir = history_root / _history_report_name()
    completed = _run_allure_generate(
        allure_executable,
        results_dir,
        report_dir,
        cwd=config.rootpath,
        env=env,
        single_file=True,
    )

    if not _report_allure_generate_result(
        config,
        completed,
        success_message=f"Allure history report generated: {report_dir}",
        failure_message="Allure history report generation failed.",
    ):
        return

    _update_latest_history_report(history_root, report_dir)
    _cleanup_old_history_reports(history_root, settings.history_report_keep_limit)
    _write_terminal_line(config, f"Allure latest history report updated: {history_root / HISTORY_LATEST_DIR}")


def _history_report_name() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _update_latest_history_report(history_root: Path, report_dir: Path) -> None:
    latest_dir = history_root / HISTORY_LATEST_DIR
    if latest_dir.exists():
        if latest_dir.is_dir() and not latest_dir.is_symlink():
            shutil.rmtree(latest_dir)
        else:
            latest_dir.unlink()

    shutil.copytree(report_dir, latest_dir)


def _cleanup_old_history_reports(history_root: Path, keep_limit: int) -> None:
    if keep_limit < 1 or not history_root.exists():
        return

    report_dirs = [
        path
        for path in history_root.iterdir()
        if path.is_dir() and not path.is_symlink() and path.name != HISTORY_LATEST_DIR
    ]
    report_dirs.sort(key=lambda path: path.name, reverse=True)

    for old_report_dir in report_dirs[keep_limit:]:
        shutil.rmtree(old_report_dir)


def _get_allure_results_dir(config: pytest.Config) -> Path | None:
    alluredir = config.getoption("--alluredir", default=None)
    if not alluredir:
        return None

    results_dir = Path(alluredir)
    if not results_dir.is_absolute():
        results_dir = config.rootpath / results_dir
    return results_dir


def _clean_directory(directory: Path) -> None:
    if directory.exists() and not directory.is_dir():
        directory.unlink()

    directory.mkdir(parents=True, exist_ok=True)

    for item in directory.iterdir():
        if item.is_dir() and not item.is_symlink():
            shutil.rmtree(item)
            continue

        item.unlink()


def _find_allure_executable(rootpath: Path) -> str | None:
    for executable in (
        rootpath / "node_modules" / ".bin" / "allure.cmd",
        rootpath / "node_modules" / ".bin" / "allure",
    ):
        if executable.exists():
            return str(executable)

    return shutil.which("allure")


def _build_allure_env() -> dict[str, str]:
    env = os.environ.copy()
    if shutil.which("java", path=env.get("PATH")):
        return env

    java_executable = _find_bundled_java()
    if java_executable is None:
        return env

    java_bin = str(java_executable.parent)
    env["PATH"] = java_bin + os.pathsep + env.get("PATH", "")
    return env


def _find_bundled_java() -> Path | None:
    search_roots = [
        Path("D:/app"),
        Path("C:/Program Files"),
        Path("C:/Program Files (x86)"),
    ]

    for root in search_roots:
        if not root.exists():
            continue

        for pattern in ("*/jbr/bin/java.exe", "*/jre/bin/java.exe", "*/bin/java.exe"):
            for java_executable in root.glob(pattern):
                if java_executable.is_file():
                    return java_executable

    return None


def _write_terminal_line(config: pytest.Config, message: str) -> None:
    reporter = config.pluginmanager.get_plugin("terminalreporter")
    if reporter is None:
        print(message)
        return

    reporter.write_line(message)
