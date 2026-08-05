from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from hashlib import sha256
from http.client import HTTPConnection
import json
import struct
import threading
from typing import Iterator
from urllib.parse import urlsplit
import zlib

import pytest
import requests

import module.offline_framework_example.offline_service as service_module
from module.offline_framework_example.offline_service import (
    CAPTURE_OVERSIZED,
    DEFAULT,
    GET_429_THEN_200,
    GET_503_THEN_200,
    INPUT_PNG_BYTES,
    INPUT_PNG_SHA256,
    OFFLINE_API_KEY,
    OFFLINE_COOKIE_NAME,
    OFFLINE_COOKIE_VALUE,
    OFFLINE_MODEL_ID,
    OFFLINE_REQUEST_ID,
    OFFLINE_TASK_ID,
    OFFLINE_TRACE_ID,
    OUTPUT_PNG_BYTES,
    OUTPUT_PNG_SHA256,
    OVERSIZED_OUTPUT_BYTES,
    POLL_FAILURE,
    POLL_SUCCESS_WITH_RETRY,
    POLL_TIMEOUT,
    POLL_UNKNOWN,
    POST_503_THEN_200,
    OfflineService,
    OfflineServiceScenario,
    assert_loopback_url,
)


@contextmanager
def _session() -> Iterator[requests.Session]:
    session = requests.Session()
    session.trust_env = False
    try:
        yield session
    finally:
        session.close()


def _url(service: OfflineService, path: str) -> str:
    return f"{service.base_url}{path}"


def _media_payload(service: OfflineService) -> dict[str, object]:
    return {
        "model": OFFLINE_MODEL_ID,
        "prompt": "offline framework example",
        "input": {
            "media": {
                "type": "image",
                "url": _url(service, "/assets/input.png"),
            }
        },
    }


def _create_task(session: requests.Session, service: OfflineService) -> None:
    response = session.post(
        _url(service, "/v1/media/generations"),
        json=_media_payload(service),
        timeout=1,
    )
    assert response.status_code == 202


def _assert_png_crc(content: bytes) -> None:
    assert content.startswith(b"\x89PNG\r\n\x1a\n")
    offset = 8
    chunk_types: list[bytes] = []
    while offset < len(content):
        assert offset + 12 <= len(content)
        chunk_length = struct.unpack(">I", content[offset : offset + 4])[0]
        chunk_type = content[offset + 4 : offset + 8]
        data_start = offset + 8
        data_end = data_start + chunk_length
        crc_end = data_end + 4
        assert crc_end <= len(content)
        expected_crc = struct.unpack(">I", content[data_end:crc_end])[0]
        actual_crc = zlib.crc32(chunk_type)
        actual_crc = zlib.crc32(content[data_start:data_end], actual_crc)
        assert actual_crc & 0xFFFFFFFF == expected_crc
        chunk_types.append(chunk_type)
        offset = crc_end
    assert offset == len(content)
    assert chunk_types == [b"IHDR", b"IDAT", b"IEND"]


def test_fixed_assets_have_stable_length_hash_and_png_crc() -> None:
    assert len(INPUT_PNG_BYTES) == 70
    assert len(OUTPUT_PNG_BYTES) == 70
    assert len(OVERSIZED_OUTPUT_BYTES) == 140
    assert OVERSIZED_OUTPUT_BYTES == OUTPUT_PNG_BYTES * 2
    assert sha256(INPUT_PNG_BYTES).hexdigest() == INPUT_PNG_SHA256
    assert sha256(OUTPUT_PNG_BYTES).hexdigest() == OUTPUT_PNG_SHA256
    _assert_png_crc(INPUT_PNG_BYTES)
    _assert_png_crc(OUTPUT_PNG_BYTES)


