from __future__ import annotations

import time
from typing import Any
from urllib.parse import urljoin

from jsonpath_ng.ext import parse
import requests

from config import Settings, settings
from common.base_decorators import download_links_from_poll_get
from util import (
    API_REQUEST_STEP_NAME,
    ApiCallLogger,
    POLL_GET_REQUEST_STEP_NAME,
    POLL_GET_RESPONSE_STEP_NAME,
    start_media_downloads,
)


class BaseRequest:
    def __init__(self, config: Settings = settings):
        self.config = config
        self.session = requests.Session()
        self.default_headers = self._build_default_headers()
        self.session.headers.update(self.default_headers)

    def request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        attach_log = kwargs.pop("_attach_log", True)
        url = self._build_url(path)
        kwargs.setdefault("timeout", self.config.timeout)

        headers = kwargs.pop("headers", None)
        if headers:
            kwargs["headers"] = self._merge_headers(headers)

        if method.upper() == "POST":
            start_media_downloads(kwargs.get("json"))

        logger = ApiCallLogger(method, url, kwargs)
        try:
            response = self.session.request(method=method, url=url, **kwargs)
        except Exception as error:
            if attach_log:
                logger.attach_failure(error)
            raise

        if attach_log:
            logger.attach_success(response)
        return response

    def set_header(self, name: str, value: str) -> None:
        self.session.headers[name] = value

    def update_headers(self, headers: dict[str, str]) -> None:
        self.session.headers.update(headers)

    def remove_header(self, name: str) -> None:
        self.session.headers.pop(name, None)

    def reset_headers(self) -> None:
        self.session.headers.clear()
        self.session.headers.update(self.default_headers)

    def get(self, path: str, **kwargs: Any) -> requests.Response:
        return self.request("GET", path, **kwargs)

    @download_links_from_poll_get
    def poll_get(
        self,
        path: str,
        *,
        poll_interval: float = 2,
        poll_timeout: float | None = None,
        success_json_path: str | None = None,
        failure_json_path: str | None = None,
        **kwargs: Any,
    ) -> requests.Response:
        if poll_interval <= 0:
            raise ValueError("poll_interval must be greater than 0")

        timeout = self.config.timeout if poll_timeout is None else poll_timeout
        if timeout <= 0:
            raise ValueError("poll_timeout must be greater than 0")

        deadline = time.monotonic() + timeout
        last_response: requests.Response
        last_status: Any
        last_logger: ApiCallLogger | None = None

        while True:
            last_response, last_logger = self._request_without_attach(
                "GET",
                path,
                step_name=POLL_GET_REQUEST_STEP_NAME,
                response_step_name=POLL_GET_RESPONSE_STEP_NAME,
                **kwargs,
            )
            failure_status = None
            try:
                if failure_json_path is not None:
                    failure_status = self._extract_json_path_value(last_response, failure_json_path)
                last_status = self._extract_json_path_value(last_response, success_json_path)
            except Exception:
                last_logger.attach_success(last_response)
                raise

            if failure_json_path is not None and failure_status is not None:
                last_logger.attach_success(last_response)
                raise AssertionError(
                    f"poll_get failed: path={path!r}, "
                    f"{failure_json_path}={failure_status!r}, "
                    f"response={last_response.text}"
                )

            if last_status is not None:
                last_logger.attach_success(last_response)
                return last_response

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                last_logger.attach_success(last_response)
                raise TimeoutError(
                    f"poll_get timed out after {timeout} seconds: path={path!r}, "
                    f"last {success_json_path}={last_status!r}, "
                    f"last response={last_response.text if last_response is not None else '<empty>'}"
                )

            time.sleep(min(poll_interval, remaining))

    def post(self, path: str, **kwargs: Any) -> requests.Response:
        return self.request("POST", path, **kwargs)

    def put(self, path: str, **kwargs: Any) -> requests.Response:
        return self.request("PUT", path, **kwargs)

    def patch(self, path: str, **kwargs: Any) -> requests.Response:
        return self.request("PATCH", path, **kwargs)

    def delete(self, path: str, **kwargs: Any) -> requests.Response:
        return self.request("DELETE", path, **kwargs)

    def close(self) -> None:
        self.session.close()

    def _build_url(self, path: str) -> str:
        if path.startswith(("http://", "https://")):
            return path
        return urljoin(f"{self.config.base_url}/", path.lstrip("/"))

    def _build_default_headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "api-v1_chat_completions-framework",
            "Authorization": f"Bearer {self.config.api_key}",
        }
        return headers

    def _merge_headers(self, headers: dict[str, str]) -> dict[str, str]:
        merged = dict(self.session.headers)
        merged.update(headers)
        return merged

    def _request_without_attach(
        self,
        method: str,
        path: str,
        *,
        step_name: str = API_REQUEST_STEP_NAME,
        response_step_name: str | None = None,
        **kwargs: Any,
    ) -> tuple[requests.Response, ApiCallLogger]:
        url = self._build_url(path)
        request_kwargs = dict(kwargs)
        request_kwargs.setdefault("timeout", self.config.timeout)

        headers = request_kwargs.pop("headers", None)
        if headers:
            request_kwargs["headers"] = self._merge_headers(headers)

        logger_kwargs: dict[str, Any] = {"step_name": step_name}
        if response_step_name is not None:
            logger_kwargs["response_step_name"] = response_step_name

        logger = ApiCallLogger(method, url, request_kwargs, **logger_kwargs)
        try:
            response = self.session.request(method=method, url=url, **request_kwargs)
        except Exception as error:
            logger.attach_failure(error)
            raise

        return response, logger

    @staticmethod
    def _extract_json_path_value(response: requests.Response, json_path: str) -> Any:
        if not json_path.startswith("$"):
            raise ValueError(f"json_path must start with '$', current value: {json_path!r}")

        try:
            body = response.json()
        except ValueError as exc:
            raise AssertionError(f"response body is not valid JSON: {response.text}") from exc

        matches = [match.value for match in parse(json_path).find(body)]
        if not matches:
            return None
        return matches[0] if len(matches) == 1 else matches
