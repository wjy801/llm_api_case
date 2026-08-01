from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ValidationError

from quality.flaky_models import (
    FLAKY_EVALUATION_SCHEMA_VERSION,
    FLAKY_IMPORTER_VERSION,
    FLAKY_IMPORT_SCHEMA_VERSION,
    FLAKY_PROJECTION_VERSION,
    FLAKY_STATE_RULE_VERSION,
    FlakyEvaluationResult,
    FlakyEvaluationStatus,
    FlakyImportResult,
    FlakyImportStatus,
    FlakyStateSummary,
    ProjectionStatus,
)
from quality.gate import GATE_RULESET_VERSION
from quality.metrics_models import (
    RUN_METRICS_AGGREGATION_VERSION,
    RUN_METRICS_MANIFEST_VERSION,
    RUN_METRICS_SCHEMA_VERSION,
    MetricCompleteness,
    NumericAggregate,
    RatioAggregate,
    RunMetricsResult,
    RunMetricsStatus,
)
from quality.models import (
    SCHEMA_VERSION,
    GateDecision,
    GateMode,
    IntegrityStatus,
    QualitySummary,
    RunRecord,
    RunStatus,
)
from quality.observation_models import (
    P1_OBSERVATION_MANIFEST_VERSION,
    P1_OBSERVATION_REPORT_VERSION,
    P1_OBSERVATION_SCHEMA_VERSION,
    AttentionLevel,
    P1AttentionItem,
    P1FlakySection,
    P1DisplayWindow,
    P1IntegritySummary,
    P1KnownTotal,
    P1MetricObservation,
    P1MetricsSection,
    P1ObservationReport,
    P1P0Section,
    P1ReportStatus,
    P1RunOverview,
    P1SourceSummary,
    P1UsageCoverage,
    SourceExpectation,
    SourceStatus,
)
from quality.redaction import redact_quality_value
from quality.report import REPORT_VERSION
from quality.storage import write_json_atomic


_T = TypeVar("_T")
_OUTPUT_JSON = "p1-observation.json"
_OUTPUT_MARKDOWN = "p1-observation.md"
_OUTPUT_MANIFEST = "p1-observation-manifest.json"
_METRICS_ARTIFACT = "metrics/run-metrics.json"
_SOURCE_FAILURE_STATUSES = {
    SourceStatus.FAILED,
    SourceStatus.MISSING,
    SourceStatus.INCOMPATIBLE,
}

_COMMON_STATUS_LABELS = {
    "PASS": "通过",
    "WARN": "警告",
    "BLOCK": "阻断",
    "NO_DATA": "无数据",
    "complete": "完整",
    "degraded": "降级",
    "failed": "失败",
    "shadow": "影子观察",
}
_SOURCE_NAME_LABELS = {
    "p0_report": "P0 质量报告",
    "run_metrics": "单次运行指标",
    "flaky_import": "Flaky 历史导入",
    "flaky_evaluation": "Flaky 状态评估",
}
_SOURCE_EXPECTATION_LABELS = {
    "required": "必需",
    "disabled": "已禁用",
}
_SOURCE_STATUS_LABELS = {
    "available": "可用",
    "degraded": "降级",
    "no_data": "无数据",
    "failed": "失败",
    "missing": "缺失",
    "incompatible": "不兼容",
    "disabled": "已禁用",
}
_COMPLETENESS_LABELS = {
    "complete": "完整",
    "partial": "部分完整",
    "no_data": "无数据",
    "not_applicable": "不适用",
}
_METRIC_LABELS = {
    "operation.success_rate": "逻辑调用成功率",
    "operation.timeout_rate": "逻辑调用超时率",
    "request_event.business_success_rate": "请求事件业务成功率",
    "request_event.http_429_rate": "请求事件 HTTP 429 比例",
    "request_event.http_5xx_rate": "请求事件 HTTP 5xx 比例",
    "request_event.timeout_rate": "请求事件超时率",
    "request_group.business_retry_rescue_rate": "业务重试挽救率",
    "request_group.final_business_success_rate": "请求组最终业务成功率",
    "request_group.final_http_success_rate": "请求组最终 HTTP 成功率",
    "request_group.final_transport_response_rate": "请求组最终传输响应率",
    "operation.total_duration_ms": "逻辑调用总耗时（毫秒）",
    "operation.polling_total_ms": "轮询总耗时（毫秒）",
    "operation.polling_sleep_ms": "轮询休眠耗时（毫秒）",
    "operation.response_headers_ms": "响应头等待耗时（毫秒）",
    "request_group.total_duration_ms": "请求组总耗时（毫秒）",
    "request_event.all_duration_ms": "请求事件总耗时（毫秒）",
}
_RESOURCE_LABELS = {
    "input tokens": "输入 Token",
    "output tokens": "输出 Token",
    "media count": "媒体数量",
    "media duration ms": "媒体时长（毫秒）",
    "retry input tokens": "重试输入 Token",
    "retry output tokens": "重试输出 Token",
    "retry media count": "重试媒体数量",
}
_GRAIN_LABELS = {
    "run": "单次运行",
    "operation_bucket": "逻辑调用分组",
    "request_group_bucket": "请求组分组",
    "request_event_bucket": "请求事件分组",
}
_DIMENSION_KEY_LABELS = {
    "model_id": "模型 ID",
    "operation_kind": "调用类型",
    "operation_name": "调用名称",
    "traffic_role": "流量角色",
    "interface_id": "接口标识",
    "protocol": "协议",
}
_DIMENSION_VALUE_LABELS = {
    "async_task": "异步任务",
    "polling": "轮询",
    "http": "HTTP",
    "sse": "SSE",
    "workload": "业务流量",
    "control": "控制流量",
    "media_generation": "媒体生成",
    "media_generation_polling": "媒体生成轮询",
    "image_generation": "图片生成",
}
_FLAKY_STATE_LABELS = {
    "OBSERVING": "观察中",
    "STABLE": "稳定",
    "SUSPECTED": "疑似不稳定",
    "CONFIRMED": "已确认不稳定",
    "QUARANTINED": "已隔离",
    "RECOVERING": "恢复观察中",
}
_PROJECTION_STATUS_LABELS = {
    "CURRENT": "当前",
    "STALE": "已过期",
}
_TRIGGER_LABELS = {
    "observation": "自动观测",
    "manual": "人工操作",
    "bootstrap": "初始建档",
    "reprojection": "重新投影",
}
_TRANSITION_REASON_LABELS = {
    "first_observation": "首次观测",
    "outcome_changed": "执行结果发生变化",
    "failure_fingerprint_changed": "失败指纹发生变化",
    "consistent_signature_threshold_met": "达到连续一致阈值",
    "stable_signature_broken": "稳定结果被打破",
    "confirmation_threshold_met": "达到 Flaky 确认阈值",
    "suspected_cleared_by_streak": "连续一致后解除疑似状态",
}
_ATTENTION_LEVEL_LABELS = {
    "info": "提示",
    "review": "需复核",
    "action_required": "需要处理",
}
_ISSUE_CODE_LABELS = {
    "expected_outcome_excluded": "预期结果已排除",
    "usage_incomplete": "用量覆盖不完整",
    "source_disabled": "数据源已禁用",
    "required_source_unavailable": "必需数据源不可用",
    "required_source_degraded": "必需数据源已降级",
    "usage_coverage_incomplete": "用量覆盖不完整",
    "flaky_projection_stale": "Flaky 投影已过期",
    "flaky_newly_suspected": "新增疑似 Flaky",
    "flaky_newly_confirmed": "新增确认 Flaky",
    "flaky_governance_overdue": "Flaky 治理已超期",
    "flaky_recovered": "Flaky 已恢复",
}
_DISPLAY_WINDOW_LABELS = {
    "flaky_governance": "Flaky 治理项",
    "flaky_new_and_ongoing": "新增及持续 Flaky",
    "flaky_transitions": "Flaky 状态迁移",
    "timing_observations": "耗时观测",
    "usage_missing_refs": "用量缺失引用",
}


@dataclass(frozen=True)
class P1ObservationRequest:
    run_id: str
    output_dir: Path
    metrics_expectation: SourceExpectation = SourceExpectation.REQUIRED
    flaky_import_expectation: SourceExpectation = SourceExpectation.REQUIRED
    flaky_evaluation_expectation: SourceExpectation = SourceExpectation.REQUIRED


@dataclass(frozen=True)
class P1ObservationGenerationResult:
    run_id: str
    output_dir: Path
    manifest_path: Path
    json_path: Path
    markdown_path: Path
    write_status: str
    report_status: P1ReportStatus | None
    issue_codes: tuple[str, ...]
    report: P1ObservationReport | None = None


@dataclass(frozen=True)
class _LoadedSource(Generic[_T]):
    summary: P1SourceSummary
    value: _T | None = None
    hashes: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class _P0Value:
    run: RunRecord
    summary: QualitySummary
    gate: GateDecision
    failure_categories: dict[str, int]


