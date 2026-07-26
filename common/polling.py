from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum
from typing import Any

from jsonpath_ng.ext import parse
import requests

from util.redaction import redact_text_body, redact_urlencoded_text


MAX_POLLING_RESPONSE_TEXT = 2000


class PollingState(Enum):
    PENDING = "pending"
    SUCCESS = "success"
    FAILURE = "failure"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class PollingPolicy:
    status_json_path: str = "$.status"
    pending: frozenset[Any] = frozenset({"queued", "running"})
    success: frozenset[Any] = frozenset({"succeeded"})
    failure: frozenset[Any] = frozenset({"failed", "cancelled"})
    result_json_path: str | None = None
    error_json_path: str | None = "$.error"
    unknown: str = "fail"

    def __post_init__(self) -> None:
        _validate_json_path("status_json_path", self.status_json_path)
        _validate_optional_json_path("result_json_path", self.result_json_path)
        _validate_optional_json_path("error_json_path", self.error_json_path)
        if self.unknown not in {"fail", "pending", "ignore"}:
            raise ValueError("unknown must be 'fail', 'pending', or 'ignore'")


@dataclass(frozen=True)
class PollingEvaluation:
    state: PollingState
    raw_status: Any
    result_value: Any = None
    error_value: Any = None


@dataclass(frozen=True)
class PollingTransition:
    attempt_index: int
    elapsed_seconds: float
    state: PollingState
    raw_status: Any
    response_status_code: int


class PollingError(AssertionError):
    def __init__(
        self,
        message: str,
        *,
        path: str,
        last_status: Any,
        last_response: requests.Response | None,
        transitions: list[PollingTransition],
    ):
        super().__init__(message)
        self.path = path
        self.last_status = last_status
        self.last_response = last_response
        self.transitions = transitions


class PollingFailedError(PollingError):
    def __init__(
        self,
        *,
        path: str,
        last_status: Any,
        last_response: requests.Response,
        transitions: list[PollingTransition],
        error_value: Any = None,
    ):
        message = (
            f"poll_get failed: path={path!r}, status={last_status!r}, "
            f"error={error_value!r}, transitions={format_transition_sequence(transitions)}"
        )
        super().__init__(
            message,
            path=path,
            last_status=last_status,
            last_response=last_response,
            transitions=transitions,
        )
        self.error_value = error_value


class PollingUnknownStateError(PollingError):
    def __init__(
        self,
        *,
        path: str,
        last_status: Any,
        last_response: requests.Response,
        transitions: list[PollingTransition],
    ):
        message = (
            f"poll_get unknown state: path={path!r}, status={last_status!r}, "
            f"transitions={format_transition_sequence(transitions)}"
        )
        super().__init__(
            message,
            path=path,
            last_status=last_status,
            last_response=last_response,
            transitions=transitions,
        )


class PollingTimeoutError(TimeoutError):
    def __init__(
        self,
        *,
        path: str,
        timeout: float,
        last_status: Any,
        last_response: requests.Response | None,
        transitions: list[PollingTransition],
    ):
        response_text = _redact_response_text(last_response) if last_response is not None else "<empty>"
        message = (
            f"poll_get timed out after {timeout} seconds: path={path!r}, "
            f"last_status={last_status!r}, transitions={format_transition_sequence(transitions)}, "
            f"last response={response_text}"
        )
        super().__init__(message)
        self.path = path
        self.timeout = timeout
        self.last_status = last_status
        self.last_response = last_response
        self.transitions = transitions


def evaluate_polling_response(response: requests.Response, policy: PollingPolicy) -> PollingEvaluation:
    try:
        body = response.json()
    except ValueError as exc:
        raise AssertionError(f"polling response body is not valid JSON: {_redact_response_text(response)}") from exc

    raw_status = _extract_json_path_value(body, policy.status_json_path)

    if policy.error_json_path is not None:
        error_value = _extract_json_path_value(body, policy.error_json_path)
        if error_value is not None:
            return PollingEvaluation(
                state=PollingState.FAILURE,
                raw_status=raw_status if raw_status is not None else error_value,
                error_value=error_value,
            )

    if policy.result_json_path is not None:
        result_value = _extract_json_path_value(body, policy.result_json_path)
        if result_value is not None:
            return PollingEvaluation(
                state=PollingState.SUCCESS,
                raw_status=raw_status,
                result_value=result_value,
            )

    if raw_status in policy.pending:
        return PollingEvaluation(state=PollingState.PENDING, raw_status=raw_status)
    if raw_status in policy.success:
        return PollingEvaluation(state=PollingState.SUCCESS, raw_status=raw_status)
    if raw_status in policy.failure:
        return PollingEvaluation(state=PollingState.FAILURE, raw_status=raw_status)

    if policy.unknown in {"pending", "ignore"}:
        return PollingEvaluation(state=PollingState.PENDING, raw_status=raw_status)
    return PollingEvaluation(state=PollingState.UNKNOWN, raw_status=raw_status)


def format_polling_transitions(transitions: Iterable[PollingTransition]) -> str:
    lines = []
    for transition in transitions:
        lines.append(
            f"{transition.attempt_index}. {transition.elapsed_seconds:.3f}s "
            f"{transition.raw_status!r} -> {transition.state.value} "
            f"HTTP {transition.response_status_code}"
        )
    return "\n".join(lines) if lines else "<empty>"


def format_transition_sequence(transitions: Iterable[PollingTransition]) -> str:
    values = [str(transition.raw_status) for transition in transitions]
    return " -> ".join(values) if values else "<empty>"


def _extract_json_path_value(body: Any, json_path: str) -> Any:
    matches = [match.value for match in parse(json_path).find(body)]
    if not matches:
        return None
    return matches[0] if len(matches) == 1 else matches


def _validate_json_path(name: str, json_path: str) -> None:
    if not json_path.startswith("$"):
        raise ValueError(f"{name} must start with '$', current value: {json_path!r}")


def _validate_optional_json_path(name: str, json_path: str | None) -> None:
    if json_path is not None:
        _validate_json_path(name, json_path)


def _redact_response_text(response: requests.Response) -> str:
    content_type = response.headers.get("Content-Type", "")
    redacted_body = redact_text_body(response.text, content_type)
    redacted_body = redact_urlencoded_text(redacted_body)
    if len(redacted_body) > MAX_POLLING_RESPONSE_TEXT:
        return f"{redacted_body[:MAX_POLLING_RESPONSE_TEXT]}...<truncated>"
    return redacted_body
