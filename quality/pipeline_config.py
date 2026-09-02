from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import math
import os
from pathlib import Path
from urllib.parse import urlparse

from quality.pipeline_contracts import normalize_pipeline_job_name


QUALITY_DASHBOARD_MAIN_PIPELINE_ENABLE_ENV = (
    "QUALITY_DASHBOARD_MAIN_PIPELINE_ENABLE"
)
QUALITY_DASHBOARD_JENKINS_ORIGIN_ENV = "QUALITY_DASHBOARD_JENKINS_ORIGIN"
QUALITY_DASHBOARD_JENKINS_JOB_ENV = "QUALITY_DASHBOARD_JENKINS_JOB"
QUALITY_DASHBOARD_JENKINS_CREDENTIAL_FILE_ENV = (
    "QUALITY_DASHBOARD_JENKINS_CREDENTIAL_FILE"
)
QUALITY_DASHBOARD_ACTIVE_POLL_SECONDS_ENV = (
    "QUALITY_DASHBOARD_ACTIVE_POLL_SECONDS"
)
QUALITY_DASHBOARD_IDLE_POLL_SECONDS_ENV = "QUALITY_DASHBOARD_IDLE_POLL_SECONDS"
QUALITY_DASHBOARD_REQUEST_TIMEOUT_SECONDS_ENV = (
    "QUALITY_DASHBOARD_REQUEST_TIMEOUT_SECONDS"
)
QUALITY_DASHBOARD_QUALITY_GRACE_SECONDS_ENV = (
    "QUALITY_DASHBOARD_QUALITY_GRACE_SECONDS"
)
PROBE_JENKINS_CREDENTIAL_FILE_ENV = "QUALITY_FLAKY_JENKINS_CREDENTIAL_FILE"

DEFAULT_ACTIVE_POLL_SECONDS = 5.0
DEFAULT_IDLE_POLL_SECONDS = 30.0
DEFAULT_REQUEST_TIMEOUT_SECONDS = 5.0
DEFAULT_QUALITY_GRACE_SECONDS = 120.0
MAX_CREDENTIAL_FILE_BYTES = 4096

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"", "0", "false", "no", "off"})


@dataclass(frozen=True)
class JenkinsReadCredentials:
    username: str
    token: str = field(repr=False)


@dataclass(frozen=True)
class PipelineMonitorConfig:
    requested_enabled: bool = False
    enabled: bool = False
    warning: str | None = None
    jenkins_origin: str | None = None
    job_full_name: str | None = None
    credential_file: Path | None = None
    active_poll_seconds: float = DEFAULT_ACTIVE_POLL_SECONDS
    idle_poll_seconds: float = DEFAULT_IDLE_POLL_SECONDS
    request_timeout_seconds: float = DEFAULT_REQUEST_TIMEOUT_SECONDS
    quality_grace_seconds: float = DEFAULT_QUALITY_GRACE_SECONDS


