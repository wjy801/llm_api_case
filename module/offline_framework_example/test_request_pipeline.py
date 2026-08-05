from __future__ import annotations

from copy import deepcopy

import pytest

from common.request_middleware import (
    LoggingMiddleware,
    MediaResourceMiddleware,
    RedactionMiddleware,
    RuntimeObservationMiddleware,
)
from common.runtime_hooks import RuntimeOperationKind, RuntimeTrafficRole
from module.offline_framework_example import (
    OfflineFrameworkAssertions,
    OfflineFrameworkRequest,
    OfflineFrameworkTask,
)
from module.offline_framework_example.conftest import OfflineRuntimeRecorder
from module.offline_framework_example.offline_service import (
    OFFLINE_API_KEY,
    OfflineService,
)


class TestRequestPipeline:
    @pytest.fixture(autouse=True)
    def _setup(
        self,
        offline_service: OfflineService,
        offline_request: OfflineFrameworkRequest,
    ):
        self.service = offline_service
        self.request = offline_request
        self.assertions = OfflineFrameworkAssertions()
        self.task = OfflineFrameworkTask()

    def test_request_uses_default_middleware_and_runtime_metadata(
        self,
        offline_runtime_recorder: OfflineRuntimeRecorder,
    ) -> None:
        payload = self.task.build_echo_payload()

        response = self.task.submit_echo(self.request, payload)

        self.assertions.assert_echo_accepted(response, payload)
        assert self.request.config.base_url == self.service.base_url
        assert self.request.session.trust_env is False
        assert tuple(type(middleware) for middleware in self.request.middlewares) == (
            RuntimeObservationMiddleware,
            MediaResourceMiddleware,
            RedactionMiddleware,
            LoggingMiddleware,
        )

        metadata = offline_runtime_recorder.operation_metadata[-1]
        assert metadata.kind == RuntimeOperationKind.HTTP
        assert metadata.name == "offline_echo"
        assert metadata.role == RuntimeTrafficRole.WORKLOAD

        group = offline_runtime_recorder.request_groups[-1]
        assert group.method == "POST"
        assert group.path == "/v1/echo"
        assert group.protocol == "http"
        assert group.configured_max_attempts == 1
        assert len(group.contexts) == 1
        assert "runtime_metadata" not in group.contexts[0].kwargs
        assert "begin_operation" in offline_runtime_recorder.delegated_calls
        assert "request_started" in offline_runtime_recorder.delegated_calls
        assert self.service.state.snapshot()["endpoint_call_counts"]["echo_calls"] == 1

    def test_sensitive_headers_preserve_business_request(self) -> None:
        payload = self.task.build_echo_payload()

        response = self.task.submit_echo(self.request, payload)

        self.assertions.assert_echo_accepted(response, payload)
        assert self.request.session.headers["Authorization"] == (
            f"Bearer {OFFLINE_API_KEY}"
        )
        assert OFFLINE_API_KEY not in response.text
        assert f"Bearer {OFFLINE_API_KEY}" not in response.text
        assert self.service.state.snapshot()["endpoint_call_counts"]["echo_calls"] == 1

    def test_middleware_does_not_mutate_original_payload(self) -> None:
        payload = self.task.build_echo_payload()
        original = deepcopy(payload)
        another_payload = self.task.build_echo_payload()

        response = self.task.submit_echo(self.request, payload)

        self.assertions.assert_echo_accepted(response, original)
        assert payload == original
        assert payload is not another_payload
        assert payload["metadata"] is not another_payload["metadata"]
        assert response.json()["received"] == original
        assert self.service.state.snapshot()["endpoint_call_counts"]["echo_calls"] == 1
