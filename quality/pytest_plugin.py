from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from quality.collector import (
    QualityCollector,
    configure_collector,
    get_collector,
    reset_collector,
)
from quality.config import QualityRuntimeConfig, load_quality_config
from quality.identifiers import (
    build_case_id,
    build_invocation_id,
    build_param_hash,
    build_run_id,
    normalize_nodeid,
)
from quality.models import CasePhase, CaseResult, CaseStatus, IssueSeverity
from quality.runtime_context import (
    QualityCaseContext,
    QualityRunContext,
    get_case_context,
    reset_case_context,
    reset_run_context,
    set_case_context,
    set_run_context,
)


_STATE_ATTR = "_quality_plugin_state"
_WORKER_INPUT_KEY = "quality_runtime"
_MANUAL_EXECUTION_ID = "manual-pytest"


@dataclass
class _PluginState:
    config: QualityRuntimeConfig
    run_context: QualityRunContext | None = None
    collector: QualityCollector | None = None
    run_token: Any = None


def pytest_configure(config: pytest.Config) -> None:
    if getattr(config.option, "collectonly", False):
        return

    try:
        runtime_config = _resolve_runtime_config(config)
    except Exception as error:
        _write_warning(config, f"quality collection disabled: {type(error).__name__}: {error}")
        return

    state = _PluginState(config=runtime_config)
    setattr(config, _STATE_ATTR, state)
    if not runtime_config.enabled:
        return

    if _is_xdist_controller(config):
        return

    worker_id = _worker_id(config)
    run_context = QualityRunContext(
        run_id=_required(runtime_config.run_id, "run_id"),
        execution_id=_required(runtime_config.execution_id, "execution_id"),
        worker_id=worker_id,
        output_dir=runtime_config.output_dir,
    )
    try:
        state.run_context = run_context
        state.run_token = set_run_context(run_context)
        state.collector = configure_collector(
            run_context,
            warning_sink=lambda message: _write_warning(config, message),
        )
    except Exception as error:
        if state.run_token is not None:
            reset_run_context(state.run_token)
            state.run_token = None
        reset_collector()
        state.run_context = None
        state.collector = None
        _write_warning(
            config,
            f"quality collector initialization failed: {type(error).__name__}: {error}",
        )


@pytest.hookimpl(optionalhook=True)
def pytest_configure_node(node: Any) -> None:
    state = _get_state(node.config)
    if state is None or not state.config.enabled:
        return
    node.workerinput[_WORKER_INPUT_KEY] = {
        "enabled": True,
        "run_id": state.config.run_id,
        "execution_id": state.config.execution_id,
        "output_dir": str(state.config.output_dir),
    }


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    state = _get_state(config)
    collector = state.collector if state is not None else None
    if collector is None:
        return
    for item in items:
        try:
            build_case_id(item.nodeid)
        except Exception as error:
            collector.capture_integrity(
                source="pytest_plugin",
                code="case_context_build_failed",
                message=f"{type(error).__name__}: {error}",
                related_id=None,
                severity=IssueSeverity.ERROR,
            )


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_protocol(item: pytest.Item, nextitem: pytest.Item | None):
    state = _get_state(item.config)
    collector = state.collector if state is not None else None
    if collector is None:
        yield
        return

    token = None
    try:
        case_context = _build_case_context(item, collector.run_context.run_id)
        token = set_case_context(case_context)
    except Exception as error:
        collector.capture_integrity(
            source="pytest_plugin",
            code="case_context_build_failed",
            message=f"{type(error).__name__}: {error}",
            related_id=None,
            severity=IssueSeverity.ERROR,
        )

    try:
        yield
    finally:
        if token is not None:
            reset_case_context(token)


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    collector = _active_collector()
    if collector is None or report.when not in {"setup", "call", "teardown"}:
        return

    case_context = get_case_context()
    if case_context is None:
        collector.capture_integrity(
            source="pytest_plugin",
            code="case_context_build_failed",
            message=f"case context missing for {report.when} report",
            related_id=None,
            severity=IssueSeverity.ERROR,
        )
        return

    try:
        end_time = datetime.now(UTC)
        duration_ms = max(float(report.duration) * 1000, 0.0)
        status = _case_status(report)
        result = CaseResult(
            run_id=collector.run_context.run_id,
            execution_id=collector.run_context.execution_id,
            worker_id=collector.run_context.worker_id,
            case_id=case_context.case_id,
            invocation_id=case_context.invocation_id,
            nodeid=case_context.nodeid,
            param_hash=case_context.param_hash,
            phase=CasePhase(report.when),
            raw_status=status,
            final_status=status,
            duration_ms=duration_ms,
            start_time=end_time - timedelta(milliseconds=duration_ms),
            end_time=end_time,
        )
        collector.record_case(result)
    except Exception as error:
        collector.capture_integrity(
            source="pytest_plugin",
            code="case_write_failed",
            message=f"{type(error).__name__}: {error}",
            related_id=case_context.invocation_id,
            severity=IssueSeverity.ERROR,
        )