def test_asset_endpoints_return_fixed_content_and_signal_events() -> None:
    with OfflineService() as service, _session() as session:
        expected = {
            "/assets/input.png": INPUT_PNG_BYTES,
            "/assets/output.png": OUTPUT_PNG_BYTES,
            "/assets/oversized-output.png": OVERSIZED_OUTPUT_BYTES,
        }
        for path, content in expected.items():
            response = session.get(_url(service, path), timeout=1)
            assert response.status_code == 200
            assert response.headers["Content-Type"] == "image/png"
            assert int(response.headers["Content-Length"]) == len(content)
            assert response.content == content

        assert service.state.input_asset_requested.wait(timeout=0.1)
        assert service.state.output_asset_requested.wait(timeout=0.1)
        assert service.state.oversized_asset_requested.wait(timeout=0.1)
        counts = service.state.snapshot()["endpoint_call_counts"]
        assert counts["input_asset_calls"] == 1
        assert counts["output_asset_calls"] == 1
        assert counts["oversized_asset_calls"] == 1


def test_service_can_start_and_stop_twenty_times_without_thread_leaks() -> None:
    with _session() as session:
        for _ in range(20):
            service = OfflineService().start()
            thread_name = service.thread_name
            assert_loopback_url(service.base_url)
            response = session.get(_url(service, "/v1/context"), timeout=1)
            assert response.status_code == 200
            service.stop()
            service.stop()
            assert not service.is_running
            assert all(
                thread.name != thread_name for thread in threading.enumerate()
            )


def test_two_service_instances_keep_state_and_lifecycle_isolated() -> None:
    with (
        OfflineService(GET_503_THEN_200) as first,
        OfflineService(DEFAULT) as second,
        _session() as session,
    ):
        first_response = session.get(_url(first, "/v1/transient"), timeout=1)
        assert first_response.status_code == 503
        assert second.state.snapshot()["endpoint_call_counts"] == {}

        _create_task(session, first)
        assert first.state.snapshot()["tasks"] == (OFFLINE_TASK_ID,)
        assert second.state.snapshot()["tasks"] == ()

        deleted = session.delete(
            _url(first, f"/v1/media/tasks/{OFFLINE_TASK_ID}"), timeout=1
        )
        assert deleted.status_code == 204
        assert second.state.snapshot()["deleted_task_ids"] == ()

        first.stop()
        assert not first.is_running
        second_response = session.get(_url(second, "/v1/context"), timeout=1)
        assert second_response.status_code == 200
        assert second.is_running


def test_transient_get_sequences_are_fixed_and_repeat_last_step() -> None:
    with OfflineService(GET_503_THEN_200) as service, _session() as session:
        responses = [
            session.get(_url(service, "/v1/transient"), timeout=1)
            for _ in range(3)
        ]
        assert [response.status_code for response in responses] == [503, 200, 200]
        assert responses[1].json() == {"status": "ok", "attempt": 2}
        assert service.state.snapshot()["endpoint_call_counts"]["transient_calls"] == 3

    with OfflineService(GET_429_THEN_200) as service, _session() as session:
        first = session.get(_url(service, "/v1/transient"), timeout=1)
        second = session.get(_url(service, "/v1/transient"), timeout=1)
        assert [first.status_code, second.status_code] == [429, 200]
        assert first.headers["Retry-After"] == "0"
        assert first.json() == {"error": {"code": "RATE_LIMITED"}}


def test_idempotent_post_sequence_is_fixed_but_does_not_initiate_retries() -> None:
    with OfflineService(POST_503_THEN_200) as service, _session() as session:
        first = session.post(
            _url(service, "/v1/idempotent-operation"),
            json={"operation": "offline-write", "value": 1},
            timeout=1,
        )
        assert first.status_code == 503
        assert service.state.snapshot()["endpoint_call_counts"][
            "idempotent_operation_calls"
        ] == 1

        second = session.post(
            _url(service, "/v1/idempotent-operation"),
            json={"operation": "offline-write", "value": 1},
            headers={"Idempotency-Key": "offline-idempotency-001"},
            timeout=1,
        )
        third = session.post(
            _url(service, "/v1/idempotent-operation"),
            json={"operation": "offline-write", "value": 1},
            timeout=1,
        )
        assert [second.status_code, third.status_code] == [200, 200]
        assert second.json() == {
            "operation": "offline-write",
            "status": "committed",
        }
        assert service.state.snapshot()["endpoint_call_counts"][
            "idempotent_operation_calls"
        ] == 3


