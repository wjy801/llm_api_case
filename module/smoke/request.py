from __future__ import annotations

from typing import Any

import requests

from common import BaseRequest
from common.polling import DEFAULT_MEDIA_POLLING_POLICY, PollingPolicy


class SmokeRequest(BaseRequest):
    chat_completions_path = "/v1/chat/completions"
    image_generations_path = "/v1/images/generations"
    account_balance_path = "/v1/account/balance"
    usage_records_path = "/v1/account/usage-records"
    media_generations_path = "/v1/media/generations"
    media_task_path_template = "/v1/media/tasks/{task_id}"

    def create_chat_completion(self, payload: dict[str, Any]) -> requests.Response:
        return self.post(self.chat_completions_path, json=payload)

    def create_stream_chat_completion(self, payload: dict[str, Any]) -> requests.Response:
        return self.post(
            self.chat_completions_path,
            json=payload,
            headers={"Accept": "text/event-stream"},
            stream=True,
            _attach_log=False,
        )

    def create_image_generation(self, payload: dict[str, Any]) -> requests.Response:
        return self.post(self.image_generations_path, json=payload)

    def create_media_generation(self, payload: dict[str, Any]) -> requests.Response:
        return self.post(self.media_generations_path, json=payload)

    def get_media_generation_task(self, task_id: str) -> requests.Response:
        return self.get(self.media_task_path_template.format(task_id=task_id))

    def poll_media_generation_result(
        self,
        task_id: str,
        *,
        poll_interval: float = 2,
        poll_timeout: float | None = None,
        polling_policy: PollingPolicy = DEFAULT_MEDIA_POLLING_POLICY,
    ) -> requests.Response:
        return self.poll_get(
            self.media_task_path_template.format(task_id=task_id),
            poll_interval=poll_interval,
            poll_timeout=poll_timeout,
            polling_policy=polling_policy,
        )

    def get_account_balance(self, control_api_key: str) -> requests.Response:
        self.update_headers(
            {
                "User-Agent": "api-v1_chat_completions-framework",
                "Accept-Encoding": "gzip, deflate, zstd",
                "Accept": "application/json",
                "Connection": "keep-alive",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {control_api_key}",
            }
        )
        try:
            return self.get(self.account_balance_path, data="")
        finally:
            self.reset_headers()

    def get_usage_records(self, control_api_key: str, request_id: str) -> requests.Response:
        self.update_headers({"Authorization": f"Bearer {control_api_key}"})
        try:
            return self.get(
                self.usage_records_path,
                params={
                    "request_id": request_id,
                    "": "",
                },
            )
        finally:
            self.reset_headers()
