from __future__ import annotations

from copy import deepcopy
from typing import Any


DOUBAO_SEEDANCE_2_5_MODEL_ID = "doubao-seedance-2-5-260628"

DOUBAO_SEEDANCE_2_5_SCENARIO_NAMES = (
    "text_to_video",
    "image_to_video",
    "first_last_frame",
    "multi_image_to_video",
    "video_to_video",
    "video_extend",
    "multimodal_reference",
    "audio_only_reference",
)

_DOUBAO_SEEDANCE_2_5_PAYLOADS: dict[str, dict[str, Any]] = {
    "text_to_video": {
        "model": DOUBAO_SEEDANCE_2_5_MODEL_ID,
        "duration": 10,
        "resolution": "720p",
        "ratio": "16:9",
        "generate_audio": True,
        "watermark": False,
        "content": [
            {
                "type": "text",
                "text": "一只橘猫在霓虹灯街道上滑滑板，电影感镜头，动态光影",
            }
        ],
    },
    "image_to_video": {
        "model": DOUBAO_SEEDANCE_2_5_MODEL_ID,
        "duration": 5,
        "resolution": "720p",
        "ratio": "adaptive",
        "generate_audio": True,
        "watermark": False,
        "content": [
            {
                "type": "image_url",
                "role": "first_frame",
                "image_url": {
                    "url": "https://arkdocs.tos-cn-beijing.volces.com/images/video-generation/seedance2.5_30s_input.png"
                },
            },
            {
                "type": "text",
                "text": "镜头缓慢推进，蒸汽朋克微缩场景中的齿轮缓缓转动，电影感灯光",
            },
        ],
    },
    "first_last_frame": {
        "model": DOUBAO_SEEDANCE_2_5_MODEL_ID,
        "duration": 5,
        "resolution": "720p",
        "ratio": "adaptive",
        "generate_audio": True,
        "watermark": False,
        "content": [
            {
                "type": "text",
                "text": "图中女孩对着镜头说“茄子”，360度环绕运镜",
            },
            {
                "type": "image_url",
                "role": "first_frame",
                "image_url": {
                    "url": "https://arkdocs.tos-cn-beijing.volces.com/images/video-generation/seedance2.5_30s_input.png"
                },
            },
            {
                "type": "image_url",
                "role": "last_frame",
                "image_url": {
                    "url": "https://arkdocs.tos-cn-beijing.volces.com/images/video-generation/seedance2.5_30s_input.png"
                },
            },
        ],
    },
    "multi_image_to_video": {
        "model": DOUBAO_SEEDANCE_2_5_MODEL_ID,
        "duration": 8,
        "resolution": "720p",
        "ratio": "16:9",
        "generate_audio": True,
        "watermark": False,
        "content": [
            {
                "type": "text",
                "text": "参考图片中的果茶杯外观与材质，生成一段干净明亮的产品展示镜头，不要编辑或延长任何已有视频",
            },
            {
                "type": "image_url",
                "role": "reference_image",
                "image_url": {
                    "url": "https://ark-project.tos-cn-beijing.volces.com/doc_image/r2v_tea_pic1.jpg"
                },
            },
            {
                "type": "image_url",
                "role": "reference_image",
                "image_url": {
                    "url": "https://ark-project.tos-cn-beijing.volces.com/doc_image/r2v_tea_pic2.jpg"
                },
            },
        ],
    },
    "video_to_video": {
        "model": DOUBAO_SEEDANCE_2_5_MODEL_ID,
        "duration": 8,
        "resolution": "720p",
        "ratio": "16:9",
        "generate_audio": True,
        "watermark": False,
        "content": [
            {
                "type": "text",
                "text": "参考视频1的运镜与节奏，生成一段海边日落的电影感画面",
            },
            {
                "type": "video_url",
                "role": "reference_video",
                "video_url": {
                    "url": "https://ark-project.tos-cn-beijing.volces.com/doc_video/r2v_tea_video1.mp4"
                },
            },
        ],
    },
    "video_extend": {
        "model": DOUBAO_SEEDANCE_2_5_MODEL_ID,
        "duration": 10,
        "resolution": "720p",
        "ratio": "adaptive",
        "generate_audio": True,
        "watermark": False,
        "output_format": "mov",
        "content": [
            {
                "type": "text",
                "text": "延长@视频1，窗户打开后进入@视频2的美术馆室内，最后镜头进入@视频3的画内",
            },
            {
                "type": "video_url",
                "role": "reference_video",
                "video_url": {
                    "url": "https://arkdocs.tos-cn-beijing.volces.com/videos/video-generation/r2v_extend_video1_75.mov"
                },
            },
            {
                "type": "video_url",
                "role": "reference_video",
                "video_url": {
                    "url": "https://ark-project.tos-cn-beijing.volces.com/doc_video/r2v_extend_video2.mp4"
                },
            },
            {
                "type": "video_url",
                "role": "reference_video",
                "video_url": {
                    "url": "https://ark-project.tos-cn-beijing.volces.com/doc_video/r2v_extend_video3.mp4"
                },
            },
        ],
    },
    "multimodal_reference": {
        "model": DOUBAO_SEEDANCE_2_5_MODEL_ID,
        "duration": 12,
        "resolution": "720p",
        "ratio": "16:9",
        "generate_audio": True,
        "watermark": False,
        "content": [
            {
                "type": "text",
                "text": "全程使用视频1的第一视角构图，全程使用音频1作为背景音乐，让图片中的果茶杯在第一人称镜头中完成宣传广告。",
            },
            {
                "type": "image_url",
                "role": "reference_image",
                "image_url": {
                    "url": "https://ark-project.tos-cn-beijing.volces.com/doc_image/r2v_tea_pic1.jpg"
                },
            },
            {
                "type": "image_url",
                "role": "reference_image",
                "image_url": {
                    "url": "https://ark-project.tos-cn-beijing.volces.com/doc_image/r2v_tea_pic2.jpg"
                },
            },
            {
                "type": "video_url",
                "role": "reference_video",
                "video_url": {
                    "url": "https://ark-project.tos-cn-beijing.volces.com/doc_video/r2v_tea_video1.mp4"
                },
            },
            {
                "type": "audio_url",
                "role": "reference_audio",
                "audio_url": {
                    "url": "https://ark-project.tos-cn-beijing.volces.com/doc_audio/r2v_tea_audio1.mp3"
                },
            },
        ],
    },
    "audio_only_reference": {
        "model": DOUBAO_SEEDANCE_2_5_MODEL_ID,
        "duration": 10,
        "resolution": "720p",
        "ratio": "16:9",
        "generate_audio": True,
        "watermark": False,
        "content": [
            {
                "type": "text",
                "text": "根据音频节奏生成一段海边乐队表演的电影感画面",
            },
            {
                "type": "audio_url",
                "role": "reference_audio",
                "audio_url": {
                    "url": "https://ark-project.tos-cn-beijing.volces.com/doc_audio/r2v_tea_audio1.mp3"
                },
            },
        ],
    },
}


