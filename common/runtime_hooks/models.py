from __future__ import annotations

from contextvars import Token
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from common.runtime_hooks.protocol import RuntimeHooks


class RuntimeOperationKind(str, Enum):
    HTTP = "http"
    SSE = "sse"
    POLLING = "polling"
    ASYNC_TASK = "async_task"


class RuntimeTrafficRole(str, Enum):
    WORKLOAD = "workload"
    CONTROL = "control"
    UNKNOWN = "unknown"


class RuntimeOperationOutcome(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    TIMEOUT = "timeout"
    INTERRUPTED = "interrupted"
    INCOMPLETE = "incomplete"
    UNKNOWN = "unknown"


class RuntimePollingOutcome(str, Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    TIMEOUT = "timeout"
    UNKNOWN = "unknown"
    INTERRUPTED = "interrupted"


class RuntimeStreamOutcome(str, Enum):
    COMPLETE = "complete"
    INTERRUPTED = "interrupted"
    ERROR = "error"
    NOT_CONSUMED = "not_consumed"


@dataclass(frozen=True)
class RuntimeOperationMetadata:
    kind: RuntimeOperationKind | str
    name: str
    role: RuntimeTrafficRole | str = RuntimeTrafficRole.UNKNOWN
    model_id: str | None = None


@dataclass(frozen=True)
class RuntimeOperationStart:
    native_handle: object | None = None
    owned: bool = False


@dataclass(frozen=True)
class RuntimeOperationLease:
    hooks: RuntimeHooks
    native_handle: object | None = None
    owned: bool = False
    context_token: Token[RuntimeOperationLease | None] | None = field(
        default=None,
        repr=False,
        compare=False,
    )


@dataclass(frozen=True)
class RuntimeRequestGroupLease:
    hooks: RuntimeHooks
    native_handle: object | None = None


@dataclass(frozen=True)
class RuntimePollingLease:
    hooks: RuntimeHooks
    native_handle: object | None = None


@dataclass(frozen=True)
class RuntimeStreamLease:
    hooks: RuntimeHooks
    native_handle: object | None = None


RUNTIME_REQUEST_HOOKS_ATTR = "runtime_observation_hooks"
RUNTIME_STREAM_LEASE_ATTR = "_runtime_stream_lease"
