from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any

import pytest
import requests

from common import BaseTask, allure_step
from common.streaming import iter_sse_lines
from module.smoke.assertions import SmokeAssertions
from module.smoke.request import SmokeRequest


KEY_CHAT_COMPLETIONS_MODEL_ID = "GLM-5"
SYNC_IMAGE_GENERATION_MODEL_ID = "gpt-image-2"
ASYNC_IMAGE_GENERATION_MODEL_ID = "gpt-image-2"
B_ACCOUNT_API_KEY = os.getenv("B_ACCOUNT_API_KEY", "").strip()
B_ACCOUNT_CONTROL_KEY = os.getenv("B_ACCOUNT_CONTROL_KEY", "").strip()


@dataclass(frozen=True)
class StreamChatCompletionResult:
    request_id: str


@dataclass(frozen=True)
class StreamChatCompletionChunks:
    raw_data_lines: list[str]
    chunks: list[dict[str, Any]]


class SmokeTask(BaseTask):
    def create_chat_completion_for_billing(self, smoke_request: SmokeRequest) -> requests.Response:
        chat_response = self.create_chat_completion(
            smoke_request,
            self.build_chat_completions_payload(),
        )
        self.get_request_id_from_response(chat_response)
        return chat_response

    @allure_step("Billing: stream POST /v1/chat/completions")
    def create_stream_chat_completion(self, smoke_request: SmokeRequest) -> requests.Response:
        return smoke_request.create_stream_chat_completion(self.build_stream_chat_completions_payload())

    @allure_step("Response validation: stream POST /v1/chat/completions")
    def create_small_stream_chat_completion(self, smoke_request: SmokeRequest) -> requests.Response:
        return smoke_request.create_stream_chat_completion(self.build_small_stream_chat_completions_payload())

    def collect_stream_chat_completion_chunks(self, response: requests.Response) -> StreamChatCompletionChunks:
        raw_data_lines: list[str] = []
        chunks: list[dict[str, Any]] = []

        try:
            for line in self.iter_stream_lines(response):
                if not line:
                    continue
                self.print_stream_raw_line(line)
                assert line.startswith("data:"), f"Stream chunk should start with 'data:', actual: {line!r}"
                raw_data_lines.append(line)

                data = line.removeprefix("data:").strip()
                if data == "[DONE]":
                    break

                try:
                    chunk = json.loads(data)
                except ValueError as exc:
                    raise AssertionError(f"Stream data chunk is not valid JSON: {data}") from exc

                chunks.append(chunk)
        finally:
            response.close()

        assert raw_data_lines, "Stream response did not produce any data lines."
        assert raw_data_lines[-1] == "data: [DONE]", (
            f"Stream response should end with 'data: [DONE]', actual: {raw_data_lines[-1]!r}"
        )
        assert chunks, "Stream response did not produce any JSON chunks before [DONE]."
        return StreamChatCompletionChunks(raw_data_lines=raw_data_lines, chunks=chunks)

    def interrupt_stream_chat_completion(
        self,
        response: requests.Response,
        max_duration_seconds: float = 15,
        print_raw_lines: bool = True,
    ) -> StreamChatCompletionResult:
        request_id = self.get_request_id_from_response(response)
        started_at = time.monotonic()

        try:
            for line in self.iter_stream_lines(response):
                elapsed_seconds = time.monotonic() - started_at
                if elapsed_seconds >= max_duration_seconds:
                    print(f"stream elapsed seconds: {round(elapsed_seconds, 3)}")
                    print(f"stream interrupted after {max_duration_seconds}s")
                    break
                if not line:
                    continue
                if print_raw_lines:
                    self.print_stream_raw_line(line)
                if not line.startswith("data:"):
                    continue

                data = line.removeprefix("data:").strip()
                if data == "[DONE]":
                    break
        finally:
            response.close()

        print(f"stream request_id: {request_id}")
        return StreamChatCompletionResult(
            request_id=request_id,
        )

    def create_sync_image_generation_for_billing(self, smoke_request: SmokeRequest) -> requests.Response:
        image_response = self.create_image_generation(
            smoke_request,
            self.build_sync_image_generation_payload(),
        )
        self.get_request_id_from_response(image_response)
        return image_response

    def create_async_image_generation(
        self,
        smoke_request: SmokeRequest,
        model_id: str = ASYNC_IMAGE_GENERATION_MODEL_ID,
    ) -> requests.Response:
        return self.create_media_generation(
            smoke_request,
            self.build_async_image_generation_payload(model_id),
        )

    @allure_step("查询异步媒体任务状态: {task_id}")
    def get_media_generation_task(
        self,
        smoke_request: SmokeRequest,
        task_id: str,
    ) -> requests.Response:
        return smoke_request.get_media_generation_task(task_id)

    def verify_account_balance_for_billing(
        self,
        smoke_request: SmokeRequest,
        smoke_assertions: SmokeAssertions,
    ) -> requests.Response:
        response = self.query_account_balance_for_billing(smoke_request)
        return smoke_assertions.assert_non_negative_total_balance(response)

    @staticmethod
    def build_chat_completions_payload(model_id: str = KEY_CHAT_COMPLETIONS_MODEL_ID) -> dict[str, Any]:
        return {
            "model": model_id,
            "messages": [
                {
                    "role": "system",
                    "content": "你是墨行AI助手，请简洁回答。",
                },
                {
                    "role": "user",
                    "content": "我们在做企业知识库问答。",
                },
                {
                    "role": "assistant",
                    "content": "收到，请告诉我你希望接入的场景。",
                },
                {
                    "role": "user",
                    "content": "请给我一个接入建议。",
                },
            ],
            "temperature": 0.7,
            "stream": False,
            "user": "demo-user-001",
        }

    @staticmethod
    def build_stream_chat_completions_payload(model_id: str = KEY_CHAT_COMPLETIONS_MODEL_ID) -> dict[str, Any]:
        return {
            "model": model_id,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是一个擅长长篇结构化写作的技术顾问。"
                        "请持续、详细、分层输出，不要提前总结或停止。"
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "请持续输出一份很长的企业级 AI 知识库问答系统建设方案。"
                        "内容至少包含 20 个章节，每个章节包含背景、目标、架构设计、"
                        "接口设计、数据治理、权限控制、评测方案、成本控制、上线步骤、"
                        "风险清单和可执行检查项。每个章节都要展开说明，使用长段落，"
                        "不要压缩内容，不要提前结束。"
                    ),
                },
            ],
            "temperature": 0.9,
            "max_tokens": 4096,
            "stream": True,
            "stream_options": {"include_usage": True},
            "user": "demo-user-001",
        }

    @staticmethod
    def build_small_stream_chat_completions_payload(model_id: str = KEY_CHAT_COMPLETIONS_MODEL_ID) -> dict[str, Any]:
        return {
            "model": model_id,
            "messages": [
                {
                    "role": "user",
                    "content": "请用一句话回答：接口自动化测试的价值是什么？",
                }
            ],
            "temperature": 0.2,
            "max_tokens": 80,
            "stream": True,
            "stream_options": {"include_usage": True},
            "user": "demo-user-001",
        }

    @staticmethod
    def build_sync_image_generation_payload(model_id: str = SYNC_IMAGE_GENERATION_MODEL_ID) -> dict[str, Any]:
        return {
            "model": model_id,
            "prompt": "生成一个花园，中间有一个花圃",
            "n": 1,
            "size": "1024x1024",
            "quality": "medium",
            "background": "auto",
            "output_format": "png",
            "response_format": "url",
            "user": "api_frame",
        }

    @staticmethod
    def build_async_image_generation_payload(model_id: str = ASYNC_IMAGE_GENERATION_MODEL_ID) -> dict[str, Any]:
        return {
            "model": model_id,
            "prompt": "生成一个花园，中间有一个花圃",
            "n": 1,
            "size": "1024x1024",
            "quality": "medium",
            "background": "auto",
            "output_format": "png",
            "response_format": "url",
            "user": "api_frame",
        }

    @staticmethod
    def print_stream_raw_line(line: str) -> None:
        try:
            print(f"stream raw line: {line}")
        except UnicodeEncodeError:
            safe_line = line.encode("unicode_escape").decode("ascii")
            print(f"stream raw line: {safe_line}")

    @staticmethod
    def iter_stream_lines(response: requests.Response):
        yield from iter_sse_lines(response)
