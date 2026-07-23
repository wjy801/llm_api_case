from __future__ import annotations

import json
import os
import time
from typing import Any

import pytest
import requests

from config import USE_CHINA_ENVIRONMENT
from common.base_decorators import allure_step
from common.base_request import BaseRequest


CHINA_CONTROL_API_KEY_ENV = "CHINA_CONTROL_API_KEY"
OVERSEAS_CONTROL_API_KEY_ENV = "OVERSEAS_CONTROL_API_KEY"
ONEAPI_REQUEST_ID_HEADER = "x-oneapi-request-id"
BALANCE_SETTLEMENT_WAIT_SECONDS = 30


class BaseTask:
    """通用业务任务封装，负责把用例层操作映射到基础请求能力。"""

    image_generations_path = "/v1/images/generations"
    chat_completions_path = "/v1/chat/completions"
    media_generations_path = "/v1/media/generations"
    media_task_path_template = "/v1/media/tasks/{task_id}"
    account_balance_path = "/v1/account/balance"
    usage_records_path = "/v1/account/usage-records"
    task_id_field = "task_id"

    @allure_step("同步图片任务调用：/v1/images/generations")
    def create_image_generation(
        self,
        request_client: BaseRequest,
        payload: dict[str, Any],
    ) -> requests.Response:
        """调用同步图片生成接口。

        Args:
            request_client: 继承 `BaseRequest` 的请求客户端，提供 `post()` 能力。
            payload: `POST /v1/images/generations` 的请求体，通常包含模型、提示词和图片生成参数。

        Returns:
            接口原始响应，响应体通常直接包含生成结果，例如 `data[0].url` 或 `data[0].b64_json`。
        """
        return request_client.post(self.image_generations_path, json=payload)

    @allure_step("文本模型对话调用：/v1/chat/completions")
    def create_chat_completion(
        self,
        request_client: BaseRequest,
        payload: dict[str, Any],
    ) -> requests.Response:
        """调用对话补全接口。

        Args:
            request_client: 继承 `BaseRequest` 的请求客户端，提供 `post()` 能力。
            payload: `POST /v1/chat/completions` 的请求体，通常包含 `model`、`messages` 等字段。

        Returns:
            接口原始响应，响应体通常包含 `choices`、`usage` 等对话补全结果字段。
        """
        return request_client.post(self.chat_completions_path, json=payload)

    @allure_step("异步媒体任务创建：/v1/media/generations")
    def create_media_generation(
        self,
        request_client: BaseRequest,
        payload: dict[str, Any],
    ) -> requests.Response:
        """创建异步媒体生成任务。

        Args:
            request_client: 继承 `BaseRequest` 的请求客户端，提供 `post()` 能力。
            payload: `POST /v1/media/generations` 的请求体，可用于图片、视频等异步媒体生成任务。

        Returns:
            创建任务接口的原始响应，后续复合流程会从该响应中提取 `task_id`。
        """
        return request_client.post(self.media_generations_path, json=payload)

    @allure_step("轮询媒体生成结果: {task_id}")
    def poll_media_generation_result(
        self,
        request_client: BaseRequest,
        task_id: str,
        *,
        poll_interval: float = 2,
        poll_timeout: float | None = None,
        success_json_path: str = "$.result.urls",
        failure_json_path: str | None = "$.error",
    ) -> requests.Response:
        """轮询异步媒体生成任务结果。

        Args:
            request_client: 继承 `BaseRequest` 的请求客户端，提供 `poll_get()` 能力。
            task_id: 创建异步媒体任务后返回的任务 ID，用于拼接 `/v1/media/tasks/{task_id}`。
            poll_interval: 每次 GET 轮询之间的等待秒数。
            poll_timeout: 总轮询超时时间；为 `None` 时使用 `BaseRequest` 中的环境超时配置。
            success_json_path: 成功判断 JSONPath；取到非空值时返回最终响应。
            failure_json_path: 失败判断 JSONPath；取到非空值时让用例失败；为 `None` 时不做失败 JSONPath 判断。

        Returns:
            满足成功条件的最终轮询响应。
        """
        return request_client.poll_get(
            self.media_task_path_template.format(task_id=task_id),
            poll_interval=poll_interval,
            poll_timeout=poll_timeout,
            success_json_path=success_json_path,
            failure_json_path=failure_json_path,
        )

    def create_and_poll_media_generation(
        self,
        request_client: BaseRequest,
        payload: dict[str, Any],
        *,
        poll_interval: float = 2,
        poll_timeout: float | None = None,
        success_json_path: str = "$.result.urls",
        failure_json_path: str | None = "$.error",
    ) -> requests.Response:
        """创建异步媒体生成任务并轮询结果。

        Args:
            request_client: 继承 `BaseRequest` 的请求客户端，提供 `post()` 和 `poll_get()` 能力。
            payload: `POST /v1/media/generations` 的请求体。
            poll_interval: 每次 GET 轮询之间的等待秒数。
            poll_timeout: 总轮询超时时间；为 `None` 时使用 `BaseRequest` 中的环境超时配置。
            success_json_path: 成功判断 JSONPath；取到非空值时返回最终响应。
            failure_json_path: 失败判断 JSONPath；取到非空值时让用例失败；为 `None` 时不做失败 JSONPath 判断。

        Returns:
            满足成功条件的最终轮询响应。
        """
        create_response = self.create_media_generation(request_client, payload)
        task_id = self.extract_task_id(create_response)
        return self.poll_media_generation_result(
            request_client,
            task_id,
            poll_interval=poll_interval,
            poll_timeout=poll_timeout,
            success_json_path=success_json_path,
            failure_json_path=failure_json_path,
        )

    def extract_task_id(self, create_response: requests.Response) -> str:
        """从异步任务创建响应中提取任务 ID。

        Args:
            create_response: 创建异步媒体任务接口的原始响应。

        Returns:
            字符串形式的任务 ID。

        Raises:
            AssertionError: 响应体不是 JSON 对象，或响应体中不存在 `task_id_field` 对应字段。
        """
        try:
            response_body = create_response.json()
        except ValueError as exc:
            raise AssertionError(f"创建任务响应不是有效 JSON。响应内容：{create_response.text}") from exc

        if not isinstance(response_body, dict):
            raise AssertionError(f"创建任务响应不是 JSON 对象。响应内容：{create_response.text}")

        task_id = response_body.get(self.task_id_field)
        assert task_id, f"创建任务响应中未返回 {self.task_id_field}。响应内容：{create_response.text}"
        return str(task_id)

    @allure_step("查询账户余额")
    def query_account_balance_for_billing(self, request_client: BaseRequest) -> requests.Response:
        """使用控制台密钥查询当前账户余额。

        Args:
            request_client: 继承 `BaseRequest` 的请求客户端，提供 `get()`、`update_headers()` 和 `reset_headers()` 能力。

        Returns:
            账户余额接口的原始响应。
        """
        control_api_key = self.get_required_control_api_key()
        return self.get_account_balance(request_client, control_api_key)

    @allure_step("等待账单结算后查询账户余额")
    def query_account_balance_after_settlement_for_billing(
        self,
        request_client: BaseRequest,
        wait_seconds: float = BALANCE_SETTLEMENT_WAIT_SECONDS,
    ) -> requests.Response:
        """等待预付费账单结算后再查询账户余额。"""
        print(f"wait {wait_seconds}s for prepaid balance settlement")
        time.sleep(wait_seconds)
        return self.query_account_balance_for_billing(request_client)

    @allure_step("查询模型用量记录")
    def query_usage_records_for_billing(
        self,
        request_client: BaseRequest,
        *,
        model_response: requests.Response | None = None,
        request_id: str | None = None,
    ) -> requests.Response:
        """按已有模型响应或指定 request id 查询模型用量记录。

        Args:
            request_client: 继承 `BaseRequest` 的请求客户端。
            model_response: 已完成的模型调用响应；传入后会从响应头提取 request id。
            request_id: 已知的 request id；传入后直接查询用量记录。

        Returns:
            用量记录接口的原始响应。
        """
        if model_response is not None:
            return self.query_usage_records_by_model_response_for_billing(request_client, model_response)
        if request_id is not None:
            return self.query_usage_records_by_request_id_for_billing(request_client, request_id)
        raise ValueError("model_response or request_id is required")

    @allure_step("按对话响应查询模型用量记录")
    def query_usage_records_by_chat_response_for_billing(
        self,
        request_client: BaseRequest,
        chat_response: requests.Response,
    ) -> requests.Response:
        """从对话响应中提取 request id 并查询模型用量记录。"""
        return self.query_usage_records_by_model_response_for_billing(request_client, chat_response)

    @allure_step("按模型响应查询模型用量记录")
    def query_usage_records_by_model_response_for_billing(
        self,
        request_client: BaseRequest,
        model_response: requests.Response,
    ) -> requests.Response:
        """从模型响应中提取 request id 并查询模型用量记录。"""
        control_api_key = self.get_required_control_api_key()
        request_id = self.get_request_id_from_response(model_response)
        return self.query_usage_records_by_request_id(request_client, control_api_key, request_id)

    @allure_step("按 request_id 查询模型用量记录: {request_id}")
    def query_usage_records_by_request_id_for_billing(
        self,
        request_client: BaseRequest,
        request_id: str,
    ) -> requests.Response:
        """使用指定 request id 查询模型用量记录。"""
        control_api_key = self.get_required_control_api_key()
        return self.query_usage_records_by_request_id(request_client, control_api_key, request_id)

    @allure_step("调用账户余额接口")
    def get_account_balance(self, request_client: BaseRequest, control_api_key: str) -> requests.Response:
        """使用指定控制台密钥调用账户余额接口。

        Args:
            request_client: 继承 `BaseRequest` 的请求客户端。
            control_api_key: 控制台接口密钥，用于覆盖当前请求的 Authorization。

        Returns:
            `GET /v1/account/balance` 的原始响应。
        """
        request_client.update_headers(
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
            return request_client.get(self.account_balance_path, data="")
        finally:
            request_client.reset_headers()

    @allure_step("查询模型用量记录")
    def get_usage_records(
        self,
        request_client: BaseRequest,
        control_api_key: str,
        request_id: str,
    ) -> requests.Response:
        """使用控制台密钥和 request id 查询模型用量记录。"""
        return self.query_usage_records_by_request_id(request_client, control_api_key, request_id)

    @allure_step("调用模型用量记录接口: {request_id}")
    def query_usage_records_by_request_id(
        self,
        request_client: BaseRequest,
        control_api_key: str,
        request_id: str,
    ) -> requests.Response:
        """使用控制台密钥和 request id 调用用量记录接口。"""
        request_client.update_headers({"Authorization": f"Bearer {control_api_key}"})
        try:
            usage_response = request_client.get(
                self.usage_records_path,
                params={
                    "request_id": request_id,
                    "": "",
                },
            )
        finally:
            request_client.reset_headers()

        print("usage_records response body:")
        print(self.format_response_body(usage_response))
        return usage_response

    @staticmethod
    def format_response_body(response: requests.Response) -> str:
        """把响应体格式化为便于日志输出的文本。"""
        try:
            return json.dumps(response.json(), ensure_ascii=False, indent=2)
        except ValueError:
            return response.text

    @staticmethod
    def get_request_id_from_response(response: requests.Response) -> str:
        """从模型响应头中提取用量查询所需的 request id。"""
        request_id = response.headers.get(ONEAPI_REQUEST_ID_HEADER, "").strip()
        print(f"{ONEAPI_REQUEST_ID_HEADER}: {request_id}")
        if not request_id:
            raise AssertionError(
                f"Response header {ONEAPI_REQUEST_ID_HEADER} is missing. "
                f"Response headers: {dict(response.headers)}"
            )
        return request_id

    @staticmethod
    def get_required_control_api_key() -> str:
        """读取当前环境对应的控制台接口密钥，未配置时跳过用例。"""
        control_api_key_env = CHINA_CONTROL_API_KEY_ENV if USE_CHINA_ENVIRONMENT else OVERSEAS_CONTROL_API_KEY_ENV
        control_api_key = os.getenv(control_api_key_env, "").strip()
        if not control_api_key:
            pytest.skip(f"Please configure {control_api_key_env} in .env first.")
        return control_api_key
