from __future__ import annotations

from module.video_model import VideoAssertions, VideoRequest, VideoTask


class TestVideoI2V:
    def setup_method(self):
        self.video_request = VideoRequest()
        self.video_assertions = VideoAssertions()
        self.video_task = VideoTask()

    def teardown_method(self):
        self.video_request.close()

    def test_pos_case_1(self):
        payload = {
            "input": {
                "media": [
                    {
                        "type": "first_frame",
                        "url": "https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250925/wpimhv/rap.png",
                    },
                    {
                        "type": "driving_audio",
                        "url": "https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250925/ozwpvi/rap.mp3",
                    },
                ],
                "prompt": "一幅都市奇幻艺术的场景。一个充满动感的涂鸦艺术角色。一个由喷漆所画成的少年，正从一面混凝土墙上活过来。他一边用极快的语速演唱一首英文rap，一边摆着一个经典的、充满活力的说唱歌手姿势。场景设定在夜晚一个充满都市感的铁路桥下。灯光来自一盏孤零零的街灯，营造出电影般的氛围，充满高能量和惊人的细节。视频的音频部分完全由rap构成，没有其他对话或杂音。",
            },
            "model": "wan2.7-i2v",
            "parameters": {
                "duration": 10,
                "prompt_extend": True,
                "resolution": "720P",
                "watermark": True,
            },
        }

        self.video_task.create_and_poll_media_generation(self.video_request, payload, poll_timeout=1500)
