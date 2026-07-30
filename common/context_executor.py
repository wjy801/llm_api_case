from __future__ import annotations

from concurrent.futures import Executor, Future
from contextvars import copy_context
from typing import Callable, ParamSpec, TypeVar


P = ParamSpec("P")
T = TypeVar("T")


def submit_with_context(
    executor: Executor,
    function: Callable[P, T],
    /,
    *args: P.args,
    **kwargs: P.kwargs,
) -> Future[T]:
    context = copy_context()
    return executor.submit(context.run, function, *args, **kwargs)
