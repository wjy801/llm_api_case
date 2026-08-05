from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

import requests

from common.base_request import BaseRequest
from common.polling import DEFAULT_MEDIA_POLLING_POLICY, PollingPolicy
from common.runtime_hooks import (
    RuntimeOperationKind,
    RuntimeTrafficRole,
    model_id_from_kwargs,
    operation_scope,
)


if TYPE_CHECKING:
    from common.retry import RetryPolicy


@dataclass(frozen=True)
class MediaGenerationCapability:
    image_generations_path: str = "/v1/images/generations"
    chat_completions_path: str = "/v1/chat/completions"
    media_generations_path: str = "/v1/media/generations"
    media_task_path_template: str = "/v1/media/tasks/{task_id}"
    task_id_field: str = "task_id"
    task_id_aliases: tuple[str, ...] = ("id", "request_id")

    def create_image_generation(
        self,
        request_client: BaseRequest,
        payload: dict[str, Any],
    ) -> requests.Response:
        with operation_scope(
            RuntimeOperationKind.HTTP,
            name="image_generation",
            role=RuntimeTrafficRole.WORKLOAD,
            model_id=model_id_from_kwargs({"json": payload}),
        ):
            return request_client.post(self.image_generations_path, json=payload)

    def create_chat_completion(
        self,
        request_client: BaseRequest,
        payload: dict[str, Any],
    ) -> requests.Response:
        with operation_scope(
            RuntimeOperationKind.HTTP,
            name="chat_completion",
            role=RuntimeTrafficRole.WORKLOAD,
            model_id=model_id_from_kwargs({"json": payload}),
        ):
            return request_client.post(self.chat_completions_path, json=payload)

    def create_media_generation(
        self,
        request_client: BaseRequest,
        payload: dict[str, Any],
    ) -> requests.Response:
        with operation_scope(
            RuntimeOperationKind.HTTP,
            name="media_generation_create",
            role=RuntimeTrafficRole.WORKLOAD,
            model_id=model_id_from_kwargs({"json": payload}),
        ):
            return request_client.post(self.media_generations_path, json=payload)

    def poll_media_generation_result(
        self,
        request_client: BaseRequest,
        task_id: str,
        *,
        poll_interval: float = 2,
        poll_timeout: float | None = None,
        polling_policy: PollingPolicy = DEFAULT_MEDIA_POLLING_POLICY,
        retry_policy: RetryPolicy | None = None,
    ) -> requests.Response:
        with operation_scope(
            RuntimeOperationKind.POLLING,
            name="media_generation_polling",
            role=RuntimeTrafficRole.WORKLOAD,
        ):
            return request_client.poll_get(
                self.media_task_path_template.format(task_id=task_id),
                poll_interval=poll_interval,
                poll_timeout=poll_timeout,
                polling_policy=polling_policy,
                retry_policy=retry_policy,
            )

    def create_and_poll_media_generation(
        self,
        request_client: BaseRequest,
        payload: dict[str, Any],
        *,
        poll_interval: float = 2,
        poll_timeout: float | None = None,
        polling_policy: PollingPolicy = DEFAULT_MEDIA_POLLING_POLICY,
        retry_policy: RetryPolicy | None = None,
        create: Callable[[BaseRequest, dict[str, Any]], requests.Response] | None = None,
        extract_task_id: Callable[[requests.Response], str] | None = None,
        poll: Callable[..., requests.Response] | None = None,
    ) -> requests.Response:
        create_call = create or self.create_media_generation
        extract_call = extract_task_id or self.extract_task_id
        poll_call = poll or self.poll_media_generation_result
        with operation_scope(
            RuntimeOperationKind.ASYNC_TASK,
            name="media_generation",
            role=RuntimeTrafficRole.WORKLOAD,
            model_id=model_id_from_kwargs({"json": payload}),
        ):
            create_response = create_call(request_client, payload)
            task_id = extract_call(create_response)
            return poll_call(
                request_client,
                task_id,
                poll_interval=poll_interval,
                poll_timeout=poll_timeout,
                polling_policy=polling_policy,
                retry_policy=retry_policy,
            )

    def extract_task_id(self, create_response: requests.Response) -> str:
        try:
            response_body = create_response.json()
        except ValueError as exc:
            raise AssertionError(
                f"创建任务响应不是有效 JSON。响应内容：{create_response.text}"
            ) from exc

        if not isinstance(response_body, dict):
            raise AssertionError(
                f"创建任务响应不是 JSON 对象。响应内容：{create_response.text}"
            )

        task_id = response_body.get(self.task_id_field)
        if not task_id:
            task_id = next(
                (
                    response_body.get(alias)
                    for alias in self.task_id_aliases
                    if response_body.get(alias)
                ),
                None,
            )
        assert task_id, (
            f"创建任务响应中未返回 {self.task_id_field}。"
            f"响应内容：{create_response.text}"
        )
        return str(task_id)
