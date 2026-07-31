#!/usr/bin/env python3
"""Call a local One API Anthropic endpoint and detect the HTTP-200/code=-2 regression.

Fill in BASE_URL, API_KEY, and MODEL below, then run:

    python3 scripts/ops/test_anthropic_http_local.py

Command-line options are available for one-off overrides. The script only makes
one POST request and never writes to the database.
"""
from __future__ import annotations

import argparse
import json
import ssl
import sys
from dataclasses import dataclass
from typing import Any
from urllib import error, request


# Fill these values for your local environment.
BASE_URL = "https://pre.juhemoxing.com"
API_KEY = "sk-mxai-ae7eea59aab0c82ab7f2d71e2a2db11a7c2dbee4618c6c730aee7d5a3d5ab8b4"
MODEL = "GPT-5.4"
PROMPT = "Reply with exactly: local anthropic smoke test"
TIMEOUT_SECONDS = 60
# Keep certificate verification enabled by default. Set False only for a
# controlled internal/pre-environment smoke test with a private CA chain.
VERIFY_TLS = False


@dataclass(frozen=True)
class HTTPResult:
    status: int
    headers: dict[str, str]
    body: str


def build_request(base_url: str, api_key: str, model: str, prompt: str) -> request.Request:
    payload = {
        "model": model,
        "max_tokens": 32,
        "messages": [{"role": "user", "content": prompt}],
    }
    url = base_url.rstrip("/") + "/chat"
    return request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": "Bearer " + api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
            "accept": "application/json",
        },
        method="POST",
    )


def call_endpoint(req: request.Request, timeout: int, verify_tls: bool = VERIFY_TLS) -> HTTPResult:
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
    return False, "request failed: HTTP %d response" % status


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=BASE_URL, help="One API base URL")
    parser.add_argument("--api-key", default=API_KEY, help="One API access key")
    parser.add_argument("--model", default=MODEL, help="Anthropic model name")
    parser.add_argument("--prompt", default=PROMPT, help="user message")
    parser.add_argument("--timeout", type=int, default=TIMEOUT_SECONDS)
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="disable TLS certificate verification for an internal smoke test",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.api_key:
        print("ERROR: 请先在脚本顶部填写 API_KEY，或使用 --api-key 传入。", file=sys.stderr)
        return 2
    req = build_request(args.base_url, args.api_key, args.model, args.prompt)
    print("POST %s" % req.full_url)
    print("model: %s" % args.model)
    try:
        result = call_endpoint(req, args.timeout, verify_tls=VERIFY_TLS and not args.insecure)
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
