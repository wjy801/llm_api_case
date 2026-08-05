from __future__ import annotations

from collections.abc import Callable
from time import perf_counter

import pytest

from common.capture import CapturePolicy
from common.polling import (
    PollingFailedError,
    PollingState,
    PollingTimeoutError,
    PollingUnknownStateError,
)
from common.runtime_hooks import RuntimePollingOutcome
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
    OFFLINE_API_KEY,
    OFFLINE_TASK_ID,
    POLL_FAILURE,
    POLL_SUCCESS_WITH_RETRY,
    POLL_TIMEOUT,
    POLL_UNKNOWN,
    OfflineService,
)
from module.offline_framework_example.response_schemas import (
    OFFLINE_POLLING_SUCCESS_SCHEMA,
)
from module.offline_framework_example.task import (
    OFFLINE_POLLING_POLICY,
    OFFLINE_RETRY_POLICY,
)


class TestPolling:
    def setup_method(self) -> None:
        self.assertions = OfflineFrameworkAssertions()
        self.task = OfflineFrameworkTask()

    def test_polling_reaches_success_with_complete_transitions(
        self,
        offline_service_factory: Callable[..., OfflineService],
        offline_request_factory: Callable[..., OfflineFrameworkRequest],
        offline_runtime_recorder: OfflineRuntimeRecorder,
        offline_capture_dirs: OfflineCaptureDirs,
    ) -> None:
        service = offline_service_factory(POLL_SUCCESS_WITH_RETRY)
        request = offline_request_factory(
            service,
            capture_policy=CapturePolicy.disabled(),
        )
        task_id = self._create_media_task(service, request)

        response = self.task.poll_media_generation_result(
            request,
            task_id,
            poll_interval=0.01,
            poll_timeout=1,
            polling_policy=OFFLINE_POLLING_POLICY,
            retry_policy=OFFLINE_RETRY_POLICY,
        )

        self.assertions.assert_status_code(response, 200)
        self.assertions.assert_task_id(response, OFFLINE_TASK_ID)
        self.assertions.assert_task_status(response, "succeeded")
        assert (
            self.assertions.assert_schema(
                response,
                OFFLINE_POLLING_SUCCESS_SCHEMA,
            )
            is response
        )

        snapshot = service.state.snapshot()
        assert snapshot["endpoint_call_counts"]["media_create_calls"] == 1
        assert snapshot["endpoint_call_counts"]["media_poll_calls"] == 4
        assert OFFLINE_TASK_ID in snapshot["tasks"]

        poll_path = request.media_task_path_template.format(task_id=task_id)
        poll_responses = [
            observed_response
            for context, observed_response in offline_runtime_recorder.responses
            if context.path == poll_path
        ]
        assert [item.status_code for item in poll_responses] == [503, 200, 200, 200]
        assert [item.json()["status"] for item in poll_responses if item.status_code == 200] == [
            "queued",
            "running",
            "succeeded",
        ]

        poll_groups = [
            group
            for group in offline_runtime_recorder.request_groups
            if group.path == poll_path
        ]
        assert [context.attributes["attempt_index"] for context in poll_groups[0].contexts] == [
            1,
            2,
        ]

        polling = offline_runtime_recorder.polling_sessions[-1]
        assert polling.states == ["pending", "pending", "success"]
        assert polling.outcome is RuntimePollingOutcome.SUCCESS
        assert list(offline_capture_dirs.input_dir.iterdir()) == []
        assert list(offline_capture_dirs.output_dir.iterdir()) == []

    def test_polling_reports_business_failure(
        self,
        offline_service_factory: Callable[..., OfflineService],
        offline_request_factory: Callable[..., OfflineFrameworkRequest],
        offline_runtime_recorder: OfflineRuntimeRecorder,
        offline_capture_dirs: OfflineCaptureDirs,
    ) -> None:
        service = offline_service_factory(POLL_FAILURE)
        request = offline_request_factory(
            service,
            capture_policy=CapturePolicy.disabled(),
        )
        task_id = self._create_media_task(service, request)

        with pytest.raises(PollingFailedError) as error_info:
            self.task.poll_media_generation_result(
                request,
                task_id,
                poll_interval=0.01,
                poll_timeout=1,
                polling_policy=OFFLINE_POLLING_POLICY,
            )

        error = error_info.value
        assert error.last_status == "failed"
        assert error.last_response is not None
        assert error.last_response.json()["status"] == "failed"
        assert error.error_value == {
            "code": "OFFLINE_TASK_FAILED",
            "type": "controlled_offline_failure",
            "message": "controlled failure",
        }
        assert [transition.raw_status for transition in error.transitions] == [
            "queued",
            "failed",
        ]
        assert [transition.state for transition in error.transitions] == [
            PollingState.PENDING,
            PollingState.FAILURE,
        ]
        assert service.state.snapshot()["endpoint_call_counts"]["media_poll_calls"] == 2
        assert offline_runtime_recorder.polling_sessions[-1].outcome is RuntimePollingOutcome.FAILURE
        error_text = str(error)
        assert request.media_task_path_template.format(task_id=task_id) in error_text
        assert "failed" in error_text
        assert "queued" in error_text
        assert OFFLINE_API_KEY not in error_text
        assert list(offline_capture_dirs.output_dir.iterdir()) == []

    def test_polling_rejects_unknown_state(
        self,
        offline_service_factory: Callable[..., OfflineService],
        offline_request_factory: Callable[..., OfflineFrameworkRequest],
        offline_runtime_recorder: OfflineRuntimeRecorder,
        offline_capture_dirs: OfflineCaptureDirs,
    ) -> None:
        service = offline_service_factory(POLL_UNKNOWN)
        request = offline_request_factory(
            service,
            capture_policy=CapturePolicy.disabled(),
        )
        task_id = self._create_media_task(service, request)

        with pytest.raises(PollingUnknownStateError) as error_info:
            self.task.poll_media_generation_result(
                request,
                task_id,
                poll_interval=0.01,
                poll_timeout=1,
                polling_policy=OFFLINE_POLLING_POLICY,
            )

        error = error_info.value
        assert error.last_status == "paused"
        assert error.last_response is not None
        assert error.last_response.json()["status"] == "paused"
        assert [transition.raw_status for transition in error.transitions] == [
            "queued",
            "paused",
        ]
        assert [transition.state for transition in error.transitions] == [
            PollingState.PENDING,
            PollingState.UNKNOWN,
        ]
        assert service.state.snapshot()["endpoint_call_counts"]["media_poll_calls"] == 2
        polling = offline_runtime_recorder.polling_sessions[-1]
        assert polling.states == ["pending", "unknown"]
        assert polling.outcome is RuntimePollingOutcome.UNKNOWN
        assert list(offline_capture_dirs.output_dir.iterdir()) == []

    def test_polling_enforces_total_deadline(
        self,
        offline_service_factory: Callable[..., OfflineService],
        offline_request_factory: Callable[..., OfflineFrameworkRequest],
        offline_runtime_recorder: OfflineRuntimeRecorder,
        offline_capture_dirs: OfflineCaptureDirs,
    ) -> None:
        service = offline_service_factory(POLL_TIMEOUT)
        request = offline_request_factory(
            service,
            capture_policy=CapturePolicy.disabled(),
        )
        task_id = self._create_media_task(service, request)

        started_at = perf_counter()
        with pytest.raises(PollingTimeoutError) as error_info:
            self.task.poll_media_generation_result(
                request,
                task_id,
                poll_interval=0.01,
                poll_timeout=0.03,
                polling_policy=OFFLINE_POLLING_POLICY,
            )
        elapsed = perf_counter() - started_at

        error = error_info.value
        assert error.last_status == "running"
        assert error.last_response is not None
        assert error.last_response.json()["status"] == "running"
        assert error.transitions
        assert all(transition.raw_status == "running" for transition in error.transitions)
        assert all(transition.state is PollingState.PENDING for transition in error.transitions)
        attempt_indexes = [transition.attempt_index for transition in error.transitions]
        assert attempt_indexes == sorted(attempt_indexes)
        assert len(attempt_indexes) == len(set(attempt_indexes))
        assert elapsed < 0.5

        first_snapshot = service.state.snapshot()
        second_snapshot = service.state.snapshot()
        first_count = first_snapshot["endpoint_call_counts"]["media_poll_calls"]
        second_count = second_snapshot["endpoint_call_counts"]["media_poll_calls"]
        assert first_count >= 1
        assert second_count == first_count

        polling = offline_runtime_recorder.polling_sessions[-1]
        assert polling.states
        assert set(polling.states) == {"pending"}
        assert polling.outcome is RuntimePollingOutcome.TIMEOUT
        assert list(offline_capture_dirs.output_dir.iterdir()) == []

    def _create_media_task(
        self,
        service: OfflineService,
        request: OfflineFrameworkRequest,
    ) -> str:
        payload = self.task.build_media_generation_payload(service.base_url)
        response = self.task.create_media_generation(request, payload)
        self.assertions.assert_status_code(response, 202)
        self.assertions.assert_task_id(response, OFFLINE_TASK_ID)
        return self.task.extract_task_id(response)
