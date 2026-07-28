from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import re
from typing import Any, Final

from jsonpath_ng.ext import parse
import requests

from util.redaction import redact_sensitive_data, redact_text_body, redact_urlencoded_text


_UNSET: Final = object()
_NO_VALUE: Final = object()
_VARIABLE_NAME_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")
_MAX_RESPONSE_TEXT_IN_ERROR: Final[int] = 2000


class TestContextError(AssertionError):
    """Base error for test-case scoped context failures."""


class ContextVariableError(TestContextError):
    """Base error for invalid or unavailable context variables."""


class ContextVariableNotFound(ContextVariableError):
    """Raised when a required context variable does not exist."""


class ContextVariableTypeError(ContextVariableError):
    """Raised when a context variable has an unexpected type."""


class ContextExtractionError(TestContextError):
    """Raised when a variable cannot be extracted from a source."""


class ContextCleanupError(TestContextError):
    """Raised after one or more cleanup callbacks fail."""

    def __init__(self, errors: list[BaseException]):
        self.errors = errors
        details = "; ".join(f"{type(error).__name__}: {_redact_text(str(error))}" for error in errors)
        super().__init__(f"Test context cleanup failed with {len(errors)} error(s): {details}")


@dataclass
class _CleanupCallback:
    callback: Callable[..., Any]
    args: tuple[Any, ...]
    kwargs: dict[str, Any]


