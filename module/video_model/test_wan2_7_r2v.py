from __future__ import annotations

from module.video_model import VideoAssertions, VideoRequest, VideoTask


class TestVideoR2V:
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
                        "type": "reference_video",
                        "url": "https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20260129/hfugmr/wan-r2v-role1.mp4",
                    },
                    {
                        "type": "reference_video",
                        "url": "https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20260129/qigswt/wan-r2v-role2.mp4",
                    },
                    {
                        "type": "reference_image",
                        "url": "https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20260129/qpzxps/wan-r2v-object4.png",
                    },
                ],
                "prompt": "视频2抱着图片3在咖啡厅里弹奏一支舒缓的美式乡村民谣，视频1笑着看着视频2",
            },
            "model": "wan2.7-r2v-2026-06-12",
            "parameters": {
                "duration": 10,
                "prompt_extend": False,
                "resolution": "720P",
                "watermark": True,
            },
        }

        self.video_task.create_and_poll_media_generation(self.video_request, payload, poll_timeout=1500)
