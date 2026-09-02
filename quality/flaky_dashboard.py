from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from functools import lru_cache
from datetime import UTC, datetime
import ipaddress
import json
from pathlib import Path
import threading
import time
from typing import Callable

from pydantic import ValidationError

from quality.flaky_read import FlakyReadService
from quality.flaky_store import FlakyStoreError
from quality.flaky_probe import (
    CsrfProtector,
    FixedJenkinsClient,
    GitTargetResolver,
    JenkinsGateway,
    ProbeControlService,
    ProbeCreateRequest,
    load_probe_runtime_config,
)


DEFAULT_DASHBOARD_HOST = "127.0.0.1"
DEFAULT_DASHBOARD_PORT = 8765


def validate_loopback_host(host: str) -> str:
    value = str(host).strip()
    try:
        address = ipaddress.ip_address(value)
    except ValueError as error:
        raise ValueError("dashboard host must be the literal 127.0.0.1 or ::1") from error
    if not address.is_loopback or value not in {"127.0.0.1", "::1"}:
        raise ValueError("dashboard host must be the literal 127.0.0.1 or ::1")
    return value


def create_app(
    database_path: str | Path,
    *,
    artifact_directory: str | Path | None = None,
    service_factory: Callable[..., FlakyReadService] = FlakyReadService,
    mode_requested: str | None = None,
    mode_effective: str | None = None,
    probe_control: ProbeControlService | None = None,
    probe_gateway: JenkinsGateway | None = None,
    probe_poll_interval_seconds: float = 2.0,
    csrf_protector: CsrfProtector | None = None,
    dashboard_origin: str = "http://127.0.0.1:8765",
):
    try:
        from fastapi import FastAPI, Query, Request
        from fastapi.exceptions import RequestValidationError
        from fastapi.responses import HTMLResponse, JSONResponse
        from fastapi.templating import Jinja2Templates
    except ImportError as error:
        raise RuntimeError(
            "dashboard dependencies are not installed; install requirements-dashboard.txt"
        ) from error

    # FastAPI resolves postponed annotations from module globals. Keep the
    # dependency import lazy while still exposing Request to its resolver.
    globals()["Request"] = Request

    database = Path(database_path)
    artifacts = Path(artifact_directory) if artifact_directory is not None else None
    if mode_requested is None or mode_effective is None:
        from quality.config import load_quality_config

        runtime = load_quality_config()
        mode_requested = runtime.flaky_skip_mode_requested
        mode_effective = runtime.flaky_skip_mode_effective
    service = service_factory(
        database,
        mode_requested=mode_requested,
        mode_effective=mode_effective,
    )
    if probe_control is None:
        probe_runtime = load_probe_runtime_config(repository_root=Path.cwd())
        if (
            probe_runtime.warning is None
            and probe_runtime.jenkins_origin is not None
            and probe_runtime.job_full_name is not None
            and probe_runtime.credential_file is not None
        ):
            probe_control = ProbeControlService(
                database.resolve(),
                probe_runtime,
                target_resolver=GitTargetResolver(
                    Path.cwd(), remote=probe_runtime.git_remote
                ).resolve_branch,
            )
            probe_gateway = probe_gateway or FixedJenkinsClient(probe_runtime)
            if csrf_protector is None and probe_runtime.csrf_secret_file is not None:
                csrf_protector = CsrfProtector(probe_runtime.csrf_secret_file.read_bytes())
    if probe_poll_interval_seconds <= 0:
        raise ValueError("Probe poll interval must be positive")
    probe_actions_enabled = bool(
        probe_control is not None
        and getattr(getattr(probe_control, "runtime", None), "enabled", True)
    )

    @asynccontextmanager
    async def lifespan(app_instance):
        task = None
        if probe_control is not None and probe_gateway is not None:
            task = asyncio.create_task(
                _run_probe_loop(
                    probe_control,
                    probe_gateway,
                    interval_seconds=probe_poll_interval_seconds,
                ),
                name="flaky-probe-dispatch-reconcile",
            )
            app_instance.state.probe_loop_task = task
        try:
            yield
        finally:
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
    templates = Jinja2Templates(directory=Path(__file__).with_name("templates"))
    app = FastAPI(
        title="Flaky Governance MVP Dashboard",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    ready_cache = _ReadinessCache(service)

    def probe_template_response(
        request: Request,
        *,
        name: str,
        context: dict[str, object],
    ):
        token = csrf_protector.issue() if csrf_protector is not None else None
        response = templates.TemplateResponse(
            request=request,
            name=name,
            context={
                **context,
                "probe_enabled": probe_actions_enabled and token is not None,
                "csrf_token": token,
            },
        )
        response.headers["Cache-Control"] = "no-store"
        if token is not None:
            response.set_cookie(
                "flaky_probe_csrf",
                token,
                httponly=True,
                samesite="strict",
                secure=dashboard_origin.startswith("https://"),
            )
        return response

    @app.middleware("http")
    async def reject_unknown_query_fields(request: Request, call_next):
        allowed = _allowed_query_keys(request.url.path)
        if allowed is not None and any(key not in allowed for key in request.query_params):
            return JSONResponse(
                status_code=400,
                content={
                    "error": {
                        "code": "invalid_query",
                        "message": "request parameters are invalid",
                    }
                },
            )
        return await call_next(request)

    @app.exception_handler(FlakyStoreError)
    async def flaky_error(_request: Request, error: FlakyStoreError):
        status = 503
        if error.code in {"identity_not_found", "run_not_found", "governance_not_found"}:
            status = 404
        elif error.code in {"invalid_query", "invalid_cursor", "invalid_page_size", "invalid_probe_request"}:
            status = 400
        elif error.code in {
            "attempt_already_active", "row_version_conflict", "idempotency_conflict",
            "governance_out_of_scope",
        }:
            status = 409
        elif error.code == "probe_capacity_exhausted":
            status = 429
        return JSONResponse(
            status_code=status,
            content={"error": {"code": error.code, "message": _safe_message(error.code)}},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, _error: RequestValidationError):
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "code": "invalid_query",
                    "message": "request parameters are invalid",
                }
            },
        )

    @app.exception_handler(Exception)
    async def unexpected_error(_request: Request, _error: Exception):
        return JSONResponse(
            status_code=503,
            content={
                "error": {
                    "code": "dashboard_unavailable",
                    "message": "dashboard data is unavailable",
                }
            },
        )

    @app.api_route("/health/live", methods=["GET", "HEAD"])
    async def live():
        return {"status": "live"}

    @app.api_route("/health/ready", methods=["GET", "HEAD"])
    async def ready():
        try:
            return ready_cache.get()
        except FlakyStoreError as error:
            return JSONResponse(
                status_code=503,
                content={
                    "status": "not_ready",
                    "error_code": error.code,
                },
            )

    @app.api_route("/api/v1/summary", methods=["GET", "HEAD"])
    async def api_summary():
        return service.summary().model_dump(mode="json")

    @app.api_route("/api/v1/governance", methods=["GET", "HEAD"])
    async def api_governance(
        status: str | None = Query(default=None, max_length=128),
        owner: str | None = Query(default=None, max_length=128),
        overdue: bool | None = None,
        environment: str | None = Query(default=None, max_length=128),
        execution_profile: str | None = Query(default=None, max_length=128),
        case_path: str | None = Query(default=None, max_length=128),
        keyword: str | None = Query(default=None, max_length=128),
        cursor: str | None = Query(default=None, max_length=1024),
        page_size: int = Query(default=50, ge=1, le=100),
    ):
        return service.governance_page(
            status=status,
            owner=owner,
            overdue=overdue,
            environment=environment,
            execution_profile=execution_profile,
            case_path=case_path,
            keyword=keyword,
            cursor=cursor,
            page_size=page_size,
        ).model_dump(mode="json")

    @app.post("/api/v1/governances/{governance_id}/probe-attempts")
    async def create_probe_attempt(request: Request, governance_id: str):
        if request.headers.get("origin") != dashboard_origin:
            return _write_error(JSONResponse, 403, "origin_rejected")
        content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().casefold()
        if content_type != "application/json":
            return _write_error(JSONResponse, 415, "json_required")
        declared = request.headers.get("content-length")
        if declared is not None:
            try:
                if int(declared) > 4096:
                    return _write_error(JSONResponse, 413, "request_too_large")
            except ValueError:
                return _write_error(JSONResponse, 400, "invalid_probe_request")
        body = await request.body()
        if len(body) > 4096:
            return _write_error(JSONResponse, 413, "request_too_large")
        if csrf_protector is None or not csrf_protector.validate(
            cookie_token=request.cookies.get("flaky_probe_csrf"),
            header_token=request.headers.get("x-csrf-token"),
        ):
            return _write_error(JSONResponse, 403, "csrf_rejected")
        try:
            payload = json.loads(body)
            if not isinstance(payload, dict):
                raise ValueError("request body must be an object")
            command = ProbeCreateRequest(governance_id=governance_id, **payload)
        except (UnicodeError, json.JSONDecodeError, TypeError, ValueError, ValidationError):
            return _write_error(JSONResponse, 400, "invalid_probe_request")
        if not probe_actions_enabled or probe_control is None:
            raise FlakyStoreError("probe_trigger_disabled", "Probe trigger is disabled")
        result = probe_control.create_attempt(command, now=datetime.now(UTC))
        return JSONResponse(status_code=201 if result["created"] else 200, content=result)

    @app.api_route("/api/v1/cases/{flaky_key}", methods=["GET", "HEAD"])
    async def api_case(flaky_key: str):
        return service.case_detail(flaky_key).model_dump(mode="json")

    @app.api_route("/api/v1/runs/{run_id}/decisions", methods=["GET", "HEAD"])
    async def api_decisions(run_id: str):
        if artifacts is None:
            raise FlakyStoreError(
                "run_artifact_unavailable", "run artifact directory is not configured"
            )
        return service.run_decisions(run_id, artifacts).model_dump(mode="json")

    @app.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse)
    async def index(request: Request):
        summary = service.summary()
        active = service.governance_page(status="ACTIVE", page_size=100)
        recovering = service.governance_page(status="RECOVERING", page_size=100)
        pending_items = tuple(
            sorted(
                (*active.items, *recovering.items),
                key=lambda item: (
                    not item.overdue,
                    item.expires_at,
                    item.case_id,
                    item.governance_id,
                ),
            )
        )
        return probe_template_response(
            request,
            name="flaky_summary.html",
            context={
                "summary": summary,
                "pending_items": pending_items,
                "pending_truncated": bool(
                    active.next_cursor is not None or recovering.next_cursor is not None
                ),
            },
        )

    @app.api_route(
        "/governance", methods=["GET", "HEAD"], response_class=HTMLResponse
    )
    async def governance_page(
        request: Request,
        status: str | None = Query(default=None, max_length=128),
        owner: str | None = Query(default=None, max_length=128),
        overdue: str | None = Query(default=None, max_length=5),
        environment: str | None = Query(default=None, max_length=128),
        execution_profile: str | None = Query(default=None, max_length=128),
        case_path: str | None = Query(default=None, max_length=128),
        keyword: str | None = Query(default=None, max_length=128),
        cursor: str | None = Query(default=None, max_length=1024),
        page_size: int = Query(default=50, ge=1, le=100),
    ):
        page = service.governance_page(
            status=status or None,
            owner=owner or None,
            overdue=_optional_bool(overdue),
            environment=environment or None,
            execution_profile=execution_profile or None,
            case_path=case_path or None,
            keyword=keyword or None,
            cursor=cursor or None,
            page_size=page_size,
        )
        return probe_template_response(
            request,
            name="flaky_governance.html",
            context={
                "page": page,
            },
        )

    @app.api_route(
        "/cases/{flaky_key}", methods=["GET", "HEAD"], response_class=HTMLResponse
    )
    async def case_page(request: Request, flaky_key: str):
        return templates.TemplateResponse(
            request=request,
            name="flaky_case.html",
            context={"case": service.case_detail(flaky_key)},
        )

    @app.api_route(
        "/runs/{run_id}/decisions",
        methods=["GET", "HEAD"],
        response_class=HTMLResponse,
    )
    async def decisions_page(request: Request, run_id: str):
        if artifacts is None:
            raise FlakyStoreError(
                "run_artifact_unavailable", "run artifact directory is not configured"
            )
        return templates.TemplateResponse(
            request=request,
            name="flaky_decisions.html",
            context={"decisions": service.run_decisions(run_id, artifacts)},
        )

    return app