class TestContext:
    """Test-case scoped variable store and cleanup stack.

    The context owns all variable state. It intentionally does not use a module-level
    shared store, so callers can safely create one context per pytest case or worker.
    """

    __test__ = False

    def __init__(self, *, name: str | None = None):
        self.name = name
        self._variables: dict[str, Any] = {}
        self._cleanup_callbacks: list[_CleanupCallback] = []

    def set(self, name: str, value: Any) -> Any:
        _validate_variable_name(name)
        self._variables[name] = value
        return value

    def get(
        self,
        name: str,
        default: Any = _UNSET,
        *,
        expected_type: type | tuple[type, ...] | None = None,
    ) -> Any:
        _validate_variable_name(name)
        if name not in self._variables:
            if default is not _UNSET:
                return default
            raise ContextVariableNotFound(f"Context variable {name!r} was not found.")

        value = self._variables[name]
        _ensure_expected_type(name, value, expected_type)
        return value

    def require(
        self,
        name: str,
        *,
        expected_type: type | tuple[type, ...] | None = None,
    ) -> Any:
        return self.get(name, expected_type=expected_type)

    def has(self, name: str) -> bool:
        _validate_variable_name(name)
        return name in self._variables

    def delete(self, name: str) -> Any:
        _validate_variable_name(name)
        if name not in self._variables:
            raise ContextVariableNotFound(f"Context variable {name!r} was not found.")
        return self._variables.pop(name)

    def clear(self) -> None:
        self._variables.clear()

    def snapshot(self) -> dict[str, Any]:
        return dict(self._variables)

    def extract(
        self,
        name: str,
        response: requests.Response | None = None,
        *,
        json_path: str | None = None,
        header: str | None = None,
        cookie: str | None = None,
        regex: str | None = None,
        group: int | str = 0,
        source_text: str | None = None,
        multiple: bool = False,
        required: bool = True,
        default: Any = _UNSET,
        expected_type: type | tuple[type, ...] | None = None,
        transform: Callable[[Any], Any] | None = None,
        allow_none: bool = False,
    ) -> Any:
        _validate_variable_name(name)
        source_count = sum(value is not None for value in (json_path, header, cookie, regex))
        if source_count != 1:
            raise ContextExtractionError(
                "extract() requires exactly one source: json_path, header, cookie, or regex."
            )

        value = self._extract_value(
            name,
            response,
            json_path=json_path,
            header=header,
            cookie=cookie,
            regex=regex,
            group=group,
            source_text=source_text,
            multiple=multiple,
        )
        return self._store_extracted_value(
            name,
            value,
            required=required,
            default=default,
            expected_type=expected_type,
            transform=transform,
            allow_none=allow_none,
            source_description=_format_source_description(
                json_path=json_path,
                header=header,
                cookie=cookie,
                regex=regex,
            ),
            response=response,
        )

    def extract_first(
        self,
        name: str,
        response: requests.Response,
        *,
        sources: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...],
        required: bool = True,
        default: Any = _UNSET,
        expected_type: type | tuple[type, ...] | None = None,
        transform: Callable[[Any], Any] | None = None,
        allow_none: bool = False,
    ) -> Any:
        _validate_variable_name(name)
        if not sources:
            raise ContextExtractionError("extract_first() requires at least one source.")

        source_errors: list[str] = []
        for source in sources:
            try:
                normalized_source = _normalize_source(source)
                value = self._extract_value(name, response, **normalized_source)
            except ContextExtractionError as exc:
                source_errors.append(str(exc))
                continue
            if _has_extracted_value(value):
                return self._store_extracted_value(
                    name,
                    value,
                    required=required,
                    default=default,
                    expected_type=expected_type,
                    transform=transform,
                    allow_none=allow_none,
                    source_description=_format_source_description(**normalized_source),
                    response=response,
                )
            source_errors.append(f"{_format_source_description(**normalized_source)} did not match any value.")

        if default is not _UNSET:
            return self._store_extracted_value(
                name,
                default,
                required=False,
                default=default,
                expected_type=expected_type,
                transform=transform,
                allow_none=allow_none,
                source_description="default",
                response=response,
            )
        if required:
            raise ContextExtractionError(
                f"Failed to extract required context variable {name!r} from any source. "
                f"Sources: {source_errors}. Response: {_redact_response_summary(response)}"
            )
        return None

    def add_cleanup(self, callback: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        if not callable(callback):
            raise TypeError(f"cleanup callback must be callable, actual: {callback!r}")
        self._cleanup_callbacks.append(_CleanupCallback(callback=callback, args=args, kwargs=dict(kwargs)))

    def cleanup(self) -> None:
        errors: list[BaseException] = []
        while self._cleanup_callbacks:
            cleanup_callback = self._cleanup_callbacks.pop()
            try:
                cleanup_callback.callback(*cleanup_callback.args, **cleanup_callback.kwargs)
            except BaseException as exc:  # noqa: BLE001 - cleanup must continue after any callback failure.
                errors.append(exc)

        if errors:
            raise ContextCleanupError(errors)

    def _extract_value(
        self,
        name: str,
        response: requests.Response | None,
        *,
        json_path: str | None = None,
        header: str | None = None,
        cookie: str | None = None,
        regex: str | None = None,
        group: int | str = 0,
        source_text: str | None = None,
        multiple: bool = False,
    ) -> Any:
        if json_path is not None:
            _require_response(name, response, "json_path")
            return _extract_json_path(name, response, json_path, multiple=multiple)
        if header is not None:
            _require_response(name, response, "header")
            return _extract_header(response, header)
        if cookie is not None:
            _require_response(name, response, "cookie")
            return _extract_cookie(response, cookie)
        if regex is not None:
            if source_text is None:
                _require_response(name, response, "regex")
                source_text = response.text
            return _extract_regex(name, source_text, regex, group)
        raise ContextExtractionError("No extraction source was provided.")

    def _store_extracted_value(
        self,
        name: str,
        value: Any,
        *,
        required: bool,
        default: Any,
        expected_type: type | tuple[type, ...] | None,
        transform: Callable[[Any], Any] | None,
        allow_none: bool,
        source_description: str,
        response: requests.Response | None,
    ) -> Any:
        if value is _NO_VALUE or not _has_extracted_value(value):
            if default is not _UNSET:
                value = default
            elif required:
                raise ContextExtractionError(
                    f"Failed to extract required context variable {name!r}. "
                    f"Source: {source_description}. Response: {_redact_response_summary(response)}"
                )
            else:
                return None

        if value is None and required and not allow_none:
            raise ContextExtractionError(
                f"Context variable {name!r} extracted None from {source_description}, but None is not allowed."
            )

        if transform is not None:
            try:
                value = transform(value)
            except Exception as exc:
                raise ContextExtractionError(
                    f"Failed to transform context variable {name!r} from {source_description}: "
                    f"{type(exc).__name__}: {_redact_text(str(exc))}"
                ) from exc

        _ensure_expected_type(name, value, expected_type)
        self.set(name, value)
        return value


def _validate_variable_name(name: str) -> None:
    if not isinstance(name, str) or not _VARIABLE_NAME_PATTERN.match(name):
        raise ContextVariableError(
            "Context variable name must match [A-Za-z_][A-Za-z0-9_.-]*, "
            f"actual: {name!r}"
        )


def _ensure_expected_type(
    name: str,
    value: Any,
    expected_type: type | tuple[type, ...] | None,
) -> None:
    if expected_type is None or isinstance(value, expected_type):
        return
    raise ContextVariableTypeError(
        f"Context variable {name!r} type mismatch. "
        f"Expected: {_format_expected_type(expected_type)}, "
        f"actual: {type(value).__name__}, value: {_format_value(name, value)}"
    )


def _format_expected_type(expected_type: type | tuple[type, ...]) -> str:
    if isinstance(expected_type, tuple):
        return " | ".join(type_.__name__ for type_ in expected_type)
    return expected_type.__name__


def _format_value(name: str, value: Any) -> str:
    redacted_value = redact_sensitive_data({name: value})[name]
    return repr(redacted_value)


def _require_response(
    name: str,
    response: requests.Response | None,
    source_name: str,
) -> None:
    if response is None:
        raise ContextExtractionError(
            f"Response is required to extract context variable {name!r} from {source_name}."
        )


def _extract_json_path(
    name: str,
    response: requests.Response,
    json_path: str,
    *,
    multiple: bool,
) -> Any:
    if not json_path.startswith("$"):
        raise ContextExtractionError(f"JSONPath must start with '$', actual: {json_path!r}.")

    try:
        body = response.json()
    except ValueError as exc:
        raise ContextExtractionError(
            f"Response body is not valid JSON while extracting context variable {name!r}. "
            f"JSONPath: {json_path!r}. Response: {_redact_response_summary(response)}"
        ) from exc

    try:
        matches = [match.value for match in parse(json_path).find(body)]
    except Exception as exc:
        raise ContextExtractionError(
            f"Invalid JSONPath while extracting context variable {name!r}: {json_path!r}. "
            f"{type(exc).__name__}: {_redact_text(str(exc))}"
        ) from exc

    if not matches:
        return _NO_VALUE
    if multiple:
        return matches
    return matches[0]


def _extract_header(response: requests.Response, header: str) -> Any:
    value = response.headers.get(header)
    if value is None:
        return _NO_VALUE
    if isinstance(value, str):
        value = value.strip()
    return value


def _extract_cookie(response: requests.Response, cookie: str) -> Any:
    value = response.cookies.get(cookie)
    if value is None:
        return _NO_VALUE
    if isinstance(value, str):
        value = value.strip()
    return value


def _extract_regex(name: str, source_text: str, pattern: str, group: int | str) -> Any:
    try:
        match = re.search(pattern, source_text)
    except re.error as exc:
        raise ContextExtractionError(
            f"Invalid regex while extracting context variable {name!r}: {pattern!r}. "
            f"{type(exc).__name__}: {_redact_text(str(exc))}"
        ) from exc

    if match is None:
        return _NO_VALUE

    try:
        value = match.group(group)
    except IndexError as exc:
        raise ContextExtractionError(
            f"Regex group {group!r} does not exist while extracting context variable {name!r}."
        ) from exc
    return value.strip() if isinstance(value, str) else value


def _normalize_source(source: Mapping[str, Any]) -> dict[str, Any]:
    allowed_keys = {"json_path", "header", "cookie", "regex", "group", "source_text", "multiple"}
    unknown_keys = set(source) - allowed_keys
    if unknown_keys:
        raise ContextExtractionError(f"Unknown extract_first() source keys: {sorted(unknown_keys)!r}.")

    normalized = {
        "json_path": source.get("json_path"),
        "header": source.get("header"),
        "cookie": source.get("cookie"),
        "regex": source.get("regex"),
        "group": source.get("group", 0),
        "source_text": source.get("source_text"),
        "multiple": bool(source.get("multiple", False)),
    }
    source_count = sum(normalized[key] is not None for key in ("json_path", "header", "cookie", "regex"))
    if source_count != 1:
        raise ContextExtractionError(
            "Each extract_first() source requires exactly one of json_path, header, cookie, or regex."
        )
    return normalized


def _format_source_description(**source: Any) -> str:
    for source_name in ("json_path", "header", "cookie", "regex"):
        value = source.get(source_name)
        if value is not None:
            return f"{source_name}={value!r}"
    return "<unknown>"


def _has_extracted_value(value: Any) -> bool:
    if value is _NO_VALUE:
        return False
    if isinstance(value, str) and value == "":
        return False
    if isinstance(value, list) and not value:
        return False
    return True


def _redact_response_summary(response: requests.Response | None) -> str:
    if response is None:
        return "<no response>"

    header_names = sorted(str(name) for name in response.headers.keys())
    content_type = response.headers.get("Content-Type", "")
    redacted_body = redact_text_body(response.text, content_type)
    redacted_body = redact_urlencoded_text(redacted_body)
    if len(redacted_body) > _MAX_RESPONSE_TEXT_IN_ERROR:
        redacted_body = f"{redacted_body[:_MAX_RESPONSE_TEXT_IN_ERROR]}...<truncated>"
    return (
        f"status_code={response.status_code}, "
        f"headers={header_names!r}, "
        f"body={redacted_body}"
    )


def _redact_text(text: str) -> str:
    return redact_urlencoded_text(text)
