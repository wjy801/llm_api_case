#!/usr/bin/env python3
"""Continuously send one stable Responses API payload for cache triggering."""

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

REQUEST_PATH = "/v1/responses"

MODEL_NAME = "gpt-5.5"
INPUT_TEXT = "请用三点总结企业知识库接入大模型网关的注意事项。"
INSTRUCTIONS = "回答要简洁，避免编造。"
REASONING_EFFORT = "medium"
MAX_OUTPUT_TOKENS = 800
STREAM = False

# Generate one new prefix per process. The first request of this process gets a
# new cache key, while all later requests reuse exactly the same prefix.
FORCE_NEW_CACHE_WRITE = True
CACHE_RUN_ID = uuid4().hex if FORCE_NEW_CACHE_WRITE else "stable-responses-cache"
CACHE_REFERENCE_REPEAT_COUNT = 16
CACHE_REFERENCE_LINES = (
    "接入前应统一模型网关的身份认证、租户隔离和最小权限策略，密钥只从受控环境变量读取，"
    "禁止进入代码、日志、报表或客户端页面，并建立轮换、吊销和异常调用告警机制。",
    "知识库检索应明确数据来源、更新时间、访问范围和引用标识，对切分、向量化、召回、"
    "重排及上下文拼接分别验证，避免过期内容、越权内容或无来源结论进入模型输入。",
    "网关需要统一管理模型映射、超时、限流、重试和降级策略；重试必须区分可恢复错误与"
    "业务错误，并设置总截止时间，防止重复计费、请求放大和下游服务持续拥塞。",
    "上线前应建立包含请求成功率、首字节耗时、完整耗时、Token 用量和缓存命中情况的观测，"
    "同时对敏感字段脱敏，并通过离线评测和小流量验证确认答案质量与系统稳定性。",
)

REQUEST_TIMEOUT_SECONDS = 60.0
REQUEST_TIME_OFFSETS = (-1, 0, 1)
IMMEDIATE_WORKER_PREPARE_SECONDS = 1.0
CACHE_WARMUP_LEAD_SECONDS = 65.0
CACHE_WARMUP_WAIT_SECONDS = 1.0
VERIFY_TLS = True

# Beijing-time scheduled start. Disable to start immediately.
SCHEDULE_ENABLED = True
SCHEDULE_START_TIME = "09:20:00"
BEIJING_TIMEZONE = timezone(timedelta(hours=8), name="Asia/Shanghai")

# ==================================================================


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
    if not INPUT_TEXT.strip():
        raise ValueError("INPUT_TEXT must not be empty")
    if not INSTRUCTIONS.strip():
        raise ValueError("INSTRUCTIONS must not be empty")
    if REASONING_EFFORT not in {"low", "medium", "high"}:
        raise ValueError("REASONING_EFFORT must be low, medium, or high")
    if MAX_OUTPUT_TOKENS <= 0:
        raise ValueError("MAX_OUTPUT_TOKENS must be greater than 0")
    if CACHE_REFERENCE_REPEAT_COUNT <= 0:
        raise ValueError("CACHE_REFERENCE_REPEAT_COUNT must be greater than 0")
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
    if SCHEDULE_ENABLED:
        _parse_schedule_start_time(SCHEDULE_START_TIME)


def _build_request_url() -> str:
    return urljoin(f"{BASE_URL.rstrip('/')}/", REQUEST_PATH.lstrip("/"))


def _build_cacheable_input() -> str:
    reference_lines: list[str] = []
    sequence = 1
    for _ in range(CACHE_REFERENCE_REPEAT_COUNT):
        for line in CACHE_REFERENCE_LINES:
            reference_lines.append(f"固定参考资料 {sequence:04d}：{line}")
            sequence += 1

    reference = "\n".join(reference_lines)
    return (
        f"缓存批次标识：{CACHE_RUN_ID}\n"
        f"{reference}\n\n"
        f"用户任务：{INPUT_TEXT}"
    )


def _build_payload() -> dict[str, object]:
    # Construct this once and reuse it without mutation. The warm-up and all
    # following requests therefore have the same cache key inputs.
    return {
        "model": MODEL_NAME,
        "input": _build_cacheable_input(),
        "instructions": INSTRUCTIONS,
        "reasoning": {"effort": REASONING_EFFORT},
        "max_output_tokens": MAX_OUTPUT_TOKENS,
        "stream": STREAM,
    }


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
        "User-Agent": "api-case-responses-cache-request-runner",
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
    payload = _build_payload()
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
