from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from jsonschema import Draft202012Validator, SchemaError
from jsonschema.exceptions import ValidationError
from jsonschema.validators import validator_for
from jsonpath_ng.ext import parse
import requests

from util.redaction import redact_sensitive_data, redact_text_body, redact_urlencoded_text


_MISSING = object()
_MAX_RESPONSE_BODY_IN_ASSERTION = 2000


class BaseAssertions:
    def assert_status_code(self, response: requests.Response, expected: int) -> requests.Response:
        actual = response.status_code
        assert actual == expected, f"状态码断言失败：期望 {expected}，实际 {actual}。响应内容：{response.text}"
        return response

    def assert_json_value(
        self,
        response: requests.Response,
        json_path: str,
        expected: Any,
    ) -> requests.Response:
        assert json_path.startswith("$"), f"JSONPath 表达式必须以 '$' 开头，当前值：{json_path!r}"

        try:
            body = response.json()
        except ValueError as exc:
            raise AssertionError(f"响应内容不是合法 JSON。响应内容：{response.text}") from exc

        matches = [match.value for match in parse(json_path).find(body)]
        assert matches, f"JSONPath {json_path!r} 未匹配到任何值。响应内容：{response.text}"

        actual = matches[0] if len(matches) == 1 else matches
        assert actual == expected, f"JSONPath 断言失败：路径 {json_path!r}，期望 {expected!r}，实际 {actual!r}"
        return response

    def assert_json_path_exists(self, response: requests.Response, json_path: str) -> requests.Response:
        assert json_path.startswith("$"), f"JSONPath expression must start with '$', current value: {json_path!r}"

        try:
            body = response.json()
        except ValueError as exc:
            raise AssertionError(f"Response body is not valid JSON. Response body: {response.text}") from exc

        matches = [match.value for match in parse(json_path).find(body)]
        assert matches, f"JSONPath {json_path!r} did not match any value. Response body: {response.text}"
        return response

    def assert_schema(self, response: requests.Response, schema: Mapping[str, Any]) -> requests.Response:
        try:
            body = response.json()
        except ValueError as exc:
            redacted_body = _redact_response_text(response)
            raise AssertionError(f"Response body is not valid JSON. Response body: {redacted_body}") from exc

        try:
            validator_cls = validator_for(schema) if "$schema" in schema else Draft202012Validator
            validator_cls.check_schema(schema)
        except SchemaError as exc:
            raise AssertionError(f"Invalid JSON Schema: {exc.message}") from exc

        validator = validator_cls(schema)
        errors = sorted(validator.iter_errors(body), key=_validation_error_sort_key)
        if errors:
            raise AssertionError(_format_schema_error(errors[0], response))

        return response

    async def async_assert_status_code(
        self,
        response: requests.Response,
        expected: int,
    ) -> requests.Response:
        return self.assert_status_code(response, expected)

    async def async_assert_json_value(
        self,
        response: requests.Response,
        json_path: str,
        expected: Any,
    ) -> requests.Response:
        return self.assert_json_value(response, json_path, expected)

    async def async_assert_json_path_exists(
        self,
        response: requests.Response,
        json_path: str,
    ) -> requests.Response:
        return self.assert_json_path_exists(response, json_path)

    async def async_assert_schema(
        self,
        response: requests.Response,
        schema: Mapping[str, Any],
    ) -> requests.Response:
        return self.assert_schema(response, schema)


_default_assertions = BaseAssertions()


def assert_status_code(response: requests.Response, expected: int) -> requests.Response:
    return _default_assertions.assert_status_code(response, expected)


def assert_json_value(response: requests.Response, json_path: str, expected: Any) -> requests.Response:
    return _default_assertions.assert_json_value(response, json_path, expected)


def assert_json_path_exists(response: requests.Response, json_path: str) -> requests.Response:
    return _default_assertions.assert_json_path_exists(response, json_path)


def assert_schema(response: requests.Response, schema: Mapping[str, Any]) -> requests.Response:
    return _default_assertions.assert_schema(response, schema)


async def async_assert_status_code(response: requests.Response, expected: int) -> requests.Response:
    return await _default_assertions.async_assert_status_code(response, expected)


async def async_assert_json_value(response: requests.Response, json_path: str, expected: Any) -> requests.Response:
    return await _default_assertions.async_assert_json_value(response, json_path, expected)


async def async_assert_json_path_exists(response: requests.Response, json_path: str) -> requests.Response:
    return await _default_assertions.async_assert_json_path_exists(response, json_path)


