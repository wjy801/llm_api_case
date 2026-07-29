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
    if model_id == "gpt-image-2":
        return {
            "aspect_ratio": "16:9",
            "capability": "image_generation",
            "model": model_id,
            "n": 1,
            "prompt": "生成一个小狗的图片",
            "quality": "low",
            "response_format": "url",
            "size": "2K",
        }

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


def build_video_v1_media_generations_payload(model_id: str) -> dict[str, Any]:
    if model_id == "seedance-2-0-oversea":
        return {
            "aspect_ratio": "16:9",
            "capability": "video_generation",
            "control_mode": "none",
            "duration_seconds": 5,
            "input_mode": "text",
            "model": model_id,
            "prompt": "一只橘猫在霓虹灯街道上滑滑板，电影感镜头，动态光影",
            "resolution": "720p",
            "watermark": False,
            "with_audio": True,
        }

    if model_id == "doubao-seedance-2-0-260128":
        return {
            "aspect_ratio": "16:9",
            "capability": "video_generation",
            "control_mode": "none",
            "duration_seconds": 5,
            "input_mode": "text",
            "model": model_id,
            "prompt": "一只橘猫在霓虹灯街道上滑滑板，电影感镜头，动态光影",
            "resolution": "720p",
            "with_audio": True,
        }

    return {
        "input": {
            "prompt": (
                "一段紧张刺激的侦探追查故事，展现电影级叙事能力。"
                "第1个镜头[0-3秒] 全景：雨夜的纽约街头，霓虹灯闪烁，一位身穿黑色风衣的侦探快步行走。 "
                "第2个镜头[3-6秒] 中景：侦探进入一栋老旧建筑，雨水打湿了他的外套，门在他身后缓缓关闭。 "
                "第3个镜头[6-9秒] 特写：侦探的眼神坚毅专注，远处传来警笛声，他微微皱眉思考。 "
                "第4个镜头[9-12秒] 中景：侦探在昏暗走廊中小心前行，手电筒照亮前方。 "
                "第5个镜头[12-15秒] 特写：侦探发现关键线索，脸上露出恍然大悟的表情。"
            )
        },
        "model": model_id,
        "parameters": {
            "duration": 15,
            "prompt_extend": True,
            "ratio": "16:9",
            "resolution": "720P",
            "watermark": True,
        },
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
