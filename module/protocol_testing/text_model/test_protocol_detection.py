from __future__ import annotations

from module.protocol_testing import ProtocolProbeResult, ProtocolRequest, ProtocolTask
from module.protocol_testing.task import ANTHROPIC_PROTOCOL, OPENAI_PROTOCOL


class TestTextModelProtocolDetection:
    def setup_method(self):
        self.protocol_request = ProtocolRequest()
        self.protocol_task = ProtocolTask()

    def teardown_method(self):
        self.protocol_request.close()

    def test_detect_text_model_available_protocols(self, text_model_id: str):
        results = self.protocol_task.detect_text_model_protocols(
            self.protocol_request,
            text_model_id,
        )
        self._print_report(text_model_id, results)

    def _print_report(
        self,
        model_id: str,
        results: list[ProtocolProbeResult],
    ) -> None:
        print(f"\n{'=' * 72}")
        print(f"文本模型协议探测: {model_id}")
        print("=" * 72)
        for protocol in (OPENAI_PROTOCOL, ANTHROPIC_PROTOCOL):
            for result in results:
                if result.protocol != protocol:
                    continue
                print(f"\n[{result.protocol}] POST {result.path}")
                print("-" * 72)
                if result.error is not None:
                    print(f"请求异常: {result.error}")
                    print("响应体:\n<无响应体>")
                    continue

                response = result.response
                if response is None:
                    print("HTTP 状态: N/A")
                    print("响应体:\n<无响应体>")
                    continue

                print(f"HTTP 状态: {response.status_code}")
                print("响应体:")
                print(self.protocol_task.format_response_body(response))
