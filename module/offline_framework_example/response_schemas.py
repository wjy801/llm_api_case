from __future__ import annotations

from typing import Any


OFFLINE_CREATE_TASK_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["task_id", "status", "model", "trace_id"],
    "properties": {
        "task_id": {"type": "string", "minLength": 1},
        "status": {"const": "queued"},
        "model": {"const": "offline-media-model"},
        "trace_id": {"type": "string", "minLength": 1},
    },
    "additionalProperties": True,
}

OFFLINE_POLLING_SUCCESS_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["task_id", "status", "model", "trace_id", "result"],
    "properties": {
        "task_id": {"type": "string", "minLength": 1},
        "status": {"const": "succeeded"},
        "model": {"const": "offline-media-model"},
        "trace_id": {"type": "string", "minLength": 1},
        "result": {
            "type": "object",
            "required": ["url"],
            "properties": {
                "url": {"type": "string", "minLength": 1},
            },
            "additionalProperties": True,
        },
    },
    "additionalProperties": True,
}

OFFLINE_BUSINESS_ERROR_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["error"],
    "properties": {
        "error": {
            "type": "object",
            "required": ["code", "type", "message"],
            "properties": {
                "code": {"type": "string", "minLength": 1},
                "type": {"type": "string", "minLength": 1},
                "message": {"type": "string", "minLength": 1},
            },
            "additionalProperties": True,
        },
    },
    "additionalProperties": True,
}

OFFLINE_AUDIT_RESPONSE_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["audit_name", "task_id", "status"],
    "properties": {
        "audit_name": {"type": "string", "minLength": 1},
        "task_id": {"type": "string", "minLength": 1},
        "status": {"const": "recorded"},
    },
    "additionalProperties": True,
}


__all__ = [
    "OFFLINE_AUDIT_RESPONSE_SCHEMA",
    "OFFLINE_BUSINESS_ERROR_SCHEMA",
    "OFFLINE_CREATE_TASK_SCHEMA",
    "OFFLINE_POLLING_SUCCESS_SCHEMA",
]
