from __future__ import annotations

from typing import Any

from jsonpath_ng.ext import parse
import requests


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


_default_assertions = BaseAssertions()


def assert_status_code(response: requests.Response, expected: int) -> requests.Response:
    return _default_assertions.assert_status_code(response, expected)


def assert_json_value(response: requests.Response, json_path: str, expected: Any) -> requests.Response:
    return _default_assertions.assert_json_value(response, json_path, expected)


def assert_json_path_exists(response: requests.Response, json_path: str) -> requests.Response:
    return _default_assertions.assert_json_path_exists(response, json_path)


async def async_assert_status_code(response: requests.Response, expected: int) -> requests.Response:
    return await _default_assertions.async_assert_status_code(response, expected)


async def async_assert_json_value(response: requests.Response, json_path: str, expected: Any) -> requests.Response:
    return await _default_assertions.async_assert_json_value(response, json_path, expected)


async def async_assert_json_path_exists(response: requests.Response, json_path: str) -> requests.Response:
    return await _default_assertions.async_assert_json_path_exists(response, json_path)
