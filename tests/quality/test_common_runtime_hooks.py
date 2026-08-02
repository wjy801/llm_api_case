from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from common.context_executor import submit_with_context
from common.runtime_hooks import (
    NoopRuntimeHooks,
    RuntimeOperationKind,
    RuntimeOperationOutcome,
    RuntimeOperationStart,
    RuntimeStreamOutcome,
    bind_runtime_hooks,
    bind_stream_response,
    finish_operation,
    finish_stream,
    get_runtime_hooks,
    get_stream_lease,
    observe_stream_line,
    operation_scope,
    reset_runtime_hooks,
)


class RecordingHooks(NoopRuntimeHooks):
    def __init__(self) -> None:
        self.events: list[tuple] = []

    def begin_operation(self, metadata):
        self.events.append(("begin", metadata.kind, metadata.name))
        return RuntimeOperationStart(native_handle="operation-1", owned=True)

    def finish_operation(self, native_handle, outcome):
        self.events.append(("finish", native_handle, outcome))

    def detach_operation(self, native_handle):
        self.events.append(("detach", native_handle))

    def bind_stream(self, response, operation_handle):
        self.events.append(("bind-stream", operation_handle))
        return "stream-1"

    def observe_stream_line(self, native_handle, line):
        self.events.append(("stream-line", native_handle, line))

    def finish_stream(self, native_handle, outcome):
        self.events.append(("stream-finish", native_handle, outcome))


def test_default_runtime_hooks_are_noop():
    assert isinstance(get_runtime_hooks(), NoopRuntimeHooks)


def test_nested_operation_reuses_parent_without_duplicate_events():
    hooks = RecordingHooks()
    token = bind_runtime_hooks(hooks)
    try:
        with operation_scope(RuntimeOperationKind.ASYNC_TASK, name="parent"):
            with operation_scope(RuntimeOperationKind.HTTP, name="child"):
                pass
    finally:
        reset_runtime_hooks(token)

    assert hooks.events == [
        ("begin", RuntimeOperationKind.ASYNC_TASK, "parent"),
        ("finish", "operation-1", RuntimeOperationOutcome.SUCCESS),
    ]


def test_operation_lease_finishes_with_starting_hooks_after_provider_changes():
    first = RecordingHooks()
    second = RecordingHooks()
    first_token = bind_runtime_hooks(first)
    try:
        from common.runtime_hooks import begin_operation

        lease = begin_operation(RuntimeOperationKind.HTTP, name="request")
        second_token = bind_runtime_hooks(second)
        try:
            finish_operation(lease, RuntimeOperationOutcome.SUCCESS)
        finally:
            reset_runtime_hooks(second_token)
    finally:
        reset_runtime_hooks(first_token)

    assert [event[0] for event in first.events] == ["begin", "finish"]
    assert second.events == []


def test_runtime_hooks_binding_propagates_with_context_executor():
    hooks = RecordingHooks()
    token = bind_runtime_hooks(hooks)
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = submit_with_context(executor, get_runtime_hooks)
            assert future.result() is hooks
    finally:
        reset_runtime_hooks(token)


def test_stream_lease_keeps_starting_hooks():
    hooks = RecordingHooks()
    token = bind_runtime_hooks(hooks)
    try:
        from common.runtime_hooks import begin_operation, detach_operation

        operation = begin_operation(RuntimeOperationKind.SSE, name="stream")
        response = SimpleNamespace()
        bind_stream_response(response, operation)
        detach_operation(operation)
    finally:
        reset_runtime_hooks(token)

    stream = get_stream_lease(response)
    observe_stream_line(stream, "data: hello")
    finish_stream(stream, RuntimeStreamOutcome.COMPLETE)

    assert hooks.events == [
        ("begin", RuntimeOperationKind.SSE, "stream"),
        ("bind-stream", "operation-1"),
        ("detach", "operation-1"),
        ("stream-line", "stream-1", "data: hello"),
        ("stream-finish", "stream-1", RuntimeStreamOutcome.COMPLETE),
    ]


def test_hook_failure_does_not_replace_business_exception():
    class FailingHooks(RecordingHooks):
        def finish_operation(self, native_handle, outcome):
            raise RuntimeError("observer failed")

    hooks = FailingHooks()
    token = bind_runtime_hooks(hooks)
    try:
        try:
            with operation_scope(RuntimeOperationKind.HTTP, name="request"):
                raise ValueError("business failed")
        except ValueError as error:
            assert str(error) == "business failed"
        else:
            raise AssertionError("business exception was not preserved")
    finally:
        reset_runtime_hooks(token)
