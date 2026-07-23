from __future__ import annotations

from module.image_model import ImageAssertions, ImageRequest, ImageTask


class TestImageGenerations:
    def setup_method(self):
        self.image_request = ImageRequest()
        self.image_assertions = ImageAssertions()
        self.image_task = ImageTask()

    def teardown_method(self):
        self.image_request.close()

    def test_create_image_generation(self):
        payload = {
            "input": {
                "messages": [
                    {
                        "content": [
                            {
                                "text": "未来城市广场上的透明玻璃艺术装置，写实摄影，清晨柔光"
                            }
                        ],
                        "role": "user",
                    }
                ]
            },
            "model": "wan2.7-image",

        }

        self.image_task.create_and_poll_media_generation(self.image_request, payload)
