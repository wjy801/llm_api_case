#!/usr/bin/env python3
"""Continuously call one OpenAI-compatible model to trigger prompt cache writes/reads."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time as datetime_time, timedelta, timezone
import os
from pathlib import Path
import sys
from threading import Event
import time
from urllib.parse import urljoin, urlparse
from uuid import uuid4

from dotenv import load_dotenv
import requests


# ==================== Environment configuration ====================

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = PROJECT_ROOT / ".env"

if not ENV_FILE.is_file():
    raise RuntimeError(f"Environment file does not exist: {ENV_FILE}")

# Keep the same precedence as the project configuration: existing process
# environment variables take precedence over values in .env.
load_dotenv(dotenv_path=ENV_FILE, override=False)

USE_CHINA_ENVIRONMENT = (
    os.getenv("USE_CHINA_ENVIRONMENT", "TRUE").strip().upper() == "TRUE"
)

if USE_CHINA_ENVIRONMENT:
    BASE_URL_ENV_NAME = "CHINA_TEST_ENVIRONMENT_BASE_URL"
    API_KEY_ENV_NAME = "CHINA_API_KEY"
else:
    BASE_URL_ENV_NAME = "OVERSEAS_TEST_BASE_URL"
    API_KEY_ENV_NAME = "OVERSEAS_API_KEY"

BASE_URL = os.getenv(BASE_URL_ENV_NAME, "").strip()
API_KEY = os.getenv(API_KEY_ENV_NAME, "").strip()


# ==================== User configuration ====================

# BASE_URL comes from .env. Modify only the API path here.
REQUEST_PATH = "/v1/chat/completions"
MODEL_NAME = "gpt-5.5"

REQUEST_TIMEOUT_SECONDS = 60.0
REQUEST_TIME_OFFSETS = (-1, 0, 1)
IMMEDIATE_WORKER_PREPARE_SECONDS = 1.0
CACHE_WARMUP_LEAD_SECONDS = 30
CACHE_WARMUP_WAIT_SECONDS = 0

VERIFY_TLS = True
MAX_OUTPUT_TOKENS = 8

# When enabled, the first model request starts at the next occurrence of this
# Beijing time. When disabled, the request starts immediately.
SCHEDULE_ENABLED = True
SCHEDULE_START_TIME = "09:07:00"
BEIJING_TIMEZONE = timezone(timedelta(hours=8), name="Asia/Shanghai")

# Use a new cache key per process, then reuse it for warm-up and all three
# scheduled requests in this process.
FORCE_NEW_CACHE_WRITE = True
CACHE_RUN_ID = uuid4().hex if FORCE_NEW_CACHE_WRITE else "stable-chat-cache"

# A cacheable prefix must usually exceed the provider's minimum token count.
# Increase this value if the target provider requires a longer prefix.
CACHE_PREFIX_LINE_COUNT = 48
CACHE_PREFIX_LINE = (
    "这是一段用于缓存触发测试的固定上下文。所有请求必须保持本段内容、排列顺序、"
    "模型名称和生成参数不变。首次请求用于促成服务端写入提示词缓存，后续请求通过"
    "复用相同前缀促成缓存读取。"
)
CACHE_REQUEST_INSTRUCTION = (
    "请完整阅读以上固定上下文。这是缓存写入与读取触发请求，请只回复：OK"
)

# ==================================================================


def _require_configuration() -> None:
    missing_names = [
        name
        for name, value in (
            (BASE_URL_ENV_NAME, BASE_URL),
            (API_KEY_ENV_NAME, API_KEY),
        )
        if not value
    ]
    if missing_names:
        names = ", ".join(missing_names)
        raise ValueError(f"Missing required values in {ENV_FILE}: {names}")

    parsed_base_url = urlparse(BASE_URL)
    if parsed_base_url.scheme not in {"http", "https"} or not parsed_base_url.netloc:
        raise ValueError(f"Invalid {BASE_URL_ENV_NAME}: expected an HTTP(S) base URL")
    if not REQUEST_PATH.strip():
        raise ValueError("REQUEST_PATH must not be empty")
    if not MODEL_NAME.strip():
        raise ValueError("MODEL_NAME must not be empty")
    if REQUEST_TIMEOUT_SECONDS <= 0:
        raise ValueError("REQUEST_TIMEOUT_SECONDS must be greater than 0")
    if not REQUEST_TIME_OFFSETS:
        raise ValueError("REQUEST_TIME_OFFSETS must not be empty")
    if len(set(REQUEST_TIME_OFFSETS)) != len(REQUEST_TIME_OFFSETS):
        raise ValueError("REQUEST_TIME_OFFSETS must not contain duplicates")
    if 0 not in REQUEST_TIME_OFFSETS:
        raise ValueError("REQUEST_TIME_OFFSETS must contain 0")
    if IMMEDIATE_WORKER_PREPARE_SECONDS <= 0:
        raise ValueError("IMMEDIATE_WORKER_PREPARE_SECONDS must be greater than 0")
    if CACHE_WARMUP_LEAD_SECONDS <= 0:
        raise ValueError("CACHE_WARMUP_LEAD_SECONDS must be greater than 0")
    if CACHE_WARMUP_WAIT_SECONDS < 0:
        raise ValueError("CACHE_WARMUP_WAIT_SECONDS must not be negative")
    if (
        SCHEDULE_ENABLED
        and CACHE_WARMUP_LEAD_SECONDS <= CACHE_WARMUP_WAIT_SECONDS
    ):
        raise ValueError(
            "CACHE_WARMUP_LEAD_SECONDS must be greater than "
            "CACHE_WARMUP_WAIT_SECONDS when scheduling is enabled"
        )
    if MAX_OUTPUT_TOKENS <= 0:
        raise ValueError("MAX_OUTPUT_TOKENS must be greater than 0")
    if CACHE_PREFIX_LINE_COUNT <= 0:
        raise ValueError("CACHE_PREFIX_LINE_COUNT must be greater than 0")
    if SCHEDULE_ENABLED:
        _parse_schedule_start_time(SCHEDULE_START_TIME)


def _parse_schedule_start_time(value: str) -> datetime_time:
    normalized = value.strip()
    if (
        len(normalized) != 8
        or normalized[2] != ":"
        or normalized[5] != ":"
        or not normalized.replace(":", "").isdigit()
    ):
        raise ValueError("SCHEDULE_START_TIME must use HH:MM:SS format")

    hour, minute, second = (int(part) for part in normalized.split(":"))
    try:
        return datetime_time(hour=hour, minute=minute, second=second)
    except ValueError as error:
        raise ValueError(
            "SCHEDULE_START_TIME must be a valid Beijing time"
        ) from error


def _resolve_next_scheduled_start(
    now: datetime,
    scheduled_time: datetime_time,
) -> datetime:
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")

    now_beijing = now.astimezone(BEIJING_TIMEZONE)
    target = now_beijing.replace(
        hour=scheduled_time.hour,
        minute=scheduled_time.minute,
        second=scheduled_time.second,
        microsecond=0,
    )

    earliest_request = target + timedelta(seconds=min(REQUEST_TIME_OFFSETS))
    if now_beijing > earliest_request:
        target += timedelta(days=1)
    return target


def _wait_until(
    target: datetime,
    event_name: str,
    abort_event: Event | None = None,
) -> bool:
    while True:
        if abort_event is not None and abort_event.is_set():
            return False
        remaining_seconds = (
            target - datetime.now(BEIJING_TIMEZONE)
        ).total_seconds()
        if remaining_seconds <= 0:
            print(f"{event_name} reached")
            return True
        sleep_seconds = min(remaining_seconds, 0.5 if remaining_seconds > 1 else 0.01)
        time.sleep(sleep_seconds)


def _build_request_targets(center: datetime) -> list[tuple[int, datetime]]:
    return [
        (offset, center + timedelta(seconds=offset))
        for offset in sorted(REQUEST_TIME_OFFSETS)
    ]


def _build_request_url() -> str:
    return urljoin(f"{BASE_URL.rstrip('/')}/", REQUEST_PATH.lstrip("/"))


def _build_cache_prefix() -> str:
    reference = "\n".join(
        f"固定上下文条目 {index:04d}：{CACHE_PREFIX_LINE}"
        for index in range(1, CACHE_PREFIX_LINE_COUNT + 1)
    )
    return f"缓存批次标识：{CACHE_RUN_ID}\n{reference}"


def _build_payload(cache_prefix: str) -> dict[str, object]:
    # The complete request body intentionally stays identical across calls.
    # The first successful call can populate the cache; later calls can reuse
    # either a prompt-prefix cache or a whole-request gateway cache.
    return {
        "model": MODEL_NAME,
        "messages": [
            {
                "role": "system",
                "content": cache_prefix,
            },
            {
                "role": "user",
                "content": CACHE_REQUEST_INSTRUCTION,
            },
        ],
        "max_tokens": MAX_OUTPUT_TOKENS,
        "temperature": 0,
        "stream": False,
    }


def _send_request(
    session: requests.Session,
    request_url: str,
    payload: dict[str, object],
) -> int:
    response = session.post(
        request_url,
        json=payload,
        timeout=REQUEST_TIMEOUT_SECONDS,
        verify=VERIFY_TLS,
    )
    try:
        return response.status_code
    finally:
        response.close()


def _build_request_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "api-case-cache-request-runner",
    }


def _run_one_scheduled_request(
    request_url: str,
    payload: dict[str, object],
    offset_seconds: int,
    target: datetime,
    abort_event: Event,
) -> tuple[int, datetime, datetime | None, int | None, str | None]:
    with requests.Session() as session:
        session.headers.update(_build_request_headers())
        if not _wait_until(
            target,
            f"request offset {offset_seconds:+d}s",
            abort_event,
        ):
            return offset_seconds, target, None, None, "aborted before dispatch"

        dispatched_at = datetime.now(BEIJING_TIMEZONE)
        try:
            status_code = _send_request(session, request_url, payload)
        except requests.RequestException as error:
            return (
                offset_seconds,
                target,
                dispatched_at,
                None,
                f"{type(error).__name__}: {error}",
            )
    return offset_seconds, target, dispatched_at, status_code, None


def _summarize_scheduled_results(
    results: list[tuple[int, datetime, datetime | None, int | None, str | None]],
) -> tuple[int, int]:
    success_count = 0
    failure_count = 0
    for offset_seconds, target, dispatched_at, status_code, error in results:
        if dispatched_at is None:
            failure_count += 1
            print(
                f"request {offset_seconds:+d}s failed: {error}",
                file=sys.stderr,
            )
            continue

        delay_ms = (dispatched_at - target).total_seconds() * 1000
        if error is not None:
            failure_count += 1
            print(
                f"request {offset_seconds:+d}s failed: {error}, "
                f"dispatch_delay_ms={delay_ms:.3f}",
                file=sys.stderr,
            )
        elif status_code is not None and 200 <= status_code < 300:
            success_count += 1
            print(
                f"request {offset_seconds:+d}s: HTTP {status_code}, "
                f"dispatch_delay_ms={delay_ms:.3f}"
            )
        else:
            failure_count += 1
            print(
                f"request {offset_seconds:+d}s failed: HTTP {status_code}, "
                f"dispatch_delay_ms={delay_ms:.3f}",
                file=sys.stderr,
            )

    return success_count, failure_count


def main() -> int:
    try:
        _require_configuration()
    except ValueError as error:
        print(f"configuration error: {error}", file=sys.stderr)
        return 2

    request_url = _build_request_url()
    cache_prefix = _build_cache_prefix()
    payload = _build_payload(cache_prefix)
    scheduled_center: datetime | None = None
    request_targets: list[tuple[int, datetime]] = []
    warmup_target: datetime | None = None

    if SCHEDULE_ENABLED:
        scheduled_center = _resolve_next_scheduled_start(
            datetime.now(BEIJING_TIMEZONE),
            _parse_schedule_start_time(SCHEDULE_START_TIME),
        )
        request_targets = _build_request_targets(scheduled_center)
        warmup_target = request_targets[0][1] - timedelta(
            seconds=CACHE_WARMUP_LEAD_SECONDS
        )

    print(f"request URL: {request_url}")
    print(f"model: {MODEL_NAME}")
    if scheduled_center is None:
        print("scheduled start: disabled; cache warm-up starts immediately")
    else:
        print(
            "cache warm-up (Beijing time): "
            f"{warmup_target:%Y-%m-%d %H:%M:%S}"
        )
        for offset_seconds, target in request_targets:
            print(
                f"request {offset_seconds:+d}s (Beijing time): "
                f"{target:%Y-%m-%d %H:%M:%S}"
            )

    abort_event = Event()
    executor = ThreadPoolExecutor(max_workers=len(REQUEST_TIME_OFFSETS))
    futures = []

    try:
        if scheduled_center is not None:
            futures = [
                executor.submit(
                    _run_one_scheduled_request,
                    request_url,
                    payload,
                    offset_seconds,
                    target,
                    abort_event,
                )
                for offset_seconds, target in request_targets
            ]

        with requests.Session() as session:
            session.headers.update(_build_request_headers())
            if warmup_target is not None:
                _wait_until(warmup_target, "cache warm-up time")

            try:
                warmup_status = _send_request(session, request_url, payload)
            except requests.RequestException as error:
                print(
                    f"cache warm-up failed: {type(error).__name__}: {error}",
                    file=sys.stderr,
                )
                abort_event.set()
                return 1

            if not 200 <= warmup_status < 300:
                print(f"cache warm-up failed: HTTP {warmup_status}", file=sys.stderr)
                abort_event.set()
                return 1

            print(f"cache warm-up succeeded: HTTP {warmup_status}")
            cache_ready_at = datetime.now(BEIJING_TIMEZONE) + timedelta(
                seconds=CACHE_WARMUP_WAIT_SECONDS
            )

            if scheduled_center is None:
                first_request_at = cache_ready_at + timedelta(
                    seconds=IMMEDIATE_WORKER_PREPARE_SECONDS
                )
                immediate_center = first_request_at - timedelta(
                    seconds=min(REQUEST_TIME_OFFSETS)
                )
                request_targets = _build_request_targets(immediate_center)
                futures = [
                    executor.submit(
                        _run_one_scheduled_request,
                        request_url,
                        payload,
                        offset_seconds,
                        target,
                        abort_event,
                    )
                    for offset_seconds, target in request_targets
                ]
            elif cache_ready_at > request_targets[0][1]:
                delay_seconds = (cache_ready_at - request_targets[0][1]).total_seconds()
                print(
                    "warning: cache may not be ready for the earliest request; "
                    f"estimated readiness is {delay_seconds:.3f} seconds late",
                    file=sys.stderr,
                )

        results = [future.result() for future in futures]
        success_count, failure_count = _summarize_scheduled_results(results)
    except KeyboardInterrupt:
        abort_event.set()
        print("scheduled requests interrupted by user", file=sys.stderr)
        return 130
    finally:
        abort_event.set()
        executor.shutdown(wait=True, cancel_futures=True)

    print(f"scheduled requests finished: success={success_count}, failure={failure_count}")
    return 0 if failure_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
