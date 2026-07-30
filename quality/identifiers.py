from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import re
import uuid
from urllib.parse import urlsplit

from quality.models import CasePhase, Protocol
from quality.redaction import (
    canonicalize_for_hash,
    redact_quality_value,
    sanitize_identifier_part,
)


_MULTIPLE_SLASH_PATTERN = re.compile(r"/{2,}")
_UUID_SEGMENT_PATTERN = re.compile(
    r"^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[1-5][0-9A-Fa-f]{3}-"
    r"[89ABab][0-9A-Fa-f]{3}-[0-9A-Fa-f]{12}$"
)
_HEX_SEGMENT_PATTERN = re.compile(r"^[0-9A-Fa-f]{16,}$")
_OPAQUE_SEGMENT_PATTERN = re.compile(r"^[A-Za-z0-9_]{24,}$")
_ISO_TIMESTAMP_PATTERN = re.compile(
    r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?\b"
)
_UUID_PATTERN = re.compile(
    r"\b[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[1-5][0-9A-Fa-f]{3}-"
    r"[89ABab][0-9A-Fa-f]{3}-[0-9A-Fa-f]{12}\b"
)
_MEMORY_ADDRESS_PATTERN = re.compile(r"\b0x[0-9A-Fa-f]+\b")
_LONG_NUMBER_PATTERN = re.compile(r"\b\d{4,}\b")
_PATH_NUMBER_PATTERN = re.compile(r"(?<=/)\d+(?=/|\s|$)")
_TEMP_PATH_PATTERN = re.compile(
    r"(?i)(?:[A-Za-z]:[\\/][^\r\n]*?[\\/]Temp[\\/][^\s:]+|/tmp/[^\s:]+)"
)
_WHITESPACE_PATTERN = re.compile(r"\s+")
_ERROR_SLUG_PATTERN = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class NormalizedNodeId:
    stable_nodeid: str
    parameter_id: str | None = None


def build_run_id(
    job_name: str | None = None,
    build_number: str | int | None = None,
    timestamp: datetime | None = None,
    random_uuid: uuid.UUID | str | None = None,
) -> str:
    if (job_name is None) != (build_number is None):
        raise ValueError("job_name and build_number must be provided together")

    run_time = timestamp or datetime.now(UTC)
    if run_time.tzinfo is None or run_time.utcoffset() is None:
        raise ValueError("timestamp must include timezone information")
    timestamp_part = run_time.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")

    run_uuid = _coerce_uuid(random_uuid)
    uuid_part = run_uuid.hex[:8]

    if job_name is None:
        return f"local-{timestamp_part}-{uuid_part}"

    job_part = sanitize_identifier_part(job_name)
    build_part = sanitize_identifier_part(build_number)
    return f"{job_part}-{build_part}-{timestamp_part}-{uuid_part}"


def build_execution_id(stage_name: str, index: int) -> str:
    if index < 1:
        raise ValueError("index must be greater than or equal to 1")
    return f"{sanitize_identifier_part(stage_name)}-{index}"


def normalize_nodeid(nodeid: str) -> NormalizedNodeId:
    normalized = _WHITESPACE_PATTERN.sub(" ", nodeid.replace("\\", "/")).strip()
    if not normalized:
        raise ValueError("nodeid must not be empty")

    parameter_id: str | None = None
    stable_nodeid = normalized
    if normalized.endswith("]"):
        parameter_start = _find_parameter_start(normalized)
        if parameter_start is not None:
            parameter_id = normalized[parameter_start + 1 : -1]
            stable_nodeid = normalized[:parameter_start].rstrip()

    if not stable_nodeid:
        raise ValueError("nodeid must contain a stable test definition")
    return NormalizedNodeId(
        stable_nodeid=stable_nodeid,
        parameter_id=parameter_id,
    )


def build_case_id(nodeid: str) -> str:
    return normalize_nodeid(nodeid).stable_nodeid


def build_param_hash(value: object) -> str:
    canonical = canonicalize_for_hash(value)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def build_invocation_id(run_id: str, case_id: str, param_hash: str) -> str:
    parts = tuple(_require_non_empty(value, name) for name, value in (
        ("run_id", run_id),
        ("case_id", case_id),
        ("param_hash", param_hash),
    ))
    digest = hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()[:24]
    return f"inv-{digest}"


def new_request_event_id() -> str:
    return str(uuid.uuid4())


def build_interface_id(
    method: str,
    url_or_path: str,
    protocol: Protocol | str = Protocol.HTTP,
) -> str:
    method_part = _require_non_empty(method, "method").upper()
    protocol_part = _normalize_protocol(protocol)
    path = build_url_template(url_or_path)
    return f"{method_part} {path} {protocol_part}"


