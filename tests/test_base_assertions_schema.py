from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
import requests

from common import async_assert_schema as exported_async_assert_schema
from common import assert_schema as exported_assert_schema
from common.base_assertions import BaseAssertions
from module.smoke.response_schemas import CHAT_COMPLETION_SUCCESS_SCHEMA, STANDARD_ERROR_RESPONSE_SCHEMA


def make_response(body: Any, status_code: int = 200, content_type: str = "application/json") -> requests.Response:
    response = requests.Response()
    response.status_code = status_code
    response.headers["Content-Type"] = content_type
    if isinstance(body, bytes):
        response._content = body
    elif isinstance(body, str):
        response._content = body.encode("utf-8")
    else:
        response._content = json.dumps(body).encode("utf-8")
    return response


def test_assert_schema_returns_original_response_on_success():
    response = make_response({"id": "response-001"})
    schema = {
        "type": "object",
        "required": ["id"],
        "properties": {"id": {"type": "string"}},
    }

    assert BaseAssertions().assert_schema(response, schema) is response


def test_assert_schema_missing_top_level_required_field_reports_exact_path():
    response = make_response({})
    schema = {
        "type": "object",
        "required": ["id"],
        "properties": {"id": {"type": "string"}},
    }

    with pytest.raises(AssertionError) as exc_info:
        BaseAssertions().assert_schema(response, schema)

    message = str(exc_info.value)
    assert "Path: $.id" in message
    assert "Validator: required" in message
    assert "Expected: required property 'id'" in message
    assert "Actual type: <missing>" in message
    assert "Actual value: <missing>" in message


def test_assert_schema_missing_nested_required_field_reports_exact_path():
    response = make_response({"usage": {"total_tokens": 2}})
    schema = {
        "type": "object",
        "required": ["usage"],
        "properties": {
            "usage": {
                "type": "object",
                "required": ["prompt_tokens", "total_tokens"],
                "properties": {
                    "prompt_tokens": {"type": "integer"},
                    "total_tokens": {"type": "integer"},
                },
            }
        },
    }

    with pytest.raises(AssertionError) as exc_info:
        BaseAssertions().assert_schema(response, schema)

    assert "Path: $.usage.prompt_tokens" in str(exc_info.value)


def test_assert_schema_wrong_type_reports_expected_and_actual():
    response = make_response({"usage": {"prompt_tokens": "12"}})
    schema = {
        "type": "object",
        "properties": {
            "usage": {
                "type": "object",
                "properties": {"prompt_tokens": {"type": "integer"}},
            }
        },
    }

    with pytest.raises(AssertionError) as exc_info:
        BaseAssertions().assert_schema(response, schema)

    message = str(exc_info.value)
    assert "Path: $.usage.prompt_tokens" in message
    assert "Validator: type" in message
    assert "Expected: integer" in message
    assert "Actual type: str" in message
    assert "Actual value: '12'" in message


def test_assert_schema_const_failure_reports_validator():
    response = make_response({"object": "chat.chunk"})
    schema = {
        "type": "object",
        "properties": {"object": {"const": "chat.completion"}},
    }

    with pytest.raises(AssertionError) as exc_info:
        BaseAssertions().assert_schema(response, schema)

    message = str(exc_info.value)
    assert "Path: $.object" in message
    assert "Validator: const" in message
    assert "Expected: chat.completion" in message


def test_assert_schema_array_item_failure_reports_indexed_path():
    response = make_response({"choices": [{"message": "not-object"}]})
    schema = {
        "type": "object",
        "properties": {
            "choices": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"message": {"type": "object"}},
                },
            }
        },
    }

    with pytest.raises(AssertionError) as exc_info:
        BaseAssertions().assert_schema(response, schema)

    assert "Path: $.choices[0].message" in str(exc_info.value)


def test_assert_schema_invalid_json_raises_assertion_error_with_redacted_body():
    response = make_response("api_key: invalid-json-secret", content_type="text/plain")

    with pytest.raises(AssertionError) as exc_info:
        BaseAssertions().assert_schema(response, {"type": "object"})

    message = str(exc_info.value)
    assert "Response body is not valid JSON" in message
    assert "api_key: <redacted>" in message
    assert "invalid-json-secret" not in message


def test_assert_schema_invalid_schema_raises_assertion_error():
    response = make_response({"id": "response-001"})

    with pytest.raises(AssertionError) as exc_info:
        BaseAssertions().assert_schema(response, {"type": 123})

    assert "Invalid JSON Schema:" in str(exc_info.value)


def test_assert_schema_redacts_sensitive_actual_value_in_failure_message():
    response = make_response({"api_key": "schema-secret"})
    schema = {
        "type": "object",
        "properties": {"api_key": {"type": "integer"}},
    }

    with pytest.raises(AssertionError) as exc_info:
        BaseAssertions().assert_schema(response, schema)

    message = str(exc_info.value)
    assert "<redacted>" in message
    assert "schema-secret" not in message


def test_module_level_assert_schema_is_exported():
    response = make_response({"id": "response-001"})
    schema = {
        "type": "object",
        "required": ["id"],
        "properties": {"id": {"type": "string"}},
    }

    assert exported_assert_schema(response, schema) is response


def test_module_level_async_assert_schema_is_exported():
    response = make_response({"id": "response-001"})
    schema = {
        "type": "object",
        "required": ["id"],
        "properties": {"id": {"type": "string"}},
    }

    assert asyncio.run(exported_async_assert_schema(response, schema)) is response


def test_chat_completion_success_schema_accepts_minimal_response():
    response = make_response(
        {
            "id": "chatcmpl-001",
            "object": "chat.completion",
            "created": 1,
            "model": "glm-5",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "ok"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 1,
                "completion_tokens": 1,
                "total_tokens": 2,
            },
        }
    )

    assert BaseAssertions().assert_schema(response, CHAT_COMPLETION_SUCCESS_SCHEMA) is response


def test_standard_error_response_schema_accepts_minimal_response():
    response = make_response(
        {
            "error": {
                "message": "model is required",
                "type": "invalid_request_error",
                "code": "model_not_found",
            }
        },
        status_code=404,
    )

    assert BaseAssertions().assert_schema(response, STANDARD_ERROR_RESPONSE_SCHEMA) is response
