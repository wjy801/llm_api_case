from __future__ import annotations

import pytest

from common import CapturePolicy
from module.video_model import VideoAssertions, VideoRequest, VideoTask
from module.video_model.payloads import build_doubao_seedance_2_5_payload


# pytestmark = pytest.mark.serial


class TestDoubaoSeedance25:
    def setup_method(self) -> None:
        self.video_request = VideoRequest(
            capture_policy=CapturePolicy.output_only(),
        )
        self.video_assertions = VideoAssertions()
        self.video_task = VideoTask()

    def teardown_method(self) -> None:
        self.video_request.close()

    def test_text_to_video(self) -> None:
        self._generate_and_assert("text_to_video")

    def test_image_to_video(self) -> None:
        self._generate_and_assert("image_to_video")

    def test_first_last_frame(self) -> None:
        self._generate_and_assert("first_last_frame")

    def test_multi_image_to_video(self) -> None:
        self._generate_and_assert("multi_image_to_video")

    def test_video_to_video(self) -> None:
        self._generate_and_assert("video_to_video")

    def test_video_extend(self) -> None:
        self._generate_and_assert("video_extend")

    def test_multimodal_reference(self) -> None:
        self._generate_and_assert("multimodal_reference")

    def test_audio_only_reference(self) -> None:
        self._generate_and_assert("audio_only_reference")

    def _generate_and_assert(self, scenario_name: str) -> None:
        payload = build_doubao_seedance_2_5_payload(scenario_name)

        response = self.video_task.create_doubao_seedance_2_5_generation(
            self.video_request,
            payload,
            scenario_name=scenario_name,
        )

        self.video_assertions.assert_status_code(response, 200)
