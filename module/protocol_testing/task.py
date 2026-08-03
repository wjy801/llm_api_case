from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import requests

from common import BaseTask, allure_step
from module.protocol_testing.payloads import (
    build_text_anthropic_count_tokens_probe_payload,
    build_text_anthropic_messages_probe_payload,
    build_text_v1_chat_completions_probe_payload,
    build_text_v1_completions_probe_payload,
    build_text_v1_responses_probe_payload,
)
from module.protocol_testing.request import ProtocolRequest


OPENAI_PROTOCOL = "OpenAI"
ANTHROPIC_PROTOCOL = "Anthropic"


@dataclass(frozen=True)
class ProtocolProbeResult:
    protocol: str
    path: str
    response: requests.Response | None = None
    error: requests.RequestException | None = None


class ProtocolTask(BaseTask):
    @allure_step("OpenAI POST /v1/chat/completions")
    def create_chat_completion(
        self,
        protocol_request: ProtocolRequest,
        payload: dict[str, Any],
        *,
        headers: dict[str, str] | None = None,
    ) -> requests.Response:
        return protocol_request.create_chat_completion(payload, headers=headers)

    @allure_step("OpenAI POST /v1/responses")
    def create_response(
        self,
        protocol_request: ProtocolRequest,
        payload: dict[str, Any],
        *,
        headers: dict[str, str] | None = None,
    ) -> requests.Response:
        return protocol_request.create_response(payload, headers=headers)

    @allure_step("OpenAI POST /v1/media/generations")
    def create_media_generation(
        self,
        protocol_request: ProtocolRequest,
        payload: dict[str, Any],
        *,
        headers: dict[str, str] | None = None,
    ) -> requests.Response:
        return protocol_request.create_media_generation(payload, headers=headers)

    @allure_step("OpenAI POST /v1/images/generations")
    def create_image_generation(
        self,
        protocol_request: ProtocolRequest,
        payload: dict[str, Any],
        *,
        headers: dict[str, str] | None = None,
    ) -> requests.Response:
        return protocol_request.create_image_generation(payload, headers=headers)

    @allure_step("OpenAI POST /v1/images/edits")
    def create_image_edit(
        self,
        protocol_request: ProtocolRequest,
        payload: dict[str, Any],
        image: bytes,
        *,
        image_filename: str = "protocol-interception.png",
        headers: dict[str, str] | None = None,
    ) -> requests.Response:
        return protocol_request.create_image_edit(
            payload,
            image,
            image_filename=image_filename,
            headers=headers,
        )

    @allure_step("Anthropic POST /v1/messages")
    def create_message(
        self,
        protocol_request: ProtocolRequest,
        payload: dict[str, Any],
        *,
        headers: dict[str, str] | None = None,
        anthropic_beta: str | None = None,
    ) -> requests.Response:
        return protocol_request.create_message(
            payload,
            headers=headers,
            anthropic_beta=anthropic_beta,
        )

    def detect_text_model_protocols(
        self,
        protocol_request: ProtocolRequest,
        model_id: str,
    ) -> list[ProtocolProbeResult]:
        """依次探测文本模型的 OpenAI 和 Anthropic 协议请求行。"""
        probes: tuple[tuple[str, str, Callable[[], requests.Response]], ...] = (
            (
                OPENAI_PROTOCOL,
                protocol_request.chat_completions_path,
                lambda: protocol_request.create_chat_completion(
                    build_text_v1_chat_completions_probe_payload(model_id)
                ),
            ),
            (
                OPENAI_PROTOCOL,
                protocol_request.responses_path,
                lambda: protocol_request.create_response(build_text_v1_responses_probe_payload(model_id)),
            ),
            (
                OPENAI_PROTOCOL,
                protocol_request.completions_path,
                lambda: protocol_request.create_completion(build_text_v1_completions_probe_payload(model_id)),
            ),
            (
                ANTHROPIC_PROTOCOL,
                protocol_request.messages_path,
                lambda: protocol_request.create_message(build_text_anthropic_messages_probe_payload(model_id)),
            ),
            (
                ANTHROPIC_PROTOCOL,
                protocol_request.messages_count_tokens_path,
                lambda: protocol_request.count_message_tokens(
                    build_text_anthropic_count_tokens_probe_payload(model_id)
                ),
            ),
        )

        return [self._run_protocol_probe(protocol, path, send) for protocol, path, send in probes]

    @staticmethod
    def _run_protocol_probe(
        protocol: str,
        path: str,
        send: Callable[[], requests.Response],
    ) -> ProtocolProbeResult:
        try:
            return ProtocolProbeResult(protocol=protocol, path=path, response=send())
        except requests.RequestException as error:
            return ProtocolProbeResult(protocol=protocol, path=path, error=error)
