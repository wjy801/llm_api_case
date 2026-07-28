from __future__ import annotations

from collections.abc import Iterable
import json

import requests

from util.redaction import (
    DEFAULT_REDACT_HEADERS,
    REDACTED_VALUE,
    redact_sensitive_data,
    redact_text_body,
    redact_url,
)


def build_curl(
    prepared_request: requests.PreparedRequest,
    *,
    redact_headers: Iterable[str] | None = DEFAULT_REDACT_HEADERS,
    multiline: bool = True,
) -> str:
    if not isinstance(prepared_request, requests.PreparedRequest):
        raise TypeError("prepared_request must be a requests.PreparedRequest instance")

    method = prepared_request.method or "GET"
    url = redact_url(prepared_request.url)
    if not url:
        raise ValueError("prepared_request.url is empty")

    redacted_header_names = _normalized_header_names(redact_headers)
    parts = [f"curl -X {method.upper()} {_shell_quote(url)}"]

    for name, value in prepared_request.headers.items():
        header_value = REDACTED_VALUE if name.lower() in redacted_header_names else str(value)
        parts.append(f"-H {_shell_quote(f'{name}: {header_value}')}")

    body = _request_body_to_text(
        prepared_request.body,
        prepared_request.headers.get("Content-Type", ""),
    )
    if body is not None:
        parts.append(f"--data-raw {_shell_quote(body)}")

    return _join_command_parts(parts, multiline=multiline)


def _normalized_header_names(header_names: Iterable[str] | None) -> set[str]:
    if header_names is None:
        return set()
    return {name.lower() for name in header_names}


def _request_body_to_text(body: object, content_type: str = "") -> str | None:
    if body is None:
        return None
    if isinstance(body, bytes):
        body = body.decode("utf-8", errors="replace")

    text = str(body)
    if _looks_like_json(content_type, text):
        try:
            return json.dumps(redact_sensitive_data(json.loads(text)), ensure_ascii=False)
        except ValueError:
            pass
    return redact_text_body(text, content_type)


def _join_command_parts(parts: list[str], *, multiline: bool) -> str:
    if not multiline:
        return " ".join(parts)
    return " \\\n  ".join(parts)


def _shell_quote(value: object) -> str:
    text = str(value)
    if text == "":
        return "''"
    return "'" + text.replace("'", "'\"'\"'") + "'"


def _looks_like_json(content_type: str, body: str) -> bool:
    stripped_body = body.lstrip()
    return (
        "json" in content_type.lower()
        or stripped_body.startswith("{")
        or stripped_body.startswith("[")
    )
