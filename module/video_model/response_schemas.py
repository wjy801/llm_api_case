from __future__ import annotations

from typing import Any

from module.video_model.payloads import MINIMAX_H3_MODEL_ID


MINIMAX_H3_SUCCESS_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["task"],
    "properties": {
        "task": {
            "type": "object",
            "required": ["id", "model", "status", "content"],
            "properties": {
                "id": {"type": "string", "minLength": 1},
                "model": {"const": MINIMAX_H3_MODEL_ID},
                "status": {"const": "succeeded"},
                "content": {
                    "type": "object",
                    "required": ["url"],
                    "properties": {
                        "url": {"type": "string", "minLength": 1},
                    },
                },
            },
        }
    },
}


__all__ = ["MINIMAX_H3_SUCCESS_RESPONSE_SCHEMA"]
