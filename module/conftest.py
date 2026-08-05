from __future__ import annotations

import os
from pathlib import Path

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
    TestContext,
    attach_model_result_file,
    start_model_result_collection,
    stop_model_result_collection,
)
from run_orchestration.allure_lifecycle import (
    ALLURE_REPORT_DIR,
    HISTORY_LATEST_DIR,
    HISTORY_REPORT_DIR,
    RUNNER_MANAGED_ALLURE_ENV,
    AllureRunLifecycle,
    build_allure_env as _build_allure_env,
    cleanup_old_history_reports as _cleanup_old_history_reports,
    clean_directory as _clean_directory,
    find_allure_executable as _find_allure_executable,
    find_bundled_java as _find_bundled_java,
    history_report_name as _history_report_name,
    run_allure_generate as _run_allure_generate,
    update_latest_history_report as _update_latest_history_report,
)


pytest_plugins = ("quality.pytest_plugin",)

TEST_RESOURCE_STATE_ATTR = "_api_case_resource_state"
ALLURE_LIFECYCLE_ATTR = "_api_case_allure_lifecycle"


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("protocol-testing")
    group.addoption(
        "--protocol-model-csv",
        action="store",
        default=None,
        help="指定协议测试通用模型 CSV 文件路径，相对路径基于 module/protocol_testing。",
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
        attach_media_download_steps(
            stop_media_download_collection(media_download_token)
        )
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


@pytest.fixture
def test_context() -> TestContext:
    context = TestContext()
    try:
        yield context
    finally:
        context.cleanup()


def pytest_sessionstart(session: pytest.Session) -> None:
    if _skip_direct_allure_lifecycle(session.config):
        return
    results_dir = _get_allure_results_dir(session.config)
    if results_dir is None:
        return
    lifecycle = AllureRunLifecycle(
        results_dir=results_dir,
        project_root=session.config.rootpath,
        generate_report=settings.generate_allure_report,
        generate_history=settings.generate_history_report,
        history_keep_limit=settings.history_report_keep_limit,
        reporter=lambda message: _write_terminal_line(session.config, message),
    )
    setattr(session.config, ALLURE_LIFECYCLE_ATTR, lifecycle)
    lifecycle.prepare()


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    lifecycle = getattr(session.config, ALLURE_LIFECYCLE_ATTR, None)
    if lifecycle is not None:
        lifecycle.finalize()


def _skip_direct_allure_lifecycle(config: pytest.Config) -> bool:
    return (
        hasattr(config, "workerinput")
        or bool(getattr(config.option, "collectonly", False))
        or os.getenv(RUNNER_MANAGED_ALLURE_ENV) == "1"
    )


def _get_allure_results_dir(config: pytest.Config) -> Path | None:
    alluredir = config.getoption("--alluredir", default=None)
    if not alluredir:
        return None
    results_dir = Path(alluredir)
    return results_dir if results_dir.is_absolute() else config.rootpath / results_dir


def _write_terminal_line(config: pytest.Config, message: str) -> None:
    reporter = config.pluginmanager.get_plugin("terminalreporter")
    if reporter is None:
        print(message)
    else:
        reporter.write_line(message)
