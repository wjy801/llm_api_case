from __future__ import annotations

from typing import Any

from common import RetryPolicy
from module.smoke import SmokeRequest, SmokeTask


def test_smoke_request_task_status_get_passes_retry_policy(monkeypatch) -> None:
    request_client = object.__new__(SmokeRequest)
    calls: list[dict[str, Any]] = []
    retry_policy = RetryPolicy(max_attempts=3)
    expected_response = object()

    def fake_get(path: str, **kwargs: Any) -> object:
        calls.append({"path": path, "kwargs": kwargs})
        return expected_response

    monkeypatch.setattr(request_client, "get", fake_get)

    response = request_client.get_media_generation_task(
        "task-001",
        retry_policy=retry_policy,
    )

    assert response is expected_response
    assert calls[0]["path"] == "/v1/media/tasks/task-001"
    assert calls[0]["kwargs"]["retry_policy"] is retry_policy


def test_smoke_request_task_poll_passes_retry_policy(monkeypatch) -> None:
    request_client = object.__new__(SmokeRequest)
    calls: list[dict[str, Any]] = []
    retry_policy = RetryPolicy(max_attempts=3)
    expected_response = object()

    def fake_poll_get(path: str, **kwargs: Any) -> object:
        calls.append({"path": path, "kwargs": kwargs})
        return expected_response

    monkeypatch.setattr(request_client, "poll_get", fake_poll_get)

    response = request_client.poll_media_generation_result(
        "task-001",
        retry_policy=retry_policy,
    )

    assert response is expected_response
    assert calls[0]["path"] == "/v1/media/tasks/task-001"
    assert calls[0]["kwargs"]["retry_policy"] is retry_policy


def test_smoke_task_status_get_passes_retry_policy() -> None:
    calls: list[dict[str, Any]] = []
    retry_policy = RetryPolicy(max_attempts=3)
    expected_response = object()

    class RecordingSmokeRequest:
        def get_media_generation_task(self, task_id: str, **kwargs: Any) -> object:
            calls.append({"task_id": task_id, "kwargs": kwargs})
            return expected_response

    response = SmokeTask().get_media_generation_task(
        RecordingSmokeRequest(),  # type: ignore[arg-type]
        "task-001",
        retry_policy=retry_policy,
    )

    assert response is expected_response
    assert calls == [
        {
            "task_id": "task-001",
            "kwargs": {"retry_policy": retry_policy},
        }
    ]