def load_pipeline_monitor_config(
    environ: Mapping[str, str] | None = None,
    *,
    repository_root: str | Path | None = None,
) -> PipelineMonitorConfig:
    values = os.environ if environ is None else environ
    issues: list[str] = []
    requested = _parse_enable(values.get(QUALITY_DASHBOARD_MAIN_PIPELINE_ENABLE_ENV), issues)
    configured = any(
        _optional_text(values.get(name)) is not None
        for name in (
            QUALITY_DASHBOARD_JENKINS_ORIGIN_ENV,
            QUALITY_DASHBOARD_JENKINS_JOB_ENV,
            QUALITY_DASHBOARD_JENKINS_CREDENTIAL_FILE_ENV,
            QUALITY_DASHBOARD_ACTIVE_POLL_SECONDS_ENV,
            QUALITY_DASHBOARD_IDLE_POLL_SECONDS_ENV,
            QUALITY_DASHBOARD_REQUEST_TIMEOUT_SECONDS_ENV,
            QUALITY_DASHBOARD_QUALITY_GRACE_SECONDS_ENV,
        )
    )
    if not requested and not configured and not issues:
        return PipelineMonitorConfig()

    origin = _capture(
        issues,
        lambda: validate_dashboard_jenkins_origin(
            values.get(QUALITY_DASHBOARD_JENKINS_ORIGIN_ENV)
        ),
    )
    job = _capture(
        issues,
        lambda: normalize_pipeline_job_name(
            _required_setting(values, QUALITY_DASHBOARD_JENKINS_JOB_ENV)
        ),
        name=QUALITY_DASHBOARD_JENKINS_JOB_ENV,
    )
    active = _seconds_setting(
        values,
        QUALITY_DASHBOARD_ACTIVE_POLL_SECONDS_ENV,
        DEFAULT_ACTIVE_POLL_SECONDS,
        issues,
        allow_zero=False,
    )
    idle = _seconds_setting(
        values,
        QUALITY_DASHBOARD_IDLE_POLL_SECONDS_ENV,
        DEFAULT_IDLE_POLL_SECONDS,
        issues,
        allow_zero=False,
    )
    timeout = _seconds_setting(
        values,
        QUALITY_DASHBOARD_REQUEST_TIMEOUT_SECONDS_ENV,
        DEFAULT_REQUEST_TIMEOUT_SECONDS,
        issues,
        allow_zero=False,
    )
    grace = _seconds_setting(
        values,
        QUALITY_DASHBOARD_QUALITY_GRACE_SECONDS_ENV,
        DEFAULT_QUALITY_GRACE_SECONDS,
        issues,
        allow_zero=True,
    )
    if active > idle:
        issues.append(
            f"{QUALITY_DASHBOARD_ACTIVE_POLL_SECONDS_ENV} must not exceed "
            f"{QUALITY_DASHBOARD_IDLE_POLL_SECONDS_ENV}"
        )

    credential_file = _capture(
        issues,
        lambda: validate_jenkins_read_credential_file(
            _required_setting(
                values,
                QUALITY_DASHBOARD_JENKINS_CREDENTIAL_FILE_ENV,
            ),
            repository_root=repository_root,
        ),
        name=QUALITY_DASHBOARD_JENKINS_CREDENTIAL_FILE_ENV,
    )
    if credential_file is not None:
        probe_path_text = _optional_text(values.get(PROBE_JENKINS_CREDENTIAL_FILE_ENV))
        if probe_path_text is not None and _same_resolved_path(
            credential_file, Path(probe_path_text)
        ):
            issues.append(
                f"{QUALITY_DASHBOARD_JENKINS_CREDENTIAL_FILE_ENV} must be separate "
                f"from {PROBE_JENKINS_CREDENTIAL_FILE_ENV}"
            )
            credential_file = None
        else:
            try:
                read_jenkins_read_credentials(credential_file)
            except ValueError as error:
                issues.append(str(error))
                credential_file = None

    return PipelineMonitorConfig(
        requested_enabled=requested,
        enabled=requested and not issues,
        warning="; ".join(dict.fromkeys(issues)) if issues else None,
        jenkins_origin=origin,
        job_full_name=job,
        credential_file=credential_file,
        active_poll_seconds=active,
        idle_poll_seconds=idle,
        request_timeout_seconds=timeout,
        quality_grace_seconds=grace,
    )


def validate_dashboard_jenkins_origin(value: object) -> str:
    text = str(value or "").strip().rstrip("/")
    try:
        parsed = urlparse(text)
        port = parsed.port
    except ValueError as error:
        raise ValueError(
            f"invalid {QUALITY_DASHBOARD_JENKINS_ORIGIN_ENV}"
        ) from error
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
        or any(character.isspace() for character in parsed.netloc)
        or port is not None
        and not 1 <= port <= 65535
    ):
        raise ValueError(
            f"{QUALITY_DASHBOARD_JENKINS_ORIGIN_ENV} must be an HTTPS origin "
            "without credentials or path"
        )
    return text


