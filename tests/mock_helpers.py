from __future__ import annotations

from collections.abc import Iterable, Sequence
from copy import deepcopy
from dataclasses import dataclass
import json
from typing import Any

import requests
from requests.structures import CaseInsensitiveDict


_UNSET = object()


@dataclass(frozen=True)
class RequestCall:
    method: str
    url: str
    kwargs: dict[str, Any]


def make_response(
    url: str,
    *,
    method: str = "GET",
    status_code: int = 200,
    reason: str = "Reason",
    headers: dict[str, str] | None = None,
    json_body: Any = _UNSET,
    json_text: str | None = None,
    text_body: str | None = None,
    content_type: str | None = None,
) -> requests.Response:
    response = requests.Response()
    response.status_code = status_code
    response.reason = reason

    if json_body is not _UNSET:
        body = json.dumps(json_body, ensure_ascii=False)
        default_content_type = "application/json"
    elif json_text is not None:
        body = json_text
        default_content_type = "application/json"
    elif text_body is not None:
        body = text_body
        default_content_type = "text/plain"
    else:
        body = json.dumps({"ok": True})
        default_content_type = "application/json"

    response._content = body.encode("utf-8")
    response.headers = CaseInsensitiveDict()
    response.headers["Content-Type"] = content_type or default_content_type
    response.headers.update(headers or {})
    response.request = requests.Request(method, url).prepare()
    return response


class SequenceTransport:
    def __init__(self, results: Iterable[requests.Response | BaseException]):
        self._results = list(results)
        self.calls: list[RequestCall] = []

    @property
    def remaining(self) -> int:
        return len(self._results)

    def __call__(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        self.calls.append(RequestCall(method=method, url=url, kwargs=_safe_copy(kwargs)))
        if not self._results:
            raise AssertionError(f"SequenceTransport has no response left for {method} {url}")

        result = self._results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


class SleepRecorder:
    def __init__(self, advance_clock: Any | None = None):
        self.calls: list[float] = []
        self.advance_clock = advance_clock

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)
        if self.advance_clock is not None:
            self.advance_clock(seconds)


class FakeApiCallLogger:
    def __init__(self, *args: Any, **kwargs: Any):
        self.args = args
        self.kwargs = kwargs
        self.success_responses: list[requests.Response] = []
        self.failure_errors: list[BaseException] = []
        self.retry_records: list[list[Any]] = []
        self.polling_transitions: list[str] = []

    def attach_success(self, response: requests.Response) -> None:
        self.success_responses.append(response)

    def attach_failure(self, error: BaseException) -> None:
        self.failure_errors.append(error)

    def attach_retry_records(self, records: list[Any]) -> None:
        self.retry_records.append(list(records))

    def attach_polling_transitions(self, transitions_text: str) -> None:
        self.polling_transitions.append(transitions_text)


class FakeStreamResponse:
    def __init__(
        self,
        *,
        lines: Sequence[bytes | str],
        status_code: int = 200,
        headers: dict[str, str] | None = None,
        error_after: int | None = None,
        error: BaseException | None = None,
    ):
        self.lines = list(lines)
        self.status_code = status_code
        self.headers = CaseInsensitiveDict(headers or {})
        self.error_after = error_after
        self.error = error or requests.exceptions.ChunkedEncodingError("stream interrupted")
        self.closed = False

    @property
    def text(self) -> str:
        return "\n".join(_line_to_text(line) for line in self.lines)

    def iter_lines(self, decode_unicode: bool = False):
        for index, line in enumerate(self.lines, start=1):
            if isinstance(line, str):
                raw_line = line.encode("utf-8")
            else:
                raw_line = line

            yield raw_line.decode("utf-8", errors="replace") if decode_unicode else raw_line

            if self.error_after is not None and index >= self.error_after:
                raise self.error

    def close(self) -> None:
        self.closed = True


def create_fake_logger(
    created_loggers: list[FakeApiCallLogger],
    *args: Any,
    **kwargs: Any,
) -> FakeApiCallLogger:
    logger = FakeApiCallLogger(*args, **kwargs)
    created_loggers.append(logger)
    return logger


def connection_error(message: str = "connection error") -> requests.ConnectionError:
    return requests.ConnectionError(message)


def connect_timeout(message: str = "connect timeout") -> requests.ConnectTimeout:
    return requests.ConnectTimeout(message)


def read_timeout(message: str = "read timeout") -> requests.ReadTimeout:
    return requests.ReadTimeout(message)


def timeout_error(message: str = "timeout") -> requests.Timeout:
    return requests.Timeout(message)


def polling_responses(
    url: str,
    statuses: Iterable[Any],
    *,
    method: str = "GET",
    status_field: str = "status",
    status_code: int | Sequence[int] = 200,
    result: Any = None,
    error: Any = None,
    headers: dict[str, str] | None = None,
) -> list[requests.Response]:
    status_values = list(statuses)
    status_codes = _expand_status_codes(status_code, len(status_values))
    responses: list[requests.Response] = []

    for index, status in enumerate(status_values):
        body = {status_field: status}
        if status in {"succeeded", "success"} and result is not None:
            body["result"] = result
        if status in {"failed", "cancelled"} and error is not None:
            body["error"] = error
        responses.append(
            make_response(
                url,
                method=method,
                status_code=status_codes[index],
                json_body=body,
                headers=headers,
            )
        )
    return responses


def _expand_status_codes(status_code: int | Sequence[int], count: int) -> list[int]:
    if isinstance(status_code, int):
        return [status_code] * count
    status_codes = list(status_code)
    if len(status_codes) != count:
        raise ValueError("status_code sequence length must match statuses length")
    return status_codes


def _line_to_text(line: bytes | str) -> str:
    if isinstance(line, bytes):
        return line.decode("utf-8", errors="replace")
    return line


def _safe_copy(value: Any) -> Any:
    try:
        return deepcopy(value)
    except Exception:
        return value
