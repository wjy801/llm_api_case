from __future__ import annotations

from base64 import b64decode
from collections import Counter
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import sys
from threading import Event, RLock, Thread
from types import MappingProxyType
from typing import Any, Final, Literal, cast
from urllib.parse import unquote, urlsplit


OFFLINE_MODEL_ID: Final = "offline-media-model"
OFFLINE_TASK_ID: Final = "offline-task-001"
OFFLINE_REQUEST_ID: Final = "offline-request-001"
OFFLINE_TRACE_ID: Final = "offline-trace-001"
OFFLINE_COOKIE_NAME: Final = "offline_session"
OFFLINE_COOKIE_VALUE: Final = "offline-session-001"
OFFLINE_API_KEY: Final = "offline-example-key"
OFFLINE_IDEMPOTENCY_KEY: Final = "offline-idempotency-001"

INPUT_PNG_BYTES: Final = b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNgYGBgAAAABQABeqhXUAAAAABJRU5ErkJggg=="
)
INPUT_PNG_SHA256: Final = (
    "f2bb5bbaca678ecad746b1fa5ecfa2c8a81dd18817be19f0187c036d25326317"
)
OUTPUT_PNG_BYTES: Final = b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP4z8DwHwAFAAH/VscvDQAAAABJRU5ErkJggg=="
)
OUTPUT_PNG_SHA256: Final = (
    "49e1dad481e94dfab7c9573a9a81d56aa2ca629fe15a3f7a910aa4f47601c00d"
)
OVERSIZED_OUTPUT_BYTES: Final = OUTPUT_PNG_BYTES + OUTPUT_PNG_BYTES


@dataclass(frozen=True, slots=True)
class OfflinePollStep:
    http_status: int
    status: str | None = None

    def __post_init__(self) -> None:
        if self.http_status == 503:
            if self.status is not None:
                raise ValueError("HTTP 503 poll step cannot contain a business status")
            return
        if self.http_status != 200:
            raise ValueError("poll step HTTP status must be 200 or 503")
        if not isinstance(self.status, str) or not self.status:
            raise ValueError("HTTP 200 poll step requires a business status")


@dataclass(frozen=True, slots=True)
class OfflineServiceScenario:
    transient_statuses: tuple[int, ...]
    idempotent_post_statuses: tuple[int, ...]
    poll_steps: tuple[OfflinePollStep, ...]
    output_asset: Literal["normal", "oversized"] = "normal"

    def __post_init__(self) -> None:
        if not self.transient_statuses:
            raise ValueError("transient_statuses cannot be empty")
        if not self.idempotent_post_statuses:
            raise ValueError("idempotent_post_statuses cannot be empty")
        if not self.poll_steps:
            raise ValueError("poll_steps cannot be empty")
        if any(status not in {200, 429, 503} for status in self.transient_statuses):
            raise ValueError("transient status must be 200, 429, or 503")
        if any(status not in {200, 503} for status in self.idempotent_post_statuses):
            raise ValueError("idempotent POST status must be 200 or 503")
        if self.output_asset not in {"normal", "oversized"}:
            raise ValueError("output_asset must be 'normal' or 'oversized'")


