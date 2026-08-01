from __future__ import annotations

from collections.abc import Iterable

from quality.models import CaseResult, CaseStatus


def fold_case_status(cases: Iterable[CaseResult], *, raw: bool = False) -> CaseStatus:
    """把 pytest 多阶段结果折叠为单次用例调用的最终状态。"""
    statuses = {
        case.raw_status if raw else case.final_status
        for case in cases
    }
    if CaseStatus.ERROR in statuses:
        return CaseStatus.ERROR
    if CaseStatus.FAILED in statuses:
        return CaseStatus.FAILED
    if statuses & {CaseStatus.SKIPPED, CaseStatus.XFAILED}:
        return CaseStatus.SKIPPED
    return CaseStatus.PASSED
