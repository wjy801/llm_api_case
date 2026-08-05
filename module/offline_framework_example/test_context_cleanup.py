from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from common import ContextCleanupError, ContextVariableTypeError, TestContext
from common.capture import CapturePolicy
from module.offline_framework_example import (
    OfflineFrameworkAssertions,
    OfflineFrameworkRequest,
    OfflineFrameworkTask,
)
from module.offline_framework_example.offline_service import (
    DEFAULT,
    OFFLINE_API_KEY,
    OFFLINE_COOKIE_NAME,
    OFFLINE_COOKIE_VALUE,
    OFFLINE_REQUEST_ID,
    OFFLINE_TASK_ID,
    OFFLINE_TRACE_ID,
    OfflineService,
)


class TestContextAndCleanup:
    def setup_method(self) -> None:
        self.assertions = OfflineFrameworkAssertions()
        self.task = OfflineFrameworkTask()

    def test_context_extracts_json_header_cookie_and_regex(
        self,
        offline_service: OfflineService,
        offline_request: OfflineFrameworkRequest,
        offline_test_context: TestContext,
    ) -> None:
        response = self.task.query_context(offline_request)
        self.assertions.assert_status_code(response, 200)

        task_id = offline_test_context.extract(
            "task_id",
            response,
            json_path="$.data.task_id",
            expected_type=str,
        )
        request_id = offline_test_context.extract(
            "request_id",
            response,
            header="X-Request-ID",
            expected_type=str,
        )
        session_id = offline_test_context.extract(
            "session_id",
            response,
            cookie=OFFLINE_COOKIE_NAME,
            expected_type=str,
        )
        trace_id = offline_test_context.extract(
            "trace_id",
            response,
            regex=r"trace=([a-z0-9-]+)",
            group=1,
            expected_type=str,
        )

        expected = {
            "task_id": OFFLINE_TASK_ID,
            "request_id": OFFLINE_REQUEST_ID,
            "session_id": OFFLINE_COOKIE_VALUE,
            "trace_id": OFFLINE_TRACE_ID,
        }
        assert [task_id, request_id, session_id, trace_id] == list(expected.values())
        assert {
            name: offline_test_context.require(name, expected_type=str)
            for name in expected
        } == expected
        assert offline_test_context.snapshot() == expected
        assert response.headers["X-Request-ID"] == OFFLINE_REQUEST_ID
        assert response.cookies[OFFLINE_COOKIE_NAME] == OFFLINE_COOKIE_VALUE
        assert offline_service.state.snapshot()["endpoint_call_counts"]["context_calls"] == 1

    def test_context_applies_type_and_transform_contracts(
        self,
        offline_service: OfflineService,
        offline_request: OfflineFrameworkRequest,
        offline_test_context: TestContext,
    ) -> None:
        response = self.task.query_context(offline_request)
        self.assertions.assert_status_code(response, 200)

        task_id = offline_test_context.extract(
            "task_upper",
            response,
            json_path="$.data.task_id",
            transform=str.upper,
            expected_type=str,
        )
        count = offline_test_context.extract(
            "count",
            response,
            json_path="$.count",
            default="2",
            transform=int,
            expected_type=int,
        )
        missing = offline_test_context.extract(
            "optional_missing",
            response,
            json_path="$.missing",
            required=False,
        )

        assert task_id == OFFLINE_TASK_ID.upper()
        assert count == 2
        assert offline_test_context.require("count", expected_type=int) == 2
        assert missing is None
        assert not offline_test_context.has("optional_missing")

        with pytest.raises(ContextVariableTypeError) as error_info:
            offline_test_context.require("task_upper", expected_type=int)

        error_text = str(error_info.value)
        assert "task_upper" in error_text
        assert "Expected: int" in error_text
        assert "actual: str" in error_text
        assert OFFLINE_API_KEY not in error_text
        assert offline_service.state.snapshot()["endpoint_call_counts"]["context_calls"] == 1

    def test_context_runs_cleanup_in_lifo_order(
        self,
        offline_service_factory: Callable[..., OfflineService],
        offline_request_factory: Callable[..., OfflineFrameworkRequest],
        offline_test_context: TestContext,
    ) -> None:
        service = offline_service_factory(DEFAULT)
        request = offline_request_factory(
            service,
            capture_policy=CapturePolicy.disabled(),
        )
        task_id = self._create_media_task(service, request)
        order: list[str] = []
        delete_responses: list[Any] = []

        def record(name: str) -> None:
            order.append(name)

        def delete_task() -> None:
            order.append("delete")
            response = self.task.delete_media_task(request, task_id)
            self.assertions.assert_status_code(response, 204)
            delete_responses.append(response)

        offline_test_context.add_cleanup(record, "first")
        offline_test_context.add_cleanup(delete_task)
        offline_test_context.add_cleanup(record, "last")

        offline_test_context.cleanup()

        assert order == ["last", "delete", "first"]
        assert len(delete_responses) == 1
        snapshot = service.state.snapshot()
        assert snapshot["endpoint_call_counts"]["media_delete_calls"] == 1
        assert snapshot["deleted_task_ids"] == (OFFLINE_TASK_ID,)
        assert snapshot["tasks"] == ()
        offline_test_context.cleanup()
        assert order == ["last", "delete", "first"]

        live_response = self.task.query_context(request)
        self.assertions.assert_status_code(live_response, 200)

    def test_context_continues_cleanup_and_reports_errors(
        self,
        offline_service_factory: Callable[..., OfflineService],
        offline_request_factory: Callable[..., OfflineFrameworkRequest],
        offline_test_context: TestContext,
    ) -> None:
        service = offline_service_factory(DEFAULT)
        request = offline_request_factory(
            service,
            capture_policy=CapturePolicy.disabled(),
        )
        task_id = self._create_media_task(service, request)
        order: list[str] = []

        def record(name: str) -> None:
            order.append(name)

        def delete_task() -> None:
            order.append("delete")
            response = self.task.delete_media_task(request, task_id)
            self.assertions.assert_status_code(response, 204)

        def fail_cleanup() -> None:
            order.append("fail")
            raise RuntimeError(f"Authorization: Bearer {OFFLINE_API_KEY}")

        offline_test_context.add_cleanup(record, "first")
        offline_test_context.add_cleanup(delete_task)
        offline_test_context.add_cleanup(fail_cleanup)
        offline_test_context.add_cleanup(record, "last")

        with pytest.raises(ContextCleanupError) as error_info:
            offline_test_context.cleanup()

        error = error_info.value
        assert order == ["last", "fail", "delete", "first"]
        assert len(error.errors) == 1
        assert isinstance(error.errors[0], RuntimeError)
        snapshot = service.state.snapshot()
        assert snapshot["endpoint_call_counts"]["media_delete_calls"] == 1
        assert snapshot["tasks"] == ()
        offline_test_context.cleanup()
        assert order == ["last", "fail", "delete", "first"]

        error_text = str(error)
        assert "RuntimeError" in error_text
        assert "1 error(s)" in error_text
        assert "<redacted>" in error_text
        assert f"Authorization: Bearer {OFFLINE_API_KEY}" not in error_text
        assert OFFLINE_API_KEY not in error_text

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