def test_poll_success_sequence_separates_transport_and_business_states() -> None:
    with OfflineService(POLL_SUCCESS_WITH_RETRY) as service, _session() as session:
        _create_task(session, service)
        responses = [
            session.get(
                _url(service, f"/v1/media/tasks/{OFFLINE_TASK_ID}"), timeout=1
            )
            for _ in range(5)
        ]
        assert [response.status_code for response in responses] == [
            503,
            200,
            200,
            200,
            200,
        ]
        assert responses[0].json()["error"]["type"] == "transport"
        assert [response.json()["status"] for response in responses[1:]] == [
            "queued",
            "running",
            "succeeded",
            "succeeded",
        ]
        assert responses[3].json()["result"]["url"] == _url(
            service, "/assets/output.png"
        )
        assert service.state.snapshot()["endpoint_call_counts"]["media_poll_calls"] == 5


def test_poll_failure_unknown_timeout_and_oversized_scenarios_are_fixed() -> None:
    with OfflineService(POLL_FAILURE) as service, _session() as session:
        _create_task(session, service)
        statuses = [
            session.get(
                _url(service, f"/v1/media/tasks/{OFFLINE_TASK_ID}"), timeout=1
            ).json()["status"]
            for _ in range(2)
        ]
        assert statuses == ["queued", "failed"]

    with OfflineService(POLL_UNKNOWN) as service, _session() as session:
        _create_task(session, service)
        statuses = [
            session.get(
                _url(service, f"/v1/media/tasks/{OFFLINE_TASK_ID}"), timeout=1
            ).json()["status"]
            for _ in range(2)
        ]
        assert statuses == ["queued", "paused"]

    with OfflineService(POLL_TIMEOUT) as service, _session() as session:
        _create_task(session, service)
        statuses = [
            session.get(
                _url(service, f"/v1/media/tasks/{OFFLINE_TASK_ID}"), timeout=1
            ).json()["status"]
            for _ in range(3)
        ]
        assert statuses == ["running", "running", "running"]

    with OfflineService(CAPTURE_OVERSIZED) as service, _session() as session:
        _create_task(session, service)
        response = session.get(
            _url(service, f"/v1/media/tasks/{OFFLINE_TASK_ID}"), timeout=1
        )
        assert response.json()["result"]["url"] == _url(
            service, "/assets/oversized-output.png"
        )


def test_task_creation_polling_headers_cookie_and_delete_contract() -> None:
    with OfflineService() as service, _session() as session:
        create_response = session.post(
            _url(service, "/v1/media/generations"),
            json=_media_payload(service),
            timeout=1,
        )
        assert create_response.status_code == 202
        assert create_response.json() == {
            "task_id": OFFLINE_TASK_ID,
            "status": "queued",
            "model": OFFLINE_MODEL_ID,
            "trace_id": OFFLINE_TRACE_ID,
        }
        assert create_response.headers["X-Request-ID"] == OFFLINE_REQUEST_ID
        assert create_response.cookies[OFFLINE_COOKIE_NAME] == OFFLINE_COOKIE_VALUE

        poll_response = session.get(
            _url(service, f"/v1/media/tasks/{OFFLINE_TASK_ID}"), timeout=1
        )
        assert poll_response.status_code == 200
        assert poll_response.headers["X-Request-ID"] == OFFLINE_REQUEST_ID

        delete_response = session.delete(
            _url(service, f"/v1/media/tasks/{OFFLINE_TASK_ID}"), timeout=1
        )
        assert delete_response.status_code == 204
        assert delete_response.content == b""
        assert delete_response.headers["Content-Length"] == "0"
        assert service.state.snapshot()["deleted_task_ids"] == (OFFLINE_TASK_ID,)

        missing = session.get(
            _url(service, f"/v1/media/tasks/{OFFLINE_TASK_ID}"), timeout=1
        )
        repeated_delete = session.delete(
            _url(service, f"/v1/media/tasks/{OFFLINE_TASK_ID}"), timeout=1
        )
        assert missing.status_code == 404
        assert repeated_delete.status_code == 404
        assert service.state.snapshot()["deleted_task_ids"] == (OFFLINE_TASK_ID,)


