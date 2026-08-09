from __future__ import annotations

from typing import Any

import requests

from common import BaseRequest, BaseTask, PollingPolicy, allure_step


MINIMAX_H3_POLLING_POLICY = PollingPolicy(
    status_json_path="$.task.status",
    pending=frozenset({"queued", "running"}),
    success=frozenset({"succeeded"}),
    failure=frozenset({"failed", "cancelled"}),
    result_json_path="$.task.content.url",
    error_json_path="$.task.error",
)


class VideoTask(BaseTask):
    @allure_step("Create and wait for MiniMax-H3 video: {scenario_name}")
    def create_minimax_h3_generation(
        self,
        request_client: BaseRequest,
        payload: dict[str, Any],
        *,
        scenario_name: str,
        poll_timeout: float = 1500,
    ) -> requests.Response:
        return self.create_and_poll_media_generation(
            request_client,
            payload,
            poll_timeout=poll_timeout,
            polling_policy=MINIMAX_H3_POLLING_POLICY,
        )

    @allure_step("创建并等待豆包 Seedance 2.0 Mini 视频：{scenario_name}")
    def create_doubao_seedance_2_0_mini_generation(
        self,
        request_client: BaseRequest,
        payload: dict[str, Any],
        *,
        scenario_name: str,
        poll_timeout: float = 1500,
    ) -> requests.Response:
        return self.create_and_poll_media_generation(
            request_client,
            payload,
            poll_timeout=poll_timeout,
        )

    @allure_step("创建并等待豆包 Seedance 2.5 视频：{scenario_name}")
    def create_doubao_seedance_2_5_generation(
        self,
        request_client: BaseRequest,
        payload: dict[str, Any],
        *,
        scenario_name: str,
        poll_timeout: float = 1500,
    ) -> requests.Response:
        return self.create_and_poll_media_generation(
            request_client,
            payload,
            poll_timeout=poll_timeout,
        )
