from __future__ import annotations

from typing import Any

import requests

from common import BaseRequest
from common.capture import CapturePolicy
from common.retry import RetryPolicy
from common.runtime_hooks import (
    RuntimeOperationKind,
    RuntimeTrafficRole,
    runtime_metadata,
)
from config import Settings
from module.offline_framework_example.offline_service import (
    OFFLINE_API_KEY,
    assert_loopback_url,
)


class OfflineFrameworkRequest(BaseRequest):
    echo_path = "/v1/echo"
    transient_path = "/v1/transient"
    idempotent_operation_path = "/v1/idempotent-operation"
    media_generations_path = "/v1/media/generations"
    media_task_path_template = "/v1/media/tasks/{task_id}"
    context_path = "/v1/context"
    audit_path = "/v1/audit"
    contract_path_template = "/v1/contracts/{mode}"

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 1,
        capture_policy: CapturePolicy | None = None,
    ) -> None:
        assert_loopback_url(base_url)
        offline_config = Settings(
            timeout=timeout,
            generate_allure_report=False,
            generate_history_report=False,
            history_report_keep_limit=1,
            base_url=base_url.rstrip("/"),
            api_key=OFFLINE_API_KEY,
            environment_name="offline",
        )
        super().__init__(config=offline_config, capture_policy=capture_policy)
        self.session.trust_env = False

    def echo(self, payload: dict[str, Any]) -> requests.Response:
        return self.post(
            self.echo_path,
            json=payload,
            runtime_metadata=runtime_metadata(
                RuntimeOperationKind.HTTP,
                name="offline_echo",
                role=RuntimeTrafficRole.WORKLOAD,
            ),
        )

    def get_transient(self, retry_policy: RetryPolicy) -> requests.Response:
        return self.get(
            self.transient_path,
            retry_policy=retry_policy,
            runtime_metadata=runtime_metadata(
                RuntimeOperationKind.HTTP,
                name="offline_transient_request",
                role=RuntimeTrafficRole.WORKLOAD,
            ),
        )

    def commit_idempotent_operation(
        self,
        payload: dict[str, Any],
        *,
        retry_policy: RetryPolicy,
        idempotency_key: str | None = None,
    ) -> requests.Response:
        headers = (
            {"Idempotency-Key": idempotency_key}
            if idempotency_key is not None
            else None
        )
        return self.post(
            self.idempotent_operation_path,
            json=payload,
            headers=headers,
            retry_policy=retry_policy,
            runtime_metadata=runtime_metadata(
                RuntimeOperationKind.HTTP,
                name="offline_idempotent_operation",
                role=RuntimeTrafficRole.WORKLOAD,
            ),
        )

    def get_context(self) -> requests.Response:
        return self.get(
            self.context_path,
            runtime_metadata=runtime_metadata(
                RuntimeOperationKind.HTTP,
                name="offline_context_query",
                role=RuntimeTrafficRole.CONTROL,
            ),
        )

    def get_audit(self, audit_name: str) -> requests.Response:
        return self.get(
            self.audit_path,
            headers={"X-Audit-Name": audit_name},
            runtime_metadata=runtime_metadata(
                RuntimeOperationKind.HTTP,
                name="offline_audit_query",
                role=RuntimeTrafficRole.CONTROL,
            ),
        )

    def delete_media_task(self, task_id: str) -> requests.Response:
        return self.delete(
            self.media_task_path_template.format(task_id=task_id),
            runtime_metadata=runtime_metadata(
                RuntimeOperationKind.HTTP,
                name="offline_task_cleanup",
                role=RuntimeTrafficRole.CONTROL,
            ),
        )

    def get_contract(self, mode: str) -> requests.Response:
        return self.get(
            self.contract_path_template.format(mode=mode),
            runtime_metadata=runtime_metadata(
                RuntimeOperationKind.HTTP,
                name="offline_contract_query",
                role=RuntimeTrafficRole.CONTROL,
            ),
        )
