from __future__ import annotations

import pytest

from module.smoke import SMOKE_GET_RETRY_POLICY, SmokeAssertions, SmokeRequest, SmokeTask


pytestmark = pytest.mark.serial


class TestKeyAPI:
    def setup_method(self):
        self.smoke_request = SmokeRequest()
        self.smoke_assertions = SmokeAssertions()
        self.smoke_task = SmokeTask()

    def teardown_method(self):
        self.smoke_request.close()

    def test_account_balance(self):
        self.smoke_task.verify_account_balance_for_billing(
            self.smoke_request,
            self.smoke_assertions,
        )

    def test_model_call_consumption(self):
        chat_response = self.smoke_task.create_chat_completion(
            self.smoke_request,
            self.smoke_task.build_chat_completions_payload(),
        )
        self.smoke_task.query_usage_records_for_billing(
            self.smoke_request,
            model_response=chat_response,
            retry_policy=SMOKE_GET_RETRY_POLICY,
        )

        response = self.smoke_task.create_image_generation(
            self.smoke_request,
            self.smoke_task.build_sync_image_generation_payload(),
        )

        self.smoke_assertions.assert_status_code(response, 200)
