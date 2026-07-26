from __future__ import annotations

from collections.abc import Iterable, Mapping
from copy import deepcopy
import json
import re
from typing import Any, Final
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


REDACTED_VALUE: Final[str] = "<redacted>"

DEFAULT_REDACT_HEADERS: Final[frozenset[str]] = frozenset(
    {
        "authorization",
        "cookie",
        "proxy-authorization",
        "set-cookie",
        "x-api-key",
    }
)

DEFAULT_REDACT_KEYS: Final[frozenset[str]] = frozenset(
    {
        "api_key",
        "key",
        "token",
        "access_token",
        "refresh_token",
        "secret",
        "password",
        "authorization",
    }
)
SENSITIVE_ASSIGNMENT_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?P<prefix>\b(?:api_key|key|token|access_token|refresh_token|secret|password|authorization)\b\s*[=:]\s*)"
    r"(?P<quote>['\"]?)"
    r"(?P<value>[^'\"\s,&)]+)"
    r"(?P=quote)",
    re.IGNORECASE,
)
SENSITIVE_HEADER_TEXT_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?P<prefix>\b(?:authorization|cookie|proxy-authorization|set-cookie|x-api-key)\b\s*:\s*)"
    r"(?P<value>[^\r\n,;]+)",
    re.IGNORECASE,
)


def redact_request_kwargs(kwargs: Mapping[str, Any]) -> dict[str, Any]:
    redacted: dict[str, Any] = {}
    for name, value in kwargs.items():
        lowered_name = name.lower()
        if lowered_name == "headers":
            redacted[name] = redact_headers(value)
        elif lowered_name in {"params", "json", "data"}:
            redacted[name] = redact_sensitive_data(value)
        elif _is_sensitive_key(name):
            redacted[name] = REDACTED_VALUE
        else:
            redacted[name] = _safe_copy(value)
    return redacted


def redact_headers(
    headers: Any,
    *,
    sensitive_headers: Iterable[str] | None = DEFAULT_REDACT_HEADERS,
) -> Any:
    if not headers:
        return headers

    sensitive_header_names = _normalized_names(sensitive_headers)
    header_items = dict(headers).items()
    return {
        name: REDACTED_VALUE if str(name).lower() in sensitive_header_names else value
        for name, value in header_items
    }


def redact_url(url: str | None) -> str | None:
    if not url:
        return url

    split_url = urlsplit(url)
    if not split_url.query:
        return url

    query_pairs = parse_qsl(split_url.query, keep_blank_values=True)
    redacted_pairs = [
        (name, REDACTED_VALUE if _is_sensitive_key(name) else value)
        for name, value in query_pairs
    ]
    return urlunsplit(
        (
            split_url.scheme,
            split_url.netloc,
            split_url.path,
            urlencode(redacted_pairs, doseq=True),
            split_url.fragment,
        )
    )


def redact_sensitive_data(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            key: REDACTED_VALUE if _is_sensitive_key(key) else redact_sensitive_data(item)
            for key, item in value.items()
        }

    if isinstance(value, list):
        return [_redact_sequence_item(item) for item in value]

    if isinstance(value, tuple):
        return tuple(_redact_sequence_item(item) for item in value)

    if isinstance(value, str):
        return redact_text_body(value)

    return _safe_copy(value)


def redact_text_body(body: str, content_type: str = "") -> str:
    if _looks_like_json(content_type, body):
        try:
            parsed_body = json.loads(body)
        except ValueError:
            return body
        return json.dumps(redact_sensitive_data(parsed_body), ensure_ascii=False)

    redacted_form_body = _redact_urlencoded_text(body)
    if redacted_form_body is not None and (
        "x-www-form-urlencoded" in content_type.lower()
        or _contains_sensitive_form_field(body)
    ):
        return redacted_form_body

    return body


def redact_urlencoded_text(body: str) -> str:
    redacted_body = _redact_urlencoded_text(body)
    if redacted_body is not None:
        return redacted_body
    redacted_text = SENSITIVE_HEADER_TEXT_PATTERN.sub(
        lambda match: f"{match.group('prefix')}{REDACTED_VALUE}",
        body,
    )
    return SENSITIVE_ASSIGNMENT_PATTERN.sub(
        lambda match: f"{match.group('prefix')}{match.group('quote')}{REDACTED_VALUE}{match.group('quote')}",
        redacted_text,
    )


def _redact_sequence_item(item: Any) -> Any:
    if (
        isinstance(item, tuple)
        and len(item) == 2
        and isinstance(item[0], str)
        and _is_sensitive_key(item[0])
    ):
        return (item[0], REDACTED_VALUE)

    if (
        isinstance(item, list)
        and len(item) == 2
        and isinstance(item[0], str)
        and _is_sensitive_key(item[0])
    ):
        return [item[0], REDACTED_VALUE]

    return redact_sensitive_data(item)


def _is_sensitive_key(name: Any) -> bool:
    return isinstance(name, str) and name.lower() in DEFAULT_REDACT_KEYS


def _normalized_names(names: Iterable[str] | None) -> set[str]:
    if names is None:
        return set()
    return {name.lower() for name in names}


def _looks_like_json(content_type: str, body: str) -> bool:
    stripped_body = body.lstrip()
    return (
        "json" in content_type.lower()
        or stripped_body.startswith("{")
        or stripped_body.startswith("[")
    )


def _redact_urlencoded_text(body: str) -> str | None:
    pairs = parse_qsl(body, keep_blank_values=True)
    if not pairs:
        return None
    if not any(_is_sensitive_key(name) for name, _ in pairs):
        return None

    redacted_pairs = [
        (name, REDACTED_VALUE if _is_sensitive_key(name) else value)
        for name, value in pairs
    ]
    return urlencode(redacted_pairs, doseq=True)


def _contains_sensitive_form_field(body: str) -> bool:
    return any(_is_sensitive_key(name) for name, _ in parse_qsl(body, keep_blank_values=True))


def _safe_copy(value: Any) -> Any:
    try:
        return deepcopy(value)
    except Exception:
        return value
