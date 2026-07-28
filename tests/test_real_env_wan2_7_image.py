from __future__ import annotations

import pytest

from util import is_enabled


RUN_REAL_ENV_TESTS_ENV = "RUN_REAL_ENV_TESTS"

pytestmark = pytest.mark.skipif(
    not is_enabled(RUN_REAL_ENV_TESTS_ENV),
    reason=f"Set {RUN_REAL_ENV_TESTS_ENV}=TRUE to run real environment tests.",
)


class TestRealEnvImageGenerations:
    def setup_method(self):
        from module.image_model import ImageAssertions, ImageRequest, ImageTask

        self.image_request = ImageRequest()
        self.image_assertions = ImageAssertions()
        self.image_task = ImageTask()

    def teardown_method(self):
        self.image_request.close()

    def test_wan2_7_image_generation(self):
        payload = {
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
            "model": "wan2.7-image-pro",
        }

        self.image_task.create_and_poll_media_generation(self.image_request, payload)
