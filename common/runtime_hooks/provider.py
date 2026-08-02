from __future__ import annotations

from contextvars import ContextVar, Token

from common.runtime_hooks.noop import NoopRuntimeHooks
from common.runtime_hooks.protocol import RuntimeHooks


_NOOP_RUNTIME_HOOKS = NoopRuntimeHooks()
_RUNTIME_HOOKS: ContextVar[RuntimeHooks] = ContextVar(
    "common_runtime_hooks",
    default=_NOOP_RUNTIME_HOOKS,
)


def get_runtime_hooks() -> RuntimeHooks:
    return _RUNTIME_HOOKS.get()


def bind_runtime_hooks(hooks: RuntimeHooks) -> Token[RuntimeHooks]:
    if hooks is None:
        raise TypeError("hooks must not be None")
    return _RUNTIME_HOOKS.set(hooks)


def reset_runtime_hooks(token: Token[RuntimeHooks]) -> None:
    _RUNTIME_HOOKS.reset(token)