def test_echo_context_and_contract_routes_return_stable_safe_json() -> None:
    with OfflineService() as service, _session() as session:
        payload = {
            "model": OFFLINE_MODEL_ID,
            "prompt": "offline framework example",
            "metadata": {"case": "request_pipeline"},
        }
        echo = session.post(
            _url(service, "/v1/echo"),
            json=payload,
            headers={"Authorization": f"Bearer {OFFLINE_API_KEY}"},
            timeout=1,
        )
        assert echo.status_code == 200
        assert echo.json() == {
            "received": payload,
            "authorization_present": True,
            "status": "accepted",
        }
        assert OFFLINE_API_KEY not in echo.text

        context = session.get(_url(service, "/v1/context"), timeout=1)
        assert context.json() == {
            "data": {"task_id": OFFLINE_TASK_ID},
            "message": f"trace={OFFLINE_TRACE_ID}",
        }
        assert context.headers["X-Request-ID"] == OFFLINE_REQUEST_ID
        assert context.cookies[OFFLINE_COOKIE_NAME] == OFFLINE_COOKIE_VALUE

        valid = session.get(_url(service, "/v1/contracts/valid"), timeout=1)
        business_error = session.get(
            _url(service, "/v1/contracts/business_error"), timeout=1
        )
        invalid_schema = session.get(
            _url(service, "/v1/contracts/invalid_schema"), timeout=1
        )
        missing = session.get(_url(service, "/v1/contracts/missing"), timeout=1)
        assert valid.json() == {
            "task_id": OFFLINE_TASK_ID,
            "status": "succeeded",
            "model": OFFLINE_MODEL_ID,
        }
        assert business_error.status_code == 400
        assert business_error.json()["error"]["code"] == "OFFLINE_BUSINESS_ERROR"
        assert invalid_schema.json() == {"status": "succeeded"}
        assert missing.status_code == 404
        assert missing.json() == {
            "error": {"code": "CONTRACT_MODE_NOT_FOUND"}
        }


def test_invalid_json_audit_header_and_unknown_routes_are_structured() -> None:
    with OfflineService() as service, _session() as session:
        invalid_json = session.post(
            _url(service, "/v1/echo"),
            data="{",
            headers={"Content-Type": "application/json"},
            timeout=1,
        )
        invalid_body = session.post(
            _url(service, "/v1/echo"),
            json=["not", "an", "object"],
            timeout=1,
        )
        missing_audit_name = session.get(_url(service, "/v1/audit"), timeout=1)
        assert invalid_json.status_code == 400
        assert invalid_json.json() == {"error": {"code": "INVALID_JSON"}}
        assert invalid_body.status_code == 400
        assert invalid_body.json() == {
            "error": {"code": "INVALID_JSON_BODY"}
        }
        assert missing_audit_name.status_code == 400
        assert missing_audit_name.json() == {
            "error": {"code": "AUDIT_NAME_REQUIRED"}
        }
        assert service.state.snapshot()["audit_records"] == ()

        unknown_responses = [
            session.get(_url(service, "/unknown"), timeout=1),
            session.post(_url(service, "/unknown"), json={}, timeout=1),
            session.delete(_url(service, "/unknown"), timeout=1),
        ]
        assert [response.status_code for response in unknown_responses] == [
            404,
            404,
            404,
        ]
        assert all(
            response.json() == {"error": {"code": "NOT_FOUND"}}
            for response in unknown_responses
        )


