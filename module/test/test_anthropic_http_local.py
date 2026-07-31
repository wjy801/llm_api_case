#!/usr/bin/env python3
"""Send one local Anthropic Messages HTTP request.

This is a standalone local script, not a pytest/framework case.

Examples:

    python module/test/test_anthropic_http_local.py
    python module/test/test_anthropic_http_local.py --model claude-opus-4-8
    python module/test/test_anthropic_http_local.py --auth-header authorization

Configuration priority:

1. Command-line arguments.
2. Process environment variables.
3. Project root .env values.

Supported standalone environment variables:

    ANTHROPIC_LOCAL_BASE_URL
    ANTHROPIC_LOCAL_API_KEY
    ANTHROPIC_LOCAL_MODEL
    ANTHROPIC_LOCAL_VERIFY_TLS

If these are absent, the script falls back to the project's existing
USE_CHINA_ENVIRONMENT, CHINA_TEST_ENVIRONMENT_BASE_URL, CHINA_API_KEY,
OVERSEAS_TEST_BASE_URL, and OVERSEAS_API_KEY values.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import ssl
import sys
from dataclasses import dataclass
from typing import Any
from urllib import error, request


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENV_FILE = PROJECT_ROOT / ".env"
ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_MODEL = "claude-opus-4-8"
DEFAULT_PROMPT = "Reply with exactly: local anthropic smoke test"
DEFAULT_TIMEOUT_SECONDS = 60
DEFAULT_AUTH_HEADER = "x-api-key"


@dataclass(frozen=True)
class HTTPResult:
    status: int
    headers: dict[str, str]
    body: str


@dataclass(frozen=True)
class RuntimeConfig:
    base_url: str
    api_key: str
    model: str
    prompt: str
    timeout: int
    verify_tls: bool
    auth_header: str


def build_request(config: RuntimeConfig) -> request.Request:
    payload = {
        "model": config.model,
        "max_tokens": 32,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": config.prompt,
                    }
                ],
            }
        ],
    }
    url = config.base_url.rstrip("/") + "/v1/messages"
    return request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=build_headers(config.api_key, config.auth_header),
        method="POST",
    )


def build_headers(api_key: str, auth_header: str) -> dict[str, str]:
    headers = {
        "anthropic-version": ANTHROPIC_VERSION,
        "content-type": "application/json",
        "accept": "application/json",
    }
    if auth_header in {"x-api-key", "both"}:
        headers["x-api-key"] = api_key
    if auth_header in {"authorization", "both"}:
        headers["Authorization"] = "Bearer " + api_key
    return headers


def call_endpoint(req: request.Request, timeout: int, verify_tls: bool) -> HTTPResult:
    context = ssl.create_default_context() if verify_tls else ssl._create_unverified_context()
    try:
        with request.urlopen(req, timeout=timeout, context=context) as response:
            body = response.read().decode("utf-8", errors="replace")
            return HTTPResult(response.status, dict(response.headers.items()), body)
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return HTTPResult(exc.code, dict(exc.headers.items()), body)


def parse_body(body: str) -> Any:
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return None


def classify_response(status: int, payload: Any) -> tuple[bool, str]:
    if status == 200 and isinstance(payload, dict) and payload.get("code") == -2:
        return False, "regression: HTTP 200 contains business error code=-2"
    if 200 <= status < 300:
        return True, "success: HTTP %d response" % status
    if status == 403:
        return False, "request reached server but was rejected by permission/model access: HTTP 403 response"
    return False, "request failed: HTTP %d response" % status


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=None, help="Anthropic-compatible base URL")
    parser.add_argument("--api-key", default=None, help="Anthropic-compatible access key")
    parser.add_argument("--model", default=None, help="Anthropic Messages model name")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT, help="user message")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument(
        "--auth-header",
        choices=("x-api-key", "authorization", "both"),
        default=DEFAULT_AUTH_HEADER,
        help="authentication header style, default: x-api-key",
    )
    parser.add_argument(
        "--env-file",
        default=str(DEFAULT_ENV_FILE),
        help="dotenv file used when process env vars are absent",
    )
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="disable TLS certificate verification for an internal smoke test",
    )
    return parser.parse_args(argv)


def build_runtime_config(args: argparse.Namespace) -> RuntimeConfig:
    env_values = load_env_values(Path(args.env_file))
    base_url = first_non_empty(
        args.base_url,
        os.getenv("ANTHROPIC_LOCAL_BASE_URL"),
        env_values.get("ANTHROPIC_LOCAL_BASE_URL"),
        selected_project_env_value(env_values, "base_url"),
        os.getenv("BASE_URL"),
        env_values.get("BASE_URL"),
    )
    api_key = first_non_empty(
        args.api_key,
        os.getenv("ANTHROPIC_LOCAL_API_KEY"),
        env_values.get("ANTHROPIC_LOCAL_API_KEY"),
        selected_project_env_value(env_values, "api_key"),
        os.getenv("API_KEY"),
        env_values.get("API_KEY"),
    )
    model = first_non_empty(
        args.model,
        os.getenv("ANTHROPIC_LOCAL_MODEL"),
        env_values.get("ANTHROPIC_LOCAL_MODEL"),
        DEFAULT_MODEL,
    )
    verify_tls = not args.insecure and parse_bool(
        first_non_empty(
            os.getenv("ANTHROPIC_LOCAL_VERIFY_TLS"),
            env_values.get("ANTHROPIC_LOCAL_VERIFY_TLS"),
            "TRUE",
        )
    )

    missing_fields = []
    if not base_url:
        missing_fields.append("base URL")
    if not api_key:
        missing_fields.append("API key")
    if missing_fields:
        raise ValueError(
            "Missing %s. Use --base-url/--api-key or configure ANTHROPIC_LOCAL_BASE_URL/"
            "ANTHROPIC_LOCAL_API_KEY in environment or .env." % " and ".join(missing_fields)
        )

    return RuntimeConfig(
        base_url=base_url,
        api_key=api_key,
        model=model,
        prompt=args.prompt,
        timeout=args.timeout,
        verify_tls=verify_tls,
        auth_header=args.auth_header,
    )


def selected_project_env_value(env_values: dict[str, str], value_type: str) -> str | None:
    use_china_environment = parse_bool(
        first_non_empty(
            os.getenv("USE_CHINA_ENVIRONMENT"),
            env_values.get("USE_CHINA_ENVIRONMENT"),
            "FALSE",
        )
    )
    if value_type == "base_url":
        env_name = "CHINA_TEST_ENVIRONMENT_BASE_URL" if use_china_environment else "OVERSEAS_TEST_BASE_URL"
    elif value_type == "api_key":
        env_name = "CHINA_API_KEY" if use_china_environment else "OVERSEAS_API_KEY"
    else:
        raise ValueError(f"Unsupported selected project env value type: {value_type!r}")

    return first_non_empty(os.getenv(env_name), env_values.get(env_name))


def load_env_values(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}

    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip().strip('"').strip("'")
        if name:
            values[name] = value
    return values


def first_non_empty(*values: str | None) -> str | None:
    for value in values:
        if value is None:
            continue
        stripped_value = str(value).strip()
        if stripped_value:
            return stripped_value
    return None


def parse_bool(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def redact_headers(headers: dict[str, str]) -> dict[str, str]:
    sensitive_headers = {"authorization", "x-api-key"}
    return {
        name: "<redacted>" if name.lower() in sensitive_headers else value
        for name, value in headers.items()
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        config = build_runtime_config(args)
    except ValueError as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 2

    req = build_request(config)
    print("POST %s" % req.full_url)
    print("model: %s" % config.model)
    print("auth header: %s" % config.auth_header)
    print("verify TLS: %s" % config.verify_tls)
    print("headers: %s" % json.dumps(redact_headers(dict(req.headers)), ensure_ascii=False))

    try:
        result = call_endpoint(req, config.timeout, verify_tls=config.verify_tls)
    except (error.URLError, TimeoutError, OSError) as exc:
        print("ERROR: request failed: %s" % exc, file=sys.stderr)
        return 1

    payload = parse_body(result.body)
    print("HTTP status: %d" % result.status)
    print("Response body:")
    if payload is None:
        print(result.body)
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    passed, message = classify_response(result.status, payload)
    print(("PASS: " if passed else "FAIL: ") + message)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