async def _run_probe_loop(
    control: ProbeControlService,
    gateway: JenkinsGateway,
    *,
    interval_seconds: float,
) -> None:
    while True:
        try:
            await asyncio.to_thread(
                control.dispatch_once, gateway, now=datetime.now(UTC)
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        try:
            await asyncio.to_thread(
                control.reconcile_once, gateway, now=datetime.now(UTC)
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            # A failed dispatch must not suppress reconciliation, and one failed
            # poll must not terminate later crash recovery attempts.
            pass
        await asyncio.sleep(interval_seconds)


def run_dashboard(
    database_path: str | Path,
    *,
    artifact_directory: str | Path | None = None,
    host: str = DEFAULT_DASHBOARD_HOST,
    port: int = DEFAULT_DASHBOARD_PORT,
) -> None:
    validated_host = validate_loopback_host(host)
    if not 1 <= int(port) <= 65535:
        raise ValueError("dashboard port must be between 1 and 65535")
    try:
        import uvicorn
    except ImportError as error:
        raise RuntimeError(
            "dashboard dependencies are not installed; install requirements-dashboard.txt"
        ) from error
    uvicorn.run(
        create_app(
            database_path,
            artifact_directory=artifact_directory,
            dashboard_origin=(
                f"http://[{validated_host}]:{int(port)}"
                if ":" in validated_host
                else f"http://{validated_host}:{int(port)}"
            ),
        ),
        host=validated_host,
        port=int(port),
    )


class _ReadinessCache:
    def __init__(self, service: FlakyReadService, ttl_seconds: float = 5.0) -> None:
        self._service = service
        self._ttl = ttl_seconds
        self._lock = threading.Lock()
        self._expires = 0.0
        self._value: dict[str, object] | None = None

    def get(self) -> dict[str, object]:
        now = time.monotonic()
        with self._lock:
            if self._value is None or now >= self._expires:
                self._value = self._service.readiness()
                self._expires = now + self._ttl
            return dict(self._value)


@lru_cache(maxsize=32)
def _safe_message(code: str) -> str:
    if code in {"identity_not_found", "run_not_found"}:
        return "requested resource was not found"
    if code.startswith("invalid_"):
        return "request parameters are invalid"
    return "dashboard data is unavailable"


def _write_error(response_type, status_code: int, code: str):
    return response_type(
        status_code=status_code,
        content={"error": {"code": code, "message": _safe_message(code)}},
    )


def _optional_bool(value: str | None) -> bool | None:
    if value is None or value == "":
        return None
    normalized = value.casefold()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise FlakyStoreError("invalid_query", "request parameters are invalid")


def _allowed_query_keys(path: str) -> frozenset[str] | None:
    if path in {"/api/v1/governance", "/governance"}:
        return frozenset(
            {
                "status",
                "owner",
                "overdue",
                "environment",
                "execution_profile",
                "case_path",
                "keyword",
                "cursor",
                "page_size",
            }
        )
    if path in {"/", "/health/live", "/health/ready", "/api/v1/summary"}:
        return frozenset()
    if path.startswith(("/api/v1/cases/", "/api/v1/runs/", "/cases/", "/runs/")):
        return frozenset()
    if path.startswith("/api/v1/governances/") and path.endswith("/probe-attempts"):
        return frozenset()
    return None


__all__ = (
    "DEFAULT_DASHBOARD_HOST",
    "DEFAULT_DASHBOARD_PORT",
    "create_app",
    "run_dashboard",
    "validate_loopback_host",
)