def generate_p1_observation_report(
    request: P1ObservationRequest,
) -> P1ObservationGenerationResult:
    run_id = _required_text(request.run_id, "run_id")
    output_dir = Path(request.output_dir)
    manifest_path = output_dir / _OUTPUT_MANIFEST
    json_path = output_dir / _OUTPUT_JSON
    markdown_path = output_dir / _OUTPUT_MARKDOWN
    created_at = datetime.now(UTC)
    metrics_expectation = SourceExpectation(request.metrics_expectation)
    flaky_import_expectation = SourceExpectation(request.flaky_import_expectation)
    flaky_evaluation_expectation = SourceExpectation(request.flaky_evaluation_expectation)
    _write_manifest(
        manifest_path,
        run_id=run_id,
        created_at=created_at,
        write_status="building",
        report_status=None,
        output_hashes={},
        source_hashes={},
        issue_codes=(),
    )
    try:
        p0 = _load_p0(run_id, output_dir)
        metrics = _load_metrics(
            run_id, output_dir, expectation=metrics_expectation
        )
        flaky_import = _load_flaky_import(
            run_id, output_dir, expectation=flaky_import_expectation
        )
        flaky_evaluation = _load_flaky_evaluation(
            run_id,
            output_dir,
            expectation=flaky_evaluation_expectation,
        )
        loaded = (p0, metrics, flaky_import, flaky_evaluation)
        report = _build_report(
            run_id,
            created_at=created_at,
            p0=p0,
            metrics=metrics,
            flaky_import=flaky_import,
            flaky_evaluation=flaky_evaluation,
        )
        markdown = render_p1_observation_markdown(report)
        _write_text_atomic(markdown_path, markdown)
        write_json_atomic(json_path, report)
        output_hashes = {
            "json": _file_sha256(json_path),
            "markdown": _file_sha256(markdown_path),
        }
        source_hashes = {
            name: digest
            for item in loaded
            for name, digest in item.hashes
        }
        issue_codes = report.integrity.issue_codes
        _write_manifest(
            manifest_path,
            run_id=run_id,
            created_at=created_at,
            write_status="complete",
            report_status=report.report_status,
            output_hashes=output_hashes,
            source_hashes=source_hashes,
            issue_codes=issue_codes,
        )
        return P1ObservationGenerationResult(
            run_id=run_id,
            output_dir=output_dir,
            manifest_path=manifest_path,
            json_path=json_path,
            markdown_path=markdown_path,
            write_status="complete",
            report_status=report.report_status,
            issue_codes=issue_codes,
            report=report,
        )
    except Exception as error:
        code = "p1_observation_generation_failed"
        try:
            _write_manifest(
                manifest_path,
                run_id=run_id,
                created_at=created_at,
                write_status="failed",
                report_status=None,
                output_hashes={},
                source_hashes={},
                issue_codes=(code, type(error).__name__),
            )
        except Exception:
            pass
        return P1ObservationGenerationResult(
            run_id=run_id,
            output_dir=output_dir,
            manifest_path=manifest_path,
            json_path=json_path,
            markdown_path=markdown_path,
            write_status="failed",
            report_status=None,
            issue_codes=(code, type(error).__name__),
        )


def _load_p0(run_id: str, output_dir: Path) -> _LoadedSource[_P0Value]:
    expectation = SourceExpectation.REQUIRED
    paths = {
        "run.json": output_dir / "run.json",
        "summary.json": output_dir / "summary.json",
        "gate-report.json": output_dir / "gate-report.json",
        "gate-report.md": output_dir / "gate-report.md",
    }
    missing = tuple(name for name, path in paths.items() if not path.is_file())
    if any(name != "gate-report.md" for name in missing):
        return _source_result(
            "p0_report",
            expectation,
            SourceStatus.MISSING,
            artifact_path="summary.json",
            issue_codes=tuple(f"p0_{_code_name(name)}_missing" for name in missing),
            evidence_refs=tuple(name for name in paths if name not in missing),
        )
    try:
        run = RunRecord.model_validate_json(paths["run.json"].read_text(encoding="utf-8"))
        summary_payload = _read_json_object(paths["summary.json"])
        gate_payload = _read_json_object(paths["gate-report.json"])
        if (
            run.run_id != run_id
            or summary_payload.get("run_id") != run_id
            or gate_payload.get("run_id") != run_id
        ):
            raise _IncompatibleSource("p0_run_id_mismatch")
        if summary_payload.get("schema_version") != SCHEMA_VERSION:
            raise _IncompatibleSource("p0_schema_version_unsupported")
        if summary_payload.get("report_version") != REPORT_VERSION:
            raise _IncompatibleSource("p0_report_version_unsupported")
        if gate_payload.get("schema_version") != SCHEMA_VERSION:
            raise _IncompatibleSource("p0_gate_schema_version_unsupported")
        if gate_payload.get("report_version") != REPORT_VERSION:
            raise _IncompatibleSource("p0_gate_report_version_unsupported")
        if gate_payload.get("gate_ruleset_version") != GATE_RULESET_VERSION:
            raise _IncompatibleSource("p0_gate_ruleset_version_unsupported")
        summary = QualitySummary.model_validate(summary_payload.get("summary"))
        gate = GateDecision.model_validate(gate_payload.get("decision"))
        if summary.run_id != run_id or gate.run_id != run_id:
            raise _IncompatibleSource("p0_nested_run_id_mismatch")
        if gate.mode is not GateMode.SHADOW:
            raise _IncompatibleSource("p0_gate_mode_not_shadow")
        if gate_payload.get("mode") != gate.mode.value or gate_payload.get("overall") != gate.overall.value:
            raise _IncompatibleSource("p0_gate_envelope_mismatch")
        categories = _count_map(summary_payload.get("failure_categories"), "failure_categories")
    except _IncompatibleSource as error:
        return _source_result(
            "p0_report",
            expectation,
            SourceStatus.INCOMPATIBLE,
            artifact_path="summary.json",
            issue_codes=(error.code,),
            evidence_refs=("run.json", "summary.json", "gate-report.json"),
            hashes=_existing_hashes(paths),
        )
    except (OSError, ValidationError, ValueError, TypeError):
        return _source_result(
            "p0_report",
            expectation,
            SourceStatus.INCOMPATIBLE,
            artifact_path="summary.json",
            issue_codes=("p0_report_invalid",),
            evidence_refs=("run.json", "summary.json", "gate-report.json"),
            hashes=_existing_hashes(paths),
        )
    issues: list[str] = []
    status = SourceStatus.AVAILABLE
    if run.status is not RunStatus.FINISHED:
        issues.append("p0_run_not_finished")
        status = SourceStatus.DEGRADED
    if summary.integrity_status is not IntegrityStatus.COMPLETE:
        issues.append("p0_integrity_not_complete")
        status = SourceStatus.DEGRADED
    if "gate-report.md" in missing:
        issues.append("p0_gate_markdown_missing")
        status = SourceStatus.DEGRADED
    hashes = _existing_hashes(paths)
    return _source_result(
        "p0_report",
        expectation,
        status,
        artifact_path="summary.json",
        schema_version=SCHEMA_VERSION,
        producer_version=REPORT_VERSION,
        sha256=_hash_for(hashes, "summary.json"),
        issue_codes=tuple(issues),
        evidence_refs=tuple(name for name in paths if name not in missing),
        value=_P0Value(
            run=run,
            summary=summary,
            gate=gate,
            failure_categories=categories,
        ),
        hashes=hashes,
    )


