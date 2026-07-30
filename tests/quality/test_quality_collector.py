from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest

from quality.collector import (
    QualityCollector,
    configure_collector,
    get_collector,
    reset_collector,
)
from quality.models import (
    BusinessStatus,
    CasePhase,
    CaseResult,
    CaseStatus,
    Protocol,
    RequestMetric,
)
from quality.runtime_context import QualityRunContext
from quality.storage import read_jsonl


@pytest.fixture(autouse=True)
def clear_collector_registry():
    reset_collector()
    yield
    reset_collector()


def _run_context(tmp_path, *, worker_id="master"):
    return QualityRunContext(
        run_id="run-1",
        execution_id="serial-pool",
        worker_id=worker_id,
        output_dir=tmp_path / "quality",
    )


def _case_result():
    started_at = datetime.now(UTC)
    return CaseResult(
        run_id="run-1",
        execution_id="serial-pool",
        worker_id="master",
        case_id="module/test_x.py::test_case",
        invocation_id="inv-1",
        nodeid="module/test_x.py::test_case",
        param_hash="hash",
        phase=CasePhase.CALL,
        raw_status=CaseStatus.PASSED,
        final_status=CaseStatus.PASSED,
        duration_ms=1.5,
        start_time=started_at,
        end_time=started_at + timedelta(milliseconds=1.5),
    )


def _request_metric(index=1):
    return RequestMetric(
        run_id="run-1",
        execution_id="serial-pool",
        worker_id="master",
        case_id="module/test_x.py::test_case",
        invocation_id="inv-1",
        request_event_id=f"request-{index}",
        interface_id="GET /v1/items http",
        method="GET",
        url_template="/v1/items",
        protocol=Protocol.HTTP,
        attempt_index=1,
        status_code=200,
        business_status=BusinessStatus.SUCCESS,
        duration_ms=2.0,
    )


def test_collector_initializes_worker_owned_shards(tmp_path):
    collector = QualityCollector(_run_context(tmp_path))

    assert collector.paths.cases.name == "cases-serial-pool-master.jsonl"
    assert collector.paths.requests.name == "requests-serial-pool-master.jsonl"
    assert collector.paths.integrity.name == "integrity-serial-pool-master.jsonl"
    assert collector.paths.cases.read_text(encoding="utf-8") == ""
    assert collector.paths.requests.read_text(encoding="utf-8") == ""
    assert collector.paths.integrity.read_text(encoding="utf-8") == ""


def test_configure_collector_registers_and_reset_clears(tmp_path):
    collector = configure_collector(_run_context(tmp_path))

    assert get_collector() is collector

    reset_collector()

    assert get_collector() is None


def test_collector_records_case_request_and_integrity(tmp_path):
    collector = QualityCollector(_run_context(tmp_path))

    assert collector.record_case(_case_result()) is True
    assert collector.record_request(_request_metric()) is True
    assert collector.capture_integrity(
        source="test",
        code="sample_issue",
        message="sample",
        related_id="inv-1",
    ) is True

    assert read_jsonl(collector.paths.cases)[0]["invocation_id"] == "inv-1"
    assert read_jsonl(collector.paths.requests)[0]["request_event_id"] == "request-1"
    assert read_jsonl(collector.paths.integrity)[0]["code"] == "sample_issue"


def test_collector_serializes_concurrent_request_writes(tmp_path):
    collector = QualityCollector(_run_context(tmp_path))

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(collector.record_request, (_request_metric(i) for i in range(80))))

    records = read_jsonl(collector.paths.requests)
    assert all(results)
    assert len(records) == 80
    assert {record["request_event_id"] for record in records} == {
        f"request-{index}" for index in range(80)
    }


def test_reinitializing_same_worker_clears_only_its_shards(tmp_path):
    master = QualityCollector(_run_context(tmp_path))
    worker = QualityCollector(_run_context(tmp_path, worker_id="gw0"))
    master.record_request(_request_metric())
    worker.paths.requests.write_text("worker-data\n", encoding="utf-8")

    replacement = QualityCollector(_run_context(tmp_path))

    assert replacement.paths.requests.read_text(encoding="utf-8") == ""
    assert worker.paths.requests.read_text(encoding="utf-8") == "worker-data\n"


def test_primary_write_failure_becomes_integrity_issue(tmp_path, monkeypatch):
    collector = QualityCollector(_run_context(tmp_path))
    original_append = __import__("quality.collector", fromlist=["append_jsonl"]).append_jsonl

    def fail_case_write(path, record):
        if path == collector.paths.cases:
            raise OSError("Authorization=secret?token=secret")
        return original_append(path, record)

    monkeypatch.setattr("quality.collector.append_jsonl", fail_case_write)

    assert collector.record_case(_case_result()) is False
    issues = read_jsonl(collector.paths.integrity)
    assert issues[0]["code"] == "case_write_failed"
    assert "secret" not in issues[0]["message"]


def test_integrity_write_failure_only_warns(tmp_path, monkeypatch):
    warnings = []
    collector = QualityCollector(_run_context(tmp_path), warning_sink=warnings.append)
    monkeypatch.setattr(
        "quality.collector.append_jsonl",
        lambda path, record: (_ for _ in ()).throw(OSError("disk full")),
    )

    assert collector.capture_integrity(
        source="test",
        code="write_failed",
        message="sample",
    ) is False
    assert warnings == ["quality integrity write failed: OSError: disk full"]
