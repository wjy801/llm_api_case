from __future__ import annotations

from typing import Sequence

from master_service import CollectedTestCase, DEFAULT_SERIAL_MARKER


def split_test_cases(
    cases: Sequence[CollectedTestCase],
    serial_marker: str = DEFAULT_SERIAL_MARKER,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    parallel_cases: list[str] = []
    serial_cases: list[str] = []
    seen: set[str] = set()

    for case in cases:
        if case.nodeid in seen:
            raise ValueError(f"duplicate nodeid in execution plan: {case.nodeid}")
        seen.add(case.nodeid)
        if serial_marker in case.markers:
            serial_cases.append(case.nodeid)
        else:
            parallel_cases.append(case.nodeid)

    parallel = tuple(parallel_cases)
    serial = tuple(serial_cases)
    if set(parallel) & set(serial):
        raise ValueError("parallel and serial execution pools overlap")
    if set(parallel) | set(serial) != seen:
        raise ValueError("execution pool union differs from the authoritative plan")
    return parallel, serial


__all__ = ("split_test_cases",)