def _load_metrics(
    run_id: str,
    output_dir: Path,
    *,
    expectation: SourceExpectation,
) -> _LoadedSource[RunMetricsResult]:
    if expectation is SourceExpectation.DISABLED:
        return _disabled_source("run_metrics")
    manifest_path = output_dir / "metrics" / "manifest.json"
    metrics_path = output_dir / _METRICS_ARTIFACT
    if not manifest_path.is_file():
        return _source_result(
            "run_metrics",
            expectation,
            SourceStatus.MISSING,
            artifact_path="metrics/manifest.json",
            issue_codes=("metrics_manifest_missing",),
        )
    manifest_hash = _file_sha256(manifest_path)
    hashes: tuple[tuple[str, str], ...] = (("metrics/manifest.json", manifest_hash),)
    try:
        manifest = _read_json_object(manifest_path)
    except (OSError, ValueError):
        return _source_result(
            "run_metrics",
            expectation,
            SourceStatus.INCOMPATIBLE,
            artifact_path="metrics/manifest.json",
            sha256=manifest_hash,
            issue_codes=("metrics_manifest_invalid",),
            evidence_refs=("metrics/manifest.json",),
            hashes=hashes,
        )
    issue_codes = _manifest_issue_codes(manifest)
    base = {
        "artifact_path": "metrics/manifest.json",
        "schema_version": _optional_manifest_text(manifest, "schema_version"),
        "producer_version": _optional_manifest_text(manifest, "aggregation_version"),
        "sha256": manifest_hash,
        "evidence_refs": ("metrics/manifest.json",),
        "hashes": hashes,
    }
    if manifest.get("run_id") != run_id:
        return _source_result(
            "run_metrics",
            expectation,
            SourceStatus.INCOMPATIBLE,
            issue_codes=("metrics_run_id_mismatch",),
            **base,
        )
    if (
        manifest.get("manifest_version") != RUN_METRICS_MANIFEST_VERSION
        or manifest.get("schema_version") != RUN_METRICS_SCHEMA_VERSION
        or manifest.get("aggregation_version") != RUN_METRICS_AGGREGATION_VERSION
    ):
        return _source_result(
            "run_metrics",
            expectation,
            SourceStatus.INCOMPATIBLE,
            issue_codes=("metrics_version_unsupported",),
            **base,
        )
    write_status = manifest.get("write_status")
    if write_status == "failed" or manifest.get("metrics_status") == RunMetricsStatus.FAILED.value:
        return _source_result(
            "run_metrics",
            expectation,
            SourceStatus.FAILED,
            issue_codes=issue_codes or ("metrics_upstream_failed",),
            **base,
        )
    if write_status != "complete":
        return _source_result(
            "run_metrics",
            expectation,
            SourceStatus.FAILED,
            issue_codes=("metrics_manifest_not_complete",),
            **base,
        )
    if not metrics_path.is_file():
        return _source_result(
            "run_metrics",
            expectation,
            SourceStatus.MISSING,
            issue_codes=("metrics_result_missing",),
            **base,
        )
    metrics_hash = _file_sha256(metrics_path)
    hashes = (*hashes, (_METRICS_ARTIFACT, metrics_hash))
    expected_hash = (manifest.get("output_hashes") or {}).get("run_metrics")
    if expected_hash != metrics_hash:
        return _source_result(
            "run_metrics",
            expectation,
            SourceStatus.INCOMPATIBLE,
            artifact_path=_METRICS_ARTIFACT,
            schema_version=RUN_METRICS_SCHEMA_VERSION,
            producer_version=RUN_METRICS_AGGREGATION_VERSION,
            sha256=metrics_hash,
            issue_codes=("metrics_result_hash_mismatch",),
            evidence_refs=("metrics/manifest.json", _METRICS_ARTIFACT),
            hashes=hashes,
        )
    try:
        metrics = RunMetricsResult.model_validate_json(metrics_path.read_text(encoding="utf-8"))
        _validate_metrics_contract(metrics, manifest, run_id)
    except (OSError, ValidationError, ValueError, TypeError):
        return _source_result(
            "run_metrics",
            expectation,
            SourceStatus.INCOMPATIBLE,
            artifact_path=_METRICS_ARTIFACT,
            schema_version=RUN_METRICS_SCHEMA_VERSION,
            producer_version=RUN_METRICS_AGGREGATION_VERSION,
            sha256=metrics_hash,
            issue_codes=("metrics_result_invalid",),
            evidence_refs=("metrics/manifest.json", _METRICS_ARTIFACT),
            hashes=hashes,
        )
    status = {
        RunMetricsStatus.AGGREGATED: SourceStatus.AVAILABLE,
        RunMetricsStatus.DEGRADED: SourceStatus.DEGRADED,
        RunMetricsStatus.NO_DATA: SourceStatus.NO_DATA,
        RunMetricsStatus.FAILED: SourceStatus.FAILED,
    }[metrics.status]
    return _source_result(
        "run_metrics",
        expectation,
        status,
        artifact_path=_METRICS_ARTIFACT,
        schema_version=RUN_METRICS_SCHEMA_VERSION,
        producer_version=RUN_METRICS_AGGREGATION_VERSION,
        sha256=metrics_hash,
        issue_codes=tuple(sorted({*issue_codes, *(item.code for item in metrics.issues)})),
        evidence_refs=("metrics/manifest.json", _METRICS_ARTIFACT),
        value=metrics,
        hashes=hashes,
    )


def _validate_metrics_contract(
    metrics: RunMetricsResult,
    manifest: dict[str, Any],
    run_id: str,
) -> None:
    if metrics.run_id != run_id:
        raise ValueError("metrics run_id mismatch")
    if metrics.status is RunMetricsStatus.FAILED or metrics.run_metrics is None:
        raise ValueError("complete manifest cannot consume failed metrics")
    counts = manifest.get("output_counts")
    if not isinstance(counts, dict):
        raise ValueError("metrics output_counts is invalid")
    expected = {
        "workload_operations": metrics.run_metrics.operation.operation_count,
        "workload_request_groups": metrics.run_metrics.request_groups.group_count,
        "workload_request_events": metrics.run_metrics.request_events.event_count,
        "operation_buckets": len(metrics.operation_buckets),
        "request_group_buckets": len(metrics.request_group_buckets),
        "request_event_buckets": len(metrics.request_event_buckets),
    }
    if any(counts.get(name) != value for name, value in expected.items()):
        raise ValueError("metrics manifest counts do not match result")
    _validate_bucket_members(
        metrics.operation_buckets,
        metrics.run_metrics.operation.operation_count,
    )
    _validate_bucket_members(
        metrics.request_group_buckets,
        metrics.run_metrics.request_groups.group_count,
    )
    _validate_bucket_members(
        metrics.request_event_buckets,
        metrics.run_metrics.request_events.event_count,
    )


def _validate_bucket_members(buckets: tuple[Any, ...], expected_count: int) -> None:
    members = [member for bucket in buckets for member in bucket.evidence.member_ids]
    if len(members) != expected_count or len(members) != len(set(members)):
        raise ValueError("metrics bucket membership is incomplete or duplicated")


def _load_flaky_import(
    run_id: str,
    output_dir: Path,
    *,
    expectation: SourceExpectation,
) -> _LoadedSource[FlakyImportResult]:
    if expectation is SourceExpectation.DISABLED:
        return _disabled_source("flaky_import")
    path = output_dir / "flaky-import.json"
    return _load_flaky_model(
        source_name="flaky_import",
        path=path,
        artifact_path="flaky-import.json",
        run_id=run_id,
        expectation=expectation,
        model=FlakyImportResult,
        schema_version=FLAKY_IMPORT_SCHEMA_VERSION,
        producer_version=FLAKY_IMPORTER_VERSION,
        status_mapper=_flaky_import_source_status,
    )


def _load_flaky_evaluation(
    run_id: str,
    output_dir: Path,
    *,
    expectation: SourceExpectation,
) -> _LoadedSource[FlakyEvaluationResult]:
    if expectation is SourceExpectation.DISABLED:
        return _disabled_source("flaky_evaluation")
    path = output_dir / "flaky-evaluation.json"
    loaded = _load_flaky_model(
        source_name="flaky_evaluation",
        path=path,
        artifact_path="flaky-evaluation.json",
        run_id=run_id,
        expectation=expectation,
        model=FlakyEvaluationResult,
        schema_version=FLAKY_EVALUATION_SCHEMA_VERSION,
        producer_version=FLAKY_STATE_RULE_VERSION,
        status_mapper=_flaky_evaluation_source_status,
    )
    if loaded.value is None:
        return loaded
    value = loaded.value
    if (
        value.rule_version != FLAKY_STATE_RULE_VERSION
        or value.projection_version != FLAKY_PROJECTION_VERSION
        or any(
            len(item.evidence_observation_ids) > 20 or len(item.evidence_run_ids) > 20
            for item in value.transitions
        )
    ):
        return _source_result(
            "flaky_evaluation",
            expectation,
            SourceStatus.INCOMPATIBLE,
            artifact_path="flaky-evaluation.json",
            schema_version=FLAKY_EVALUATION_SCHEMA_VERSION,
            producer_version=FLAKY_STATE_RULE_VERSION,
            sha256=loaded.summary.sha256,
            issue_codes=("flaky_evaluation_contract_incompatible",),
            evidence_refs=("flaky-evaluation.json",),
            hashes=loaded.hashes,
        )
    if value.stale_count > 0 and loaded.summary.status is SourceStatus.AVAILABLE:
        loaded = _source_result(
            "flaky_evaluation",
            expectation,
            SourceStatus.DEGRADED,
            artifact_path="flaky-evaluation.json",
            schema_version=FLAKY_EVALUATION_SCHEMA_VERSION,
            producer_version=FLAKY_STATE_RULE_VERSION,
            sha256=loaded.summary.sha256,
            issue_codes=tuple(sorted({*loaded.summary.issue_codes, "flaky_projection_stale"})),
            evidence_refs=("flaky-evaluation.json",),
            value=value,
            hashes=loaded.hashes,
        )
    return loaded


