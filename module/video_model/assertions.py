from __future__ import annotations

import requests

from common import BaseAssertions
from module.video_model.response_schemas import MINIMAX_H3_SUCCESS_RESPONSE_SCHEMA


class VideoAssertions(BaseAssertions):
    def assert_minimax_h3_generation_succeeded(
        self,
        response: requests.Response,
    ) -> requests.Response:
        self.assert_status_code(response, 200)
        self.assert_schema(response, MINIMAX_H3_SUCCESS_RESPONSE_SCHEMA)
        return response
