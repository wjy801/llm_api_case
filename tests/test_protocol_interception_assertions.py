from __future__ import annotations

import pytest

from module.protocol_testing import ProtocolInterceptionAssertions
from tests.mock_helpers import make_response


def test_blocked_protocol_accepts_legacy_message_fragment():
    response = _blocked_response(
        {
            "message": "当前使用协议不支持该模型",
            "type": "invalid_request_error",
        }
    )

    assert (
        ProtocolInterceptionAssertions().assert_protocol_interception_blocked(
            response,
            case_id="legacy-message",
        )
        is response
    )


def test_blocked_protocol_accepts_stable_model_capability_code():
    response = _blocked_response(
        {
            "code": "model_capability_not_supported",
            "message": "当前模型类型与请求接口不匹配，请切换对应接口后重试。",
            "type": "invalid_request_error",
        }
    )

    ProtocolInterceptionAssertions().assert_protocol_interception_blocked(
        response,
        case_id="model-capability-code",
    )


def test_blocked_protocol_rejects_unrelated_request_validation_error():
    response = _blocked_response(
        {
            "message": "image is required",
            "type": "invalid_request_error",
        }
    )

    with pytest.raises(AssertionError, match="协议/模型能力错误标识"):
        ProtocolInterceptionAssertions().assert_protocol_interception_blocked(
            response,
            case_id="missing-image",
        )


def test_blocked_protocol_accepts_case_specific_error_contract():
    response = _blocked_response(
        {
            "category": "user_request_invalid",
            "code": "invalid_request_error",
            "message": "请求参数不合法，请检查后重试。",
            "type": "invalid_request_error",
        }
    )

    ProtocolInterceptionAssertions().assert_protocol_interception_blocked(
        response,
        case_id="image-edit",
        expected_error_code="invalid_request_error",
        expected_error_category="user_request_invalid",
        expected_message_fragment="请求参数不合法",
    )


def test_blocked_protocol_rejects_server_error():
    response = make_response(
        "https://example.com/v1/chat/completions",
        method="POST",
        status_code=500,
        json_body={
            "error": {
                "code": "model_capability_not_supported",
                "message": "当前模型类型与请求接口不匹配",
                "type": "invalid_request_error",
            }
        },
    )

    with pytest.raises(AssertionError, match="应返回 4xx"):
        ProtocolInterceptionAssertions().assert_protocol_interception_blocked(
            response,
            case_id="server-error",
        )


def _blocked_response(error: dict[str, object]):
    return make_response(
        "https://example.com/v1/chat/completions",
        method="POST",
        status_code=400,
        json_body={"error": error},
    )