DEFAULT = OfflineServiceScenario(
    transient_statuses=(200,),
    idempotent_post_statuses=(200,),
    poll_steps=(OfflinePollStep(200, "succeeded"),),
)
GET_503_THEN_200 = OfflineServiceScenario(
    transient_statuses=(503, 200),
    idempotent_post_statuses=(200,),
    poll_steps=(OfflinePollStep(200, "succeeded"),),
)
GET_429_THEN_200 = OfflineServiceScenario(
    transient_statuses=(429, 200),
    idempotent_post_statuses=(200,),
    poll_steps=(OfflinePollStep(200, "succeeded"),),
)
POST_503_THEN_200 = OfflineServiceScenario(
    transient_statuses=(200,),
    idempotent_post_statuses=(503, 200),
    poll_steps=(OfflinePollStep(200, "succeeded"),),
)
POLL_SUCCESS_WITH_RETRY = OfflineServiceScenario(
    transient_statuses=(200,),
    idempotent_post_statuses=(200,),
    poll_steps=(
        OfflinePollStep(503),
        OfflinePollStep(200, "queued"),
        OfflinePollStep(200, "running"),
        OfflinePollStep(200, "succeeded"),
    ),
)
POLL_FAILURE = OfflineServiceScenario(
    transient_statuses=(200,),
    idempotent_post_statuses=(200,),
    poll_steps=(
        OfflinePollStep(200, "queued"),
        OfflinePollStep(200, "failed"),
    ),
)
POLL_UNKNOWN = OfflineServiceScenario(
    transient_statuses=(200,),
    idempotent_post_statuses=(200,),
    poll_steps=(
        OfflinePollStep(200, "queued"),
        OfflinePollStep(200, "paused"),
    ),
)
POLL_TIMEOUT = OfflineServiceScenario(
    transient_statuses=(200,),
    idempotent_post_statuses=(200,),
    poll_steps=(OfflinePollStep(200, "running"),),
)
CAPTURE_OVERSIZED = OfflineServiceScenario(
    transient_statuses=(200,),
    idempotent_post_statuses=(200,),
    poll_steps=(OfflinePollStep(200, "succeeded"),),
    output_asset="oversized",
)

OFFLINE_SERVICE_SCENARIOS = MappingProxyType(
    {
        "DEFAULT": DEFAULT,
        "GET_503_THEN_200": GET_503_THEN_200,
        "GET_429_THEN_200": GET_429_THEN_200,
        "POST_503_THEN_200": POST_503_THEN_200,
        "POLL_SUCCESS_WITH_RETRY": POLL_SUCCESS_WITH_RETRY,
        "POLL_FAILURE": POLL_FAILURE,
        "POLL_UNKNOWN": POLL_UNKNOWN,
        "POLL_TIMEOUT": POLL_TIMEOUT,
        "CAPTURE_OVERSIZED": CAPTURE_OVERSIZED,
    }
)


@dataclass(slots=True)
class _OfflineTaskCursor:
    steps: tuple[OfflinePollStep, ...]
    index: int = 0

    def next_step(self) -> OfflinePollStep:
        step = self.steps[min(self.index, len(self.steps) - 1)]
        if self.index < len(self.steps) - 1:
            self.index += 1
        return step


@dataclass(slots=True)
class OfflineServiceState:
    scenario: OfflineServiceScenario
    lock: RLock = field(default_factory=RLock, repr=False)
    tasks: dict[str, _OfflineTaskCursor] = field(default_factory=dict)
    endpoint_call_counts: Counter[str] = field(default_factory=Counter)
    transient_index: int = 0
    idempotent_post_index: int = 0
    audit_records: list[str] = field(default_factory=list)
    deleted_task_ids: list[str] = field(default_factory=list)
    request_hosts: list[str] = field(default_factory=list)
    input_asset_requested: Event = field(default_factory=Event, repr=False)
    output_asset_requested: Event = field(default_factory=Event, repr=False)
    oversized_asset_requested: Event = field(default_factory=Event, repr=False)
    handler_errors: list[str] = field(default_factory=list)

    def record_request_host(self, host_header: str) -> None:
        try:
            hostname = urlsplit(f"//{host_header}").hostname
        except ValueError:
            hostname = None
        with self.lock:
            self.request_hosts.append(hostname or host_header)

    def next_transient_status(self) -> tuple[int, int]:
        with self.lock:
            self.endpoint_call_counts["transient_calls"] += 1
            attempt = self.endpoint_call_counts["transient_calls"]
            statuses = self.scenario.transient_statuses
            status = statuses[min(self.transient_index, len(statuses) - 1)]
            if self.transient_index < len(statuses) - 1:
                self.transient_index += 1
            return status, attempt

    def next_idempotent_post_status(self) -> int:
        with self.lock:
            self.endpoint_call_counts["idempotent_operation_calls"] += 1
            statuses = self.scenario.idempotent_post_statuses
            status = statuses[min(self.idempotent_post_index, len(statuses) - 1)]
            if self.idempotent_post_index < len(statuses) - 1:
                self.idempotent_post_index += 1
            return status

    def create_task(self) -> None:
        with self.lock:
            self.endpoint_call_counts["media_create_calls"] += 1
            self.tasks[OFFLINE_TASK_ID] = _OfflineTaskCursor(self.scenario.poll_steps)

    def next_poll_step(self, task_id: str) -> OfflinePollStep | None:
        with self.lock:
            self.endpoint_call_counts["media_poll_calls"] += 1
            task = self.tasks.get(task_id)
            return None if task is None else task.next_step()

    def delete_task(self, task_id: str) -> bool:
        with self.lock:
            self.endpoint_call_counts["media_delete_calls"] += 1
            if task_id not in self.tasks:
                return False
            del self.tasks[task_id]
            self.deleted_task_ids.append(task_id)
            return True

    def record_call(self, counter_name: str) -> None:
        with self.lock:
            self.endpoint_call_counts[counter_name] += 1

    def record_audit(self, audit_name: str) -> None:
        with self.lock:
            self.endpoint_call_counts["audit_calls"] += 1
            self.audit_records.append(audit_name)

    def record_asset(self, asset_name: str) -> None:
        event_by_name = {
            "input": self.input_asset_requested,
            "output": self.output_asset_requested,
            "oversized": self.oversized_asset_requested,
        }
        with self.lock:
            self.endpoint_call_counts[f"{asset_name}_asset_calls"] += 1
            event = event_by_name[asset_name]
        event.set()

    def record_handler_error(self, error: BaseException) -> None:
        message = f"{type(error).__name__}: {error}"
        with self.lock:
            self.handler_errors.append(message)

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "tasks": tuple(self.tasks),
                "endpoint_call_counts": dict(self.endpoint_call_counts),
                "audit_records": tuple(self.audit_records),
                "deleted_task_ids": tuple(self.deleted_task_ids),
                "request_hosts": tuple(self.request_hosts),
                "handler_errors": tuple(self.handler_errors),
            }


