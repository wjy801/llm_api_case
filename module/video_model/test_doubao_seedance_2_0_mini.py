from __future__ import annotations

import pytest

from common import CapturePolicy
from module.video_model import VideoAssertions, VideoRequest, VideoTask
from module.video_model.payloads import (
    DOUBAO_SEEDANCE_2_0_MINI_SCENARIO_NAMES,
    build_doubao_seedance_2_0_mini_payload,
)


class TestDoubaoSeedance20Mini:
    def setup_method(self):
        self.video_request = VideoRequest(
            capture_policy=CapturePolicy.output_only(),
        )
        self.video_assertions = VideoAssertions()
        self.video_task = VideoTask()

    def teardown_method(self):
        self.video_request.close()

    @pytest.mark.parametrize(
        "scenario_name",
        DOUBAO_SEEDANCE_2_0_MINI_SCENARIO_NAMES,
        ids=DOUBAO_SEEDANCE_2_0_MINI_SCENARIO_NAMES,
    )
    def test_generate_video(self, scenario_name: str):
        payload = build_doubao_seedance_2_0_mini_payload(scenario_name)

        response = self.video_task.create_doubao_seedance_2_0_mini_generation(
            self.video_request,
            payload,
            scenario_name=scenario_name,
        )

        self.video_assertions.assert_status_code(response, 200)
