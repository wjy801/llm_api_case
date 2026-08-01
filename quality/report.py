from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from quality.case_lifecycle import fold_case_status
from quality.gate import (
    GATE_RULESET_VERSION,
    ShadowGateConfig,
    ShadowGateContext,
    evaluate_shadow_gate,
)
from quality.models import (
    SCHEMA_VERSION,
    BusinessStatus,
    CaseResult,
    CaseStatus,
    Confidence,
    FailureCategory,
    FailureRecord,
    GateDecision,
    GateResult,
    IntegrityIssue,
    IntegrityStatus,
    OwnerDomain,
    QualitySummary,
    RequestMetric,
)
from quality.storage import ensure_quality_dirs, write_json_atomic


REPORT_VERSION = "p0-report.v1"

_GATE_RESULT_LABELS = {
    "PASS": "通过",
    "WARN": "警告",
    "BLOCK": "阻断",
    "NO_DATA": "无数据",
}
_GATE_MODE_LABELS = {"shadow": "影子观察"}
_INTEGRITY_STATUS_LABELS = {
    "complete": "完整",
    "degraded": "降级",
    "failed": "失败",
}
_FAILURE_CATEGORY_LABELS = {
    "PRODUCT_DEFECT": "产品缺陷",
    "TEST_DEFECT": "测试缺陷",
    "FRAMEWORK_DEFECT": "框架缺陷",
    "ENVIRONMENT": "环境问题",
    "CONFIGURATION": "配置问题",
    "TRANSIENT": "瞬时故障",
    "UNKNOWN": "未知原因",
}
_ISSUE_SEVERITY_LABELS = {
    "info": "提示",
    "warn": "警告",
    "error": "错误",
}
_GATE_RULE_LABELS = {
    "p0.shadow_gate.enabled": "影子门禁已启用",
    "p0.integrity.available": "质量数据可用",
    "p0.integrity.degraded": "质量数据未降级",
    "p0.failure.product_defect": "产品缺陷为零",
    "p0.failure.configuration": "配置问题为零",
    "p0.failure.framework_defect": "框架缺陷为零",
    "p0.failure.unknown": "未知失败为零",
    "p0.request.http_5xx_rate": "HTTP 5xx 比例",
    "p0.request.timeout_rate": "请求超时比例",
}

_ModelT = TypeVar("_ModelT", bound=BaseModel)


@dataclass(frozen=True)
class QualityReportRequest:
    run_id: str
    output_dir: Path
    shadow_gate: bool = True
    min_request_samples: int = 20
    http_5xx_warn_rate: float = 0.02
    timeout_warn_rate: float = 0.05


@dataclass(frozen=True)
class QualityReportResult:
    run_id: str
    output_dir: Path
    summary_path: Path
    gate_report_json_path: Path
    gate_report_md_path: Path
    overall: GateResult
    integrity_status: IntegrityStatus


@dataclass(frozen=True)
class _LoadedInput:
    available: bool
    manifest: dict[str, Any] | None
    cases: tuple[CaseResult, ...] = ()
    requests: tuple[RequestMetric, ...] = ()
    failures: tuple[FailureRecord, ...] = ()
    integrity_issues: tuple[IntegrityIssue, ...] = ()
    evidence: tuple[str, ...] = ()
    report_warnings: tuple[str, ...] = ()