def build_doubao_seedance_2_5_payload(scenario_name: str) -> dict[str, Any]:
    try:
        payload = _DOUBAO_SEEDANCE_2_5_PAYLOADS[scenario_name]
    except KeyError as exc:
        raise ValueError(f"未知的豆包 Seedance 2.5 场景：{scenario_name}") from exc
    return deepcopy(payload)


DOUBAO_SEEDANCE_2_0_MINI_MODEL_ID = "doubao-seedance-2-0-mini-260615"

DOUBAO_SEEDANCE_2_0_MINI_SCENARIO_NAMES = (
    "text_to_video",
    "image_to_video",
    "multi_image_to_video",
    "video_to_video",
    "multimodal_reference",
)

_DOUBAO_SEEDANCE_2_0_MINI_PAYLOADS: dict[str, dict[str, Any]] = {
    "text_to_video": {
        "model": DOUBAO_SEEDANCE_2_0_MINI_MODEL_ID,
        "duration": 5,
        "resolution": "720p",
        "ratio": "16:9",
        "generate_audio": False,
        "watermark": False,
        "content": [
            {
                "type": "text",
                "text": "一只橘猫在霓虹灯街道上滑滑板，电影感镜头，动态光影",
            }
        ],
    },
    "image_to_video": {
        "model": DOUBAO_SEEDANCE_2_0_MINI_MODEL_ID,
        "duration": 5,
        "resolution": "720p",
        "ratio": "16:9",
        "generate_audio": False,
        "watermark": False,
        "content": [
            {
                "type": "image_url",
                "role": "first_frame",
                "image_url": {
                    "url": "https://ark-project.tos-cn-beijing.volces.com/doc_image/r2v_tea_pic1.jpg"
                },
            },
            {
                "type": "text",
                "text": "让人物自然转身并看向镜头，镜头轻微推进",
            },
        ],
    },
    "multi_image_to_video": {
        "model": DOUBAO_SEEDANCE_2_0_MINI_MODEL_ID,
        "duration": 5,
        "resolution": "720p",
        "ratio": "16:9",
        "generate_audio": False,
        "watermark": False,
        "content": [
            {
                "type": "text",
                "text": "参考图片中的果茶杯外观与材质，让果茶杯随着轻快节奏自然旋转展示",
            },
            {
                "type": "image_url",
                "role": "reference_image",
                "image_url": {
                    "url": "https://ark-project.tos-cn-beijing.volces.com/doc_image/r2v_tea_pic1.jpg"
                },
            },
            {
                "type": "image_url",
                "role": "reference_image",
                "image_url": {
                    "url": "https://ark-project.tos-cn-beijing.volces.com/doc_image/r2v_tea_pic2.jpg"
                },
            },
        ],
    },
    "video_to_video": {
        "model": DOUBAO_SEEDANCE_2_0_MINI_MODEL_ID,
        "duration": 5,
        "resolution": "720p",
        "ratio": "16:9",
        "generate_audio": True,
        "watermark": False,
        "content": [
            {
                "type": "text",
                "text": "参考视频1的第一视角运镜与节奏，让图片中的果茶杯完成一段明亮的产品展示",
            },
            {
                "type": "video_url",
                "role": "reference_video",
                "video_url": {
                    "url": "https://ark-project.tos-cn-beijing.volces.com/doc_video/r2v_tea_video1.mp4"
                },
            },
            {
                "type": "image_url",
                "role": "reference_image",
                "image_url": {
                    "url": "https://ark-project.tos-cn-beijing.volces.com/doc_image/r2v_tea_pic1.jpg"
                },
            },
            {
                "type": "image_url",
                "role": "reference_image",
                "image_url": {
                    "url": "https://ark-project.tos-cn-beijing.volces.com/doc_image/r2v_tea_pic2.jpg"
                },
            },
        ],
    },
    "multimodal_reference": {
        "model": DOUBAO_SEEDANCE_2_0_MINI_MODEL_ID,
        "duration": 5,
        "resolution": "720p",
        "ratio": "16:9",
        "generate_audio": True,
        "watermark": False,
        "content": [
            {
                "type": "text",
                "text": "全程使用视频1的第一视角构图，全程使用音频1作为背景音乐，让图片中的果茶杯在第一人称镜头中完成宣传广告。",
            },
            {
                "type": "image_url",
                "role": "reference_image",
                "image_url": {
                    "url": "https://ark-project.tos-cn-beijing.volces.com/doc_image/r2v_tea_pic1.jpg"
                },
            },
            {
                "type": "image_url",
                "role": "reference_image",
                "image_url": {
                    "url": "https://ark-project.tos-cn-beijing.volces.com/doc_image/r2v_tea_pic2.jpg"
                },
            },
            {
                "type": "video_url",
                "role": "reference_video",
                "video_url": {
                    "url": "https://ark-project.tos-cn-beijing.volces.com/doc_video/r2v_tea_video1.mp4"
                },
            },
            {
                "type": "audio_url",
                "role": "reference_audio",
                "audio_url": {
                    "url": "https://ark-project.tos-cn-beijing.volces.com/doc_audio/r2v_tea_audio1.mp3"
                },
            },
        ],
    },
}


