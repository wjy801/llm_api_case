from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
from types import SimpleNamespace

import pytest

from quality.collector import configure_collector, reset_collector
from quality.aggregator import MANIFEST_VERSION, MERGE_VERSION
from quality.classifier import FINGERPRINT_VERSION
from quality.models import (
    SCHEMA_VERSION,
    CasePhase,
    CaseResult,
    CaseStatus,
    Confidence,
    FailureCategory,
    FailureFingerprintSource,
    FailureRecord,
    IntegrityStatus,
    IssueSeverity,
    OwnerDomain,
    RunRecord,
    RunStatus,
)
from quality.runtime_context import (
    QualityCaseContext,
    QualityRunContext,
    clear_case_context,
    clear_run_context,
    reset_case_context,
    reset_run_context,
    set_case_context,
    set_run_context,
)
from quality.semantic_collector import (
    configure_semantic_collector,
    reset_semantic_collector,
)
from quality.storage import write_json_atomic, write_jsonl_atomic


@pytest.fixture
def semantic_runtime(tmp_path):
    output_dir = tmp_path / "quality"
    run_context = QualityRunContext(
        run_id="run-semantic",
        execution_id="serial-pool",
        worker_id="master",
        output_dir=output_dir,
    )
    reset_semantic_collector()
    reset_collector()
    run_token = set_run_context(run_context)
    case_context = QualityCaseContext(
        case_id="module/test_semantic.py::test_case",
        invocation_id="inv-semantic",
        nodeid="module/test_semantic.py::test_case[param]",
        param_hash="param-hash",
    )
    case_token = set_case_context(case_context)
    p0 = configure_collector(run_context)
    semantic = configure_semantic_collector(run_context)
    yield SimpleNamespace(
        output_dir=output_dir,
        run_context=run_context,
        case_context=case_context,
        p0=p0,
        semantic=semantic,
    )
    semantic.finalize_pending()
    reset_semantic_collector()
    reset_collector()
    reset_case_context(case_token)
    reset_run_context(run_token)
    clear_case_context()
    clear_run_context()


@pytest.fixture
def p0_artifact_factory(tmp_path):
    def build(
        *,
        run_id="run-1",
        outcome="pass",
        environment="overseas",
        integrity_status=IntegrityStatus.COMPLETE,
        integrity_issues=(),
        run_status=RunStatus.FINISHED,
        execution_id="serial-pool",
        worker_id="master",
        case_id="module/test_demo.py::test_case",
        param_hash="param-hash",
        job_name=None,
        build_number=None,
    ):
        output_dir = tmp_path / f"quality-{run_id}"
        merged = output_dir / "merged"
        start = datetime(2026, 8, 1, 1, 0, tzinfo=UTC)
        phase_time = start
        failure_id = "fail-call-assertionerror-demo" if outcome == "fail" else None
        cases = []
        for phase in (CasePhase.SETUP, CasePhase.CALL, CasePhase.TEARDOWN):
            status = (
                CaseStatus.FAILED
                if outcome == "fail" and phase is CasePhase.CALL
                else CaseStatus.PASSED
            )
            cases.append(
                CaseResult(
                    run_id=run_id,
                    execution_id=execution_id,
                    worker_id=worker_id,
                    case_id=case_id,
                    invocation_id=f"inv-{run_id}",
                    nodeid=f"{case_id}[demo]",
                    param_hash=param_hash,
                    phase=phase,
                    raw_status=status,
                    final_status=status,
                    duration_ms=1,
                    start_time=phase_time,
                    end_time=phase_time + timedelta(milliseconds=1),
                    failure_id=failure_id if status is CaseStatus.FAILED else None,
                )
            )
            phase_time += timedelta(milliseconds=2)
        failures = []
        if failure_id is not None:
            failures.append(
                FailureRecord(
                    run_id=run_id,
                    failure_id=failure_id,
                    case_id=case_id,
                    invocation_id=f"inv-{run_id}",
                    phase=CasePhase.CALL,
                    category=FailureCategory.PRODUCT_DEFECT,
                    owner_domain=OwnerDomain.PRODUCT,
                    confidence=Confidence.HIGH,
                    error_type="AssertionError",
                    normalized_message="expected response",
                    fingerprint_source=FailureFingerprintSource(
                        phase=CasePhase.CALL,
                        error_type="AssertionError",
                        message_hash="message-hash",
                    ),
                )
            )
        issues = tuple(integrity_issues)
        write_jsonl_atomic(merged / "case-results.jsonl", cases)
        write_jsonl_atomic(merged / "failures.jsonl", failures)
        write_jsonl_atomic(merged / "integrity-issues.jsonl", issues)
        output_hashes = {
            "case-results": _sha256(merged / "case-results.jsonl"),
            "failures": _sha256(merged / "failures.jsonl"),
            "integrity-issues": _sha256(merged / "integrity-issues.jsonl"),
        }
        manifest = {
            "manifest_version": MANIFEST_VERSION,
            "schema_version": SCHEMA_VERSION,
            "run_id": run_id,
            "status": "complete",
            "merge_version": MERGE_VERSION,
            "classifier_rule_version": "p0-classifier.v1",
            "fingerprint_version": FINGERPRINT_VERSION,
            "created_at": (start + timedelta(seconds=1)).isoformat(),
            "expected_execution_ids": [execution_id],
            "expected_case_count": 1,
            "source_shards": [],
            "junit_files": [],
            "output_counts": {
                "case_results": len(cases),
                "invocations": 1,
                "request_metrics": 0,
                "failure_occurrences": len(failures),
                "failure_fingerprints": len(failures),
                "integrity_issues": len(issues),
            },
            "output_hashes": output_hashes,
            "integrity_status": integrity_status.value,
        }
        write_json_atomic(merged / "manifest.json", manifest)
        run = RunRecord(
            run_id=run_id,
            job_name=job_name,
            build_number=build_number,
            trigger="jenkins" if job_name and build_number else "local",
            environment=environment,
            start_time=start,
            end_time=start + timedelta(seconds=2),
            status=run_status,
            integrity_status=integrity_status,
            integrity_issues=issues,
        )
        write_json_atomic(output_dir / "run.json", run)
        return SimpleNamespace(
            output_dir=output_dir,
            merged=merged,
            run=run,
            cases=cases,
            failures=failures,
            issues=issues,
            manifest=manifest,
        )

    return build


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()
