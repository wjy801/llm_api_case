from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
import requests

from config import USE_CHINA_ENVIRONMENT
from common.base_decorators import allure_step
from common.base_request import BaseRequest
from common.polling import DEFAULT_MEDIA_POLLING_POLICY, PollingPolicy
from common.task_capabilities import (
    BALANCE_SETTLEMENT_WAIT_SECONDS,
    CHINA_CONTROL_API_KEY_ENV,
    ONEAPI_REQUEST_ID_HEADER,
    OVERSEAS_CONTROL_API_KEY_ENV,
    USAGE_RECORD_SETTLEMENT_POLL_INTERVAL_SECONDS,
    USAGE_RECORD_SETTLEMENT_POLLING_POLICY,
    USAGE_RECORD_SETTLEMENT_TIMEOUT_SECONDS,
    BillingCapability,
    MediaGenerationCapability,
)


if TYPE_CHECKING:
    from common.retry import RetryPolicy


class BaseTask:
    """Legacy task facade. New domain behavior belongs in Task capabilities."""

    image_generations_path = "/v1/images/generations"
    chat_completions_path = "/v1/chat/completions"
    media_generations_path = "/v1/media/generations"
    media_task_path_template = "/v1/media/tasks/{task_id}"
    account_balance_path = "/v1/account/balance"
    usage_records_path = "/v1/account/usage-records"
    task_id_field = "task_id"
    task_id_aliases = ("id", "request_id")

    def _media_capability(self) -> MediaGenerationCapability:
        return MediaGenerationCapability(
            image_generations_path=self.image_generations_path,
            chat_completions_path=self.chat_completions_path,
            media_generations_path=self.media_generations_path,
            media_task_path_template=self.media_task_path_template,
            task_id_field=self.task_id_field,
            task_id_aliases=tuple(self.task_id_aliases),
        )

    def _billing_capability(self) -> BillingCapability:
        return BillingCapability(
            account_balance_path=self.account_balance_path,
            usage_records_path=self.usage_records_path,
        )

    @allure_step("同步图片任务调用：/v1/images/generations")
    def create_image_generation(
        self,
        request_client: BaseRequest,
        payload: dict[str, Any],
    ) -> requests.Response:
        return self._media_capability().create_image_generation(request_client, payload)

    @allure_step("文本模型对话调用：/v1/chat/completions")
    def create_chat_completion(
        self,
        request_client: BaseRequest,
        payload: dict[str, Any],
    ) -> requests.Response:
        return self._media_capability().create_chat_completion(request_client, payload)

    @allure_step("异步媒体任务创建：/v1/media/generations")
    def create_media_generation(
        self,
        request_client: BaseRequest,
        payload: dict[str, Any],
    ) -> requests.Response:
        return self._media_capability().create_media_generation(request_client, payload)

    @allure_step("轮询媒体生成结果: {task_id}")
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
        return self._media_capability().poll_media_generation_result(
            request_client,
            task_id,
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
    ) -> requests.Response:
        return self._media_capability().create_and_poll_media_generation(
            request_client,
            payload,
            poll_interval=poll_interval,
            poll_timeout=poll_timeout,
            polling_policy=polling_policy,
            retry_policy=retry_policy,
            create=self.create_media_generation,
            extract_task_id=self.extract_task_id,
            poll=self.poll_media_generation_result,
        )

    def extract_task_id(self, create_response: requests.Response) -> str:
        return self._media_capability().extract_task_id(create_response)

    @allure_step("查询账户余额")
    def query_account_balance_for_billing(
        self,
        request_client: BaseRequest,
    ) -> requests.Response:
        return self.get_account_balance(
            request_client,
            self.get_required_control_api_key(),
        )

    @allure_step("等待账单结算后查询账户余额")
    def query_account_balance_after_settlement_for_billing(
        self,
        request_client: BaseRequest,
        wait_seconds: float = BALANCE_SETTLEMENT_WAIT_SECONDS,
    ) -> requests.Response:
        self._billing_capability().wait_for_balance_settlement(wait_seconds)
        return self.query_account_balance_for_billing(request_client)

    @allure_step("查询模型用量记录")
    def query_usage_records_for_billing(
        self,
        request_client: BaseRequest,
        *,
        model_response: requests.Response | None = None,
        request_id: str | None = None,
    ) -> requests.Response:
        if model_response is not None:
            return self.query_usage_records_by_model_response_for_billing(
                request_client,
                model_response,
            )
        if request_id is not None:
            return self.query_usage_records_by_request_id_for_billing(
                request_client,
                request_id,
            )
        raise ValueError("model_response or request_id is required")

    @allure_step("按对话响应查询模型用量记录")
    def query_usage_records_by_chat_response_for_billing(
        self,
        request_client: BaseRequest,
        chat_response: requests.Response,
    ) -> requests.Response:
        return self.query_usage_records_by_model_response_for_billing(
            request_client,
            chat_response,
        )

    @allure_step("按模型响应查询模型用量记录")
    def query_usage_records_by_model_response_for_billing(
        self,
        request_client: BaseRequest,
        model_response: requests.Response,
    ) -> requests.Response:
        return self.query_usage_records_by_request_id_for_billing(
            request_client,
            self.get_request_id_from_response(model_response),
        )

    @allure_step("按 request_id 查询模型用量记录: {request_id}")
    def query_usage_records_by_request_id_for_billing(
        self,
        request_client: BaseRequest,
        request_id: str,
    ) -> requests.Response:
        return self.wait_for_usage_record_settlement_by_request_id(
            request_client,
            self.get_required_control_api_key(),
            request_id,
        )

    @allure_step("等待模型用量记录结算: {request_id}")
    def wait_for_usage_record_settlement_by_request_id(
        self,
        request_client: BaseRequest,
        control_api_key: str,
        request_id: str,
        *,
        poll_interval: float = USAGE_RECORD_SETTLEMENT_POLL_INTERVAL_SECONDS,
        poll_timeout: float = USAGE_RECORD_SETTLEMENT_TIMEOUT_SECONDS,
    ) -> requests.Response:
        return self._billing_capability().wait_for_usage_record_settlement_by_request_id(
            request_client,
            control_api_key,
            request_id,
            poll_interval=poll_interval,
            poll_timeout=poll_timeout,
        )

    @allure_step("调用账户余额接口")
    def get_account_balance(
        self,
        request_client: BaseRequest,
        control_api_key: str,
    ) -> requests.Response:
        return self._billing_capability().get_account_balance(
            request_client,
            control_api_key,
        )

    @allure_step("查询模型用量记录")
    def get_usage_records(
        self,
        request_client: BaseRequest,
        control_api_key: str,
        request_id: str,
    ) -> requests.Response:
        return self.query_usage_records_by_request_id(
            request_client,
            control_api_key,
            request_id,
        )

    @allure_step("调用模型用量记录接口: {request_id}")
    def query_usage_records_by_request_id(
        self,
        request_client: BaseRequest,
        control_api_key: str,
        request_id: str,
    ) -> requests.Response:
        return self._billing_capability().query_usage_records_by_request_id(
            request_client,
            control_api_key,
            request_id,
        )

    @staticmethod
    def format_response_body(response: requests.Response) -> str:
        return BillingCapability.format_response_body(response)

    @staticmethod
    def get_request_id_from_response(response: requests.Response) -> str:
        return BillingCapability.get_request_id_from_response(response)

    @staticmethod
    def get_required_control_api_key() -> str:
        lookup = BillingCapability().lookup_control_api_key(
            use_china_environment=USE_CHINA_ENVIRONMENT,
        )
        if not lookup.is_configured:
            pytest.skip(
                f"Please configure {lookup.environment_variable} in .env first."
            )
        assert lookup.value is not None
        return lookup.value
