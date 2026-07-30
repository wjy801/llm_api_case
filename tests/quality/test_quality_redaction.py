from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from quality.models import Protocol
from quality.redaction import (
    canonicalize_for_hash,
    redact_quality_value,
    sanitize_identifier_part,
    strip_url_query,
)
from util.redaction import REDACTED_VALUE


def test_redact_quality_value_handles_nested_sensitive_values_without_mutation():
    original = {
        "Authorization": "Bearer auth-secret",
        "nested": {
            "api_key": "key-secret",
            "safe": "visible",
        },
        "items": [("token", "token-secret"), {"password": "password-secret"}],
    }

    redacted = redact_quality_value(original)

    assert redacted["Authorization"] == REDACTED_VALUE
    assert redacted["nested"]["api_key"] == REDACTED_VALUE
    assert redacted["nested"]["safe"] == "visible"
    assert redacted["items"][0][1] == REDACTED_VALUE
    assert redacted["items"][1]["password"] == REDACTED_VALUE
    assert original["Authorization"] == "Bearer auth-secret"
    assert original["nested"]["api_key"] == "key-secret"


def test_redact_quality_text_masks_bearer_assignments_and_random_tokens():
    random_token = "AbCdEfGhIjKlMnOpQrStUvWx12345678"
    text = (
        "Authorization: Bearer bearer-secret, "
        "token=plain-secret, "
        f"request={random_token}"
    )

    redacted = redact_quality_value(text)

    assert "bearer-secret" not in redacted
    assert "plain-secret" not in redacted
    assert random_token not in redacted
    assert REDACTED_VALUE in redacted


def test_query_handling_differs_between_identity_and_parameter_summary():
    url = "https://host/v1/tasks/123?page=2&token=query-secret"

    identity_value = redact_quality_value(url, remove_url_query=True)
    parameter_value = redact_quality_value(url)

    assert identity_value == "https://host/v1/tasks/123"
    assert "page=2" in parameter_value
    assert "query-secret" not in parameter_value
    assert REDACTED_VALUE in parameter_value


def test_strip_url_query_preserves_surrounding_error_text():
    text = "request https://host/v1/tasks/123?token=secret returned status 500"

    stripped = strip_url_query(text)

    assert stripped == "request https://host/v1/tasks/123 returned status 500"


def test_canonicalize_for_hash_is_stable_for_mapping_set_and_supported_types():
    @dataclass(frozen=True)
    class Demo:
        path: Path
        protocol: Protocol

    first = {
        "time": datetime(2026, 7, 30, 8, 0, tzinfo=UTC),
        "values": {3, 1, 2},
        "demo": Demo(path=Path("reports\\quality"), protocol=Protocol.HTTP),
        "mapping": {"b": 2, "a": 1},
    }
    second = {
        "mapping": {"a": 1, "b": 2},
        "demo": Demo(path=Path("reports\\quality"), protocol=Protocol.HTTP),
        "values": {2, 3, 1},
        "time": datetime(2026, 7, 30, 16, 0, tzinfo=UTC),
    }

    first_canonical = canonicalize_for_hash(first)
    repeated_canonical = canonicalize_for_hash(first)

    assert first_canonical == repeated_canonical
    assert '"mapping":{"a":1,"b":2}' in first_canonical
    assert '"values":[1,2,3]' in first_canonical
    assert canonicalize_for_hash(second) != first_canonical


def test_canonicalize_custom_object_removes_memory_address():
    class DemoObject:
        pass

    canonical = canonicalize_for_hash(DemoObject())

    assert "0x" not in canonical
    assert "<memory-address>" in canonical


def test_sanitize_identifier_part_returns_safe_ascii_or_hash_fallback():
    assert sanitize_identifier_part("parallel pool/1") == "parallel-pool-1"

    fallback = sanitize_identifier_part("中文")

    assert fallback.startswith("part-")
    assert fallback.isascii()
