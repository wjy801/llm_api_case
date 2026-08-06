from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from threading import Barrier, Lock
from time import perf_counter

import requests

from common import TestContext, submit_with_context
from common.capture import CapturePolicy
from common.runtime_hooks import (
    RuntimeHooks,
    RuntimeOperationKind,
    RuntimePollingOutcome,
    RuntimeTrafficRole,
    get_runtime_hooks,
)
from module.offline_framework_example import (
    OfflineFrameworkAssertions,
    OfflineFrameworkRequest,
    OfflineFrameworkTask,
)
from module.offline_framework_example.conftest import (
    OfflineCaptureDirs,
    OfflineRuntimeRecorder,
)
from module.offline_framework_example.offline_service import (
    OFFLINE_COOKIE_NAME,
    OFFLINE_COOKIE_VALUE,
    OFFLINE_MODEL_ID,
    OFFLINE_REQUEST_ID,
    OFFLINE_TASK_ID,
    OFFLINE_TRACE_ID,
    OUTPUT_PNG_BYTES,
    OUTPUT_PNG_SHA256,
    POLL_SUCCESS_WITH_RETRY,
    OfflineService,
)
from module.offline_framework_example.response_schemas import (
    OFFLINE_AUDIT_RESPONSE_SCHEMA,
    OFFLINE_CREATE_TASK_SCHEMA,
    OFFLINE_POLLING_SUCCESS_SCHEMA,
)
from module.offline_framework_example.task import (
    OFFLINE_POLLING_POLICY,
    OFFLINE_RETRY_POLICY,
)


@dataclass(frozen=True, slots=True)
class _GoldenAuditWorkerResult:
    audit_name: str
    response: requests.Response
    session_id: int
    runtime_hooks: RuntimeHooks


def _query_golden_audit(
    base_url: str,
    audit_name: str,
    *,
    barrier: Barrier,
    closed_session_ids: set[int],
    closed_session_lock: Lock,
) -> _GoldenAuditWorkerResult:
    request_client = OfflineFrameworkRequest(
        base_url,
        capture_policy=CapturePolicy.disabled(),
    )
    session_id = id(request_client.session)
    try:
        barrier.wait(timeout=1)
        response = OfflineFrameworkTask().query_audit(request_client, audit_name)
        return _GoldenAuditWorkerResult(
            audit_name=audit_name,
            response=response,
            session_id=session_id,
            runtime_hooks=get_runtime_hooks(),
        )
    finally:
        request_client.close()
        with closed_session_lock:
            closed_session_ids.add(session_id)