def generate_quality_report(request: QualityReportRequest) -> QualityReportResult:
    output_dir = Path(request.output_dir)
    layout = ensure_quality_dirs(output_dir)
    created_at = datetime.now(UTC)
    loaded = _load_input(request, layout.merged)
    report_warnings = list(loaded.report_warnings)
    if loaded.available:
        effective_integrity = _effective_integrity_status(
            loaded.manifest,
            loaded.integrity_issues,
            report_warnings,
        )
        summary = _build_summary(
            request.run_id,
            cases=loaded.cases,
            requests=loaded.requests,
            failures=loaded.failures,
            integrity_status=effective_integrity,
        )
        details = _build_details(
            cases=loaded.cases,
            requests=loaded.requests,
            failures=loaded.failures,
            integrity_issues=loaded.integrity_issues,
            report_warnings=tuple(report_warnings),
        )
        gate_context = ShadowGateContext(
            run_id=request.run_id,
            input_available=True,
            integrity_status=effective_integrity,
            summary=summary,
            failure_category_counts=details["failure_categories"],
            failure_evidence=details["failure_evidence"],
            input_evidence=_integrity_evidence(loaded.integrity_issues, tuple(report_warnings)),
        )
    else:
        effective_integrity = IntegrityStatus.FAILED
        summary = _empty_summary(request.run_id, effective_integrity)
        details = _empty_details(loaded.evidence)
        gate_context = ShadowGateContext(
            run_id=request.run_id,
            input_available=False,
            integrity_status=effective_integrity,
            summary=summary,
            input_evidence=loaded.evidence,
        )

    gate_config = ShadowGateConfig(
        enabled=request.shadow_gate,
        min_request_samples=request.min_request_samples,
        http_5xx_warn_rate=request.http_5xx_warn_rate,
        timeout_warn_rate=request.timeout_warn_rate,
    )
    gate_decision = evaluate_shadow_gate(gate_context, gate_config)
    source_manifest = _source_manifest(loaded.manifest)
    summary_envelope = _summary_envelope(
        request.run_id,
        created_at,
        source_manifest=source_manifest,
        summary=summary,
        details=details,
    )
    gate_envelope = _gate_envelope(
        request.run_id,
        created_at,
        gate_decision=gate_decision,
        source_manifest=source_manifest,
    )

    summary_path = output_dir / "summary.json"
    gate_json_path = output_dir / "gate-report.json"
    gate_md_path = output_dir / "gate-report.md"
    write_json_atomic(summary_path, summary_envelope)
    write_json_atomic(gate_json_path, gate_envelope)
    _write_text_atomic(gate_md_path, _render_markdown(summary_envelope, gate_decision))
    return QualityReportResult(
        run_id=request.run_id,
        output_dir=output_dir,
        summary_path=summary_path,
        gate_report_json_path=gate_json_path,
        gate_report_md_path=gate_md_path,
        overall=gate_decision.overall,
        integrity_status=summary.integrity_status,
    )


def _load_input(request: QualityReportRequest, merged_dir: Path) -> _LoadedInput:
    manifest_path = merged_dir / "manifest.json"
    if not manifest_path.exists():
        return _unavailable("merged/manifest.json is missing")

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return _unavailable(f"merged/manifest.json cannot be parsed: {type(error).__name__}: {error}")

    errors = _validate_manifest(request, manifest, merged_dir)
    if errors:
        return _LoadedInput(
            available=False,
            manifest=manifest if isinstance(manifest, dict) else None,
            evidence=tuple(errors),
        )

    try:
        cases = tuple(_read_jsonl_model(merged_dir / "case-results.jsonl", CaseResult))
        requests = tuple(_read_jsonl_model(merged_dir / "request-metrics.jsonl", RequestMetric))
        failures = tuple(_read_jsonl_model(merged_dir / "failures.jsonl", FailureRecord))
        integrity_issues = tuple(_read_jsonl_model(merged_dir / "integrity-issues.jsonl", IntegrityIssue))
    except ValueError as error:
        return _LoadedInput(
            available=False,
            manifest=manifest,
            evidence=(str(error),),
        )

    warnings = _run_metadata_warnings(request, merged_dir.parent, manifest)
    return _LoadedInput(
        available=True,
        manifest=manifest,
        cases=cases,
        requests=requests,
        failures=failures,
        integrity_issues=integrity_issues,
        report_warnings=tuple(warnings),
    )