def _load_flaky_model(
    *,
    source_name: str,
    path: Path,
    artifact_path: str,
    run_id: str,
    expectation: SourceExpectation,
    model: type[_T],
    schema_version: str,
    producer_version: str,
    status_mapper: Any,
) -> _LoadedSource[_T]:
    if not path.is_file():
        return _source_result(
            source_name,
            expectation,
            SourceStatus.MISSING,
            artifact_path=artifact_path,
            issue_codes=(f"{source_name}_missing",),
        )
    digest = _file_sha256(path)
    hashes = ((artifact_path, digest),)
    try:
        value = model.model_validate_json(path.read_text(encoding="utf-8"))
        if getattr(value, "run_id", None) != run_id:
            raise _IncompatibleSource(f"{source_name}_run_id_mismatch")
        artifact_ref = getattr(value, "artifact_ref", None)
        if artifact_ref and Path(artifact_ref).is_absolute():
            raise _IncompatibleSource(f"{source_name}_absolute_artifact_ref")
    except _IncompatibleSource as error:
        return _source_result(
            source_name,
            expectation,
            SourceStatus.INCOMPATIBLE,
            artifact_path=artifact_path,
            sha256=digest,
            issue_codes=(error.code,),
            evidence_refs=(artifact_path,),
            hashes=hashes,
        )
    except (OSError, ValidationError, ValueError):
        return _source_result(
            source_name,
            expectation,
            SourceStatus.INCOMPATIBLE,
            artifact_path=artifact_path,
            sha256=digest,
            issue_codes=(f"{source_name}_invalid",),
            evidence_refs=(artifact_path,),
            hashes=hashes,
        )
    source_status = status_mapper(getattr(value, "status"))
    return _source_result(
        source_name,
        expectation,
        source_status,
        artifact_path=artifact_path,
        schema_version=schema_version,
        producer_version=producer_version,
        sha256=digest,
        issue_codes=tuple(sorted(item.code for item in getattr(value, "issues", ()))),
        evidence_refs=(artifact_path,),
        value=value if source_status is not SourceStatus.FAILED else None,
        hashes=hashes,
    )


def _build_report(
    run_id: str,
    *,
    created_at: datetime,
    p0: _LoadedSource[_P0Value],
    metrics: _LoadedSource[RunMetricsResult],
    flaky_import: _LoadedSource[FlakyImportResult],
    flaky_evaluation: _LoadedSource[FlakyEvaluationResult],
) -> P1ObservationReport:
    loaded = (p0, metrics, flaky_import, flaky_evaluation)
    sources = tuple(item.summary for item in loaded)
    report_status = _report_status(sources)
    p0_section = _p0_section(p0.value) if p0.value is not None else None
    metrics_section = (
        _metrics_section(metrics.value) if metrics.value is not None else None
    )
    usage = _usage_coverage(metrics.value) if metrics.value is not None else None
    flaky = _flaky_section(flaky_import.value, flaky_evaluation.value)
    attention = _attention_items(sources, usage, flaky)
    source_issue_codes = {
        code for source in sources for code in source.issue_codes
    }
    required_failures = sum(
        source.expectation is SourceExpectation.REQUIRED
        and source.status in _SOURCE_FAILURE_STATUSES
        for source in sources
    )
    degraded_sources = tuple(
        source.source_name
        for source in sources
        if source.status
        in {
            SourceStatus.DEGRADED,
            SourceStatus.FAILED,
            SourceStatus.MISSING,
            SourceStatus.INCOMPATIBLE,
        }
    )
    overview = _overview(
        run_id,
        created_at=created_at,
        report_status=report_status,
        p0=p0_section,
        metrics=metrics_section,
        usage=usage,
        flaky=flaky,
        required_source_failures=required_failures,
    )
    return P1ObservationReport(
        run_id=run_id,
        generated_at=created_at,
        report_status=report_status,
        overview=overview,
        sources=sources,
        p0=p0_section,
        metrics=metrics_section,
        usage_coverage=usage,
        flaky=flaky,
        display_windows=_display_windows(metrics_section, usage, flaky),
        attention_items=attention,
        integrity=P1IntegritySummary(
            issue_codes=tuple(sorted(source_issue_codes)),
            degraded_sources=degraded_sources,
            required_source_failure_count=required_failures,
            evidence_refs=tuple(
                sorted(
                    {
                        ref
                        for source in sources
                        for ref in source.evidence_refs
                    }
                )
            ),
        ),
    )


def _report_status(sources: tuple[P1SourceSummary, ...]) -> P1ReportStatus:
    required = tuple(
        item for item in sources if item.expectation is SourceExpectation.REQUIRED
    )
    consumable = {
        SourceStatus.AVAILABLE,
        SourceStatus.DEGRADED,
        SourceStatus.NO_DATA,
    }
    if required and not any(item.status in consumable for item in required):
        return P1ReportStatus.NO_DATA
    if any(
        item.status
        in {
            SourceStatus.DEGRADED,
            SourceStatus.FAILED,
            SourceStatus.MISSING,
            SourceStatus.INCOMPATIBLE,
        }
        for item in required
    ):
        return P1ReportStatus.DEGRADED
    return P1ReportStatus.COMPLETE


def _p0_section(value: _P0Value) -> P1P0Section:
    summary = value.summary
    return P1P0Section(
        gate_mode=value.gate.mode.value,
        gate_overall=value.gate.overall.value,
        integrity_status=summary.integrity_status.value,
        case_total=summary.case_total,
        case_passed=summary.case_passed,
        case_failed=summary.case_failed,
        case_error=summary.case_error,
        case_skipped=summary.case_skipped,
        request_total=summary.request_total,
        http_5xx_count=summary.http_5xx_count,
        timeout_count=summary.timeout_count,
        failure_categories=value.failure_categories,
        evidence_refs=("run.json", "summary.json", "gate-report.json", "gate-report.md"),
    )


