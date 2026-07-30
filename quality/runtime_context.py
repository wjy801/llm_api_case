from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class QualityRunContext:
    run_id: str
    execution_id: str
    worker_id: str
    output_dir: Path

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", _require_non_empty(self.run_id, "run_id"))
        object.__setattr__(
            self,
            "execution_id",
            _require_non_empty(self.execution_id, "execution_id"),
        )
        object.__setattr__(
            self,
            "worker_id",
            _require_non_empty(self.worker_id, "worker_id"),
        )
        object.__setattr__(self, "output_dir", Path(self.output_dir))


@dataclass(frozen=True)
class QualityCaseContext:
    case_id: str
    invocation_id: str
    nodeid: str
    param_hash: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "case_id", _require_non_empty(self.case_id, "case_id"))
        object.__setattr__(
            self,
            "invocation_id",
            _require_non_empty(self.invocation_id, "invocation_id"),
        )
        object.__setattr__(self, "nodeid", _require_non_empty(self.nodeid, "nodeid"))
        object.__setattr__(
            self,
            "param_hash",
            _require_non_empty(self.param_hash, "param_hash"),
        )


_RUN_CONTEXT: ContextVar[QualityRunContext | None] = ContextVar(
    "quality_run_context",
    default=None,
)
_CASE_CONTEXT: ContextVar[QualityCaseContext | None] = ContextVar(
    "quality_case_context",
    default=None,
)


def set_run_context(
    context: QualityRunContext,
) -> Token[QualityRunContext | None]:
    return _RUN_CONTEXT.set(context)


def get_run_context(
    default: QualityRunContext | None = None,
) -> QualityRunContext | None:
    return _RUN_CONTEXT.get() or default


def reset_run_context(token: Token[QualityRunContext | None]) -> None:
    _RUN_CONTEXT.reset(token)


def clear_run_context() -> None:
    _RUN_CONTEXT.set(None)


def set_case_context(
    context: QualityCaseContext,
) -> Token[QualityCaseContext | None]:
    return _CASE_CONTEXT.set(context)


def get_case_context(
    default: QualityCaseContext | None = None,
) -> QualityCaseContext | None:
    return _CASE_CONTEXT.get() or default


def reset_case_context(token: Token[QualityCaseContext | None]) -> None:
    _CASE_CONTEXT.reset(token)


def clear_case_context() -> None:
    _CASE_CONTEXT.set(None)


def _require_non_empty(value: str, name: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise ValueError(f"{name} must not be empty")
    return stripped
