from __future__ import annotations

from typing import Any

import pytest

from module.smoke import SmokeAssertions, SmokeRequest, SmokeTask


MODEL_ID_STRICT_VALIDATION_CASES: list[dict[str, Any]] = [
    {
        "case_id": "accept-supported-image-model-id",
        "model_id": "gpt-image-2",
        "expected_status_code": 200,
        "expected_json_values": [
            {"json_path": "$.code", "expected": 200},
            {"json_path": "$.model", "expected": "gpt-image-2"},
            {"json_path": "$.status", "expected": "queued"},
        ],
    },
    {
        "case_id": "reject-unknown-model-id-with-valid-prefix",
        "model_id": "wan2.7-image111",
        "expected_status_code": 404,
        "expected_json_values": [
            {"json_path": "$.error.code", "expected": "model_not_found"},
            {"json_path": "$.error.message", "expected": "模型 wan2.7-image111 不存在，请检查 model 参数"},
            {"json_path": "$.error.type", "expected": "invalid_request_error"},
        ],
    },
    {
        "case_id": "reject-model-id-with-special-characters",
        "model_id": "&^*&(&(",
        "expected_status_code": 404,
        "expected_json_values": [
            {"json_path": "$.error.code", "expected": "model_not_found"},
            {"json_path": "$.error.message", "expected": "模型 &^*&(&( 不存在，请检查 model 参数"},
            {"json_path": "$.error.type", "expected": "invalid_request_error"},
        ],
    },
    {
        "case_id": "reject-empty-model-id",
        "model_id": "",
        "expected_status_code": 400,
        "expected_json_values": [
            {"json_path": "$.error.message", "expected": "model is required"},
            {"json_path": "$.error.type", "expected": "api_error"},
        ],
    },
    {
        "case_id": "reject-not-enabled-non-image-model-id",
        "model_id": "qwen3.5-plus",
        "expected_status_code": 404,
        "expected_json_values": [
            {"json_path": "$.error.code", "expected": "model_not_enabled"},
            {"json_path": "$.error.message", "expected": "模型 qwen3.5-plus 已下架或未开通，请更换其他模型"},
            {"json_path": "$.error.type", "expected": "invalid_request_error"},
        ],
    },
    # {
    #     "case_id": "accept-model-id-with-surrounding-spaces",
    #     "model_id": "    gpt-image-2   ",
    #     "expected_status_code": 200,
    #     "expected_json_values": [
    #         {"json_path": "$.code", "expected": 200},
    #         {"json_path": "$.model", "expected": "gpt-image-2"},
    #         {"json_path": "$.status", "expected": "queued"},
    #     ],
    # },
    {
        "case_id": "accept-canonical-image-model-id",
        "model_id": "gpt-image-2",
        "expected_status_code": 200,
        "expected_json_values": [
            {"json_path": "$.code", "expected": 200},
            {"json_path": "$.model", "expected": "gpt-image-2"},
            {"json_path": "$.status", "expected": "queued"},
        ],
    },
    {
        "case_id": "reject-uppercase-model-id-variant",
        "model_id": "WAN2.7-IMAGE",
        "expected_status_code": 404,
        "expected_json_values": [
            {"json_path": "$.error.code", "expected": "model_not_found"},
            {"json_path": "$.error.message", "expected": "模型 WAN2.7-IMAGE 不存在，请检查 model 参数"},
            {"json_path": "$.error.type", "expected": "invalid_request_error"},
        ],
    },
    {
        "case_id": "accept-repeated-canonical-image-model-id",
        "model_id": "gpt-image-2",
        "expected_status_code": 200,
        "expected_json_values": [
            {"json_path": "$.code", "expected": 200},
            {"json_path": "$.model", "expected": "gpt-image-2"},
            {"json_path": "$.status", "expected": "queued"},
        ],
    },
    {
        "case_id": "reject-overlength-model-id",
        "model_id": "mdl_20260722_alpha_v1_A9xK3mP7qL2nZ8rT5yU1bC6dE4fG0hJ9kL8mN7pQ6rS5tU4vW3xY2zA1B0C9D8E7F6G5H4I3J2K1L0M9N8O7P6Q5R4S3T2U1V0W9X8Y7Z6_aBcDeFgHiJkLmNoPqRsTuVwXyZ0123456789_alphaBetaGammaDeltaEpsilonZetaEtaThetaIotaKappaLambdaMuNuXiOmicronPiRhoSigmaTauUpsilonPhiChiPsiOmega_2026_07_22_segment_0001_segment_0002_segment_0003_segment_0004_segment_0005_segment_0006_segment_0007_segment_0008_segment_0009_segment_0010_extra_payload_for_api_boundary_testing_and_database_field_length_validation_case_end",
        "expected_status_code": 404,
        "expected_json_values": [
            {"json_path": "$.error.code", "expected": "model_not_found"},
            {"json_path": "$.error.message", "expected": "模型 mdl_20260722_alpha_v1_A9xK3mP7qL2nZ8rT5yU1bC6dE4fG0hJ9kL8mN7pQ6rS5tU4vW3xY2zA1B0C9D8E7F6G5H4I3J2K1L0M9N8O7P6Q5R4S3T2U1V0W9X8Y7Z6_aBcDeFgHiJkLmNoPqRsTuVwXyZ0123456789_alphaBetaGammaDeltaEpsilonZetaEtaThetaIotaKappaLambdaMuNuXiOmicronPiRhoSigmaTauUpsilonPhiChiPsiOmega_2026_07_22_segment_0001_segment_0002_segment_0003_segment_0004_segment_0005_segment_0006_segment_0007_segment_0008_segment_0009_segment_0010_extra_payload_for_api_boundary_testing_and_database_field_length_validation_case_end 不存在，请检查 model 参数"},
            {"json_path": "$.error.type", "expected": "invalid_request_error"},
        ],
    },
]


def build_image_generation_payload(model_id: str) -> dict[str, Any]:
    return {
        "model": model_id,
        "prompt": "生成一个花园，中间有一个花圃",
        "n": 1,
        "size": "1024x1024",
        "quality": "medium",
        "background": "auto",
        "output_format": "png",
        "response_format": "url",
        "user": "api_frame",
    }


class TestModelIDStrictValidation:
    def setup_method(self):
        self.smoke_request = SmokeRequest()
        self.smoke_assertions = SmokeAssertions()
        self.smoke_task = SmokeTask()

    def teardown_method(self):
        self.smoke_request.close()

    @pytest.mark.parametrize(
        "case",
        [pytest.param(case, id=case["case_id"]) for case in MODEL_ID_STRICT_VALIDATION_CASES],
    )
    def test_media_generation_rejects_unknown_model_id(self, case: dict[str, Any]):
        model_id = case["model_id"]
        payload = build_image_generation_payload(model_id)

        response = self.smoke_task.create_media_generation(self.smoke_request, payload)

        self.smoke_assertions.assert_status_code(response, case["expected_status_code"])
        for assertion in case["expected_json_values"]:
            self.smoke_assertions.assert_json_value(response, assertion["json_path"], assertion["expected"])