def build_url_template(url_or_path: str) -> str:
    return _normalize_path(url_or_path)


def normalize_failure_message(message: str) -> str:
    redacted = redact_quality_value(message, remove_url_query=True)
    if not isinstance(redacted, str):
        redacted = str(redacted)

    normalized = _TEMP_PATH_PATTERN.sub("<temp-path>", redacted)
    normalized = _ISO_TIMESTAMP_PATTERN.sub("<timestamp>", normalized)
    normalized = _UUID_PATTERN.sub("<uuid>", normalized)
    normalized = _MEMORY_ADDRESS_PATTERN.sub("<memory-address>", normalized)
    normalized = _PATH_NUMBER_PATTERN.sub("{id}", normalized)
    normalized = _LONG_NUMBER_PATTERN.sub("<num>", normalized)
    return _WHITESPACE_PATTERN.sub(" ", normalized).strip()


def build_failure_message_hash(message: str) -> str:
    normalized_message = normalize_failure_message(message)
    return hashlib.sha256(normalized_message.casefold().encode("utf-8")).hexdigest()[:16]


def build_failure_fingerprint(
    phase: CasePhase | str,
    error_type: str,
    message: str,
    interface_id: str | None = None,
    assert_location: str | None = None,
) -> str:
    phase_part = _normalize_phase(phase)
    error_type_value = _require_non_empty(error_type, "error_type")
    normalized_message = normalize_failure_message(message)
    payload = {
        "phase": phase_part,
        "error_type": error_type_value.casefold(),
        "normalized_message": normalized_message.casefold(),
        "interface_id": _optional_non_empty(interface_id),
        "assert_location": _normalize_assert_location(assert_location),
    }
    digest = hashlib.sha256(canonicalize_for_hash(payload).encode("utf-8")).hexdigest()[:12]
    error_slug = _ERROR_SLUG_PATTERN.sub("-", error_type_value.casefold()).strip("-")
    if not error_slug:
        error_slug = "error"
    return f"fail-{phase_part}-{error_slug[:32]}-{digest}"


def _normalize_path(url_or_path: str) -> str:
    raw_value = _require_non_empty(url_or_path, "url_or_path")
    split = urlsplit(raw_value)
    path = split.path or "/"
    path = _MULTIPLE_SLASH_PATTERN.sub("/", path.replace("\\", "/"))
    if not path.startswith("/"):
        path = f"/{path}"
    if path != "/":
        path = path.rstrip("/")

    segments = path.split("/")
    templated = [_template_path_segment(segment) for segment in segments]
    return "/".join(templated) or "/"


def _find_parameter_start(nodeid: str) -> int | None:
    depth = 0
    for index in range(len(nodeid) - 1, -1, -1):
        character = nodeid[index]
        if character == "]":
            depth += 1
        elif character == "[":
            depth -= 1
            if depth == 0:
                return index
    return None


def _template_path_segment(segment: str) -> str:
    if not segment or (segment.startswith("{") and segment.endswith("}")):
        return segment
    if segment.isdigit():
        return "{id}"
    if _UUID_SEGMENT_PATTERN.fullmatch(segment):
        return "{uuid}"
    if _HEX_SEGMENT_PATTERN.fullmatch(segment):
        return "{hash}"
    if (
        _OPAQUE_SEGMENT_PATTERN.fullmatch(segment)
        and any(character.isalpha() for character in segment)
        and any(character.isdigit() for character in segment)
    ):
        return "{hash}"
    return segment


def _normalize_protocol(protocol: Protocol | str) -> str:
    if isinstance(protocol, Protocol):
        return protocol.value
    try:
        return Protocol(_require_non_empty(protocol, "protocol").lower()).value
    except ValueError as error:
        raise ValueError(f"unsupported protocol: {protocol!r}") from error


def _normalize_phase(phase: CasePhase | str) -> str:
    if isinstance(phase, CasePhase):
        return phase.value
    try:
        return CasePhase(_require_non_empty(phase, "phase").lower()).value
    except ValueError as error:
        raise ValueError(f"unsupported case phase: {phase!r}") from error


def _normalize_assert_location(value: str | None) -> str | None:
    normalized = _optional_non_empty(value)
    if normalized is None:
        return None
    return normalized.replace("\\", "/")


def _coerce_uuid(value: uuid.UUID | str | None) -> uuid.UUID:
    if value is None:
        return uuid.uuid4()
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except ValueError as error:
        raise ValueError("random_uuid must be a valid UUID") from error


def _require_non_empty(value: object, name: str) -> str:
    text = str(value).strip()
    if not text:
        raise ValueError(f"{name} must not be empty")
    return text


def _optional_non_empty(value: str | None) -> str | None:
    if value is None:
        return None
    return _require_non_empty(value, "value")
