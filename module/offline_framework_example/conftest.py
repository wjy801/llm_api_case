from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
import requests

import common.base_decorators as base_decorators
from common import TestContext
from common.capture import CapturePolicy
from common.request_context import RequestContext
from common.runtime_hooks import (
    RuntimeHooks,
    RuntimeOperationMetadata,
    RuntimeOperationStart,
    RuntimePollingOutcome,
    bind_runtime_hooks,
    get_runtime_hooks,
    reset_runtime_hooks,
)
from module.offline_framework_example.offline_service import (
    DEFAULT,
    OfflineService,
    OfflineServiceScenario,
    assert_loopback_url,
)
from module.offline_framework_example.request import OfflineFrameworkRequest
import util.media_resources as media_resources


@dataclass(frozen=True, slots=True)
class OfflineCaptureDirs:
    input_dir: Path
    output_dir: Path


@dataclass(slots=True)
class OfflineRequestGroupRecord:
    method: str
    path: str
    protocol: str
    configured_max_attempts: int
    delegate_handle: object | None = field(default=None, repr=False)
    contexts: list[RequestContext] = field(default_factory=list)
    retry_wait_seconds: float | None = None


@dataclass(slots=True)
class OfflinePollingRecord:
    delegate_handle: object | None = field(default=None, repr=False)
    states: list[str] = field(default_factory=list)
    sleep_seconds: list[float] = field(default_factory=list)
    outcome: RuntimePollingOutcome | None = None


class OfflineRuntimeRecorder:
    def __init__(self, delegate: RuntimeHooks) -> None:
        self.delegate = delegate
        self.operation_metadata: list[RuntimeOperationMetadata] = []
        self.request_groups: list[OfflineRequestGroupRecord] = []
        self.polling_sessions: list[OfflinePollingRecord] = []
        self.responses: list[tuple[RequestContext, requests.Response]] = []
        self.delegated_calls: list[str] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(self.delegate, name)

    def begin_operation(
        self,
        metadata: RuntimeOperationMetadata,
    ) -> RuntimeOperationStart:
        self.operation_metadata.append(metadata)
        self.delegated_calls.append("begin_operation")
        return self.delegate.begin_operation(metadata)

    def start_request_group(
        self,
        *,
        method: str,
        path: str,
        protocol: str,
        configured_max_attempts: int,
    ) -> object | None:
        self.delegated_calls.append("start_request_group")
        delegate_handle = self.delegate.start_request_group(
            method=method,
            path=path,
            protocol=protocol,
            configured_max_attempts=configured_max_attempts,
        )
        record = OfflineRequestGroupRecord(
            method=method,
            path=path,
            protocol=protocol,
            configured_max_attempts=configured_max_attempts,
            delegate_handle=delegate_handle,
        )
        self.request_groups.append(record)
        return record

    def bind_request_context(
        self,
        context: Any,
        native_handle: object | None,
    ) -> None:
        record = self._request_group_record(native_handle)
        if isinstance(context, RequestContext):
            record.contexts.append(context)
        self.delegated_calls.append("bind_request_context")
        self.delegate.bind_request_context(context, record.delegate_handle)

    def finish_request_group(
        self,
        native_handle: object | None,
        *,
        retry_wait_seconds: float = 0.0,
    ) -> None:
        record = self._request_group_record(native_handle)
        record.retry_wait_seconds = retry_wait_seconds
        self.delegated_calls.append("finish_request_group")
        self.delegate.finish_request_group(
            record.delegate_handle,
            retry_wait_seconds=retry_wait_seconds,
        )

    def request_started(self, context: Any) -> None:
        self.delegated_calls.append("request_started")
        self.delegate.request_started(context)

    def request_succeeded(self, context: Any, response: Any) -> None:
        if isinstance(context, RequestContext) and isinstance(response, requests.Response):
            self.responses.append((context, response))
        self.delegated_calls.append("request_succeeded")
        self.delegate.request_succeeded(context, response)

    def begin_polling_session(self) -> object | None:
        self.delegated_calls.append("begin_polling_session")
        record = OfflinePollingRecord(
            delegate_handle=self.delegate.begin_polling_session(),
        )
        self.polling_sessions.append(record)
        return record

    def observe_polling_state(
        self,
        native_handle: object | None,
        state: str,
    ) -> None:
        record = self._polling_record(native_handle)
        record.states.append(state)
        self.delegated_calls.append("observe_polling_state")
        self.delegate.observe_polling_state(record.delegate_handle, state)

    def add_polling_sleep(
        self,
        native_handle: object | None,
        seconds: float,
    ) -> None:
        record = self._polling_record(native_handle)
        record.sleep_seconds.append(seconds)
        self.delegated_calls.append("add_polling_sleep")
        self.delegate.add_polling_sleep(record.delegate_handle, seconds)

    def finish_polling_session(
        self,
        native_handle: object | None,
        outcome: RuntimePollingOutcome,
    ) -> None:
        record = self._polling_record(native_handle)
        record.outcome = outcome
        self.delegated_calls.append("finish_polling_session")
        self.delegate.finish_polling_session(record.delegate_handle, outcome)

    @staticmethod
    def _request_group_record(
        native_handle: object | None,
    ) -> OfflineRequestGroupRecord:
        if not isinstance(native_handle, OfflineRequestGroupRecord):
            raise TypeError("runtime request group handle was not created by recorder")
        return native_handle

    @staticmethod
    def _polling_record(
        native_handle: object | None,
    ) -> OfflinePollingRecord:
        if not isinstance(native_handle, OfflinePollingRecord):
            raise TypeError("runtime polling handle was not created by recorder")
        return native_handle


