from __future__ import annotations

from module.video_model import VideoAssertions, VideoRequest, VideoTask


class TestVideo:
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
                        "type": "video",
                        "url": "https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20260402/ldnfdf/wan2.7-videoedit-style-change.mp4",
                    }
                ],
                "prompt": "将整个画面转换为黏土风格",
            },
            "model": "wan2.7-videoedit",
            "parameters": {
                "prompt_extend": True,
                "resolution": "720P",
                "watermark": True,
            },
        }

        self.video_task.create_and_poll_media_generation(self.video_request, payload)
