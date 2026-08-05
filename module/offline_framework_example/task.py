from __future__ import annotations

from typing import Any

import requests

from common import BaseTask, allure_step
from common.polling import PollingPolicy
from common.retry import RetryPolicy
from module.offline_framework_example.offline_service import (
    OFFLINE_IDEMPOTENCY_KEY,
    OFFLINE_MODEL_ID,
    assert_loopback_url,
)
from module.offline_framework_example.request import OfflineFrameworkRequest


OFFLINE_RETRY_POLICY = RetryPolicy(
    max_attempts=2,
    base_delay=0,
    max_delay=0,
    jitter=False,
    respect_retry_after=True,
    max_elapsed=1,
    allow_post=False,
)

OFFLINE_POLLING_POLICY = PollingPolicy(
    status_json_path="$.status",
    pending=frozenset({"queued", "running"}),
    success=frozenset({"succeeded"}),
    failure=frozenset({"failed", "cancelled"}),
    result_json_path="$.result.url",
    error_json_path="$.error",
    unknown="fail",
)


class OfflineFrameworkTask(BaseTask):
    media_generations_path = OfflineFrameworkRequest.media_generations_path
    media_task_path_template = OfflineFrameworkRequest.media_task_path_template

    @staticmethod
    def build_echo_payload() -> dict[str, Any]:
        return {
            "model": OFFLINE_MODEL_ID,
            "prompt": "offline framework example",
            "metadata": {"case": "request_pipeline"},
        }

    @staticmethod
    def build_idempotent_operation_payload() -> dict[str, Any]:
        return {
            "operation": "offline-write",
            "value": 1,
        }

    @staticmethod
    def build_media_generation_payload(base_url: str) -> dict[str, Any]:
        assert_loopback_url(base_url)
        return {
            "model": OFFLINE_MODEL_ID,
            "prompt": "offline framework example",
            "input": {
                "media": {
                    "type": "image",
                    "url": f"{base_url.rstrip('/')}/assets/input.png",
                }
            },
        }

    @allure_step("提交离线Echo请求")
    def submit_echo(
        self,
        request_client: OfflineFrameworkRequest,
        payload: dict[str, Any],
    ) -> requests.Response:
        return request_client.echo(payload)

    @allure_step("执行离线瞬时GET请求")
    def request_transient(
        self,
        request_client: OfflineFrameworkRequest,
        retry_policy: RetryPolicy = OFFLINE_RETRY_POLICY,
    ) -> requests.Response:
        return request_client.get_transient(retry_policy)

    @allure_step("提交离线幂等写操作")
    def commit_idempotent_operation(
        self,
        request_client: OfflineFrameworkRequest,
        payload: dict[str, Any],
        *,
        retry_policy: RetryPolicy = OFFLINE_RETRY_POLICY,
        idempotency_key: str | None = OFFLINE_IDEMPOTENCY_KEY,
    ) -> requests.Response:
        return request_client.commit_idempotent_operation(
            payload,
            retry_policy=retry_policy,
            idempotency_key=idempotency_key,
        )

    @allure_step("查询离线上下文响应")
    def query_context(
        self,
        request_client: OfflineFrameworkRequest,
    ) -> requests.Response:
        return request_client.get_context()

    @allure_step("提交离线审计请求")
    def query_audit(
        self,
        request_client: OfflineFrameworkRequest,
        audit_name: str,
    ) -> requests.Response:
        return request_client.get_audit(audit_name)

    @allure_step("删除离线媒体任务")
    def delete_media_task(
        self,
        request_client: OfflineFrameworkRequest,
        task_id: str,
    ) -> requests.Response:
        return request_client.delete_media_task(task_id)

    @allure_step("查询离线响应合同")
    def query_contract(
        self,
        request_client: OfflineFrameworkRequest,
        mode: str,
    ) -> requests.Response:
        return request_client.get_contract(mode)


__all__ = [
    "OFFLINE_POLLING_POLICY",
    "OFFLINE_RETRY_POLICY",
    "OfflineFrameworkTask",
]
