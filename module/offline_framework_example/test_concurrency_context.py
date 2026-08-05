from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from dataclasses import dataclass
from threading import Barrier, Lock

import requests

from common import submit_with_context
from common.capture import CapturePolicy
from common.runtime_hooks import RuntimeOperationKind, RuntimeTrafficRole
from module.offline_framework_example import (
    OfflineFrameworkAssertions,
    OfflineFrameworkRequest,
    OfflineFrameworkTask,
)
from module.offline_framework_example.conftest import OfflineRuntimeRecorder
from module.offline_framework_example.offline_service import OfflineService


@dataclass(frozen=True, slots=True)
class _OfflineAuditWorkerResult:
    audit_name: str
    response: requests.Response
    session_id: int
    inherited_owner: str | None
    trust_env: bool
    audit_header_in_session: bool


def _query_audit_worker(
    base_url: str,
    audit_name: str,
    *,
    barrier: Barrier,
    owner_var: ContextVar[str] | None,
    closed_session_ids: set[int],
    closed_session_lock: Lock,
) -> _OfflineAuditWorkerResult:
    request_client = OfflineFrameworkRequest(
        base_url,
        capture_policy=CapturePolicy.disabled(),
    )
    session_id = id(request_client.session)
    try:
        barrier.wait(timeout=1)
        inherited_owner = owner_var.get() if owner_var is not None else None
        response = OfflineFrameworkTask().query_audit(request_client, audit_name)
        return _OfflineAuditWorkerResult(
            audit_name=audit_name,
            response=response,
            session_id=session_id,
            inherited_owner=inherited_owner,
            trust_env=request_client.session.trust_env,
            audit_header_in_session="X-Audit-Name" in request_client.session.headers,
        )
    finally:
        request_client.close()
        with closed_session_lock:
            closed_session_ids.add(session_id)


def _run_audit_workers(
    service: OfflineService,
    audit_names: tuple[str, str],
    *,
    owner_var: ContextVar[str] | None = None,
) -> tuple[list[_OfflineAuditWorkerResult], set[int]]:
    barrier = Barrier(2)
    closed_session_ids: set[int] = set()
    closed_session_lock = Lock()
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            submit_with_context(
                executor,
                _query_audit_worker,
                service.base_url,
                audit_name,
                barrier=barrier,
                owner_var=owner_var,
                closed_session_ids=closed_session_ids,
                closed_session_lock=closed_session_lock,
            )
            for audit_name in audit_names
        ]
        results = [future.result(timeout=2) for future in futures]
    return results, closed_session_ids


class TestConcurrencyContext:
    def setup_method(self) -> None:
        self.assertions = OfflineFrameworkAssertions()

    def test_submit_with_context_preserves_case_ownership(
        self,
        offline_service: OfflineService,
        offline_runtime_recorder: OfflineRuntimeRecorder,
    ) -> None:
        audit_names = ("audit-a", "audit-b")
        owner_var: ContextVar[str] = ContextVar("offline_concurrency_owner")
        token = owner_var.set("offline-concurrency-owner")
        try:
            results, closed_session_ids = _run_audit_workers(
                offline_service,
                audit_names,
                owner_var=owner_var,
            )
        finally:
            owner_var.reset(token)

        responses = [result.response for result in results]
        self.assertions.assert_audit_names(responses, set(audit_names))
        assert {result.inherited_owner for result in results} == {
            "offline-concurrency-owner"
        }
        session_ids = {result.session_id for result in results}
        assert len(session_ids) == 2
        assert closed_session_ids == session_ids

        audit_metadata = [
            metadata
            for metadata in offline_runtime_recorder.operation_metadata
            if metadata.kind is RuntimeOperationKind.HTTP
            and metadata.name == "offline_audit_query"
        ]
        assert len(audit_metadata) == 2
        assert all(
            metadata.role is RuntimeTrafficRole.CONTROL
            for metadata in audit_metadata
        )
        assert {metadata.name for metadata in audit_metadata} == {
            "offline_audit_query"
        }

        audit_groups = [
            group
            for group in offline_runtime_recorder.request_groups
            if group.path == OfflineFrameworkRequest.audit_path
        ]
        audit_responses = [
            response
            for context, response in offline_runtime_recorder.responses
            if context.path == OfflineFrameworkRequest.audit_path
        ]
        assert len(audit_groups) == 2
        assert len(audit_responses) == 2
        assert all(response.status_code == 200 for response in audit_responses)
        snapshot = offline_service.state.snapshot()
        assert snapshot["endpoint_call_counts"]["audit_calls"] == 2
        assert set(snapshot["audit_records"]) == set(audit_names)

    def test_parallel_requests_use_independent_sessions(
        self,
        offline_service: OfflineService,
    ) -> None:
        audit_names = ("audit-a", "audit-b")

        results, closed_session_ids = _run_audit_workers(
            offline_service,
            audit_names,
        )

        result_by_name = {result.audit_name: result for result in results}
        assert set(result_by_name) == set(audit_names)
        session_ids = {result.session_id for result in results}
        assert len(session_ids) == 2
        assert closed_session_ids == session_ids
        assert all(result.trust_env is False for result in results)
        for audit_name in audit_names:
            response = result_by_name[audit_name].response
            self.assertions.assert_status_code(response, 200)
            self.assertions.assert_json_value(response, "$.audit_name", audit_name)

        snapshot = offline_service.state.snapshot()
        assert snapshot["endpoint_call_counts"]["audit_calls"] == 2
        assert set(snapshot["audit_records"]) == set(audit_names)
        assert snapshot["request_hosts"]
        assert set(snapshot["request_hosts"]) == {"127.0.0.1"}

    def test_single_request_headers_do_not_leak_between_workers(
        self,
        offline_service: OfflineService,
    ) -> None:
        audit_names = ("audit-a", "audit-b")

        results, closed_session_ids = _run_audit_workers(
            offline_service,
            audit_names,
        )

        result_by_name = {result.audit_name: result for result in results}
        assert set(result_by_name) == set(audit_names)
        for audit_name in audit_names:
            result = result_by_name[audit_name]
            self.assertions.assert_status_code(result.response, 200)
            self.assertions.assert_json_value(
                result.response,
                "$.audit_name",
                audit_name,
            )
            assert result.audit_header_in_session is False

        session_ids = {result.session_id for result in results}
        assert closed_session_ids == session_ids
        snapshot = offline_service.state.snapshot()
        assert snapshot["endpoint_call_counts"]["audit_calls"] == 2
        assert set(snapshot["audit_records"]) == set(audit_names)
        assert "" not in snapshot["audit_records"]