@pytest.fixture
def offline_service_factory() -> Iterator[Callable[..., OfflineService]]:
    services: list[OfflineService] = []

    def factory(
        scenario: OfflineServiceScenario = DEFAULT,
    ) -> OfflineService:
        service = OfflineService(scenario)
        try:
            service.start()
            assert_loopback_url(service.base_url)
        except BaseException:
            service.stop()
            raise
        services.append(service)
        return service

    yield factory

    errors: list[str] = []
    for service in reversed(services):
        try:
            service.stop()
        except BaseException as error:
            errors.append(f"service cleanup failed: {type(error).__name__}: {error}")
        handler_errors = service.state.snapshot()["handler_errors"]
        if handler_errors:
            errors.append(
                "service handler errors: " + "; ".join(handler_errors)
            )
    if errors:
        raise RuntimeError(" | ".join(errors))


@pytest.fixture
def offline_service(
    offline_service_factory: Callable[..., OfflineService],
) -> OfflineService:
    return offline_service_factory(DEFAULT)


@pytest.fixture
def offline_network_guard() -> Callable[[str], None]:
    return assert_loopback_url


@pytest.fixture
def offline_capture_dirs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> OfflineCaptureDirs:
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    input_dir.mkdir()
    output_dir.mkdir()
    monkeypatch.setattr(media_resources, "MEDIA_DOWNLOAD_DIR", input_dir)
    monkeypatch.setattr(base_decorators, "DOWNLOAD_DIR", output_dir)
    return OfflineCaptureDirs(input_dir=input_dir, output_dir=output_dir)


@pytest.fixture
def offline_request_factory(
    offline_service_factory: Callable[..., OfflineService],
) -> Iterator[Callable[..., OfflineFrameworkRequest]]:
    clients: list[OfflineFrameworkRequest] = []

    def factory(
        service: OfflineService,
        *,
        capture_policy: CapturePolicy | None = None,
    ) -> OfflineFrameworkRequest:
        assert_loopback_url(service.base_url)
        client = OfflineFrameworkRequest(
            service.base_url,
            capture_policy=capture_policy,
        )
        clients.append(client)
        return client

    yield factory

    errors: list[str] = []
    for client in reversed(clients):
        try:
            client.close()
        except BaseException as error:
            errors.append(
                f"request cleanup failed: {type(error).__name__}: {error}"
            )
    if errors:
        raise RuntimeError(" | ".join(errors))


@pytest.fixture
def offline_request(
    offline_service: OfflineService,
    offline_request_factory: Callable[..., OfflineFrameworkRequest],
) -> OfflineFrameworkRequest:
    return offline_request_factory(offline_service)


@pytest.fixture
def offline_test_context(
    offline_request_factory: Callable[..., OfflineFrameworkRequest],
) -> Iterator[TestContext]:
    context = TestContext(name="offline-framework-example")
    try:
        yield context
    finally:
        context.cleanup()


@pytest.fixture
def offline_runtime_recorder() -> Iterator[OfflineRuntimeRecorder]:
    recorder = OfflineRuntimeRecorder(get_runtime_hooks())
    token = bind_runtime_hooks(recorder)
    try:
        yield recorder
    finally:
        reset_runtime_hooks(token)
