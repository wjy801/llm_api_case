from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from time import monotonic, sleep
from urllib.parse import urlsplit

import pytest

from common.capture import CapturePolicy
from module.offline_framework_example import (
    OfflineFrameworkAssertions,
    OfflineFrameworkRequest,
    OfflineFrameworkTask,
)
from module.offline_framework_example.conftest import OfflineCaptureDirs
from module.offline_framework_example.offline_service import (
    CAPTURE_OVERSIZED,
    DEFAULT,
    INPUT_PNG_BYTES,
    INPUT_PNG_SHA256,
    OFFLINE_API_KEY,
    OFFLINE_TASK_ID,
    OUTPUT_PNG_BYTES,
    OUTPUT_PNG_SHA256,
    OfflineService,
)
from module.offline_framework_example.response_schemas import (
    OFFLINE_CREATE_TASK_SCHEMA,
    OFFLINE_POLLING_SUCCESS_SCHEMA,
)
from module.offline_framework_example.task import OFFLINE_POLLING_POLICY


def _wait_for_file(path: Path, *, timeout: float = 1.0) -> Path:
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        if path.is_file():
            return path
        sleep(0.005)
    raise AssertionError(f"Timed out waiting for completed capture file: {path}")


def _file_hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


class TestCaptureAndAssertions:
    def setup_method(self) -> None:
        self.assertions = OfflineFrameworkAssertions()
        self.task = OfflineFrameworkTask()

    def test_output_capture_and_contract_assertions(
        self,
        offline_service_factory: Callable[..., OfflineService],
        offline_request_factory: Callable[..., OfflineFrameworkRequest],
        offline_network_guard: Callable[[str], None],
        offline_capture_dirs: OfflineCaptureDirs,
    ) -> None:
        service = offline_service_factory(DEFAULT)
        request = offline_request_factory(
            service,
            capture_policy=CapturePolicy.output_only(),
        )
        payload = self.task.build_media_generation_payload(service.base_url)
        original_payload = deepcopy(payload)

        response = self.task.create_and_poll_media_generation(
            request,
            payload,
            poll_interval=0.01,
            poll_timeout=1,
            polling_policy=OFFLINE_POLLING_POLICY,
        )

        assert self.assertions.assert_status_code(response, 200) is response
        assert self.assertions.assert_task_id(response, OFFLINE_TASK_ID) is response
        assert self.assertions.assert_task_status(response, "succeeded") is response
        assert (
            self.assertions.assert_schema(
                response,
                OFFLINE_POLLING_SUCCESS_SCHEMA,
            )
            is response
        )
        result_url = response.json()["result"]["url"]
        offline_network_guard(result_url)
        assert service.state.output_asset_requested.is_set()
        assert service.state.snapshot()["endpoint_call_counts"]["output_asset_calls"] == 1

        output_file = offline_capture_dirs.output_dir / "output.png"
        assert set(offline_capture_dirs.output_dir.iterdir()) == {output_file}
        assert output_file.stat().st_size == len(OUTPUT_PNG_BYTES) == 70
        assert _file_hash(output_file) == OUTPUT_PNG_SHA256
        assert list(offline_capture_dirs.input_dir.iterdir()) == []
        assert payload == original_payload

    def test_input_capture_uses_only_loopback_resource(
        self,
        offline_service_factory: Callable[..., OfflineService],
        offline_request_factory: Callable[..., OfflineFrameworkRequest],
        offline_network_guard: Callable[[str], None],
        offline_capture_dirs: OfflineCaptureDirs,
    ) -> None:
        service = offline_service_factory(DEFAULT)
        request = offline_request_factory(
            service,
            capture_policy=CapturePolicy.input_only(),
        )
        payload = self.task.build_media_generation_payload(service.base_url)
        input_url = payload["input"]["media"]["url"]
        offline_network_guard(input_url)
        assert urlsplit(input_url).hostname == "127.0.0.1"

        response = self.task.create_media_generation(request, payload)

        assert self.assertions.assert_status_code(response, 202) is response
        assert self.assertions.assert_task_id(response, OFFLINE_TASK_ID) is response
        assert self.assertions.assert_schema(response, OFFLINE_CREATE_TASK_SCHEMA) is response
        assert service.state.input_asset_requested.wait(timeout=1)
        assert service.state.snapshot()["endpoint_call_counts"]["input_asset_calls"] == 1

        input_file = _wait_for_file(offline_capture_dirs.input_dir / "input.png")
        assert set(offline_capture_dirs.input_dir.iterdir()) == {input_file}
        assert not any(path.name.endswith(".part") for path in offline_capture_dirs.input_dir.iterdir())
        assert input_file.stat().st_size == len(INPUT_PNG_BYTES) == 70
        assert _file_hash(input_file) == INPUT_PNG_SHA256
        assert list(offline_capture_dirs.output_dir.iterdir()) == []

    def test_capture_limit_failure_does_not_override_response(
        self,
        offline_service_factory: Callable[..., OfflineService],
        offline_request_factory: Callable[..., OfflineFrameworkRequest],
        offline_network_guard: Callable[[str], None],
        offline_capture_dirs: OfflineCaptureDirs,
    ) -> None:
        service = offline_service_factory(CAPTURE_OVERSIZED)
        request = offline_request_factory(
            service,
            capture_policy=CapturePolicy.output_only(max_bytes=100),
        )
        payload = self.task.build_media_generation_payload(service.base_url)

        response = self.task.create_and_poll_media_generation(
            request,
            payload,
            poll_interval=0.01,
            poll_timeout=1,
            polling_policy=OFFLINE_POLLING_POLICY,
        )

        assert self.assertions.assert_status_code(response, 200) is response
        assert self.assertions.assert_task_status(response, "succeeded") is response
        assert self.assertions.assert_schema(response, OFFLINE_POLLING_SUCCESS_SCHEMA) is response
        result_url = response.json()["result"]["url"]
        assert result_url.endswith("/assets/oversized-output.png")
        offline_network_guard(result_url)
        assert service.state.oversized_asset_requested.is_set()
        assert service.state.snapshot()["endpoint_call_counts"]["oversized_asset_calls"] == 1
        assert list(offline_capture_dirs.output_dir.iterdir()) == []
        assert not any(path.name.endswith(".part") for path in offline_capture_dirs.output_dir.iterdir())

    def test_schema_error_has_path_and_redacted_diagnostics(
        self,
        offline_service: OfflineService,
        offline_request_factory: Callable[..., OfflineFrameworkRequest],
        offline_capture_dirs: OfflineCaptureDirs,
    ) -> None:
        request = offline_request_factory(
            offline_service,
            capture_policy=CapturePolicy.disabled(),
        )
        response = self.task.query_contract(request, "invalid_schema")
        self.assertions.assert_status_code(response, 200)

        with pytest.raises(AssertionError) as error_info:
            self.assertions.assert_schema(response, OFFLINE_CREATE_TASK_SCHEMA)

        error_text = str(error_info.value)
        assert "JSON Schema assertion failed" in error_text
        assert "Path: $.task_id" in error_text
        assert "Schema path: required" in error_text
        assert "Validator: required" in error_text
        assert "Actual type: <missing>" in error_text
        assert OFFLINE_API_KEY not in error_text
        assert f"Bearer {OFFLINE_API_KEY}" not in error_text
        assert "Authorization: Bearer" not in error_text
        assert list(offline_capture_dirs.input_dir.iterdir()) == []
        assert list(offline_capture_dirs.output_dir.iterdir()) == []