class _OfflineHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        server_address: tuple[str, int],
        handler_class: type[BaseHTTPRequestHandler],
        service: OfflineService,
    ) -> None:
        self.offline_service = service
        super().__init__(server_address, handler_class)

    def handle_error(self, request: Any, client_address: Any) -> None:
        error = sys.exc_info()[1]
        if error is not None:
            self.offline_service.state.record_handler_error(error)


class _InvalidRequestBody(ValueError):
    pass


class _OfflineRequestHandler(BaseHTTPRequestHandler):
    server_version = "OfflineFrameworkExample/1.0"
    sys_version = ""

    @property
    def service(self) -> OfflineService:
        server = cast(_OfflineHTTPServer, self.server)
        return server.offline_service

    def log_message(self, format: str, *args: Any) -> None:
        return None

    def do_GET(self) -> None:
        self._record_host()
        path = urlsplit(self.path).path

        if path == "/v1/transient":
            self._handle_transient()
            return
        if path.startswith("/v1/media/tasks/"):
            task_id = unquote(path.removeprefix("/v1/media/tasks/"))
            if task_id and "/" not in task_id:
                self._handle_media_poll(task_id)
                return
        if path == "/v1/context":
            self._handle_context()
            return
        if path == "/v1/audit":
            self._handle_audit()
            return
        if path.startswith("/v1/contracts/"):
            mode = unquote(path.removeprefix("/v1/contracts/"))
            if mode and "/" not in mode:
                self._handle_contract(mode)
                return
        if path == "/assets/input.png":
            self._handle_asset("input", INPUT_PNG_BYTES)
            return
        if path == "/assets/output.png":
            self._handle_asset("output", OUTPUT_PNG_BYTES)
            return
        if path == "/assets/oversized-output.png":
            self._handle_asset("oversized", OVERSIZED_OUTPUT_BYTES)
            return
        self._write_not_found()

    def do_POST(self) -> None:
        self._record_host()
        path = urlsplit(self.path).path
        if path not in {
            "/v1/echo",
            "/v1/idempotent-operation",
            "/v1/media/generations",
        }:
            self._discard_request_body()
            self._write_not_found()
            return

        try:
            body = self._read_json_object()
        except _InvalidRequestBody as error:
            self._write_json(400, {"error": {"code": str(error)}})
            return

        if path == "/v1/echo":
            self._handle_echo(body)
        elif path == "/v1/idempotent-operation":
            self._handle_idempotent_operation()
        else:
            self._handle_media_create()

    def do_DELETE(self) -> None:
        self._record_host()
        path = urlsplit(self.path).path
        if path.startswith("/v1/media/tasks/"):
            task_id = unquote(path.removeprefix("/v1/media/tasks/"))
            if task_id and "/" not in task_id:
                self._handle_media_delete(task_id)
                return
        self._write_not_found()

    def _record_host(self) -> None:
        self.service.state.record_request_host(self.headers.get("Host", ""))

    def _read_json_object(self) -> dict[str, Any]:
        content_length = self.headers.get("Content-Length")
        if content_length is None:
            raise _InvalidRequestBody("INVALID_CONTENT_LENGTH")
        try:
            length = int(content_length)
        except (TypeError, ValueError) as error:
            raise _InvalidRequestBody("INVALID_CONTENT_LENGTH") from error
        if length <= 0:
            raise _InvalidRequestBody("INVALID_JSON")
        try:
            raw_body = self.rfile.read(length)
            payload = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise _InvalidRequestBody("INVALID_JSON") from error
        if not isinstance(payload, dict):
            raise _InvalidRequestBody("INVALID_JSON_BODY")
        return payload

    def _discard_request_body(self) -> None:
        content_length = self.headers.get("Content-Length")
        if content_length is None:
            return
        try:
            length = int(content_length)
        except (TypeError, ValueError):
            return
        if length > 0:
            self.rfile.read(length)

    def _handle_echo(self, body: dict[str, Any]) -> None:
        self.service.state.record_call("echo_calls")
        authorization_present = self.headers.get("Authorization") == (
            f"Bearer {OFFLINE_API_KEY}"
        )
        self._write_json(
            200,
            {
                "received": body,
                "authorization_present": authorization_present,
                "status": "accepted",
            },
        )

    def _handle_transient(self) -> None:
        status, attempt = self.service.state.next_transient_status()
        if status == 503:
            self._write_json(
                503,
                {"error": {"code": "TEMPORARY_UNAVAILABLE"}},
            )
            return
        if status == 429:
            self._write_json(
                429,
                {"error": {"code": "RATE_LIMITED"}},
                headers={"Retry-After": "0"},
            )
            return
        self._write_json(200, {"status": "ok", "attempt": attempt})

    def _handle_idempotent_operation(self) -> None:
        status = self.service.state.next_idempotent_post_status()
        if status == 503:
            self._write_json(
                503,
                {"error": {"code": "TEMPORARY_UNAVAILABLE"}},
            )
            return
        self._write_json(
            200,
            {"operation": "offline-write", "status": "committed"},
        )

    def _handle_media_create(self) -> None:
        self.service.state.create_task()
        self._write_json(
            202,
            {
                "task_id": OFFLINE_TASK_ID,
                "status": "queued",
                "model": OFFLINE_MODEL_ID,
                "trace_id": OFFLINE_TRACE_ID,
            },
            headers=self._task_headers(),
        )

    def _handle_media_poll(self, task_id: str) -> None:
        step = self.service.state.next_poll_step(task_id)
        if step is None:
            self._write_json(404, {"error": {"code": "TASK_NOT_FOUND"}})
            return
        if step.http_status == 503:
            self._write_json(
                503,
                {
                    "error": {
                        "code": "TEMPORARY_POLL_UNAVAILABLE",
                        "type": "transport",
                    }
                },
            )
            return

        status = cast(str, step.status)
        if status == "failed":
            body: dict[str, Any] = {
                "task_id": OFFLINE_TASK_ID,
                "status": "failed",
                "error": {
                    "code": "OFFLINE_TASK_FAILED",
                    "type": "controlled_offline_failure",
                    "message": "controlled failure",
                },
            }
        elif status == "paused":
            body = {"task_id": OFFLINE_TASK_ID, "status": "paused"}
        else:
            body = {
                "task_id": OFFLINE_TASK_ID,
                "status": status,
                "model": OFFLINE_MODEL_ID,
                "trace_id": OFFLINE_TRACE_ID,
            }
            if status == "succeeded":
                asset_path = (
                    "/assets/oversized-output.png"
                    if self.service.scenario.output_asset == "oversized"
                    else "/assets/output.png"
                )
                body["result"] = {"url": f"{self.service.base_url}{asset_path}"}
        self._write_json(200, body, headers=self._task_headers())

    def _handle_media_delete(self, task_id: str) -> None:
        if not self.service.state.delete_task(task_id):
            self._write_json(404, {"error": {"code": "TASK_NOT_FOUND"}})
            return
        self._write_bytes(204, b"")

    def _handle_context(self) -> None:
        self.service.state.record_call("context_calls")
        self._write_json(
            200,
            {
                "data": {"task_id": OFFLINE_TASK_ID},
                "message": f"trace={OFFLINE_TRACE_ID}",
            },
            headers=self._task_headers(),
        )

    def _handle_audit(self) -> None:
        audit_name = self.headers.get("X-Audit-Name", "").strip()
        if not audit_name:
            self._write_json(400, {"error": {"code": "AUDIT_NAME_REQUIRED"}})
            return
        self.service.state.record_audit(audit_name)
        self._write_json(
            200,
            {
                "audit_name": audit_name,
                "task_id": OFFLINE_TASK_ID,
                "status": "recorded",
            },
        )

    def _handle_contract(self, mode: str) -> None:
        self.service.state.record_call("contract_calls")
        if mode == "valid":
            self._write_json(
                200,
                {
                    "task_id": OFFLINE_TASK_ID,
                    "status": "succeeded",
                    "model": OFFLINE_MODEL_ID,
                },
            )
            return
        if mode == "business_error":
            self._write_json(
                400,
                {
                    "error": {
                        "code": "OFFLINE_BUSINESS_ERROR",
                        "type": "controlled_offline_failure",
                        "message": "controlled failure",
                    }
                },
            )
            return
        if mode == "invalid_schema":
            self._write_json(200, {"status": "succeeded"})
            return
        self._write_json(
            404,
            {"error": {"code": "CONTRACT_MODE_NOT_FOUND"}},
        )

    def _handle_asset(self, asset_name: str, content: bytes) -> None:
        self.service.state.record_asset(asset_name)
        self._write_bytes(200, content, content_type="image/png")

    @staticmethod
    def _task_headers() -> dict[str, str]:
        return {
            "X-Request-ID": OFFLINE_REQUEST_ID,
            "Set-Cookie": (
                f"{OFFLINE_COOKIE_NAME}={OFFLINE_COOKIE_VALUE}; Path=/"
            ),
        }

    def _write_not_found(self) -> None:
        self._write_json(404, {"error": {"code": "NOT_FOUND"}})

    def _write_json(
        self,
        status: int,
        payload: dict[str, Any],
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        content = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        self._write_bytes(
            status,
            content,
            content_type="application/json; charset=utf-8",
            headers=headers,
        )

    def _write_bytes(
        self,
        status: int,
        content: bytes,
        *,
        content_type: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        if content_type is not None:
            self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if content:
            self.wfile.write(content)


class OfflineService:
    def __init__(self, scenario: OfflineServiceScenario = DEFAULT) -> None:
        if not isinstance(scenario, OfflineServiceScenario):
            raise TypeError("scenario must be an OfflineServiceScenario")
        self.scenario = scenario
        self.state = OfflineServiceState(scenario)
        self._lifecycle_lock = RLock()
        self._server: _OfflineHTTPServer | None = None
        self._thread: Thread | None = None
        self._thread_name: str | None = None
        self._base_url: str | None = None
        self._started_once = False
        self._stopped = False

    @property
    def base_url(self) -> str:
        with self._lifecycle_lock:
            if self._base_url is None:
                raise RuntimeError("offline service has not been started")
            return self._base_url

    @property
    def thread_name(self) -> str:
        with self._lifecycle_lock:
            if self._thread_name is None:
                raise RuntimeError("offline service has not been started")
            return self._thread_name

    @property
    def is_running(self) -> bool:
        with self._lifecycle_lock:
            return self._thread is not None and self._thread.is_alive()

    def start(self) -> OfflineService:
        with self._lifecycle_lock:
            if self._server is not None and self._thread is not None:
                if self._thread.is_alive():
                    return self
            if self._started_once or self._stopped:
                raise RuntimeError("a stopped offline service cannot be restarted")

            server: _OfflineHTTPServer | None = None
            thread: Thread | None = None
            ready = Event()
            cancelled = Event()
            try:
                server = _OfflineHTTPServer(
                    ("127.0.0.1", 0),
                    _OfflineRequestHandler,
                    self,
                )
                host, port = server.server_address[:2]
                self._base_url = f"http://{host}:{port}"
                thread_name = f"offline-http-server-{port}"

                def serve() -> None:
                    ready.set()
                    if cancelled.is_set():
                        return
                    try:
                        server.serve_forever(poll_interval=0.01)
                    except BaseException as error:
                        self.state.record_handler_error(error)

                thread = Thread(target=serve, name=thread_name, daemon=True)
                self._server = server
                self._thread = thread
                self._thread_name = thread_name
                thread.start()
                if not ready.wait(timeout=2):
                    cancelled.set()
                    raise RuntimeError("offline service thread did not become ready")
                if not thread.is_alive():
                    raise RuntimeError("offline service thread stopped during startup")
                self._started_once = True
                return self
            except BaseException:
                cancelled.set()
                self._server = None
                self._thread = None
                self._stopped = True
                if server is not None:
                    server.server_close()
                if thread is not None:
                    thread.join(timeout=2)
                raise

    def stop(self) -> None:
        with self._lifecycle_lock:
            if self._stopped:
                return
            server = self._server
            thread = self._thread
            if server is None:
                return
            self._server = None
            self._thread = None
            self._stopped = True

        errors: list[str] = []
        if thread is not None and thread.is_alive():
            try:
                server.shutdown()
            except BaseException as error:
                errors.append(f"shutdown failed: {type(error).__name__}: {error}")
        try:
            server.server_close()
        except BaseException as error:
            errors.append(f"server_close failed: {type(error).__name__}: {error}")
        if thread is not None:
            thread.join(timeout=2)
            if thread.is_alive():
                errors.append(f"server thread did not stop: {thread.name}")
        if errors:
            raise RuntimeError("; ".join(errors))

    def __enter__(self) -> OfflineService:
        return self.start()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: Any,
    ) -> None:
        self.stop()


def assert_loopback_url(url: str) -> None:
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except (TypeError, ValueError) as error:
        raise ValueError("offline URL is invalid") from error

    target = parsed.hostname or "<missing>"
    if parsed.scheme != "http":
        raise ValueError(f"offline URL must use http, target={target!r}")
    if parsed.hostname != "127.0.0.1":
        raise ValueError(f"offline URL must target 127.0.0.1, target={target!r}")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError(f"offline URL cannot contain userinfo, target={target!r}")
    if port is None or port == 0:
        raise ValueError(f"offline URL requires an explicit port, target={target!r}")


__all__ = [
    "CAPTURE_OVERSIZED",
    "DEFAULT",
    "GET_429_THEN_200",
    "GET_503_THEN_200",
    "INPUT_PNG_BYTES",
    "INPUT_PNG_SHA256",
    "OFFLINE_API_KEY",
    "OFFLINE_COOKIE_NAME",
    "OFFLINE_COOKIE_VALUE",
    "OFFLINE_IDEMPOTENCY_KEY",
    "OFFLINE_MODEL_ID",
    "OFFLINE_REQUEST_ID",
    "OFFLINE_SERVICE_SCENARIOS",
    "OFFLINE_TASK_ID",
    "OFFLINE_TRACE_ID",
    "OUTPUT_PNG_BYTES",
    "OUTPUT_PNG_SHA256",
    "OVERSIZED_OUTPUT_BYTES",
    "POLL_FAILURE",
    "POLL_SUCCESS_WITH_RETRY",
    "POLL_TIMEOUT",
    "POLL_UNKNOWN",
    "POST_503_THEN_200",
    "OfflinePollStep",
    "OfflineService",
    "OfflineServiceScenario",
    "OfflineServiceState",
    "assert_loopback_url",
]