def validate_jenkins_read_credential_file(
    value: str | Path,
    *,
    repository_root: str | Path | None = None,
) -> Path:
    path = Path(value)
    if not path.is_absolute():
        raise ValueError(
            f"{QUALITY_DASHBOARD_JENKINS_CREDENTIAL_FILE_ENV} must be absolute"
        )
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise ValueError(
            f"{QUALITY_DASHBOARD_JENKINS_CREDENTIAL_FILE_ENV} does not exist"
        ) from error
    if not resolved.is_file():
        raise ValueError(
            f"{QUALITY_DASHBOARD_JENKINS_CREDENTIAL_FILE_ENV} must be a regular file"
        )
    if repository_root is not None:
        try:
            root = Path(repository_root).resolve(strict=True)
        except OSError as error:
            raise ValueError("repository root cannot be resolved") from error
        if resolved == root or resolved.is_relative_to(root):
            raise ValueError(
                f"{QUALITY_DASHBOARD_JENKINS_CREDENTIAL_FILE_ENV} must be outside "
                "the repository"
            )
    return resolved


def read_jenkins_read_credentials(path: str | Path) -> JenkinsReadCredentials:
    credential_file = Path(path)
    try:
        if credential_file.stat().st_size > MAX_CREDENTIAL_FILE_BYTES:
            raise ValueError("Jenkins read credential file is too large")
        raw = credential_file.read_text(encoding="utf-8").strip()
    except ValueError:
        raise
    except (OSError, UnicodeError) as error:
        raise ValueError("Jenkins read credential file cannot be read") from error
    if "\n" in raw or "\r" in raw or ":" not in raw:
        raise ValueError("Jenkins read credential file must contain username:token")
    username, token = raw.split(":", 1)
    username = username.strip()
    token = token.strip()
    if not username or not token:
        raise ValueError("Jenkins read credential file must contain username:token")
    return JenkinsReadCredentials(username=username, token=token)


def _parse_enable(value: str | None, issues: list[str]) -> bool:
    normalized = (value or "").strip().casefold()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    issues.append(f"invalid {QUALITY_DASHBOARD_MAIN_PIPELINE_ENABLE_ENV} value")
    return False


def _required_setting(values: Mapping[str, str], name: str) -> str:
    value = _optional_text(values.get(name))
    if value is None:
        raise ValueError(f"{name} is required")
    return value


def _seconds_setting(
    values: Mapping[str, str],
    name: str,
    default: float,
    issues: list[str],
    *,
    allow_zero: bool,
) -> float:
    value = _optional_text(values.get(name))
    if value is None:
        return default
    try:
        parsed = float(value)
    except ValueError:
        issues.append(f"invalid {name} value")
        return default
    if not math.isfinite(parsed) or parsed < 0 or (not allow_zero and parsed == 0):
        issues.append(f"invalid {name} value")
        return default
    return parsed


def _capture(issues: list[str], operation, *, name: str | None = None):
    try:
        return operation()
    except ValueError as error:
        message = str(error)
        issues.append(message if name is None or name in message else f"{name}: {message}")
        return None


def _same_resolved_path(left: Path, right: Path) -> bool:
    try:
        return left == right.resolve(strict=False)
    except OSError:
        return False


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    return text or None


__all__ = (
    "DEFAULT_ACTIVE_POLL_SECONDS",
    "DEFAULT_IDLE_POLL_SECONDS",
    "DEFAULT_QUALITY_GRACE_SECONDS",
    "DEFAULT_REQUEST_TIMEOUT_SECONDS",
    "JenkinsReadCredentials",
    "PipelineMonitorConfig",
    "QUALITY_DASHBOARD_ACTIVE_POLL_SECONDS_ENV",
    "QUALITY_DASHBOARD_IDLE_POLL_SECONDS_ENV",
    "QUALITY_DASHBOARD_JENKINS_CREDENTIAL_FILE_ENV",
    "QUALITY_DASHBOARD_JENKINS_JOB_ENV",
    "QUALITY_DASHBOARD_JENKINS_ORIGIN_ENV",
    "QUALITY_DASHBOARD_MAIN_PIPELINE_ENABLE_ENV",
    "QUALITY_DASHBOARD_QUALITY_GRACE_SECONDS_ENV",
    "QUALITY_DASHBOARD_REQUEST_TIMEOUT_SECONDS_ENV",
    "load_pipeline_monitor_config",
    "read_jenkins_read_credentials",
    "validate_dashboard_jenkins_origin",
    "validate_jenkins_read_credential_file",
)
