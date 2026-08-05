"""Stable lightweight pytest plugin entry point for optional Quality hooks."""

from __future__ import annotations

from importlib import import_module
from typing import Any


_RUNTIME_PLUGIN_MODULE = "quality.pytest_plugin_runtime"
_RUNTIME_PLUGIN_NAME = "quality-runtime"
_WORKER_INPUT_KEY = "quality_runtime"


def pytest_configure(config: Any) -> None:
    if getattr(config.option, "collectonly", False):
        return
    try:
        enabled = _quality_enabled(config)
    except Exception as error:
        _write_warning(
            config,
            f"quality collection disabled: {type(error).__name__}: {error}",
        )
        return
    if not enabled:
        return

    runtime = import_module(_RUNTIME_PLUGIN_MODULE)
    if not config.pluginmanager.has_plugin(_RUNTIME_PLUGIN_NAME):
        config.pluginmanager.register(runtime, _RUNTIME_PLUGIN_NAME)


def _quality_enabled(config: Any) -> bool:
    worker_input = getattr(config, "workerinput", None)
    payload = (
        worker_input.get(_WORKER_INPUT_KEY)
        if isinstance(worker_input, dict)
        else None
    )
    if payload is not None:
        return bool(payload.get("enabled"))

    from quality.config import load_quality_config

    return load_quality_config().enabled


def _write_warning(config: Any, message: str) -> None:
    reporter = config.pluginmanager.get_plugin("terminalreporter")
    if reporter is None:
        print(message)
        return
    reporter.write_line(message, yellow=True)