def pytest_collectreport(report: pytest.CollectReport) -> None:
    collector = _active_collector()
    if collector is None or not report.failed:
        return
    collector.capture_integrity(
        source="pytest_plugin",
        code="collection_failed",
        message=f"collection failed: {report.nodeid}",
        related_id=report.nodeid or None,
        severity=IssueSeverity.ERROR,
    )


def pytest_unconfigure(config: pytest.Config) -> None:
    state = _get_state(config)
    if state is None:
        return
    try:
        if state.run_token is not None:
            reset_run_context(state.run_token)
    finally:
        if state.collector is not None:
            reset_collector()
        delattr(config, _STATE_ATTR)


def _resolve_runtime_config(config: pytest.Config) -> QualityRuntimeConfig:
    worker_input = getattr(config, "workerinput", None)
    payload = worker_input.get(_WORKER_INPUT_KEY) if isinstance(worker_input, dict) else None
    if payload is not None:
        return QualityRuntimeConfig(
            enabled=bool(payload["enabled"]),
            run_id=_required(payload.get("run_id"), "run_id"),
            execution_id=_required(payload.get("execution_id"), "execution_id"),
            output_dir=Path(payload["output_dir"]),
        )

    loaded = load_quality_config()
    if not loaded.enabled:
        return loaded

    output_dir = loaded.output_dir
    if not output_dir.is_absolute():
        output_dir = config.rootpath / output_dir
    return QualityRuntimeConfig(
        enabled=True,
        run_id=loaded.run_id or build_run_id(),
        execution_id=loaded.execution_id or _MANUAL_EXECUTION_ID,
        output_dir=output_dir,
    )


def _build_case_context(item: pytest.Item, run_id: str) -> QualityCaseContext:
    normalized = normalize_nodeid(item.nodeid)
    callspec = getattr(item, "callspec", None)
    parameter_value = None
    if callspec is not None:
        parameter_value = {
            "parameter_id": normalized.parameter_id,
            "params": callspec.params,
        }
    param_hash = build_param_hash(parameter_value)
    case_id = build_case_id(item.nodeid)
    return QualityCaseContext(
        case_id=case_id,
        invocation_id=build_invocation_id(run_id, case_id, param_hash),
        nodeid=item.nodeid,
        param_hash=param_hash,
    )


def _case_status(report: pytest.TestReport) -> CaseStatus:
    was_xfail = hasattr(report, "wasxfail")
    if report.skipped:
        return CaseStatus.XFAILED if was_xfail else CaseStatus.SKIPPED
    if report.passed:
        return CaseStatus.XPASSED if was_xfail else CaseStatus.PASSED
    if report.when == "call":
        return CaseStatus.FAILED
    return CaseStatus.ERROR


def _active_collector() -> QualityCollector | None:
    return get_collector()


def _get_state(config: pytest.Config) -> _PluginState | None:
    return getattr(config, _STATE_ATTR, None)


def _is_xdist_controller(config: pytest.Config) -> bool:
    if hasattr(config, "workerinput"):
        return False
    return bool(getattr(config.option, "numprocesses", None))


def _worker_id(config: pytest.Config) -> str:
    worker_input = getattr(config, "workerinput", None)
    if isinstance(worker_input, dict):
        return str(worker_input.get("workerid") or "worker")
    return "master"


def _required(value: object, name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{name} must not be empty")
    return text


def _write_warning(config: pytest.Config, message: str) -> None:
    reporter = config.pluginmanager.get_plugin("terminalreporter")
    if reporter is None:
        print(message)
        return
    reporter.write_line(message, yellow=True)
