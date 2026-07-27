from __future__ import annotations

from typing import Any


def build_text_v1_chat_completions_payload(model_id: str) -> dict[str, Any]:
    return {
        "model": model_id,
        "messages": [
            {
                "role": "system",
                "content": "你是墨行AI助手，请简洁回答。",
            },
            {
                "role": "user",
                "content": "我们在做企业知识库问答。",
            },
            {
                "role": "assistant",
                "content": "收到，请告诉我你希望接入的场景。",
            },
            {
                "role": "user",
                "content": "请给我一个最小接入建议。",
            },
        ],
        "temperature": 0.7,
        "stream": False,
        "user": "demo-user-001",
    }


def build_text_v1_responses_payload(model_id: str) -> dict[str, Any]:
    return {
        "model": model_id,
        "input": "hi",
        "stream": False,
    }


def build_image_v1_media_generations_payload(model_id: str) -> dict[str, Any]:
    return {
        "input": {
            "messages": [
                {
                    "content": [
                        {
                            "text": "生成陡峭的山脉",
                        }
                    ],
                    "role": "user",
                }
            ]
        },
        "model": model_id,
    }


def build_image_v1_images_generations_payload(model_id: str) -> dict[str, Any]:
    return {
        "model": model_id,
        "prompt": "生成陡峭的山脉",
    }


def build_image_v1_images_edits_payload(model_id: str) -> dict[str, Any]:
    return {
        "model": model_id,
        "prompt": "在图片中添加陡峭的山脉",
    }


def build_text_anthropic_messages_payload(model_id: str) -> dict[str, Any]:
    return {
        "model": model_id,
        "max_tokens": 500,
        "system": "你是一个乐于助人的AI",
        "temperature": 0.7,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "hi",
                    }
                ],
            }
        ],
    }


def build_text_gemini_generate_content_payload(model_id: str) -> dict[str, Any]:
    return {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {
                        "text": "hi",
                    }
                ],
            }
        ],
        "generationConfig": {
            "temperature": 0.7,
        },
    }