def build_doubao_seedance_2_0_mini_payload(scenario_name: str) -> dict[str, Any]:
    try:
        payload = _DOUBAO_SEEDANCE_2_0_MINI_PAYLOADS[scenario_name]
    except KeyError as exc:
        raise ValueError(
            f"未知的豆包 Seedance 2.0 Mini 场景：{scenario_name}"
        ) from exc
    return deepcopy(payload)


MINIMAX_H3_MODEL_ID = "MiniMax-H3"

MINIMAX_H3_SCENARIO_NAMES = (
    "text_to_video",
    "image_to_video",
    "image_to_video_last_frame",
    "image_to_video_end_frames",
    "reference_to_video",
)

_MINIMAX_H3_REFERENCE_IMAGE_1_URL = (
    "https://ark-project.tos-cn-beijing.volces.com/doc_image/r2v_tea_pic1.jpg"
)
_MINIMAX_H3_REFERENCE_IMAGE_2_URL = (
    "https://ark-project.tos-cn-beijing.volces.com/doc_image/r2v_tea_pic2.jpg"
)
_MINIMAX_H3_REFERENCE_AUDIO_URL = (
    "https://ark-project.tos-cn-beijing.volces.com/doc_audio/r2v_tea_audio1.mp3"
)

_MINIMAX_H3_PAYLOADS: dict[str, dict[str, Any]] = {
    "text_to_video": {
        "model": MINIMAX_H3_MODEL_ID,
        "content": [
            {
                "type": "text",
                "text": "An orange cat runs across a misty meadow at sunrise in a cinematic tracking shot.",
            }
        ],
        "duration": 5,
        "resolution": "768P",
        "ratio": "16:9",
        "aigc_watermark": False,
    },
    "image_to_video": {
        "model": MINIMAX_H3_MODEL_ID,
        "content": [
            {
                "type": "image_url",
                "role": "first_frame",
                "image_url": {"url": _MINIMAX_H3_REFERENCE_IMAGE_1_URL},
            },
            {
                "type": "text",
                "text": "The fruit tea cup slowly rotates while the camera moves forward with bright commercial lighting.",
            },
        ],
        "duration": 5,
        "resolution": "768P",
        "aigc_watermark": False,
    },
    "image_to_video_last_frame": {
        "model": MINIMAX_H3_MODEL_ID,
        "content": [
            {
                "type": "image_url",
                "role": "last_frame",
                "image_url": {"url": _MINIMAX_H3_REFERENCE_IMAGE_2_URL},
            },
            {
                "type": "text",
                "text": "A clean product shot gradually moves toward the supplied final composition.",
            },
        ],
        "duration": 5,
        "resolution": "768P",
        "aigc_watermark": False,
    },
    "image_to_video_end_frames": {
        "model": MINIMAX_H3_MODEL_ID,
        "content": [
            {
                "type": "image_url",
                "role": "first_frame",
                "image_url": {"url": _MINIMAX_H3_REFERENCE_IMAGE_1_URL},
            },
            {
                "type": "image_url",
                "role": "last_frame",
                "image_url": {"url": _MINIMAX_H3_REFERENCE_IMAGE_2_URL},
            },
            {
                "type": "text",
                "text": "Create a smooth cinematic transition between the first and last product frames.",
            },
        ],
        "duration": 5,
        "resolution": "768P",
        "aigc_watermark": False,
    },
    "reference_to_video": {
        "model": MINIMAX_H3_MODEL_ID,
        "content": [
            {
                "type": "text",
                "text": "Keep the referenced product appearance consistent and create a rhythmic commercial camera move.",
            },
            {
                "type": "image_url",
                "role": "reference_image",
                "image_url": {"url": _MINIMAX_H3_REFERENCE_IMAGE_1_URL},
            },
            {
                "type": "audio_url",
                "role": "reference_audio",
                "audio_url": {"url": _MINIMAX_H3_REFERENCE_AUDIO_URL},
            },
        ],
        "duration": 5,
        "resolution": "768P",
        "ratio": "16:9",
        "aigc_watermark": False,
    },
}


def build_minimax_h3_payload(scenario_name: str) -> dict[str, Any]:
    try:
        payload = _MINIMAX_H3_PAYLOADS[scenario_name]
    except KeyError as exc:
        raise ValueError(f"Unknown MiniMax-H3 scenario: {scenario_name}") from exc
    return deepcopy(payload)


__all__ = [
    "DOUBAO_SEEDANCE_2_0_MINI_MODEL_ID",
    "DOUBAO_SEEDANCE_2_0_MINI_SCENARIO_NAMES",
    "DOUBAO_SEEDANCE_2_5_MODEL_ID",
    "DOUBAO_SEEDANCE_2_5_SCENARIO_NAMES",
    "MINIMAX_H3_MODEL_ID",
    "MINIMAX_H3_SCENARIO_NAMES",
    "build_doubao_seedance_2_0_mini_payload",
    "build_doubao_seedance_2_5_payload",
    "build_minimax_h3_payload",
]
