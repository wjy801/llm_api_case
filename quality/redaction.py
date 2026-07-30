from __future__ import annotations

from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from datetime import UTC, datetime
from enum import Enum
import hashlib
import json
from pathlib import Path
import re
from typing import Any
import unicodedata
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel

from util.redaction import REDACTED_VALUE


QUALITY_SENSITIVE_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "client_secret",
        "cookie",
        "key",
        "password",
        "proxy_authorization",
        "refresh_token",
        "secret",
        "set_cookie",
        "token",
        "access_token",
        "x_api_key",
    }
)

_BEARER_PATTERN = re.compile(
    r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+",
)
_SENSITIVE_ASSIGNMENT_PATTERN = re.compile(
    r"(?P<prefix>\b(?:api[_-]?key|authorization|client[_-]?secret|cookie|key|password|"
    r"refresh[_-]?token|secret|token|access[_-]?token|x[_-]?api[_-]?key)\b\s*[=:]\s*)"
    r"(?P<quote>['\"]?)"
    r"(?P<value>[^'\"\s,&;)]+)"
    r"(?P=quote)",
    re.IGNORECASE,
)
_SENSITIVE_HEADER_PATTERN = re.compile(
    r"(?P<prefix>\b(?:authorization|cookie|proxy-authorization|set-cookie|x-api-key)\b\s*:\s*)"
    r"(?P<value>[^\r\n,;]+)",
    re.IGNORECASE,
)
_HIGH_ENTROPY_TOKEN_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])(?=[A-Za-z0-9_]{24,}(?![A-Za-z0-9_]))"
    r"(?=[A-Za-z0-9_]*[A-Za-z])(?=[A-Za-z0-9_]*\d)[A-Za-z0-9_]{24,}"
)
_MEMORY_ADDRESS_PATTERN = re.compile(r"\b0x[0-9A-Fa-f]+\b")
_URL_WITH_QUERY_PATTERN = re.compile(
    r"(?P<base>(?:https?://[^\s?#]+|/[^\s?#]+))\?[^\s#]*",
    re.IGNORECASE,
)
_UNSAFE_IDENTIFIER_PATTERN = re.compile(r"[^A-Za-z0-9._-]+")
_REPEATED_SEPARATOR_PATTERN = re.compile(r"[-_.]{2,}")


def redact_quality_value(value: Any, *, remove_url_query: bool = False) -> Any:
    if isinstance(value, Mapping):
        return {
            key: (
                REDACTED_VALUE
                if _is_sensitive_key(key)
                else redact_quality_value(item, remove_url_query=remove_url_query)
            )
            for key, item in value.items()
        }

    if isinstance(value, list):
        if _is_sensitive_pair(value):
            return [value[0], REDACTED_VALUE]
        return [
            redact_quality_value(item, remove_url_query=remove_url_query)
            for item in value
        ]

    if isinstance(value, tuple):
        if _is_sensitive_pair(value):
            return (value[0], REDACTED_VALUE)
        return tuple(
            redact_quality_value(item, remove_url_query=remove_url_query)
            for item in value
        )

    if isinstance(value, set):
        return {
            redact_quality_value(item, remove_url_query=remove_url_query)
            for item in value
        }

    if isinstance(value, frozenset):
        return frozenset(
            redact_quality_value(item, remove_url_query=remove_url_query)
            for item in value
        )

    if isinstance(value, str):
        return _redact_text(value, remove_url_query=remove_url_query)

    return value


def canonicalize_for_hash(value: Any) -> str:
    redacted = redact_quality_value(value)
    canonical = _to_canonical_value(redacted)
    return json.dumps(
        canonical,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def sanitize_identifier_part(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value)).strip()
    if not text:
        raise ValueError("identifier part must not be empty")

    sanitized = _UNSAFE_IDENTIFIER_PATTERN.sub("-", text)
    sanitized = _REPEATED_SEPARATOR_PATTERN.sub("-", sanitized).strip("-._")
    if sanitized:
        return sanitized

    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]
    return f"part-{digest}"


def strip_url_query(value: str) -> str:
    if not value:
        return value

    if any(character.isspace() for character in value):
        return _URL_WITH_QUERY_PATTERN.sub(lambda match: match.group("base"), value)

    split = urlsplit(value)
    if split.query:
        return urlunsplit(
            (
                split.scheme,
                split.netloc,
                split.path,
                "",
                split.fragment,
            )
        )
    return value


def _redact_text(value: str, *, remove_url_query: bool) -> str:
    redacted = strip_url_query(value) if remove_url_query else value
    redacted = _SENSITIVE_HEADER_PATTERN.sub(
        lambda match: f"{match.group('prefix')}{REDACTED_VALUE}",
        redacted,
    )
    redacted = _SENSITIVE_ASSIGNMENT_PATTERN.sub(
        lambda match: (
            f"{match.group('prefix')}{match.group('quote')}"
            f"{REDACTED_VALUE}{match.group('quote')}"
        ),
        redacted,
    )
    redacted = _BEARER_PATTERN.sub(f"Bearer {REDACTED_VALUE}", redacted)
    redacted = _HIGH_ENTROPY_TOKEN_PATTERN.sub(REDACTED_VALUE, redacted)
    return _MEMORY_ADDRESS_PATTERN.sub("<memory-address>", redacted)


def _to_canonical_value(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _to_canonical_value(value.model_dump(mode="python"))

    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _to_canonical_value(getattr(value, field.name))
            for field in fields(value)
        }

    if isinstance(value, Mapping):
        return {
            str(key): _to_canonical_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }

    if isinstance(value, (list, tuple)):
        return [_to_canonical_value(item) for item in value]

    if isinstance(value, (set, frozenset)):
        canonical_items = [_to_canonical_value(item) for item in value]
        return sorted(
            canonical_items,
            key=lambda item: json.dumps(
                item,
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ),
        )

    if isinstance(value, Enum):
        return _to_canonical_value(value.value)

    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("datetime must include timezone information")
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")

    if isinstance(value, Path):
        return value.as_posix()

    if isinstance(value, bytes):
        return {"__type__": "bytes", "length": len(value)}

    if value is None or isinstance(value, (str, int, float, bool)):
        return value

    type_name = f"{type(value).__module__}.{type(value).__qualname__}"
    stable_repr = _redact_text(repr(value), remove_url_query=True)
    return {
        "__type__": type_name,
        "repr": stable_repr,
    }


def _is_sensitive_key(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    normalized = value.strip().lower().replace("-", "_")
    return normalized in QUALITY_SENSITIVE_KEYS


def _is_sensitive_pair(value: list[Any] | tuple[Any, ...]) -> bool:
    return len(value) == 2 and _is_sensitive_key(value[0])