def test_invalid_content_length_returns_structured_400() -> None:
    with OfflineService() as service:
        parsed = urlsplit(service.base_url)
        connection = HTTPConnection(parsed.hostname, parsed.port, timeout=1)
        try:
            connection.putrequest("POST", "/v1/echo")
            connection.putheader("Content-Type", "application/json")
            connection.putheader("Content-Length", "not-a-number")
            connection.endheaders()
            response = connection.getresponse()
            assert response.status == 400
            assert json.loads(response.read()) == {
                "error": {"code": "INVALID_CONTENT_LENGTH"}
            }
        finally:
            connection.close()


def test_concurrent_audit_updates_are_locked_and_complete() -> None:
    audit_names = [f"audit-{index}" for index in range(16)]
    with OfflineService() as service:

        def call_audit(audit_name: str) -> tuple[int, str]:
            with _session() as session:
                response = session.get(
                    _url(service, "/v1/audit"),
                    headers={"X-Audit-Name": audit_name},
                    timeout=1,
                )
                return response.status_code, response.json()["audit_name"]

        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(call_audit, audit_names))

        assert {status for status, _ in results} == {200}
        assert {audit_name for _, audit_name in results} == set(audit_names)
        snapshot = service.state.snapshot()
        assert snapshot["endpoint_call_counts"]["audit_calls"] == len(audit_names)
        assert set(snapshot["audit_records"]) == set(audit_names)
        assert snapshot["handler_errors"] == ()


def test_context_manager_stops_service_when_test_body_raises() -> None:
    service = OfflineService()
    with pytest.raises(RuntimeError, match="controlled test failure"):
        with service:
            thread_name = service.thread_name
            raise RuntimeError("controlled test failure")
    assert not service.is_running
    assert all(thread.name != thread_name for thread in threading.enumerate())


def test_start_failure_is_clean_and_stop_remains_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_to_construct(*args: object, **kwargs: object) -> object:
        raise OSError("controlled bind failure")

    monkeypatch.setattr(service_module, "_OfflineHTTPServer", fail_to_construct)
    service = OfflineService()
    with pytest.raises(OSError, match="controlled bind failure"):
        service.start()
    service.stop()
    service.stop()
    assert not service.is_running


def test_loopback_guard_rejects_every_non_contract_target() -> None:
    assert_loopback_url("http://127.0.0.1:12345")
    assert_loopback_url("http://127.0.0.1:12345/assets/input.png")

    rejected_urls = [
        "http://localhost:12345",
        "http://[::1]:12345",
        "https://127.0.0.1:12345",
        "http://127.0.0.1",
        "http://127.0.0.1:0",
        "http://user:password@127.0.0.1:12345",
        "not-a-url",
    ]
    for url in rejected_urls:
        with pytest.raises(ValueError):
            assert_loopback_url(url)


def test_all_received_requests_are_recorded_as_ipv4_loopback() -> None:
    with OfflineService() as service, _session() as session:
        session.get(_url(service, "/v1/context"), timeout=1)
        session.get(
            _url(service, "/v1/audit"),
            headers={"X-Audit-Name": "audit-a"},
            timeout=1,
        )
        assert service.state.snapshot()["request_hosts"] == (
            "127.0.0.1",
            "127.0.0.1",
        )


def test_scenario_type_rejects_dynamic_or_invalid_protocol_data() -> None:
    with pytest.raises(ValueError, match="transient_statuses"):
        OfflineServiceScenario((), (200,), DEFAULT.poll_steps)
    with pytest.raises(ValueError, match="transient status"):
        OfflineServiceScenario((418,), (200,), DEFAULT.poll_steps)
    with pytest.raises(ValueError, match="idempotent POST status"):
        OfflineServiceScenario((200,), (429,), DEFAULT.poll_steps)
    with pytest.raises(ValueError, match="output_asset"):
        OfflineServiceScenario(
            (200,),
            (200,),
            DEFAULT.poll_steps,
            output_asset="dynamic",  # type: ignore[arg-type]
        )