def _validate_manifest(
    request: QualityReportRequest,
    manifest: Any,
    merged_dir: Path,
) -> tuple[str, ...]:
    if not isinstance(manifest, dict):
        return ("merged/manifest.json must contain a JSON object",)

    errors: list[str] = []
    if manifest.get("run_id") != request.run_id:
        errors.append(
            f"manifest run_id mismatch: expected {request.run_id}, got {manifest.get('run_id')}"
        )
    if manifest.get("status") != "complete":
        errors.append(f"manifest status is not complete: {manifest.get('status')}")
    if manifest.get("schema_version") != SCHEMA_VERSION:
        errors.append(
            f"manifest schema_version mismatch: expected {SCHEMA_VERSION}, got {manifest.get('schema_version')}"
        )

    output_hashes = manifest.get("output_hashes")
    if not isinstance(output_hashes, dict):
        errors.append("manifest output_hashes must be an object")
        return tuple(errors)

    for name, filename in _OUTPUT_FILES.items():
        expected_hash = output_hashes.get(name)
        path = merged_dir / filename
        if not isinstance(expected_hash, str) or not expected_hash.strip():
            errors.append(f"manifest output hash missing for {name}")
            continue
        if not path.exists():
            errors.append(f"merged output file is missing: {filename}")
            continue
        actual_hash = _file_sha256(path)
        if actual_hash != expected_hash:
            errors.append(f"merged output hash mismatch for {filename}")

    return tuple(errors)


def _read_jsonl_model(path: Path, model: type[_ModelT]) -> list[_ModelT]:
    records: list[_ModelT] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    payload = json.loads(line)
                    records.append(model.model_validate(payload))
                except (json.JSONDecodeError, ValidationError) as error:
                    raise ValueError(
                        f"{path.name}:{line_number}: {type(error).__name__}: {error}"
                    ) from error
    except OSError as error:
        raise ValueError(f"{path.name} cannot be read: {type(error).__name__}: {error}") from error
    return records