def _metrics_section(result: RunMetricsResult) -> P1MetricsSection:
    run = result.run_metrics
    if run is None:
        raise ValueError("consumable metrics result has no run metrics")
    observations: list[P1MetricObservation] = []
    run_ratios = {
        "operation.success_rate": run.operation.success_rate,
        "operation.timeout_rate": run.operation.timeout_rate,
        "request_group.retry_rate": run.request_groups.retry_rate,
        "request_group.first_transport_response_rate": run.request_groups.first_transport_response_rate,
        "request_group.final_transport_response_rate": run.request_groups.final_transport_response_rate,
        "request_group.first_http_success_rate": run.request_groups.first_http_success_rate,
        "request_group.final_http_success_rate": run.request_groups.final_http_success_rate,
        "request_group.first_business_success_rate": run.request_groups.first_business_success_rate,
        "request_group.final_business_success_rate": run.request_groups.final_business_success_rate,
        "request_group.http_retry_rescue_rate": run.request_groups.http_retry_rescue_rate,
        "request_group.business_retry_rescue_rate": run.request_groups.business_retry_rescue_rate,
        "request_event.timeout_rate": run.request_events.timeout_rate,
        "request_event.http_5xx_rate": run.request_events.http_5xx_rate,
        "request_event.http_429_rate": run.request_events.http_429_rate,
        "request_event.business_success_rate": run.request_events.business_success_rate,
    }
    observations.extend(
        _ratio_observation(
            metric_id=f"run:{name}",
            grain="run",
            dimension={},
            name=name,
            aggregate=aggregate,
            evidence_refs=("metrics/run-metrics.json",),
        )
        for name, aggregate in run_ratios.items()
    )
    run_numeric = {
        "operation.total_duration_ms": run.operation_timing.total_duration_ms,
        "operation.response_headers_ms": run.operation_timing.response_headers_ms,
        "operation.first_data_ms": run.operation_timing.first_data_ms,
        "operation.first_content_ms": run.operation_timing.first_content_ms,
        "operation.stream_duration_ms": run.operation_timing.stream_duration_ms,
        "operation.create_request_ms": run.operation_timing.create_request_ms,
        "operation.polling_total_ms": run.operation_timing.polling_total_ms,
        "operation.polling_sleep_ms": run.operation_timing.polling_sleep_ms,
        "request_group.total_duration_ms": run.request_group_timing.total_duration_ms,
        "request_group.retry_wait_ms": run.request_group_timing.retry_wait_ms,
        "request_group.first_attempt_duration_ms": run.request_group_timing.first_attempt_duration_ms,
        "request_group.retry_attempt_duration_ms": run.request_group_timing.retry_attempt_duration_ms,
        "request_event.all_duration_ms": run.request_event_timing.all_duration_ms,
        "request_event.timeout_duration_ms": run.request_event_timing.timeout_duration_ms,
        "request_event.transport_error_duration_ms": run.request_event_timing.transport_error_duration_ms,
    }
    observations.extend(
        _numeric_observation(
            metric_id=f"run:{name}",
            grain="run",
            dimension={},
            name=name,
            aggregate=aggregate,
            evidence_refs=("metrics/run-metrics.json",),
        )
        for name, aggregate in run_numeric.items()
    )
    for bucket in result.operation_buckets:
        dimension = bucket.dimension.model_dump(mode="json")
        base_id = bucket.evidence.metric_bucket_id
        evidence = _bucket_evidence(bucket.evidence)
        observations.append(
            _ratio_observation(
                metric_id=f"{base_id}:success_rate",
                grain="operation_bucket",
                dimension=dimension,
                name="operation.success_rate",
                aggregate=bucket.stability.success_rate,
                evidence_refs=evidence,
            )
        )
        for name, aggregate in (
            ("operation.total_duration_ms", bucket.timing.total_duration_ms),
            ("operation.response_headers_ms", bucket.timing.response_headers_ms),
            ("operation.first_data_ms", bucket.timing.first_data_ms),
            ("operation.first_content_ms", bucket.timing.first_content_ms),
            ("operation.stream_duration_ms", bucket.timing.stream_duration_ms),
            ("operation.create_request_ms", bucket.timing.create_request_ms),
            ("operation.polling_total_ms", bucket.timing.polling_total_ms),
            ("operation.polling_sleep_ms", bucket.timing.polling_sleep_ms),
        ):
            observations.append(
                _numeric_observation(
                    metric_id=f"{base_id}:{name}",
                    grain="operation_bucket",
                    dimension=dimension,
                    name=name,
                    aggregate=aggregate,
                    evidence_refs=evidence,
                )
            )
    for bucket in result.request_group_buckets:
        dimension = bucket.dimension.model_dump(mode="json")
        base_id = bucket.evidence.metric_bucket_id
        evidence = _bucket_evidence(bucket.evidence)
        for name, aggregate in (
            ("request_group.retry_rate", bucket.stability.retry_rate),
            ("request_group.final_http_success_rate", bucket.stability.final_http_success_rate),
            ("request_group.http_retry_rescue_rate", bucket.stability.http_retry_rescue_rate),
        ):
            observations.append(
                _ratio_observation(
                    metric_id=f"{base_id}:{name}",
                    grain="request_group_bucket",
                    dimension=dimension,
                    name=name,
                    aggregate=aggregate,
                    evidence_refs=evidence,
                )
            )
        observations.append(
            _numeric_observation(
                metric_id=f"{base_id}:request_group.total_duration_ms",
                grain="request_group_bucket",
                dimension=dimension,
                name="request_group.total_duration_ms",
                aggregate=bucket.timing.total_duration_ms,
                evidence_refs=evidence,
            )
        )
    for bucket in result.request_event_buckets:
        dimension = bucket.dimension.model_dump(mode="json")
        base_id = bucket.evidence.metric_bucket_id
        evidence = _bucket_evidence(bucket.evidence)
        for name, aggregate in (
            ("request_event.timeout_rate", bucket.stability.timeout_rate),
            ("request_event.http_5xx_rate", bucket.stability.http_5xx_rate),
        ):
            observations.append(
                _ratio_observation(
                    metric_id=f"{base_id}:{name}",
                    grain="request_event_bucket",
                    dimension=dimension,
                    name=name,
                    aggregate=aggregate,
                    evidence_refs=evidence,
                )
            )
        observations.append(
            _numeric_observation(
                metric_id=f"{base_id}:request_event.all_duration_ms",
                grain="request_event_bucket",
                dimension=dimension,
                name="request_event.all_duration_ms",
                aggregate=bucket.timing.all_duration_ms,
                evidence_refs=evidence,
            )
        )
    exclusions = result.exclusions
    return P1MetricsSection(
        metrics_status=result.status.value,
        aggregation_version=result.aggregation_version,
        workload_operation_count=run.operation.operation_count,
        request_group_count=run.request_groups.group_count,
        request_event_count=run.request_events.event_count,
        operation_outcomes=run.operation.outcomes.counts,
        control_operation_count=len(exclusions.control_operation_ids),
        control_group_count=len(exclusions.control_group_ids),
        control_event_count=len(exclusions.control_event_ids),
        unknown_operation_count=len(exclusions.unknown_operation_ids),
        unknown_group_count=len(exclusions.unknown_group_ids),
        unknown_event_count=len(exclusions.unknown_event_ids),
        unknown_role_count=(
            len(exclusions.unknown_operation_ids)
            + len(exclusions.unknown_group_ids)
            + len(exclusions.unknown_event_ids)
        ),
        unassigned_event_count=len(exclusions.unassigned_event_ids),
        observations=tuple(
            sorted(
                observations,
                key=lambda item: (
                    item.grain,
                    json.dumps(item.dimension, sort_keys=True),
                    item.metric_name,
                    item.metric_id,
                ),
            )
        ),
        source_artifact=_METRICS_ARTIFACT,
    )


def _usage_coverage(result: RunMetricsResult) -> P1UsageCoverage:
    run = result.run_metrics
    if run is None:
        raise ValueError("consumable metrics result has no usage metrics")
    usage = run.usage
    counts = usage.completeness.counts
    missing_buckets = tuple(
        bucket.evidence.metric_bucket_id
        for bucket in result.operation_buckets
        if (
            bucket.usage.completeness.counts.get("partial", 0)
            + bucket.usage.completeness.counts.get("missing", 0)
        )
        > 0
    )
    missing_event_buckets = tuple(
        bucket.evidence.metric_bucket_id
        for bucket in result.operation_buckets
        if bucket.usage.missing_source_event_count > 0
    )
    retry = usage.retry_extra_usage
    return P1UsageCoverage(
        eligible_operation_count=run.operation.operation_count,
        complete_count=counts.get("complete", 0),
        partial_count=counts.get("partial", 0),
        missing_count=counts.get("missing", 0),
        not_applicable_count=counts.get("not_applicable", 0),
        input_tokens=_known_total(usage.input_tokens),
        output_tokens=_known_total(usage.output_tokens),
        media_count=_known_total(usage.media_count),
        media_duration_ms=_known_total(usage.media_duration_ms),
        retry_input_tokens=_known_total(retry.retry_input_tokens),
        retry_output_tokens=_known_total(retry.retry_output_tokens),
        retry_media_count=_known_total(retry.retry_media_count),
        retry_missing_attempt_count=retry.retry_missing_attempt_count,
        missing_operation_refs=missing_buckets,
        missing_event_refs=missing_event_buckets,
        source_artifact=_METRICS_ARTIFACT,
    )


def _known_total(aggregate: NumericAggregate) -> P1KnownTotal:
    return P1KnownTotal(
        sample_size=aggregate.sample_size,
        missing_sample_size=aggregate.missing_count,
        total=aggregate.total,
        completeness=aggregate.completeness,
    )


def _flaky_section(
    imported: FlakyImportResult | None,
    evaluated: FlakyEvaluationResult | None,
) -> P1FlakySection | None:
    if imported is None and evaluated is None:
        return None
    if evaluated is None:
        return P1FlakySection(
            import_status=imported.status.value if imported is not None else None,
            import_database_schema_version=(
                imported.database_schema_version if imported is not None else None
            ),
            quick_check=(
                _safe_text(imported.quick_check, 128)
                if imported is not None and imported.quick_check is not None
                else None
            ),
            issue_codes=tuple(item.code for item in imported.issues) if imported else (),
            source_artifacts=("flaky-import.json",) if imported is not None else (),
        )
    issue_codes = {
        *(item.code for item in evaluated.issues),
        *(item.code for item in (imported.issues if imported is not None else ())),
    }
    return P1FlakySection(
        import_status=imported.status.value if imported is not None else None,
        evaluation_status=evaluated.status.value,
        rule_version=evaluated.rule_version,
        projection_version=evaluated.projection_version,
        import_database_schema_version=(
            imported.database_schema_version if imported is not None else None
        ),
        evaluation_database_schema_version=evaluated.database_schema_version,
        quick_check=(
            _safe_text(evaluated.quick_check, 128)
            if evaluated.quick_check is not None
            else (
                _safe_text(imported.quick_check, 128)
                if imported is not None and imported.quick_check is not None
                else None
            )
        ),
        affected_count=evaluated.affected_count,
        evaluated_count=evaluated.evaluated_count,
        transitioned_count=evaluated.transitioned_count,
        stale_count=evaluated.stale_count,
        newly_suspected=_sorted_states(evaluated.newly_suspected),
        newly_confirmed=_sorted_states(evaluated.newly_confirmed),
        ongoing_confirmed=_sorted_states(evaluated.ongoing_confirmed),
        quarantined=_sorted_states(evaluated.quarantined),
        recovering=_sorted_states(evaluated.recovering),
        recovered=_sorted_states(evaluated.recovered),
        overdue=_sorted_states(evaluated.overdue),
        transitions=tuple(
            sorted(evaluated.transitions, key=lambda item: item.transition_id)
        ),
        issue_codes=tuple(sorted(issue_codes)),
        source_artifacts=tuple(
            item
            for item, exists in (
                ("flaky-import.json", imported is not None),
                ("flaky-evaluation.json", True),
            )
            if exists
        ),
    )


def _sorted_states(
    values: tuple[FlakyStateSummary, ...],
) -> tuple[FlakyStateSummary, ...]:
    return tuple(sorted(values, key=lambda item: item.flaky_key))


