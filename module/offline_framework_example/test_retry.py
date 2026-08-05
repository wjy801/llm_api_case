from __future__ import annotations

from collections.abc import Callable

from common.runtime_hooks import RuntimeTrafficRole
from module.offline_framework_example import (
    OfflineFrameworkAssertions,
    OfflineFrameworkRequest,
    OfflineFrameworkTask,
)
from module.offline_framework_example.conftest import OfflineRuntimeRecorder
from module.offline_framework_example.offline_service import (
    GET_429_THEN_200,
    GET_503_THEN_200,
    POST_503_THEN_200,
    OfflineService,
)
from module.offline_framework_example.task import OFFLINE_RETRY_POLICY


class TestRetry:
    def setup_method(self) -> None:
        self.assertions = OfflineFrameworkAssertions()
        self.task = OfflineFrameworkTask()

    def test_retry_rescues_transient_get_failure(
        self,
        offline_service_factory: Callable[..., OfflineService],
        offline_request_factory: Callable[..., OfflineFrameworkRequest],
        offline_runtime_recorder: OfflineRuntimeRecorder,
    ) -> None:
        service = offline_service_factory(GET_503_THEN_200)
        request = offline_request_factory(service)

        response = self.task.request_transient(request)

        self.assertions.assert_transient_recovered(response)
        assert service.state.snapshot()["endpoint_call_counts"]["transient_calls"] == 2
        group = offline_runtime_recorder.request_groups[-1]
        assert group.configured_max_attempts == 2
        assert [context.attributes["attempt_index"] for context in group.contexts] == [
            1,
            2,
        ]
        assert [
            response.status_code
            for _, response in offline_runtime_recorder.responses
        ] == [503, 200]
        metadata = offline_runtime_recorder.operation_metadata[-1]
        assert metadata.name == "offline_transient_request"
        assert metadata.role == RuntimeTrafficRole.WORKLOAD

    def test_retry_honors_retry_after(
        self,
        offline_service_factory: Callable[..., OfflineService],
        offline_request_factory: Callable[..., OfflineFrameworkRequest],
        offline_runtime_recorder: OfflineRuntimeRecorder,
    ) -> None:
        service = offline_service_factory(GET_429_THEN_200)
        request = offline_request_factory(service)

        response = self.task.request_transient(request)

        self.assertions.assert_transient_recovered(response)
        assert service.state.snapshot()["endpoint_call_counts"]["transient_calls"] == 2
        assert [
            observed_response.status_code
            for _, observed_response in offline_runtime_recorder.responses
        ] == [429, 200]
        assert offline_runtime_recorder.responses[0][1].headers["Retry-After"] == "0"
        group = offline_runtime_recorder.request_groups[-1]
        assert group.retry_wait_seconds == 0
        assert [context.attributes["attempt_index"] for context in group.contexts] == [
            1,
            2,
        ]

    def test_idempotent_post_can_retry(
        self,
        offline_service_factory: Callable[..., OfflineService],
        offline_request_factory: Callable[..., OfflineFrameworkRequest],
        offline_runtime_recorder: OfflineRuntimeRecorder,
    ) -> None:
        service = offline_service_factory(POST_503_THEN_200)
        request = offline_request_factory(service)
        payload = self.task.build_idempotent_operation_payload()

        response = self.task.commit_idempotent_operation(request, payload)

        self.assertions.assert_idempotent_committed(response)
        assert service.state.snapshot()["endpoint_call_counts"][
            "idempotent_operation_calls"
        ] == 2
        group = offline_runtime_recorder.request_groups[-1]
        assert [context.attributes["attempt_index"] for context in group.contexts] == [
            1,
            2,
        ]
        assert [
            observed_response.status_code
            for _, observed_response in offline_runtime_recorder.responses
        ] == [503, 200]
        assert "Idempotency-Key" not in request.session.headers
        assert OFFLINE_RETRY_POLICY.allow_post is False

    def test_non_idempotent_post_is_not_retried(
        self,
        offline_service_factory: Callable[..., OfflineService],
        offline_request_factory: Callable[..., OfflineFrameworkRequest],
        offline_runtime_recorder: OfflineRuntimeRecorder,
    ) -> None:
        service = offline_service_factory(POST_503_THEN_200)
        request = offline_request_factory(service)
        payload = self.task.build_idempotent_operation_payload()

        response = self.task.commit_idempotent_operation(
            request,
            payload,
            idempotency_key=None,
        )

        self.assertions.assert_status_code(response, 503)
        assert service.state.snapshot()["endpoint_call_counts"][
            "idempotent_operation_calls"
        ] == 1
        group = offline_runtime_recorder.request_groups[-1]
        assert group.configured_max_attempts == 2
        assert [context.attributes["attempt_index"] for context in group.contexts] == [1]
        assert [
            observed_response.status_code
            for _, observed_response in offline_runtime_recorder.responses
        ] == [503]
        assert "Idempotency-Key" not in request.session.headers
        assert OFFLINE_RETRY_POLICY.allow_post is False
