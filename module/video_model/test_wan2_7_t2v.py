from __future__ import annotations

from module.video_model import VideoAssertions, VideoRequest, VideoTask


class TestVideoT2V:
    def setup_method(self):
        self.video_request = VideoRequest()
        self.video_assertions = VideoAssertions()
        self.video_task = VideoTask()

    def teardown_method(self):
        self.video_request.close()

    def test_pos_case_1(self):
        payload = {
            "input": {
                "prompt": "一段紧张刺激的侦探追查故事，展现电影级叙事能力。第1个镜头[0-3秒] 全景：雨夜的纽约街头，霓虹灯闪烁，一位身穿黑色风衣的侦探快步行走。 第2个镜头[3-6秒] 中景：侦探进入一栋老旧建筑，雨水打湿了他的外套，门在他身后缓缓关闭。 第3个镜头[6-9秒] 特写：侦探的眼神坚毅专注，远处传来警笛声，他微微皱眉思考。 第4个镜头[9-12秒] 中景：侦探在昏暗走廊中小心前行，手电筒照亮前方。 第5个镜头[12-15秒] 特写：侦探发现关键线索，脸上露出恍然大悟的表情。"
            },
            "model": "wan2.7-t2v",
            "parameters": {
                "duration": 15,
                "prompt_extend": True,
                "ratio": "16:9",
                "resolution": "720P",
                "watermark": True,
            },
        }

        self.video_task.create_and_poll_media_generation(self.video_request, payload, poll_timeout=1500)

    def test_pos_case_2(self):
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
            "model": "wan2.7-t2v-2026-06-12",
            "parameters": {
                "duration": 15,
                "prompt_extend": True,
                "ratio": "16:9",
                "resolution": "720P",
                "watermark": True,
            },
        }

        self.video_task.create_and_poll_media_generation(self.video_request, payload, poll_timeout=1500)
