from __future__ import annotations

from typing import Any

import pytest

from common import RetryPolicy
from common.base_task import (
    USAGE_RECORD_SETTLEMENT_POLLING_POLICY,
    USAGE_RECORD_SETTLEMENT_POLL_INTERVAL_SECONDS,
    USAGE_RECORD_SETTLEMENT_TIMEOUT_SECONDS,
    BaseTask,
)
from common.polling import DEFAULT_MEDIA_POLLING_POLICY, PollingState, evaluate_polling_response
from common.task_capabilities import BillingCapability
from module.image_model.task import ImageTask
from module.video_model.task import VideoTask


class FakeResponse:
    def __init__(self, body: Any, headers: dict[str, str] | None = None):
        self.body = body
        self.text = repr(body)
        self.headers = headers or {}

    def json(self) -> Any:
        return self.body


class FakeGenerationRequest:
    def __init__(self):
        self.post_calls: list[dict[str, Any]] = []
        self.get_calls: list[dict[str, Any]] = []
        self.poll_calls: list[dict[str, Any]] = []
        self.updated_headers: list[dict[str, str]] = []
        self.reset_headers_count = 0

    def post(self, path: str, **kwargs: Any) -> FakeResponse:
        self.post_calls.append({"path": path, "kwargs": kwargs})
        if path == "/v1/images/generations":
            return FakeResponse({"data": [{"url": "https://example.com/sync-image.png"}]})
        if path == "/v1/chat/completions":
            return FakeResponse(
                {"choices": [{"message": {"content": "ok"}}]},
                headers={"x-oneapi-request-id": "request-001"},
            )
        if path == "/v1/media/generations":
            return FakeResponse({"task_id": "task-001"})
        return FakeResponse({"path": path})

    def get(self, path: str, **kwargs: Any) -> FakeResponse:
        self.get_calls.append({"path": path, "kwargs": kwargs})
        if path == "/v1/account/balance":
            return FakeResponse({"data": {"total_balance": "100"}})
        if path == "/v1/account/usage-records":
            return FakeResponse({"data": [{"request_id": kwargs.get("params", {}).get("request_id")}]})
        return FakeResponse({"path": path})

    def update_headers(self, headers: dict[str, str]) -> None:
        self.updated_headers.append(headers)

    def reset_headers(self) -> None:
        self.reset_headers_count += 1

    def poll_get(
        self,
        path: str,
        *,
        poll_interval: float = 2,
        poll_timeout: float | None = None,
        polling_policy: Any = None,
        retry_policy: Any = None,
        **kwargs: Any,
    ) -> FakeResponse:
        call = {
            "path": path,
            "poll_interval": poll_interval,
            "poll_timeout": poll_timeout,
            "polling_policy": polling_policy,
            "retry_policy": retry_policy,
        }
        call.update(kwargs)
        self.poll_calls.append(call)
        if path == "/v1/account/usage-records":
            request_id = kwargs.get("params", {}).get("request_id")
            return FakeResponse({"data": {"request_id": request_id, "status": "success"}})
        return FakeResponse({"result": {"urls": ["https://example.com/image.png"]}})


