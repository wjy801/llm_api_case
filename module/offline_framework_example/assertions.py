from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import requests

from common import BaseAssertions, allure_step
from module.offline_framework_example.offline_service import OFFLINE_TASK_ID


class OfflineFrameworkAssertions(BaseAssertions):
    @allure_step("校验离线Echo响应")
    def assert_echo_accepted(
        self,
        response: requests.Response,
        expected_payload: dict[str, Any],
    ) -> requests.Response:
        self.assert_status_code(response, 200)
        self.assert_json_value(response, "$.status", "accepted")
        self.assert_json_value(response, "$.authorization_present", True)
        self.assert_json_value(response, "$.received", expected_payload)
        return response

    @allure_step("校验瞬时失败已被重试挽救")
    def assert_transient_recovered(
        self,
        response: requests.Response,
    ) -> requests.Response:
        self.assert_status_code(response, 200)
        self.assert_json_value(response, "$.status", "ok")
        self.assert_json_value(response, "$.attempt", 2)
        return response

    @allure_step("校验幂等写操作已提交")
    def assert_idempotent_committed(
        self,
        response: requests.Response,
    ) -> requests.Response:
        self.assert_status_code(response, 200)
        self.assert_json_value(response, "$.operation", "offline-write")
        self.assert_json_value(response, "$.status", "committed")
        return response

    @allure_step("校验离线任务ID")
    def assert_task_id(
        self,
        response: requests.Response,
        expected: str = OFFLINE_TASK_ID,
    ) -> requests.Response:
        self.assert_json_path_exists(response, "$.task_id")
        self.assert_json_value(response, "$.task_id", expected)
        return response

    @allure_step("校验离线任务状态")
    def assert_task_status(
        self,
        response: requests.Response,
        expected: str,
    ) -> requests.Response:
        self.assert_json_value(response, "$.status", expected)
        return response

    @allure_step("校验离线审计名称集合")
    def assert_audit_names(
        self,
        responses: Sequence[requests.Response],
        expected: set[str],
    ) -> Sequence[requests.Response]:
        actual: set[str] = set()
        for response in responses:
            self.assert_status_code(response, 200)
            self.assert_json_value(response, "$.status", "recorded")
            self.assert_json_value(response, "$.task_id", OFFLINE_TASK_ID)
            audit_name = response.json().get("audit_name")
            assert isinstance(audit_name, str) and audit_name, (
                "$.audit_name should be a non-empty string, "
                f"actual: {audit_name!r}"
            )
            actual.add(audit_name)
        assert actual == expected, (
            f"Audit name set mismatch: expected {expected!r}, actual {actual!r}"
        )
        return responses