def _attention_items(
    sources: tuple[P1SourceSummary, ...],
    usage: P1UsageCoverage | None,
    flaky: P1FlakySection | None,
) -> tuple[P1AttentionItem, ...]:
    items: list[P1AttentionItem] = []
    for source in sources:
        if (
            source.expectation is SourceExpectation.REQUIRED
            and source.status in _SOURCE_FAILURE_STATUSES
        ):
            items.append(
                P1AttentionItem(
                    attention_code="required_source_unavailable",
                    level=AttentionLevel.ACTION_REQUIRED,
                    title="必需质量数据源不可用",
                    summary=f"{source.source_name} 当前状态为 {source.status.value}。",
                    source_name=source.source_name,
                    related_ids=source.issue_codes,
                    suggested_action="修复对应质量阶段并使用当前 run Artifact 重放报告。",
                )
            )
        elif source.status is SourceStatus.DEGRADED:
            items.append(
                P1AttentionItem(
                    attention_code="required_source_degraded",
                    level=AttentionLevel.REVIEW,
                    title="质量数据源已降级",
                    summary=f"{source.source_name} 保留可信摘要，但覆盖不完整。",
                    source_name=source.source_name,
                    related_ids=source.issue_codes,
                    suggested_action="查看源 Artifact 的完整性问题并补齐缺失证据。",
                )
            )
    if usage is not None and (usage.partial_count or usage.missing_count):
        items.append(
            P1AttentionItem(
                attention_code="usage_coverage_incomplete",
                level=AttentionLevel.REVIEW,
                title="资源用量覆盖不完整",
                summary=(
                    f"partial={usage.partial_count}, missing={usage.missing_count}；"
                    "缺失值未按零计入。"
                ),
                source_name="run_metrics",
                related_ids=usage.missing_operation_refs,
                suggested_action="补齐协议 usage 采集，或明确确认该 operation 为 not_applicable。",
            )
        )
    if flaky is not None:
        if flaky.stale_count:
            items.append(
                P1AttentionItem(
                    attention_code="flaky_projection_stale",
                    level=AttentionLevel.ACTION_REQUIRED,
                    title="Flaky 投影已过期",
                    summary=f"本次有 {flaky.stale_count} 个状态投影不可作为可信当前结论。",
                    source_name="flaky_evaluation",
                    related_ids=tuple(
                        item.flaky_key
                        for item in _all_flaky_states(flaky)
                        if item.projection_status is ProjectionStatus.STALE
                    ),
                    suggested_action="人工检查数据库状态并使用 Flaky CLI 执行 dry-run 重建。",
                )
            )
        for item in flaky.newly_suspected:
            items.append(
                P1AttentionItem(
                    attention_code="flaky_newly_suspected",
                    level=AttentionLevel.REVIEW,
                    title="发现新的疑似 Flaky",
                    summary="当前样本出现结果或失败签名切换，需要继续观察。",
                    source_name="flaky_evaluation",
                    related_ids=(item.flaky_key, item.latest_observation_id),
                    suggested_action="继续观察后续可比较执行，不自动隔离或重跑。",
                )
            )
        for item in flaky.newly_confirmed:
            if item.projection_status is ProjectionStatus.STALE:
                continue
            items.append(
                P1AttentionItem(
                    attention_code="flaky_newly_confirmed",
                    level=AttentionLevel.ACTION_REQUIRED,
                    title="发现新的已确认 Flaky",
                    summary="状态机已确认波动，但这不是测试通过结论。",
                    source_name="flaky_evaluation",
                    related_ids=(item.flaky_key, item.latest_observation_id),
                    suggested_action="人工复核证据并决定是否创建 quarantine 治理项。",
                )
            )
        for item in flaky.overdue:
            items.append(
                P1AttentionItem(
                    attention_code="flaky_governance_overdue",
                    level=AttentionLevel.ACTION_REQUIRED,
                    title="Flaky 治理项已超期",
                    summary="隔离治理已超过计划到期时间，需要 owner 复核。",
                    owner=item.owner,
                    expires_at=item.expires_at,
                    source_name="flaky_evaluation",
                    related_ids=(item.flaky_key, item.governance_id or item.flaky_key),
                    suggested_action="owner 复核超期原因并决定恢复、延期或取消隔离。",
                )
            )
        if flaky.recovered:
            items.append(
                P1AttentionItem(
                    attention_code="flaky_recovered",
                    level=AttentionLevel.INFO,
                    title="Flaky 恢复证据已满足",
                    summary=f"本次有 {len(flaky.recovered)} 个治理项达到恢复条件。",
                    source_name="flaky_evaluation",
                    related_ids=tuple(item.flaky_key for item in flaky.recovered),
                    suggested_action="查看 transition 证据并确认治理生命周期已经正确收口。",
                )
            )
    deduplicated: dict[tuple[str, tuple[str, ...]], P1AttentionItem] = {}
    for item in items:
        deduplicated[(item.attention_code, item.related_ids)] = item
    level_order = {
        AttentionLevel.ACTION_REQUIRED: 0,
        AttentionLevel.REVIEW: 1,
        AttentionLevel.INFO: 2,
    }
    return tuple(
        sorted(
            deduplicated.values(),
            key=lambda item: (
                level_order[item.level],
                item.attention_code,
                item.related_ids,
            ),
        )
    )


def _all_flaky_states(flaky: P1FlakySection) -> tuple[FlakyStateSummary, ...]:
    return (
        *flaky.newly_suspected,
        *flaky.newly_confirmed,
        *flaky.ongoing_confirmed,
        *flaky.quarantined,
        *flaky.recovering,
        *flaky.recovered,
        *flaky.overdue,
    )


def _display_windows(
    metrics: P1MetricsSection | None,
    usage: P1UsageCoverage | None,
    flaky: P1FlakySection | None,
) -> tuple[P1DisplayWindow, ...]:
    windows: list[P1DisplayWindow] = []
    if metrics is not None:
        timing_count = sum(
            "duration_ms" in item.metric_name or item.metric_name.endswith("_ms")
            for item in metrics.observations
        )
        windows.append(_display_window("timing_observations", timing_count, _METRICS_ARTIFACT))
    if usage is not None:
        windows.append(
            _display_window(
                "usage_missing_refs",
                len(usage.missing_operation_refs),
                usage.source_artifact,
            )
        )
    if flaky is not None:
        windows.append(
            _display_window(
                "flaky_new_and_ongoing",
                len(flaky.newly_suspected)
                + len(flaky.newly_confirmed)
                + len(flaky.ongoing_confirmed),
                "flaky-evaluation.json",
            )
        )
        windows.append(
            _display_window(
                "flaky_governance",
                len(flaky.quarantined)
                + len(flaky.recovering)
                + len(flaky.recovered)
                + len(flaky.overdue),
                "flaky-evaluation.json",
            )
        )
        windows.append(
            _display_window(
                "flaky_transitions",
                len(flaky.transitions),
                "flaky-evaluation.json",
            )
        )
    return tuple(sorted(windows, key=lambda item: item.category))


def _display_window(category: str, total: int, source: str) -> P1DisplayWindow:
    shown = min(total, 10)
    return P1DisplayWindow(
        category=category,
        total_count=total,
        shown_count=shown,
        omitted_count=total - shown,
        source_artifact=source,
    )


def _overview(
    run_id: str,
    *,
    created_at: datetime,
    report_status: P1ReportStatus,
    p0: P1P0Section | None,
    metrics: P1MetricsSection | None,
    usage: P1UsageCoverage | None,
    flaky: P1FlakySection | None,
    required_source_failures: int,
) -> P1RunOverview:
    outcomes = metrics.operation_outcomes if metrics is not None else {}
    workload = metrics.workload_operation_count if metrics is not None else 0
    control = metrics.control_operation_count if metrics is not None else 0
    unknown_operations = metrics.unknown_operation_count if metrics is not None else 0
    return P1RunOverview(
        run_id=run_id,
        report_status=report_status,
        p0_gate_mode=p0.gate_mode if p0 is not None else None,
        p0_gate_overall=p0.gate_overall if p0 is not None else None,
        p0_integrity_status=p0.integrity_status if p0 is not None else None,
        case_total=p0.case_total if p0 is not None else 0,
        case_failed=p0.case_failed if p0 is not None else 0,
        case_error=p0.case_error if p0 is not None else 0,
        operation_count=workload + control + unknown_operations,
        workload_operation_count=workload,
        operation_success_count=outcomes.get("success", 0),
        operation_failed_count=outcomes.get("failed", 0),
        operation_timeout_count=outcomes.get("timeout", 0),
        usage_complete_count=usage.complete_count if usage is not None else 0,
        usage_partial_count=usage.partial_count if usage is not None else 0,
        usage_missing_count=usage.missing_count if usage is not None else 0,
        flaky_affected_count=flaky.affected_count if flaky is not None else 0,
        flaky_transitioned_count=flaky.transitioned_count if flaky is not None else 0,
        flaky_stale_count=flaky.stale_count if flaky is not None else 0,
        newly_suspected_count=len(flaky.newly_suspected) if flaky is not None else 0,
        newly_confirmed_count=len(flaky.newly_confirmed) if flaky is not None else 0,
        quarantined_count=len(flaky.quarantined) if flaky is not None else 0,
        recovering_count=len(flaky.recovering) if flaky is not None else 0,
        recovered_count=len(flaky.recovered) if flaky is not None else 0,
        overdue_count=len(flaky.overdue) if flaky is not None else 0,
        required_source_failure_count=required_source_failures,
        generated_at=created_at,
    )


