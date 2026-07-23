from __future__ import annotations

from module.image_model import ImageAssertions, ImageRequest, ImageTask


class TestImageGenerations:
    def setup_method(self):
        self.image_request = ImageRequest()
        self.image_assertions = ImageAssertions()
        self.image_task = ImageTask()

    def teardown_method(self):
        self.image_request.close()

    def test_pos_case_1(self):
        payload = {
            "input": {
                "messages": [
                    {
                        "content": [
                            {
                                "text": "生成陡峭的山脉"
                            }
                        ],
                        "role": "user",
                    }
                ]
            },
            "model": "wan2.7-image-pro",

        }

        self.image_task.create_and_poll_media_generation(self.image_request, payload)
