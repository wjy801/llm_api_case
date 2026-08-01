from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from quality.models import Protocol
from quality.semantic_models import (
    AttemptTransportOutcome,
    OperationUsage,
    RecordCompleteness,
    RequestGroupRecord,
    TrafficRole,
    UsageCompleteness,
)


def _group(**overrides):
    values = {
        "run_id": "run-1",
        "execution_id": "serial-pool",
        "worker_id": "master",
        "case_id": "test_demo.py::test_case",
        "invocation_id": "inv-1",
        "created_at": datetime(2026, 8, 1, tzinfo=UTC),
        "request_group_id": "group-1",
        "operation_id": "op-1",
        "interface_id": "GET /v1/items/{id} http",
        "method": "GET",
        "url_template": "/v1/items/{id}",
        "protocol": Protocol.HTTP,
        "traffic_role": TrafficRole.WORKLOAD,
        "attempt_event_ids": ("event-1",),
        "attempt_count": 1,
        "configured_max_attempts": 1,
        "retry_wait_ms": 0,
        "started_at": datetime(2026, 8, 1, tzinfo=UTC),
        "ended_at": datetime(2026, 8, 1, tzinfo=UTC),
        "total_duration_ms": 1,
        "first_transport_outcome": AttemptTransportOutcome.RESPONSE,
        "final_transport_outcome": AttemptTransportOutcome.RESPONSE,
        "first_status_code": 200,
        "final_status_code": 200,
        "final_request_event_id": "event-1",
        "completeness": RecordCompleteness.COMPLETE,
    }
    values.update(overrides)
    return RequestGroupRecord(**values)


def test_request_group_round_trip_preserves_semantic_schema():
    group = _group()

    restored = RequestGroupRecord.model_validate_json(group.model_dump_json())

    assert restored == group
    assert restored.schema_version == "quality.semantic.v1"


def test_request_group_rejects_attempt_count_mismatch():
    with pytest.raises(ValidationError, match="attempt_count"):
        _group(attempt_count=2)


def test_missing_usage_cannot_contain_known_values():
    with pytest.raises(ValidationError, match="missing/not_applicable"):
        OperationUsage(
            input_tokens=1,
            completeness=UsageCompleteness.MISSING,
        )


def test_semantic_usage_contract_has_no_money_fields():
    fields = OperationUsage.model_fields

    assert not {"amount", "price", "currency", "quota"} & set(fields)
