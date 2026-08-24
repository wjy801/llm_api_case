from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json
import os
import time
from typing import TYPE_CHECKING

import requests

from common.base_request import BaseRequest
from common.polling import PollingPolicy
from common.runtime_hooks import (
    RuntimeOperationKind,
    RuntimeTrafficRole,
    operation_scope,
    runtime_metadata,
)


if TYPE_CHECKING:
    from common.retry import RetryPolicy


CHINA_CONTROL_API_KEY_ENV = "CHINA_CONTROL_API_KEY"
OVERSEAS_CONTROL_API_KEY_ENV = "OVERSEAS_CONTROL_API_KEY"
ONEAPI_REQUEST_ID_HEADER = "x-oneapi-request-id"
BALANCE_SETTLEMENT_WAIT_SECONDS = 5
USAGE_RECORD_SETTLEMENT_POLL_INTERVAL_SECONDS = 2
USAGE_RECORD_SETTLEMENT_TIMEOUT_SECONDS = 60
USAGE_RECORD_SETTLEMENT_POLLING_POLICY = PollingPolicy(
    status_json_path="$.data.status",
    pending=frozenset({"queued", "pending", "processing", "running"}),
    success=frozenset(
        {
            "success",
            "succeeded",
            "completed",
            "failed",
            "failure",
            "cancelled",
            "canceled",
        }
    ),
    failure=frozenset(),
    error_json_path=None,
)


@dataclass(frozen=True)
class ControlApiKeyLookup:
    environment_variable: str
    value: str | None

    @property
    def is_configured(self) -> bool:
        return bool(self.value)


@dataclass(frozen=True)
class BillingCapability:
    account_balance_path: str = "/v1/account/balance"
    usage_records_path: str = "/v1/account/usage-records"

    def lookup_control_api_key(
        self,
        *,
        use_china_environment: bool,
        environ: Mapping[str, str] | None = None,
    ) -> ControlApiKeyLookup:
        variable = (
            CHINA_CONTROL_API_KEY_ENV
            if use_china_environment
            else OVERSEAS_CONTROL_API_KEY_ENV
        )
        source = os.environ if environ is None else environ
        value = source.get(variable, "").strip()
        return ControlApiKeyLookup(
            environment_variable=variable,
            value=value or None,
        )

    def wait_for_balance_settlement(self, wait_seconds: float) -> None:
        print(f"wait {wait_seconds}s for prepaid balance settlement")
        time.sleep(wait_seconds)

    def wait_for_usage_record_settlement_by_request_id(
        self,
        request_client: BaseRequest,
        control_api_key: str,
        request_id: str,
        *,
        poll_interval: float = USAGE_RECORD_SETTLEMENT_POLL_INTERVAL_SECONDS,
        poll_timeout: float = USAGE_RECORD_SETTLEMENT_TIMEOUT_SECONDS,
        retry_policy: RetryPolicy | None = None,
    ) -> requests.Response:
        usage_response = request_client.poll_get(
            self.usage_records_path,
            params={"request_id": request_id},
            headers={"Authorization": f"Bearer {control_api_key}"},
            poll_interval=poll_interval,
            poll_timeout=poll_timeout,
            polling_policy=USAGE_RECORD_SETTLEMENT_POLLING_POLICY,
            retry_policy=retry_policy,
            runtime_metadata=runtime_metadata(
                RuntimeOperationKind.POLLING,
                name="usage_record_settlement",
                role=RuntimeTrafficRole.CONTROL,
            ),
        )
        print("settled usage_records response body:")
        print(self.format_response_body(usage_response))
        return usage_response

    def get_account_balance(
        self,
        request_client: BaseRequest,
        control_api_key: str,
        *,
        retry_policy: RetryPolicy | None = None,
    ) -> requests.Response:
        headers = {
            "User-Agent": "api-v1_chat_completions-framework",
            "Accept-Encoding": "gzip, deflate, zstd",
            "Accept": "application/json",
            "Connection": "keep-alive",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {control_api_key}",
        }
        with operation_scope(
            RuntimeOperationKind.HTTP,
            name="account_balance",
            role=RuntimeTrafficRole.CONTROL,
        ):
            return request_client.get(
                self.account_balance_path,
                data="",
                headers=headers,
                retry_policy=retry_policy,
            )

    def query_usage_records_by_request_id(
        self,
        request_client: BaseRequest,
        control_api_key: str,
        request_id: str,
    ) -> requests.Response:
        with operation_scope(
            RuntimeOperationKind.HTTP,
            name="usage_records",
            role=RuntimeTrafficRole.CONTROL,
        ):
            usage_response = request_client.get(
                self.usage_records_path,
                params={"request_id": request_id},
                headers={"Authorization": f"Bearer {control_api_key}"},
            )
        print("usage_records response body:")
        print(self.format_response_body(usage_response))
        return usage_response

    @staticmethod
    def format_response_body(response: requests.Response) -> str:
        try:
            return json.dumps(response.json(), ensure_ascii=False, indent=2)
        except ValueError:
            return response.text

    @staticmethod
    def get_request_id_from_response(response: requests.Response) -> str:
        request_id = response.headers.get(ONEAPI_REQUEST_ID_HEADER, "").strip()
        print(f"{ONEAPI_REQUEST_ID_HEADER}: {request_id}")
        if not request_id:
            raise AssertionError(
                f"Response header {ONEAPI_REQUEST_ID_HEADER} is missing. "
                f"Response headers: {dict(response.headers)}"
            )
        return request_id