def _run_metadata_warnings(
    request: QualityReportRequest,
    output_dir: Path,
    manifest: dict[str, Any],
) -> list[str]:
    run_path = output_dir / "run.json"
    if not run_path.exists():
        return ["run.json is missing"]
    try:
        payload = json.loads(run_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return [f"run.json cannot be parsed: {type(error).__name__}: {error}"]
    if not isinstance(payload, dict):
        return ["run.json must contain a JSON object"]
    if payload.get("run_id") != request.run_id:
        return [f"run.json run_id mismatch: expected {request.run_id}, got {payload.get('run_id')}"]
    run_integrity = payload.get("integrity_status")
    manifest_integrity = manifest.get("integrity_status")
    if run_integrity is not None and run_integrity != manifest_integrity:
        return [
            "run.json integrity_status mismatch: "
            f"manifest={manifest_integrity}, run={run_integrity}"
        ]
    return []


def _effective_integrity_status(
    manifest: dict[str, Any] | None,
    integrity_issues: Iterable[IntegrityIssue],
    report_warnings: list[str],
) -> IntegrityStatus:
    if manifest is None:
        return IntegrityStatus.FAILED
    try:
        manifest_status = IntegrityStatus(manifest.get("integrity_status"))
    except ValueError:
        report_warnings.append(f"manifest integrity_status is invalid: {manifest.get('integrity_status')}")
        return IntegrityStatus.DEGRADED
    if report_warnings and manifest_status is IntegrityStatus.COMPLETE:
        return IntegrityStatus.DEGRADED
    severities = {issue.severity.value for issue in integrity_issues}
    if "error" in severities:
        return IntegrityStatus.FAILED
    if "warn" in severities and manifest_status is IntegrityStatus.COMPLETE:
        return IntegrityStatus.DEGRADED
    return manifest_status


def _build_summary(
    run_id: str,
    *,
    cases: tuple[CaseResult, ...],
    requests: tuple[RequestMetric, ...],
    failures: tuple[FailureRecord, ...],
    integrity_status: IntegrityStatus,
) -> QualitySummary:
    final_statuses = _fold_invocations(cases, raw=False)
    raw_statuses = _fold_invocations(cases, raw=True)
    case_total = len(final_statuses)
    case_passed = _count_status(final_statuses, CaseStatus.PASSED)
    case_failed = _count_status(final_statuses, CaseStatus.FAILED)
    case_error = _count_status(final_statuses, CaseStatus.ERROR)
    case_skipped = _count_status(final_statuses, CaseStatus.SKIPPED)
    raw_passed = _count_status(raw_statuses, CaseStatus.PASSED)
    request_total = len(requests)
    request_success = sum(1 for item in requests if _request_succeeded(item))
    http_5xx_count = sum(
        1
        for item in requests
        if item.status_code is not None and 500 <= item.status_code <= 599
    )
    timeout_count = sum(1 for item in requests if item.timeout)
    unknown_failure_count = sum(
        1 for failure in failures if failure.category is FailureCategory.UNKNOWN
    )
    return QualitySummary(
        run_id=run_id,
        case_total=case_total,
        case_passed=case_passed,
        case_failed=case_failed,
        case_error=case_error,
        case_skipped=case_skipped,
        raw_pass_rate=_rate(raw_passed, case_total),
        final_pass_rate=_rate(case_passed, case_total),
        retry_passed=0,
        request_total=request_total,
        request_success_rate=_rate(request_success, request_total),
        http_5xx_count=http_5xx_count,
        timeout_count=timeout_count,
        unknown_failure_count=unknown_failure_count,
        integrity_status=integrity_status,
    )


def _empty_summary(run_id: str, integrity_status: IntegrityStatus) -> QualitySummary:
    return QualitySummary(
        run_id=run_id,
        case_total=0,
        case_passed=0,
        case_failed=0,
        case_error=0,
        case_skipped=0,
        raw_pass_rate=0,
        final_pass_rate=0,
        retry_passed=0,
        request_total=0,
        request_success_rate=0,
        http_5xx_count=0,
        timeout_count=0,
        unknown_failure_count=0,
        integrity_status=integrity_status,
    )


def _fold_invocations(cases: Iterable[CaseResult], *, raw: bool) -> dict[str, CaseStatus]:
    grouped: dict[str, list[CaseResult]] = defaultdict(list)
    for case in cases:
        grouped[case.invocation_id].append(case)
    return {
        invocation_id: fold_case_status(invocation_cases, raw=raw)
        for invocation_id, invocation_cases in grouped.items()
    }


def _count_status(values: dict[str, CaseStatus], status: CaseStatus) -> int:
    return sum(1 for value in values.values() if value is status)


def _request_succeeded(metric: RequestMetric) -> bool:
    return (
        metric.business_status is BusinessStatus.SUCCESS
        and not metric.timeout
        and metric.error_type is None
    )


def _build_details(
    *,
    cases: tuple[CaseResult, ...],
    requests: tuple[RequestMetric, ...],
    failures: tuple[FailureRecord, ...],
    integrity_issues: tuple[IntegrityIssue, ...],
    report_warnings: tuple[str, ...],
) -> dict[str, Any]:
    final_statuses = _fold_invocations(cases, raw=False)
    case_status = Counter(status.value for status in final_statuses.values())
    failure_categories = Counter(failure.category.value for failure in failures)
    failure_evidence = _failure_evidence(failures)
    request_success = sum(1 for item in requests if _request_succeeded(item))
    request_details = {
        "success_count": request_success,
        "failure_count": len(requests) - request_success,
        "http_5xx_count": sum(
            1
            for item in requests
            if item.status_code is not None and 500 <= item.status_code <= 599
        ),
        "timeout_count": sum(1 for item in requests if item.timeout),
    }
    return {
        "case_status": dict(sorted(case_status.items())),
        "failure_categories": _enum_counter_with_zeroes(failure_categories, FailureCategory),
        "failure_evidence": failure_evidence,
        "failures": _failure_details(failures),
        "request": request_details,
        "interfaces": _interface_summaries(requests),
        "integrity": {
            "issue_count": len(integrity_issues),
            "issues": [_integrity_issue_summary(issue) for issue in integrity_issues[:20]],
            "report_warnings": list(report_warnings),
        },
    }


def _empty_details(evidence: tuple[str, ...]) -> dict[str, Any]:
    return {
        "case_status": {},
        "failure_categories": _enum_counter_with_zeroes(Counter(), FailureCategory),
        "failure_evidence": {},
        "failures": _failure_details(()),
        "request": {
            "success_count": 0,
            "failure_count": 0,
            "http_5xx_count": 0,
            "timeout_count": 0,
        },
        "interfaces": [],
        "integrity": {
            "issue_count": len(evidence),
            "issues": [],
            "report_warnings": list(evidence),
        },
    }


def _failure_evidence(failures: tuple[FailureRecord, ...]) -> dict[str, tuple[str, ...]]:
    grouped: dict[str, list[str]] = defaultdict(list)
    for failure in failures:
        if len(grouped[failure.category.value]) >= 5:
            continue
        grouped[failure.category.value].append(
            f"{failure.failure_id} case={failure.case_id} invocation={failure.invocation_id}"
        )
    return {category: tuple(items) for category, items in grouped.items()}


def _failure_details(failures: tuple[FailureRecord, ...]) -> dict[str, Any]:
    owner_domains = Counter(failure.owner_domain.value for failure in failures)
    confidence = Counter(failure.confidence.value for failure in failures)
    categories: dict[str, dict[str, Any]] = {}
    for category in FailureCategory:
        items = [failure for failure in failures if failure.category is category]
        categories[category.value] = {
            "occurrence_count": len(items),
            "fingerprint_count": len({failure.failure_id for failure in items}),
            "affected_invocation_count": len({failure.invocation_id for failure in items}),
            "affected_case_count": len({failure.case_id for failure in items}),
            "examples": [
                {
                    "failure_id": failure.failure_id,
                    "case_id": failure.case_id,
                    "invocation_id": failure.invocation_id,
                }
                for failure in items[:3]
            ],
        }
    return {
        "occurrence_count": len(failures),
        "fingerprint_count": len({failure.failure_id for failure in failures}),
        "affected_invocation_count": len({failure.invocation_id for failure in failures}),
        "affected_case_count": len({failure.case_id for failure in failures}),
        "owner_domains": _enum_counter_with_zeroes(owner_domains, OwnerDomain),
        "confidence": _enum_counter_with_zeroes(confidence, Confidence),
        "categories": categories,
    }


def _enum_counter_with_zeroes(counter: Counter, enum_type) -> dict[str, int]:
    return {item.value: int(counter.get(item.value, 0)) for item in enum_type}


def _interface_summaries(requests: tuple[RequestMetric, ...]) -> list[dict[str, Any]]:
    grouped: dict[str, list[RequestMetric]] = defaultdict(list)
    for request in requests:
        grouped[request.interface_id].append(request)

    summaries: list[dict[str, Any]] = []
    for interface_id, items in grouped.items():
        success_count = sum(1 for item in items if _request_succeeded(item))
        http_5xx_count = sum(
            1
            for item in items
            if item.status_code is not None and 500 <= item.status_code <= 599
        )
        timeout_count = sum(1 for item in items if item.timeout)
        durations = [item.duration_ms for item in items]
        first = items[0]
        summaries.append(
            {
                "interface_id": interface_id,
                "method": first.method,
                "url_template": first.url_template,
                "request_count": len(items),
                "success_count": success_count,
                "failure_count": len(items) - success_count,
                "http_5xx_count": http_5xx_count,
                "timeout_count": timeout_count,
                "avg_duration_ms": sum(durations) / len(durations),
                "max_duration_ms": max(durations),
            }
        )

    return sorted(
        summaries,
        key=lambda item: (
            -(item["http_5xx_count"] + item["timeout_count"]),
            -item["max_duration_ms"],
            -item["request_count"],
            item["interface_id"],
        ),
    )


def _integrity_issue_summary(issue: IntegrityIssue) -> dict[str, Any]:
    return {
        "severity": issue.severity.value,
        "source": issue.source,
        "code": issue.code,
        "message": issue.message[:300],
        "related_id": issue.related_id,
    }


def _integrity_evidence(
    issues: tuple[IntegrityIssue, ...],
    report_warnings: tuple[str, ...],
) -> tuple[str, ...]:
    evidence = [
        f"{issue.severity.value}:{issue.source}:{issue.code}:{issue.related_id or '-'}"
        for issue in issues[:10]
    ]
    evidence.extend(report_warnings[:10])
    return tuple(evidence) or ("quality integrity status is complete",)


def _source_manifest(manifest: dict[str, Any] | None) -> dict[str, Any]:
    if manifest is None:
        return {}
    keys = (
        "manifest_version",
        "schema_version",
        "run_id",
        "status",
        "merge_version",
        "classifier_rule_version",
        "fingerprint_version",
        "integrity_status",
    )
    return {"path": "merged/manifest.json", **{key: manifest.get(key) for key in keys}}


def _summary_envelope(
    run_id: str,
    created_at: datetime,
    *,
    source_manifest: dict[str, Any],
    summary: QualitySummary,
    details: dict[str, Any],
) -> dict[str, Any]:
    return {
        "report_version": REPORT_VERSION,
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "created_at": created_at.isoformat(),
        "source_manifest": source_manifest,
        "summary": summary,
        "case_status": details["case_status"],
        "failure_categories": details["failure_categories"],
        "failures": details["failures"],
        "request": details["request"],
        "interfaces": details["interfaces"],
        "integrity": details["integrity"],
    }


def _gate_envelope(
    run_id: str,
    created_at: datetime,
    *,
    gate_decision: GateDecision,
    source_manifest: dict[str, Any],
) -> dict[str, Any]:
    return {
        "report_version": REPORT_VERSION,
        "gate_ruleset_version": GATE_RULESET_VERSION,
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "created_at": created_at.isoformat(),
        "mode": gate_decision.mode.value,
        "overall": gate_decision.overall.value,
        "source_manifest": source_manifest,
        "decision": gate_decision,
        "rules": gate_decision.rules,
    }


def _render_markdown(summary_envelope: dict[str, Any], gate_decision: GateDecision) -> str:
    summary_payload = summary_envelope["summary"].model_dump(mode="json")
    lines = [
        "# P0 质量影子门禁报告",
        "",
        "## 结论",
        "",
        f"- 总体结论：{_localized_code(gate_decision.overall.value, _GATE_RESULT_LABELS)}",
        f"- 运行模式：{_localized_code(gate_decision.mode.value, _GATE_MODE_LABELS)}",
        f"- 数据完整性：{_localized_code(summary_payload['integrity_status'], _INTEGRITY_STATUS_LABELS)}",
        f"- 运行 ID：`{summary_envelope['run_id']}`",
        "",
        "## 为什么得到这个结论",
        "",
        _md_table(
            ("规则", "结论", "实际值", "阈值", "样本量", "证据"),
            [
                (
                    _localized_code(rule.rule_id, _GATE_RULE_LABELS),
                    _localized_code(rule.decision.value, _GATE_RESULT_LABELS),
                    _localized_value(rule.actual),
                    _localized_value(rule.threshold),
                    str(rule.sample_size),
                    "<br>".join(
                        _truncate(_localized_gate_evidence(item), 120)
                        for item in rule.evidence
                    ),
                )
                for rule in gate_decision.rules
            ],
        ),
        "",
        "## 用例结果",
        "",
        _md_table(
            ("总数", "通过", "失败", "错误", "跳过", "通过率"),
            [
                (
                    str(summary_payload["case_total"]),
                    str(summary_payload["case_passed"]),
                    str(summary_payload["case_failed"]),
                    str(summary_payload["case_error"]),
                    str(summary_payload["case_skipped"]),
                    f"{summary_payload['final_pass_rate']:.2%}",
                )
            ],
        ),
        "",
        "## 失败分类",
        "",
        _md_table(
            ("分类", "出现次数", "问题模式数", "受影响调用实例", "示例"),
            [
                (
                    _localized_code(category, _FAILURE_CATEGORY_LABELS),
                    str(details["occurrence_count"]),
                    str(details["fingerprint_count"]),
                    str(details["affected_invocation_count"]),
                    _failure_example(details["examples"]),
                )
                for category, details in summary_envelope["failures"]["categories"].items()
                if details["occurrence_count"]
            ]
            or [("-", "0", "0", "0", "-")],
        ),
        "",
        "## 请求指标",
        "",
        _md_table(
            ("总请求", "成功率", "5xx", "超时"),
            [
                (
                    str(summary_payload["request_total"]),
                    f"{summary_payload['request_success_rate']:.2%}",
                    str(summary_payload["http_5xx_count"]),
                    str(summary_payload["timeout_count"]),
                )
            ],
        ),
        "",
        "## 接口风险 Top",
        "",
        _md_table(
            ("接口标识", "请求数", "5xx", "超时", "平均耗时（毫秒）", "最大耗时（毫秒）"),
            [
                (
                    item["interface_id"],
                    str(item["request_count"]),
                    str(item["http_5xx_count"]),
                    str(item["timeout_count"]),
                    f"{item['avg_duration_ms']:.2f}",
                    f"{item['max_duration_ms']:.2f}",
                )
                for item in summary_envelope["interfaces"][:10]
            ]
            or [("-", "0", "0", "0", "0", "0")],
        ),
        "",
        "## 完整性问题",
        "",
        _md_table(
            ("级别", "来源", "代码", "说明"),
            [
                (
                    _localized_code(item["severity"], _ISSUE_SEVERITY_LABELS),
                    item["source"],
                    item["code"],
                    _truncate(item["message"], 160),
                )
                for item in summary_envelope["integrity"]["issues"]
            ]
            or [
                (
                    _localized_code("warn", _ISSUE_SEVERITY_LABELS),
                    "报告（`report`）",
                    "报告警告（`report_warning`）",
                    _truncate(warning, 160),
                )
                for warning in summary_envelope["integrity"]["report_warnings"]
            ]
            or [("-", "-", "-", "无")],
        ),
        "",
    ]
    return "\n".join(lines)


def _md_table(headers: tuple[str, ...], rows: Iterable[tuple[str, ...]]) -> str:
    table = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        table.append("| " + " | ".join(_escape_md(value) for value in row) + " |")
    return "\n".join(table)


def _md_value(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def _localized_code(value: object, labels: dict[str, str]) -> str:
    raw = str(value)
    label = labels.get(raw)
    return f"{label}（`{raw}`）" if label else f"`{raw}`"


def _localized_value(value: Any) -> str:
    if isinstance(value, bool):
        return f"{'是' if value else '否'}（`{str(value).lower()}`）"
    raw = _md_value(value)
    for labels in (_INTEGRITY_STATUS_LABELS, _GATE_RESULT_LABELS):
        if raw in labels:
            return _localized_code(raw, labels)
    return raw


def _localized_gate_evidence(value: str) -> str:
    exact = {
        "shadow gate disabled": "影子门禁已关闭",
        "quality merged snapshot is not available": "质量归并快照不可用",
        "quality integrity status is failed": "质量数据完整性检查失败",
        "manifest is complete and output hashes match": "清单完整，且输出文件哈希一致",
        "quality integrity status is degraded": "质量数据完整性已降级",
        "quality integrity status is not degraded": "质量数据完整性未降级",
    }
    if value in exact:
        return exact[value]
    occurrence = re.fullmatch(r"([A-Z_]+) occurrence count: (\d+)", value)
    if occurrence:
        category, count = occurrence.groups()
        label = _FAILURE_CATEGORY_LABELS.get(category, category)
        return f"{label}（{category}）出现次数：{count}"
    rate_count = re.fullmatch(r"(.+) count (\d+) of (\d+)", value)
    if rate_count:
        label, count, total = rate_count.groups()
        localized_label = "超时" if label == "timeout" else label
        separator = " " if localized_label.startswith("HTTP") else ""
        return f"{total} 个请求中出现 {count} 次{separator}{localized_label}"
    small_sample = re.fullmatch(
        r"(.+) sample size (\d+) is below minimum (\d+)", value
    )
    if small_sample:
        label, sample_size, minimum = small_sample.groups()
        localized_label = "超时" if label == "timeout" else label
        separator = " " if localized_label.startswith("HTTP") else ""
        return f"{localized_label}{separator}样本量 {sample_size}，低于最小要求 {minimum}"
    return value


def _failure_example(examples: list[dict[str, str]]) -> str:
    if not examples:
        return "-"
    example = examples[0]
    return f"{example['failure_id']} / {example['case_id']}"


def _escape_md(value: str) -> str:
    return str(value).replace("\r", " ").replace("\n", " ").replace("|", "\\|")


def _truncate(value: str, limit: int) -> str:
    text = str(value).replace("\r", " ").replace("\n", " ").strip()
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _rate(numerator: int, denominator: int) -> float:
    return (numerator / denominator) if denominator else 0.0


def _unavailable(message: str) -> _LoadedInput:
    return _LoadedInput(available=False, manifest=None, evidence=(message,))


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_text_atomic(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            temporary_file.write(content)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
        return path
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


_OUTPUT_FILES = {
    "case-results": "case-results.jsonl",
    "request-metrics": "request-metrics.jsonl",
    "failures": "failures.jsonl",
    "integrity-issues": "integrity-issues.jsonl",
}
