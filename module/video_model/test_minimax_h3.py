from __future__ import annotations

import pytest

from common import CapturePolicy
from module.video_model import VideoAssertions, VideoRequest, VideoTask
from module.video_model.payloads import (
    MINIMAX_H3_SCENARIO_NAMES,
    build_minimax_h3_payload,
)


class TestMiniMaxH3:
    def setup_method(self) -> None:
        self.video_request = VideoRequest(
            capture_policy=CapturePolicy.output_only(),
        )
        self.video_assertions = VideoAssertions()
        self.video_task = VideoTask()

    def teardown_method(self) -> None:
        self.video_request.close()

    @pytest.mark.parametrize(
        "scenario_name",
        MINIMAX_H3_SCENARIO_NAMES,
        ids=MINIMAX_H3_SCENARIO_NAMES,
    )
    def test_generate_video(self, scenario_name: str) -> None:
        payload = build_minimax_h3_payload(scenario_name)

        response = self.video_task.create_minimax_h3_generation(
            self.video_request,
            payload,
            scenario_name=scenario_name,
        )

        self.video_assertions.assert_minimax_h3_generation_succeeded(response)
