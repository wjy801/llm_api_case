from __future__ import annotations

from module.smoke import SmokeAssertions, SmokeRequest, SmokeTask


FORBIDDEN_RESPONSE_FIELDS = ["upstream", "provider", "api_key", "ip"]


def build_chat_completions_payload(model: str) -> dict[str, Any]:
    return {
  "input": {
    "messages": [
      {
        "content": [
          {
            "text": "一间有着精致窗户的花店，漂亮的木质门，摆放着花朵"
          }
        ],
        "role": "user"
      }
    ]
  },
 "model": model,
}


class TestImageModelExportMessage:
    def setup_method(self):
        self.smoke_request = SmokeRequest()
        self.smoke_assertions = SmokeAssertions()
        self.smoke_task = SmokeTask()

    def teardown_method(self):
        self.smoke_request.close()

    def test_image_model_generation_flow(self):
        payload = self.smoke_task.build_sync_image_generation_payload()

        response = self.smoke_task.create_image_generation(self.smoke_request, payload)
        self.smoke_assertions.assert_status_code(response, 200)
        self.smoke_assertions.assert_response_text_not_contains(
            response,
            FORBIDDEN_RESPONSE_FIELDS,
        )

    def test_non_image_model_generation_flow(self):
        payload = build_chat_completions_payload("wan2.7-im")

        response = self.smoke_task.create_chat_completion(self.smoke_request, payload)
        self.smoke_assertions.assert_response_text_not_contains(
            response,
            FORBIDDEN_RESPONSE_FIELDS,
        )