async def async_assert_schema(response: requests.Response, schema: Mapping[str, Any]) -> requests.Response:
    return await _default_assertions.async_assert_schema(response, schema)


def _validation_error_sort_key(error: ValidationError) -> tuple[list[str], list[str]]:
    return (
        [str(part) for part in error.absolute_path],
        [str(part) for part in error.absolute_schema_path],
    )


def _format_schema_error(error: ValidationError, response: requests.Response) -> str:
    path_parts = _error_path_parts(error)
    actual_value = _actual_value(error)
    redacted_actual_value = _redact_value_for_path(actual_value, path_parts)
    expected = _format_expected(error)
    message = _redact_error_message(error.message, actual_value, path_parts)

    lines = [
        "JSON Schema assertion failed.",
        f"Path: {_format_json_path(path_parts)}",
        f"Schema path: {_format_schema_path(error.absolute_schema_path)}",
        f"Validator: {error.validator}",
        f"Expected: {expected}",
        f"Actual type: {_type_name(actual_value)}",
        f"Actual value: {_format_actual_value(redacted_actual_value)}",
        f"Message: {message}",
    ]

    if error.validator == "required":
        lines.append(f"Response body: {_redact_response_text(response)}")

    return "\n".join(lines)


def _error_path_parts(error: ValidationError) -> list[Any]:
    path_parts = list(error.absolute_path)
    if error.validator == "required":
        missing_property = _missing_required_property(error)
        if missing_property is not None:
            path_parts.append(missing_property)
    return path_parts


def _missing_required_property(error: ValidationError) -> str | None:
    if error.validator != "required":
        return None
    if not isinstance(error.instance, Mapping):
        return None
    if not isinstance(error.validator_value, Iterable):
        return None

    for property_name in error.validator_value:
        if isinstance(property_name, str) and property_name not in error.instance:
            return property_name
    return None


def _actual_value(error: ValidationError) -> Any:
    if error.validator == "required":
        return _MISSING
    return error.instance


def _format_json_path(path_parts: Iterable[Any]) -> str:
    path = "$"
    for part in path_parts:
        if isinstance(part, int):
            path += f"[{part}]"
        elif isinstance(part, str) and part.isidentifier():
            path += f".{part}"
        else:
            path += f"[{part!r}]"
    return path


def _format_schema_path(path_parts: Iterable[Any]) -> str:
    parts = [str(part) for part in path_parts]
    return "/".join(parts) if parts else "$"


def _format_expected(error: ValidationError) -> str:
    if error.validator == "required":
        missing_property = _missing_required_property(error)
        if missing_property is not None:
            return f"required property {missing_property!r}"

    if error.validator == "minimum":
        return f">= {error.validator_value!r}"

    if error.validator == "minLength":
        return f"length >= {error.validator_value!r}"

    if error.validator == "minItems":
        return f"items >= {error.validator_value!r}"

    return _format_schema_value(error.validator_value)


def _format_schema_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    return repr(value)


def _type_name(value: Any) -> str:
    if value is _MISSING:
        return "<missing>"
    return type(value).__name__


def _format_actual_value(value: Any) -> str:
    if value is _MISSING:
        return "<missing>"
    return repr(value)


def _redact_value_for_path(value: Any, path_parts: list[Any]) -> Any:
    if value is _MISSING:
        return value
    redacted_value = redact_sensitive_data(value)
    if path_parts and isinstance(path_parts[-1], str):
        field_name = path_parts[-1]
        return redact_sensitive_data({field_name: redacted_value})[field_name]
    return redacted_value


def _redact_error_message(message: str, actual_value: Any, path_parts: list[Any]) -> str:
    redacted_message = redact_urlencoded_text(message)
    if actual_value is _MISSING:
        return redacted_message

    redacted_value = _redact_value_for_path(actual_value, path_parts)
    if redacted_value == actual_value:
        return redacted_message

    for raw, replacement in (
        (repr(actual_value), repr(redacted_value)),
        (str(actual_value), str(redacted_value)),
    ):
        if raw:
            redacted_message = redacted_message.replace(raw, replacement)
    return redacted_message


def _redact_response_text(response: requests.Response) -> str:
    content_type = response.headers.get("Content-Type", "")
    redacted_body = redact_text_body(response.text, content_type)
    redacted_body = redact_urlencoded_text(redacted_body)
    if len(redacted_body) > _MAX_RESPONSE_BODY_IN_ASSERTION:
        return f"{redacted_body[:_MAX_RESPONSE_BODY_IN_ASSERTION]}...<truncated>"
    return redacted_body