class TestFullFrameworkFlow:
    def setup_method(self) -> None:
        self.assertions = OfflineFrameworkAssertions()
        self.task = OfflineFrameworkTask()

    def test_offline_async_media_flow(
        self,
        offline_service_factory: Callable[..., OfflineService],
        offline_request_factory: Callable[..., OfflineFrameworkRequest],
        offline_network_guard: Callable[[str], None],
        offline_capture_dirs: OfflineCaptureDirs,
        offline_runtime_recorder: OfflineRuntimeRecorder,
        offline_test_context: TestContext,
    ) -> None:
        service = offline_service_factory(POLL_SUCCESS_WITH_RETRY)
        request = offline_request_factory(
            service,
            capture_policy=CapturePolicy.output_only(),
        )
        payload = self.task.build_media_generation_payload(service.base_url)
        original_payload = deepcopy(payload)
        assert payload["model"] == OFFLINE_MODEL_ID

        flow_started = perf_counter()
        final_response = self.task.create_and_poll_media_generation(
            request,
            payload,
            poll_interval=0.01,
            poll_timeout=1,
            polling_policy=OFFLINE_POLLING_POLICY,
            retry_policy=OFFLINE_RETRY_POLICY,
        )

        create_responses = [
            response
            for context, response in offline_runtime_recorder.responses
            if context.path == OfflineFrameworkRequest.media_generations_path
            and response.status_code == 202
        ]
        assert len(create_responses) == 1
        create_response = create_responses[0]
        assert self.assertions.assert_status_code(create_response, 202) is create_response
        assert (
            self.assertions.assert_schema(create_response, OFFLINE_CREATE_TASK_SCHEMA)
            is create_response
        )
        created_task_id = self.task.extract_task_id(create_response)
        assert created_task_id == OFFLINE_TASK_ID

        assert self.assertions.assert_status_code(final_response, 200) is final_response
        assert (
            self.assertions.assert_task_id(final_response, created_task_id)
            is final_response
        )
        assert (
            self.assertions.assert_task_status(final_response, "succeeded")
            is final_response
        )
        assert (
            self.assertions.assert_schema(
                final_response,
                OFFLINE_POLLING_SUCCESS_SCHEMA,
            )
            is final_response
        )
        assert payload == original_payload

        task_id = offline_test_context.extract(
            "task_id",
            final_response,
            json_path="$.task_id",
            expected_type=str,
        )
        request_id = offline_test_context.extract(
            "request_id",
            final_response,
            header="X-Request-ID",
            expected_type=str,
        )
        session_id = offline_test_context.extract(
            "session_id",
            final_response,
            cookie=OFFLINE_COOKIE_NAME,
            expected_type=str,
        )
        trace_id = offline_test_context.extract(
            "trace_id",
            final_response,
            json_path="$.trace_id",
            expected_type=str,
        )
        expected_context = {
            "task_id": OFFLINE_TASK_ID,
            "request_id": OFFLINE_REQUEST_ID,
            "session_id": OFFLINE_COOKIE_VALUE,
            "trace_id": OFFLINE_TRACE_ID,
        }
        assert {
            "task_id": task_id,
            "request_id": request_id,
            "session_id": session_id,
            "trace_id": trace_id,
        } == expected_context
        assert offline_test_context.snapshot() == expected_context
        assert task_id == created_task_id

        delete_responses: list[requests.Response] = []

        def delete_task() -> None:
            response = self.task.delete_media_task(request, task_id)
            self.assertions.assert_status_code(response, 204)
            delete_responses.append(response)

        offline_test_context.add_cleanup(delete_task)

        audit_names = ("audit-a", "audit-b")
        barrier = Barrier(2)
        closed_session_ids: set[int] = set()
        closed_session_lock = Lock()
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                submit_with_context(
                    executor,
                    _query_golden_audit,
                    service.base_url,
                    audit_name,
                    barrier=barrier,
                    closed_session_ids=closed_session_ids,
                    closed_session_lock=closed_session_lock,
                )
                for audit_name in audit_names
            ]
            audit_results = [future.result(timeout=2) for future in futures]

        audit_responses = [result.response for result in audit_results]
        self.assertions.assert_audit_names(audit_responses, set(audit_names))
        for audit_response in audit_responses:
            assert (
                self.assertions.assert_schema(
                    audit_response,
                    OFFLINE_AUDIT_RESPONSE_SCHEMA,
                )
                is audit_response
            )
        worker_session_ids = {result.session_id for result in audit_results}
        assert len(worker_session_ids) == 2
        assert closed_session_ids == worker_session_ids
        assert all(
            result.runtime_hooks is offline_runtime_recorder
            for result in audit_results
        )

        result_url = final_response.json()["result"]["url"]
        offline_network_guard(result_url)
        assert service.state.output_asset_requested.is_set()
        assert not service.state.input_asset_requested.is_set()
        output_file = offline_capture_dirs.output_dir / "output.png"
        assert set(offline_capture_dirs.output_dir.iterdir()) == {output_file}
        assert not any(
            path.name.endswith(".part")
            for path in offline_capture_dirs.output_dir.iterdir()
        )
        assert output_file.stat().st_size == len(OUTPUT_PNG_BYTES) == 70
        assert sha256(output_file.read_bytes()).hexdigest() == OUTPUT_PNG_SHA256
        assert list(offline_capture_dirs.input_dir.iterdir()) == []

        assert any(
            metadata.kind is RuntimeOperationKind.ASYNC_TASK
            and metadata.name == "media_generation"
            and metadata.role is RuntimeTrafficRole.WORKLOAD
            for metadata in offline_runtime_recorder.operation_metadata
        )
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

        poll_path = request.media_task_path_template.format(task_id=task_id)
        poll_responses = [
            response
            for context, response in offline_runtime_recorder.responses
            if context.path == poll_path
        ]
        assert [response.status_code for response in poll_responses] == [
            503,
            200,
            200,
            200,
        ]
        assert [
            response.json()["status"]
            for response in poll_responses
            if response.status_code == 200
        ] == ["queued", "running", "succeeded"]
        polling = offline_runtime_recorder.polling_sessions[-1]
        assert polling.states == ["pending", "pending", "success"]
        assert polling.outcome is RuntimePollingOutcome.SUCCESS
        poll_groups = [
            group
            for group in offline_runtime_recorder.request_groups
            if group.path == poll_path
        ]
        assert len(poll_groups) == 3
        assert [
            context.attributes["attempt_index"]
            for context in poll_groups[0].contexts
        ] == [1, 2]

        snapshot = service.state.snapshot()
        assert snapshot["endpoint_call_counts"] == {
            "media_create_calls": 1,
            "media_poll_calls": 4,
            "output_asset_calls": 1,
            "audit_calls": 2,
        }
        assert set(snapshot["audit_records"]) == set(audit_names)
        assert snapshot["tasks"] == (OFFLINE_TASK_ID,)

        offline_test_context.cleanup()
        offline_test_context.cleanup()

        assert len(delete_responses) == 1
        final_snapshot = service.state.snapshot()
        assert final_snapshot["endpoint_call_counts"]["media_delete_calls"] == 1
        assert final_snapshot["deleted_task_ids"] == (OFFLINE_TASK_ID,)
        assert final_snapshot["tasks"] == ()
        assert final_snapshot["handler_errors"] == ()
        assert final_snapshot["request_hosts"]
        assert set(final_snapshot["request_hosts"]) == {"127.0.0.1"}
        assert perf_counter() - flow_started < 2