class TestBaseTask:
    def test_create_image_generation_calls_request_client(self):
        task = BaseTask()
        request_client = FakeGenerationRequest()
        payload = {"model": "gpt-image-2"}

        response = task.create_image_generation(request_client, payload)

        assert response.json() == {"data": [{"url": "https://example.com/sync-image.png"}]}
        assert request_client.post_calls == [{"path": "/v1/images/generations", "kwargs": {"json": payload}}]

    def test_create_chat_completion_calls_request_client(self):
        task = BaseTask()
        request_client = FakeGenerationRequest()
        payload = {"model": "GLM-5", "messages": []}

        response = task.create_chat_completion(request_client, payload)

        assert response.json() == {"choices": [{"message": {"content": "ok"}}]}
        assert request_client.post_calls == [{"path": "/v1/chat/completions", "kwargs": {"json": payload}}]

    def test_create_media_generation_calls_request_client(self):
        task = BaseTask()
        request_client = FakeGenerationRequest()
        payload = {"model": "wan2.7-image"}

        response = task.create_media_generation(request_client, payload)

        assert response.json() == {"task_id": "task-001"}
        assert request_client.post_calls == [{"path": "/v1/media/generations", "kwargs": {"json": payload}}]

    def test_poll_media_generation_result_calls_request_client(self):
        task = BaseTask()
        request_client = FakeGenerationRequest()

        response = task.poll_media_generation_result(
            request_client,
            "task-001",
            poll_interval=5,
            poll_timeout=120,
        )

        assert response.json() == {"result": {"urls": ["https://example.com/image.png"]}}
        assert request_client.poll_calls == [
            {
                "path": "/v1/media/tasks/task-001",
                "poll_interval": 5,
                "poll_timeout": 120,
                "polling_policy": DEFAULT_MEDIA_POLLING_POLICY,
                "retry_policy": None,
            }
        ]

    def test_poll_media_generation_result_passes_polling_and_retry_policy(self):
        task = BaseTask()
        request_client = FakeGenerationRequest()
        polling_policy = object()
        retry_policy = object()

        task.poll_media_generation_result(
            request_client,
            "task-001",
            polling_policy=polling_policy,
            retry_policy=retry_policy,
        )

        assert request_client.poll_calls == [
            {
                "path": "/v1/media/tasks/task-001",
                "poll_interval": 2,
                "poll_timeout": None,
                "polling_policy": polling_policy,
                "retry_policy": retry_policy,
            }
        ]

    def test_create_and_poll_media_generation_combines_create_and_poll(self):
        task = BaseTask()
        request_client = FakeGenerationRequest()
        payload = {"model": "wan2.7-image"}

        response = task.create_and_poll_media_generation(request_client, payload, poll_timeout=300)

        assert response.json() == {"result": {"urls": ["https://example.com/image.png"]}}
        assert request_client.post_calls == [{"path": "/v1/media/generations", "kwargs": {"json": payload}}]
        assert request_client.poll_calls[0]["path"] == "/v1/media/tasks/task-001"
        assert request_client.poll_calls[0]["poll_timeout"] == 300

    def test_extract_task_id_requires_task_id(self):
        task = BaseTask()

        with pytest.raises(AssertionError, match="未返回 task_id"):
            task.extract_task_id(FakeResponse({}))

    @pytest.mark.parametrize("field", ["id", "request_id"])
    def test_extract_task_id_accepts_existing_smoke_aliases(self, field):
        task = BaseTask()

        assert task.extract_task_id(FakeResponse({field: "task-001"})) == "task-001"

    def test_get_account_balance_uses_control_key_and_resets_headers(self):
        task = BaseTask()
        request_client = FakeGenerationRequest()

        response = task.get_account_balance(request_client, "control-key")

        assert response.json() == {"data": {"total_balance": "100"}}
        assert request_client.get_calls[0]["path"] == "/v1/account/balance"
        assert request_client.get_calls[0]["kwargs"]["data"] == ""
        assert request_client.get_calls[0]["kwargs"]["headers"]["Authorization"] == "Bearer control-key"
        assert request_client.updated_headers == []
        assert request_client.reset_headers_count == 0

    def test_query_usage_records_by_request_id_uses_control_key_and_resets_headers(self):
        task = BaseTask()
        request_client = FakeGenerationRequest()

        response = task.query_usage_records_by_request_id(request_client, "control-key", "request-001")

        assert response.json() == {"data": [{"request_id": "request-001"}]}
        assert request_client.get_calls == [
            {
                "path": "/v1/account/usage-records",
                "kwargs": {
                    "params": {"request_id": "request-001"},
                    "headers": {"Authorization": "Bearer control-key"},
                },
            }
        ]
        assert request_client.updated_headers == []
        assert request_client.reset_headers_count == 0

    def test_get_usage_records_queries_usage_by_request_id(self):
        task = BaseTask()
        request_client = FakeGenerationRequest()

        response = task.get_usage_records(request_client, "control-key", "request-001")

        assert response.json() == {"data": [{"request_id": "request-001"}]}
        assert request_client.post_calls == []
        assert request_client.get_calls[0]["path"] == "/v1/account/usage-records"
        assert request_client.get_calls[0]["kwargs"]["params"]["request_id"] == "request-001"

    def test_query_usage_records_for_billing_requires_response_or_request_id(self):
        task = BaseTask()
        request_client = FakeGenerationRequest()

        with pytest.raises(ValueError, match="model_response or request_id"):
            task.query_usage_records_for_billing(request_client)

    def test_query_usage_records_for_billing_accepts_model_response(self):
        task = BaseTask()
        request_client = FakeGenerationRequest()
        model_response = FakeResponse({}, headers={"x-oneapi-request-id": "request-001"})

        response = task.query_usage_records_for_billing(request_client, model_response=model_response)

        assert response.json() == {"data": {"request_id": "request-001", "status": "success"}}

    def test_query_usage_records_for_billing_accepts_request_id(self):
        task = BaseTask()
        request_client = FakeGenerationRequest()

        response = task.query_usage_records_for_billing(request_client, request_id="request-001")

        assert response.json() == {"data": {"request_id": "request-001", "status": "success"}}

    def test_billing_usage_query_waits_for_terminal_record(self, monkeypatch):
        task = BaseTask()
        request_client = FakeGenerationRequest()
        retry_policy = RetryPolicy(max_attempts=3)
        monkeypatch.setattr(task, "get_required_control_api_key", lambda: "control-key")

        response = task.query_usage_records_by_request_id_for_billing(
            request_client,
            "request-001",
            retry_policy=retry_policy,
        )

        assert response.json()["data"]["status"] == "success"
        assert len(request_client.poll_calls) == 1
        call = request_client.poll_calls[0]
        assert call["path"] == "/v1/account/usage-records"
        assert call["poll_interval"] == USAGE_RECORD_SETTLEMENT_POLL_INTERVAL_SECONDS
        assert call["poll_timeout"] == USAGE_RECORD_SETTLEMENT_TIMEOUT_SECONDS
        assert call["polling_policy"] == USAGE_RECORD_SETTLEMENT_POLLING_POLICY
        assert call["retry_policy"] is retry_policy
        assert call["params"] == {"request_id": "request-001"}
        assert call["headers"] == {"Authorization": "Bearer control-key"}
        assert call["runtime_metadata"].name == "usage_record_settlement"
        assert call["runtime_metadata"].role == "control"
        assert request_client.updated_headers == []
        assert request_client.reset_headers_count == 0

    @pytest.mark.parametrize(
        ("status", "expected_state"),
        [
            ("pending", PollingState.PENDING),
            ("success", PollingState.SUCCESS),
            ("failed", PollingState.SUCCESS),
        ],
    )
    def test_usage_record_settlement_policy_recognizes_pending_and_terminal_statuses(
        self,
        status,
        expected_state,
    ):
        response = FakeResponse({"data": {"status": status}})

        evaluation = evaluate_polling_response(
            response,
            USAGE_RECORD_SETTLEMENT_POLLING_POLICY,
        )

        assert evaluation.state is expected_state

    def test_get_request_id_from_response_requires_header(self):
        with pytest.raises(AssertionError, match="x-oneapi-request-id"):
            BaseTask.get_request_id_from_response(FakeResponse({}))

    def test_billing_capability_returns_neutral_missing_key_result(self):
        lookup = BillingCapability().lookup_control_api_key(
            use_china_environment=False,
            environ={},
        )

        assert lookup.environment_variable == "OVERSEAS_CONTROL_API_KEY"
        assert lookup.value is None
        assert lookup.is_configured is False

    def test_domain_task_types_keep_real_class_identity_and_mro(self):
        assert ImageTask.__name__ == "ImageTask"
        assert VideoTask.__name__ == "VideoTask"
        assert ImageTask is not BaseTask
        assert VideoTask is not BaseTask
        assert BaseTask in ImageTask.__mro__
        assert BaseTask in VideoTask.__mro__