def _ratio_observation(
    *,
    metric_id: str,
    grain: str,
    dimension: dict[str, str | None],
    name: str,
    aggregate: RatioAggregate,
    evidence_refs: tuple[str, ...],
) -> P1MetricObservation:
    return P1MetricObservation(
        metric_id=metric_id,
        grain=grain,
        dimension=dimension,
        metric_name=name,
        value=aggregate.value,
        numerator=aggregate.numerator,
        sample_size=aggregate.sample_size,
        missing_sample_size=aggregate.unknown_count,
        completeness=aggregate.completeness,
        algorithm_version=RUN_METRICS_AGGREGATION_VERSION,
        source_artifact=_METRICS_ARTIFACT,
        evidence_refs=evidence_refs,
    )


def _numeric_observation(
    *,
    metric_id: str,
    grain: str,
    dimension: dict[str, str | None],
    name: str,
    aggregate: NumericAggregate,
    evidence_refs: tuple[str, ...],
) -> P1MetricObservation:
    return P1MetricObservation(
        metric_id=metric_id,
        grain=grain,
        dimension=dimension,
        metric_name=name,
        value=aggregate.mean,
        total=aggregate.total,
        minimum=aggregate.minimum,
        maximum=aggregate.maximum,
        sample_size=aggregate.sample_size,
        missing_sample_size=aggregate.missing_count,
        completeness=aggregate.completeness,
        algorithm_version=RUN_METRICS_AGGREGATION_VERSION,
        source_artifact=_METRICS_ARTIFACT,
        evidence_refs=evidence_refs,
    )


def _bucket_evidence(evidence: Any) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                evidence.metric_bucket_id,
                *evidence.source_artifact_refs,
                *evidence.member_ids[:10],
            }
        )
    )


def render_p1_observation_markdown(report: P1ObservationReport) -> str:
    overview = report.overview
    lines = [
        "# P1 单次观察与 Flaky 报告",
        "",
        "## 报告状态与 P0 影子门禁",
        "",
        f"- 报告状态：{_localized_code(report.report_status.value, _COMMON_STATUS_LABELS)}",
        f"- 运行 ID：`{_md(report.run_id)}`",
        f"- P0 门禁：{_localized_code(overview.p0_gate_overall or '-', _COMMON_STATUS_LABELS)}（{_localized_code(overview.p0_gate_mode or '-', _COMMON_STATUS_LABELS)}）",
        f"- P0 数据完整性：{_localized_code(overview.p0_integrity_status or '-', _COMMON_STATUS_LABELS)}",
        "- P1 报告状态只表示观察数据完整性，不是门禁结论，也不会修改 Jenkins 结果。",
        "",
        "## 数据源健康度",
        "",
        _md_table(
            ("数据源", "要求", "状态", "版本", "问题", "产物文件"),
            [
                (
                    _localized_code(item.source_name, _SOURCE_NAME_LABELS),
                    _localized_code(item.expectation.value, _SOURCE_EXPECTATION_LABELS),
                    _localized_code(item.status.value, _SOURCE_STATUS_LABELS),
                    item.producer_version or item.schema_version or "-",
                    "，".join(_localized_issue_code(code) for code in item.issue_codes)
                    or "-",
                    item.artifact_path or "-",
                )
                for item in report.sources
            ],
        ),
        "",
        "## 本次逻辑调用稳定性",
        "",
        (
            f"- 业务逻辑调用：共 {overview.workload_operation_count} 次；"
            f"成功={overview.operation_success_count}，失败={overview.operation_failed_count}，"
            f"超时={overview.operation_timeout_count}。"
        ),
    ]
    if report.metrics is None:
        lines.append("- 指标源不可消费，本节不展示伪造值。")
    else:
        ratio_rows = [
            item
            for item in report.metrics.observations
            if item.numerator is not None and item.grain == "run"
        ][:10]
        lines.extend(
            [
                "",
                _md_table(
                    ("指标", "值", "分子", "样本量", "未知/缺失", "完整性"),
                    [
                        (
                            _localized_code(item.metric_name, _METRIC_LABELS),
                            _display_value(item.value, item.sample_size),
                            item.numerator,
                            item.sample_size,
                            item.missing_sample_size,
                            _localized_code(
                                item.completeness.value, _COMPLETENESS_LABELS
                            ),
                        )
                        for item in ratio_rows
                    ],
                ),
            ]
        )
    lines.extend(["", "## 资源用量与覆盖率", ""])
    if report.usage_coverage is None:
        lines.append("指标源不可消费，资源用量不按零展示。")
    else:
        usage = report.usage_coverage
        lines.extend(
            [
                f"- 完整={usage.complete_count}，部分完整={usage.partial_count}，缺失={usage.missing_count}，不适用={usage.not_applicable_count}",
                _md_table(
                    ("资源", "已知总量", "样本量", "缺失", "完整性"),
                    [
                        _usage_row("input tokens", usage.input_tokens),
                        _usage_row("output tokens", usage.output_tokens),
                        _usage_row("media count", usage.media_count),
                        _usage_row("media duration ms", usage.media_duration_ms),
                        _usage_row("retry input tokens", usage.retry_input_tokens),
                        _usage_row("retry output tokens", usage.retry_output_tokens),
                        _usage_row("retry media count", usage.retry_media_count),
                    ],
                ),
            ]
        )
    lines.extend(["", "## HTTP/SSE/异步耗时", ""])
    if report.metrics is None:
        lines.append("指标源不可消费，无耗时观察。")
    else:
        timing = sorted(
            (
                item
                for item in report.metrics.observations
                if "duration_ms" in item.metric_name
                or item.metric_name.endswith("_ms")
            ),
            key=lambda item: (
                -(float(item.value) if item.value is not None else -1),
                item.metric_id,
            ),
        )[:10]
        lines.append(
            _md_table(
                ("粒度", "维度", "指标", "均值", "最小", "最大", "样本量", "缺失"),
                [
                    (
                        _localized_code(item.grain, _GRAIN_LABELS),
                        _dimension_text(item.dimension),
                        _localized_code(item.metric_name, _METRIC_LABELS),
                        _display_value(item.value, item.sample_size),
                        _display_value(item.minimum, item.sample_size),
                        _display_value(item.maximum, item.sample_size),
                        item.sample_size,
                        item.missing_sample_size,
                    )
                    for item in timing
                ],
            )
        )
    lines.extend(["", "## Flaky 新增与持续", ""])
    if report.flaky is None:
        lines.append("Flaky 数据源已关闭或不可消费。")
    else:
        flaky = report.flaky
        lines.extend(
            [
                f"- 新增疑似={len(flaky.newly_suspected)}，新增确认={len(flaky.newly_confirmed)}，持续确认={len(flaky.ongoing_confirmed)}，过期投影={flaky.stale_count}",
                _flaky_table(
                    (*flaky.newly_suspected, *flaky.newly_confirmed, *flaky.ongoing_confirmed)[:10]
                ),
                "",
                "## 隔离、恢复与超期治理",
                "",
                "“已隔离（QUARANTINED）”是治理标签，不代表测试通过，也不会自动跳过用例。",
                "",
                f"- 已隔离={len(flaky.quarantined)}，恢复观察中={len(flaky.recovering)}，已恢复={len(flaky.recovered)}，已超期={len(flaky.overdue)}",
                _flaky_table(
                    (*flaky.quarantined, *flaky.recovering, *flaky.recovered, *flaky.overdue)[:10]
                ),
                "",
                "### 本次 Flaky 状态迁移",
                "",
                _md_table(
                    ("迁移 ID", "状态", "触发方式", "原因", "样本", "操作者", "证据"),
                    [
                        (
                            item.transition_id,
                            (
                                f"{_localized_code(item.from_state.value, _FLAKY_STATE_LABELS) if item.from_state else '-'}"
                                f" → {_localized_code(item.to_state.value, _FLAKY_STATE_LABELS)}"
                            ),
                            _localized_code(item.trigger_type.value, _TRIGGER_LABELS),
                            _localized_code(
                                item.reason_code, _TRANSITION_REASON_LABELS
                            ),
                            item.sample_size,
                            item.actor or "-",
                            ", ".join(
                                (
                                    *item.evidence_run_ids[:3],
                                    *item.evidence_observation_ids[:3],
                                )
                            )
                            or "-",
                        )
                        for item in flaky.transitions[:10]
                    ],
                ),
            ]
        )
    lines.extend(["", "## 待关注事项", ""])
    lines.append(
        _md_table(
            ("级别", "代码", "标题", "摘要", "建议动作"),
            [
                (
                    _localized_code(item.level.value, _ATTENTION_LEVEL_LABELS),
                    _localized_issue_code(item.attention_code),
                    item.title,
                    item.summary,
                    item.suggested_action,
                )
                for item in report.attention_items[:20]
            ],
        )
        if report.attention_items
        else "本次没有需要额外处理的关注事项。"
    )
    lines.extend(
        [
            "",
            "## 完整性与证据入口",
            "",
            f"- 必需数据源失败数：{report.integrity.required_source_failure_count}",
            (
                "- 问题代码："
                + (
                    "，".join(
                        _localized_issue_code(code)
                        for code in report.integrity.issue_codes
                    )
                    or "-"
                )
            ),
            "- 完整机器数据请查看 `p1-observation.json`；指标与 Flaky 详情请回到各自源产物文件。",
            "",
            _md_table(
                ("展示窗口", "总数", "已展示", "已省略", "完整源"),
                [
                    (
                        _localized_code(item.category, _DISPLAY_WINDOW_LABELS),
                        item.total_count,
                        item.shown_count,
                        item.omitted_count,
                        item.source_artifact,
                    )
                    for item in report.display_windows
                ],
            ),
            "",
        ]
    )
    return "\n".join(lines)


