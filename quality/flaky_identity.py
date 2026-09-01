from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from quality.redaction import sanitize_identifier_part


FLAKY_IDENTITY_RULE_VERSION = "flaky-identity.v1"
FLAKY_ENVIRONMENT_RULE_VERSION = "flaky-environment.v1"
FLAKY_EXECUTION_PROFILE_RULE_VERSION = "flaky-execution-profile.v1"

_GW_WORKER_PATTERN = re.compile(r"^gw\d+$", re.IGNORECASE)
_CUSTOM_PROFILE_PATTERN = re.compile(r"^custom:[a-z0-9._-]+$")


def normalize_flaky_environment(value: str) -> str:
    normalized = _required_text(value, "environment").casefold()
    if normalized not in {"china", "overseas"}:
        raise ValueError(f"unsupported Flaky environment: {value!r}")
    return normalized


def normalize_execution_profile(execution_id: str, worker_id: str) -> str:
    execution = _required_text(execution_id, "execution_id").casefold()
    worker = _required_text(worker_id, "worker_id")
    if execution == "serial-pool":
        return "serial"
    if execution == "parallel-pool":
        return "parallel"
    if execution == "manual-pytest":
        if worker.casefold() == "master":
            return "manual-serial"
        if _GW_WORKER_PATTERN.fullmatch(worker):
            return "manual-parallel"
        raise ValueError(f"unsupported manual-pytest worker: {worker_id!r}")
    sanitized = sanitize_identifier_part(execution).casefold()
    if len(sanitized) > 64:
        digest = hashlib.sha256(sanitized.encode("utf-8")).hexdigest()[:16]
        sanitized = f"{sanitized[:47]}-{digest}"
    return f"custom:{sanitized}"


def normalize_stored_execution_profile(value: str) -> str:
    normalized = _required_text(value, "execution_profile").casefold()
    if normalized in {"serial", "parallel", "manual-serial", "manual-parallel"}:
        return normalized
    if _CUSTOM_PROFILE_PATTERN.fullmatch(normalized):
        if len(normalized.removeprefix("custom:")) > 64:
            raise ValueError("custom execution profile exceeds 64 characters")
        return normalized
    raise ValueError(f"unsupported execution profile: {value!r}")


def build_epoch_scope_key(
    case_id: str,
    environment: str,
    execution_profile: str,
) -> str:
    payload = {
        "case_id": _required_text(case_id, "case_id"),
        "environment": normalize_flaky_environment(environment),
        "execution_profile": normalize_stored_execution_profile(execution_profile),
    }
    return f"epoch-scope-v1-{_full_hash(payload)}"


def build_flaky_key(
    case_id: str,
    param_hash: str,
    environment: str,
    execution_profile: str,
    state_epoch: int,
) -> str:
    if state_epoch < 1:
        raise ValueError("state_epoch must be greater than or equal to 1")
    payload = {
        "case_id": _required_text(case_id, "case_id"),
        "param_hash": _required_text(param_hash, "param_hash"),
        "environment": normalize_flaky_environment(environment),
        "execution_profile": normalize_stored_execution_profile(execution_profile),
        "state_epoch": state_epoch,
    }
    return f"flaky-v1-{_full_hash(payload)}"


def _full_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _required_text(value: str, name: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f"{name} must not be empty")
    return normalized
