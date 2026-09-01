"""Runtime pytest hooks loaded only when Quality collection is enabled."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from common.runtime_hooks import (
    RuntimeHooks,
    bind_runtime_hooks,
    reset_runtime_hooks,
)
from quality.collector import (
    QualityCollector,
    configure_collector,
    get_collector,
    reset_collector,
)
from quality.config import QualityRuntimeConfig, load_quality_config
from quality.identifiers import (
    build_invocation_id,
    build_run_id,
)
from quality.junit import QUALITY_CASE_ID_PROPERTY, QUALITY_INVOCATION_ID_PROPERTY
from quality.flaky_identity import normalize_execution_profile, runtime_flaky_environment
from quality.models import CasePhase, CaseResult, CaseStatus, IssueSeverity
from quality.pytest_identity import build_pytest_item_identity
from quality.runtime_context import (
    QualityCaseContext,
    QualityRunContext,
    get_case_context,
    reset_case_context,
    reset_run_context,
    set_case_context,
    set_run_context,
)
from quality.semantic_collector import (
    SemanticCollector,
    configure_semantic_collector,
    get_semantic_collector,
    reset_semantic_collector,
)
from quality.runtime_adapter import QualityRuntimeHooks


_STATE_ATTR = "_quality_plugin_state"
_WORKER_INPUT_KEY = "quality_runtime"
_MANUAL_EXECUTION_ID = "manual-pytest"


@dataclass
class _PluginState:
    config: QualityRuntimeConfig
    run_context: QualityRunContext | None = None
    collector: QualityCollector | None = None
    semantic_collector: SemanticCollector | None = None
    run_token: Any = None
    runtime_hooks: RuntimeHooks | None = None
    runtime_hooks_token: Any = None


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
    if runtime_config.semantic_warning:
        _write_warning(
            config,
            f"quality semantic collection disabled: {runtime_config.semantic_warning}",
        )
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
        if runtime_config.semantic_enabled:
            try:
                state.semantic_collector = configure_semantic_collector(
                    run_context,
                    warning_sink=lambda message: _write_warning(config, message),
                )
            except Exception as error:
                reset_semantic_collector()
                state.semantic_collector = None
                _write_warning(
                    config,
                    "quality semantic collector initialization failed: "
                    f"{type(error).__name__}: {error}",
                )
        state.runtime_hooks = QualityRuntimeHooks()
        state.runtime_hooks_token = bind_runtime_hooks(state.runtime_hooks)
    except Exception as error:
        if state.runtime_hooks_token is not None:
            reset_runtime_hooks(state.runtime_hooks_token)
            state.runtime_hooks_token = None
        state.runtime_hooks = None
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
        "semantic_enabled": state.config.semantic_enabled,
        "semantic_warning": state.config.semantic_warning,
        "flaky_decision_plan_path": (
            str(state.config.flaky_decision_plan_path)
            if state.config.flaky_decision_plan_path is not None
            else None
        ),
        "flaky_decision_checksum": state.config.flaky_decision_checksum,
    }


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    state = _get_state(config)
    collector = state.collector if state is not None else None
    if collector is None:
        return
    _validate_flaky_shadow_plan(config, items, state, collector)
    for item in items:
        try:
            build_pytest_item_identity(item, config.rootpath)
        except Exception as error:
            collector.capture_integrity(
                source="pytest_plugin",
                code="case_context_build_failed",
                message=f"{type(error).__name__}: {error}",
                related_id=None,
                severity=IssueSeverity.ERROR,
            )


def _validate_flaky_shadow_plan(
    config: pytest.Config,
    items: list[pytest.Item],
    state: _PluginState,
    collector: QualityCollector,
) -> None:
    path = state.config.flaky_decision_plan_path
    checksum = state.config.flaky_decision_checksum
    if path is None and checksum is None:
        return
    try:
        from quality.flaky_shadow import read_decision_plan

        plan = read_decision_plan(
            path or "",
            expected_run_id=_required(state.config.run_id, "run_id"),
            expected_checksum=_required(checksum, "flaky_decision_checksum"),
        )
        by_nodeid = {item.nodeid: item for item in plan.decisions}
        execution_id = _required(state.config.execution_id, "execution_id")
        profile = normalize_execution_profile(execution_id, _worker_id(config))
        environment = runtime_flaky_environment()
        for item in items:
            identity = build_pytest_item_identity(item, config.rootpath)
            decision = by_nodeid.get(item.nodeid)
            if decision is None:
                raise ValueError(f"decision missing for nodeid: {item.nodeid}")
            expected = (
                identity.case_id,
                identity.param_hash,
                environment,
                profile,
                identity.normalized_case_path,
            )
            actual = (
                decision.case_id,
                decision.param_hash,
                decision.environment,
                decision.execution_profile,
                decision.normalized_case_path,
            )
            if actual != expected:
                raise ValueError(f"decision identity mismatch: {item.nodeid}")
    except Exception as error:
        collector.capture_integrity(
            source="pytest_plugin",
            code="flaky_decision_plan_invalid",
            message=f"{type(error).__name__}: {error}",
            related_id=None,
            severity=IssueSeverity.ERROR,
        )


@pytest.fixture(autouse=True)
def _quality_junit_identity_property(request: pytest.FixtureRequest, record_property):
    case_context = get_case_context()
    if case_context is not None:
        record_property(QUALITY_CASE_ID_PROPERTY, case_context.case_id)
        record_property(QUALITY_INVOCATION_ID_PROPERTY, case_context.invocation_id)
    yield


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_protocol(item: pytest.Item, nextitem: pytest.Item | None):
    state = _get_state(item.config)
    collector = state.collector if state is not None else None
    if collector is None:
        yield
        return

    token = None
    try:
        case_context = _build_case_context(
            item,
            collector.run_context.run_id,
            item.config.rootpath,
        )
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
        semantic_collector = get_semantic_collector()
        if semantic_collector is not None:
            semantic_collector.finalize_pending(
                case_context.invocation_id if "case_context" in locals() else None
            )
        if token is not None:
            reset_case_context(token)


@pytest.hookimpl(tryfirst=True)
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
        _add_junit_identity_properties(report, case_context.case_id, case_context.invocation_id)
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
        if state.semantic_collector is not None:
            state.semantic_collector.finalize_pending()
    finally:
        try:
            if state.runtime_hooks_token is not None:
                reset_runtime_hooks(state.runtime_hooks_token)
                state.runtime_hooks_token = None
            state.runtime_hooks = None
        finally:
            if state.run_token is not None:
                reset_run_context(state.run_token)
            if state.semantic_collector is not None:
                reset_semantic_collector()
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
            semantic_enabled=bool(payload.get("semantic_enabled", False)),
            semantic_warning=payload.get("semantic_warning"),
            flaky_decision_plan_path=(
                Path(payload["flaky_decision_plan_path"])
                if payload.get("flaky_decision_plan_path")
                else None
            ),
            flaky_decision_checksum=payload.get("flaky_decision_checksum"),
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
        semantic_enabled=loaded.semantic_enabled,
        semantic_warning=loaded.semantic_warning,
        metrics_enabled=loaded.metrics_enabled,
        metrics_warning=loaded.metrics_warning,
        flaky_history_enabled=loaded.flaky_history_enabled,
        flaky_database_path=loaded.flaky_database_path,
        flaky_history_warning=loaded.flaky_history_warning,
        flaky_state_enabled=loaded.flaky_state_enabled,
        flaky_state_warning=loaded.flaky_state_warning,
        flaky_auto_skip_enabled=loaded.flaky_auto_skip_enabled,
        flaky_skip_mode_requested=loaded.flaky_skip_mode_requested,
        flaky_skip_mode_effective=loaded.flaky_skip_mode_effective,
        flaky_skip_warning=loaded.flaky_skip_warning,
        flaky_snapshot_max_age_minutes=loaded.flaky_snapshot_max_age_minutes,
        flaky_decision_plan_path=loaded.flaky_decision_plan_path,
        flaky_decision_checksum=loaded.flaky_decision_checksum,
        flaky_dashboard_host=loaded.flaky_dashboard_host,
        flaky_dashboard_port=loaded.flaky_dashboard_port,
        flaky_dashboard_warning=loaded.flaky_dashboard_warning,
    )


def _build_case_context(
    item: pytest.Item,
    run_id: str,
    repository_root: str | Path,
) -> QualityCaseContext:
    identity = build_pytest_item_identity(item, repository_root)
    return QualityCaseContext(
        case_id=identity.case_id,
        invocation_id=build_invocation_id(
            run_id,
            identity.case_id,
            identity.param_hash,
        ),
        nodeid=item.nodeid,
        param_hash=identity.param_hash,
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


def _add_junit_identity_properties(
    report: pytest.TestReport,
    case_id: str,
    invocation_id: str,
) -> None:
    properties = list(getattr(report, "user_properties", ()))
    existing_names = {name for name, _value in properties}
    if QUALITY_CASE_ID_PROPERTY not in existing_names:
        properties.append((QUALITY_CASE_ID_PROPERTY, case_id))
    if QUALITY_INVOCATION_ID_PROPERTY not in existing_names:
        properties.append((QUALITY_INVOCATION_ID_PROPERTY, invocation_id))
    report.user_properties = properties


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
