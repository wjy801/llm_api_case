from __future__ import annotations

from typing import Any


CHAT_COMPLETION_SUCCESS_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["id", "object", "model", "choices", "usage"],
    "properties": {
        "id": {"type": "string", "minLength": 1},
        "object": {"const": "chat.completion"},
        "created": {"type": "integer", "minimum": 0},
        "model": {"type": "string", "minLength": 1},
        "choices": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["message"],
                "properties": {
                    "index": {"type": "integer", "minimum": 0},
                    "finish_reason": {"type": ["string", "null"]},
                    "message": {
                        "type": "object",
                        "properties": {
                            "role": {"type": "string"},
                            "content": {"type": ["string", "array", "null"]},
                        },
                    },
                },
            },
        },
        "usage": {
            "type": "object",
            "required": ["prompt_tokens", "completion_tokens", "total_tokens"],
            "properties": {
                "prompt_tokens": {"type": "integer", "minimum": 0},
                "completion_tokens": {"type": "integer", "minimum": 0},
                "total_tokens": {"type": "integer", "minimum": 0},
            },
        },
    },
}

STANDARD_ERROR_RESPONSE_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["error"],
    "properties": {
        "error": {
            "type": "object",
            "required": ["message", "type", "code"],
            "properties": {
                "message": {"type": "string", "minLength": 1},
                "type": {"type": "string", "minLength": 1},
                "code": {"type": ["string", "null"]},
            },
        },
    },
}


__all__ = ["CHAT_COMPLETION_SUCCESS_SCHEMA", "STANDARD_ERROR_RESPONSE_SCHEMA"]
