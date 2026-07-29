from __future__ import annotations

from typing import Any

import pytest
import requests

from module.protocol_testing import ProtocolInterceptionAssertions, ProtocolRequest, ProtocolTask
from module.protocol_testing.payloads import build_image_v1_media_generations_payload
from module.protocol_testing.image_model.protocol_interception_cases import (
    ProtocolInterceptionCase,
    load_protocol_interception_cases,
)


def protocol_interception_case_params() -> list[pytest.ParameterSet]:
    cases = load_protocol_interception_cases()
    if not cases:
        return [
            pytest.param(
                ProtocolInterceptionCase(
                    case_id="unconfigured-image-protocol-interception-case",
                    protocol_path="media_generations",
                    header_protocol="openai",
                    body_protocol="image_media",
                    model_id="your-image-model",
                    expected="block",
                ),
                marks=pytest.mark.skip(reason="请先配置 image_model/protocol_interception.csv"),
                id="unconfigured-image-protocol-interception-case",
            )
        ]
    return [pytest.param(case, id=case.case_id) for case in cases]


def build_protocol_interception_payload(case: ProtocolInterceptionCase) -> dict[str, Any]:
    if case.body_protocol == "image_media":
        return build_image_v1_media_generations_payload(case.model_id)
    raise ValueError(f"Unsupported body protocol: {case.body_protocol!r}")


class TestImageModelProtocolInterception:
    def setup_method(self):
        self.protocol_request = ProtocolRequest()
        self.protocol_assertions = ProtocolInterceptionAssertions()
        self.protocol_task = ProtocolTask()

    def teardown_method(self):
        self.protocol_request.close()

    @pytest.mark.parametrize("case", protocol_interception_case_params())
    def test_image_model_protocol_interception(self, case: ProtocolInterceptionCase):
        payload = build_protocol_interception_payload(case)
        headers = self._build_headers_by_protocol(case.header_protocol)
        response = self._create_by_protocol_path(case.protocol_path, payload, headers=headers)

        if case.expected == "allow":
            self.protocol_assertions.assert_protocol_interception_allowed(response, case_id=case.case_id)
            return

        self.protocol_assertions.assert_protocol_interception_blocked(response, case_id=case.case_id)

    def _create_by_protocol_path(
        self,
        protocol_path: str,
        payload: dict[str, Any],
        *,
        headers: dict[str, str] | None,
    ) -> requests.Response:
        if protocol_path == "media_generations":
            return self.protocol_task.create_media_generation(
                self.protocol_request,
                payload,
                headers=headers,
            )
        if protocol_path == "images_generations":
            return self.protocol_task.create_image_generation(
                self.protocol_request,
                payload,
                headers=headers,
            )
        if protocol_path == "images_edits":
            return self.protocol_request.post(
                self.protocol_request.image_edits_path,
                json=payload,
                headers=headers,
            )
        if protocol_path == "openai_chat_completions":
            return self.protocol_task.create_chat_completion(
                self.protocol_request,
                payload,
                headers=headers,
            )
        if protocol_path == "openai_responses":
            return self.protocol_task.create_response(
                self.protocol_request,
                payload,
                headers=headers,
            )
        raise ValueError(f"Unsupported protocol path: {protocol_path!r}")

    def _build_headers_by_protocol(self, header_protocol: str) -> dict[str, str] | None:
        if header_protocol == "openai":
            return None
        raise ValueError(f"Unsupported header protocol: {header_protocol!r}")