def _source_result(
    source_name: str,
    expectation: SourceExpectation,
    status: SourceStatus,
    *,
    artifact_path: str | None = None,
    schema_version: str | None = None,
    producer_version: str | None = None,
    sha256: str | None = None,
    issue_codes: tuple[str, ...] = (),
    evidence_refs: tuple[str, ...] = (),
    value: _T | None = None,
    hashes: tuple[tuple[str, str], ...] = (),
) -> _LoadedSource[_T]:
    return _LoadedSource(
        summary=P1SourceSummary(
            source_name=source_name,
            expectation=expectation,
            status=status,
            artifact_path=artifact_path,
            schema_version=schema_version,
            producer_version=producer_version,
            sha256=sha256,
            issue_codes=issue_codes,
            evidence_refs=evidence_refs,
        ),
        value=value,
        hashes=hashes,
    )


def _disabled_source(source_name: str) -> _LoadedSource[Any]:
    return _source_result(
        source_name,
        SourceExpectation.DISABLED,
        SourceStatus.DISABLED,
        issue_codes=("source_disabled",),
    )


def _flaky_import_source_status(status: FlakyImportStatus) -> SourceStatus:
    return {
        FlakyImportStatus.IMPORTED: SourceStatus.AVAILABLE,
        FlakyImportStatus.NOOP: SourceStatus.AVAILABLE,
        FlakyImportStatus.DEGRADED: SourceStatus.DEGRADED,
        FlakyImportStatus.FAILED: SourceStatus.FAILED,
        FlakyImportStatus.NO_DATA: SourceStatus.NO_DATA,
    }[status]


def _flaky_evaluation_source_status(status: FlakyEvaluationStatus) -> SourceStatus:
    return {
        FlakyEvaluationStatus.EVALUATED: SourceStatus.AVAILABLE,
        FlakyEvaluationStatus.NOOP: SourceStatus.AVAILABLE,
        FlakyEvaluationStatus.DEGRADED: SourceStatus.DEGRADED,
        FlakyEvaluationStatus.FAILED: SourceStatus.FAILED,
        FlakyEvaluationStatus.NO_DATA: SourceStatus.NO_DATA,
    }[status]


def _manifest_issue_codes(manifest: dict[str, Any]) -> tuple[str, ...]:
    issues = manifest.get("issues")
    if not isinstance(issues, list):
        return ()
    return tuple(
        sorted(
            {
                str(item.get("code")).strip()
                for item in issues
                if isinstance(item, dict) and str(item.get("code") or "").strip()
            }
        )
    )


def _optional_manifest_text(manifest: dict[str, Any], name: str) -> str | None:
    value = manifest.get(name)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _existing_hashes(paths: dict[str, Path]) -> tuple[tuple[str, str], ...]:
    return tuple(
        (name, _file_sha256(path))
        for name, path in sorted(paths.items())
        if path.is_file()
    )


def _hash_for(hashes: tuple[tuple[str, str], ...], name: str) -> str | None:
    return dict(hashes).get(name)


def _read_json_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return value


def _count_map(value: object, name: str) -> dict[str, int]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    result: dict[str, int] = {}
    for key, count in value.items():
        if not isinstance(key, str) or not key.strip():
            raise ValueError(f"{name} has an invalid key")
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ValueError(f"{name} has an invalid count")
        result[key.strip()] = count
    return dict(sorted(result.items()))


def _required_text(value: str, name: str) -> str:
    text = value.strip()
    if not text:
        raise ValueError(f"{name} must not be empty")
    return text


class _IncompatibleSource(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _code_name(filename: str) -> str:
    return filename.replace(".", "_").replace("-", "_")


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_manifest(
    path: Path,
    *,
    run_id: str,
    created_at: datetime,
    write_status: str,
    report_status: P1ReportStatus | None,
    output_hashes: dict[str, str],
    source_hashes: dict[str, str],
    issue_codes: tuple[str, ...],
) -> None:
    write_json_atomic(
        path,
        {
            "manifest_version": P1_OBSERVATION_MANIFEST_VERSION,
            "schema_version": P1_OBSERVATION_SCHEMA_VERSION,
            "report_version": P1_OBSERVATION_REPORT_VERSION,
            "run_id": run_id,
            "write_status": write_status,
            "report_status": report_status.value if report_status is not None else None,
            "created_at": created_at,
            "output_hashes": dict(sorted(output_hashes.items())),
            "source_hashes": dict(sorted(source_hashes.items())),
            "issue_codes": sorted(set(issue_codes)),
        },
    )


def _write_text_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(content)
            if not content.endswith("\n"):
                handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _safe_text(value: object, maximum: int = 500) -> str:
    redacted = redact_quality_value(str(value), remove_url_query=True)
    text = str(redacted).replace("\x00", "").strip()
    return text[:maximum] or "-"


def _md(value: object) -> str:
    return (
        _safe_text(value)
        .replace("|", "\\|")
        .replace("\r", " ")
        .replace("\n", "<br>")
    )


def _localized_code(value: object, labels: dict[str, str]) -> str:
    raw = str(value)
    label = labels.get(raw)
    return f"{label}（`{raw}`）" if label else f"`{raw}`"


def _localized_issue_code(value: str) -> str:
    return _localized_code(value, _ISSUE_CODE_LABELS)


def _md_table(headers: tuple[str, ...], rows: list[tuple[Any, ...]]) -> str:
    rendered = [
        "| " + " | ".join(_md(item) for item in headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    rendered.extend(
        "| " + " | ".join(_md(item) for item in row) + " |" for row in rows
    )
    if not rows:
        rendered.append("| " + " | ".join("-" for _ in headers) + " |")
    return "\n".join(rendered)


def _display_value(value: int | float | None, sample_size: int) -> str:
    if sample_size == 0 or value is None:
        return "无数据（NO_DATA）"
    return str(value)


def _dimension_text(value: dict[str, str | None]) -> str:
    parts = []
    for key, item in value.items():
        display_key = _DIMENSION_KEY_LABELS.get(key, key)
        raw_value = item if item is not None else "-"
        display_value = _DIMENSION_VALUE_LABELS.get(raw_value, raw_value)
        if display_key != key:
            display_key = f"{display_key}（{key}）"
        if display_value != raw_value:
            display_value = f"{display_value}（{raw_value}）"
        parts.append(f"{display_key}={display_value}")
    return "，".join(parts) or "单次运行（run）"


def _usage_row(name: str, value: P1KnownTotal) -> tuple[Any, ...]:
    return (
        _localized_code(name, _RESOURCE_LABELS),
        _display_value(value.total, value.sample_size),
        value.sample_size,
        value.missing_sample_size,
        _localized_code(value.completeness.value, _COMPLETENESS_LABELS),
    )


def _flaky_table(values: tuple[FlakyStateSummary, ...]) -> str:
    return _md_table(
        ("用例", "环境/执行画像", "当前/检测状态", "样本", "投影", "责任人", "到期"),
        [
            (
                item.case_id,
                f"{item.environment}/{item.execution_profile}",
                (
                    f"{_localized_code(item.current_state.value, _FLAKY_STATE_LABELS)}"
                    f" / {_localized_code(item.detected_state.value, _FLAKY_STATE_LABELS)}"
                ),
                item.sample_size,
                _localized_code(
                    item.projection_status.value, _PROJECTION_STATUS_LABELS
                ),
                item.owner or "-",
                item.expires_at.isoformat() if item.expires_at is not None else "-",
            )
            for item in values
        ],
    )
