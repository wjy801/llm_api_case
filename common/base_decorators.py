from __future__ import annotations

from collections.abc import Callable, Iterable
from contextvars import ContextVar, Token
from functools import wraps
import inspect
from pathlib import Path
import re
from typing import Any, TypeVar

import allure
from allure_commons.types import AttachmentType
import requests

from common.capture import DEFAULT_CAPTURE_POLICY
from util.downloads import (
    attachment_type_for_file,
    download_url,
    filename_from_url,
    sanitize_filename,
    unique_file_path,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DOWNLOAD_DIR = PROJECT_ROOT / "data"
URL_PATTERN = re.compile(r"https?://[^\s\"'<>]+")
MODEL_RESULT_ATTACHMENT_NAME = "模型响应结果"
_MODEL_RESULT_FILES: ContextVar[list[Path] | None] = ContextVar(
    "model_result_files",
    default=None,
)
F = TypeVar("F", bound=Callable[..., requests.Response])
AnyCallable = TypeVar("AnyCallable", bound=Callable[..., Any])


class BaseDecorators:
    def allure_step(self, title: str | None = None) -> Callable[[AnyCallable], AnyCallable]:
        def decorator(func: AnyCallable) -> AnyCallable:
            @wraps(func)
            def wrapper(*args: Any, **kwargs: Any) -> Any:
                step_title = self._build_step_title(func, title, args, kwargs)
                with allure.step(step_title):
                    return func(*args, **kwargs)

            return wrapper

        return decorator

    def download_links_from_poll_get(self, func: F) -> F:
        @wraps(func)
        def wrapper(instance: Any, path: str, *args: Any, **kwargs: Any) -> requests.Response:
            capture_policy = getattr(instance, "capture_policy", DEFAULT_CAPTURE_POLICY)
            polling_policy = kwargs.get("polling_policy")
            result_json_path = getattr(polling_policy, "result_json_path", None)
            response = func(instance, path, *args, **kwargs)
            if result_json_path is None or not capture_policy.capture_output_results:
                return response
            try:
                link_value = self._extract_json_path_value(response, result_json_path)
                for url in self._extract_urls(link_value):
                    try:
                        file_path = self._download_url(
                            url,
                            DOWNLOAD_DIR,
                            max_bytes=capture_policy.max_output_bytes,
                        )
                        self._record_model_result_file(file_path)
                    except Exception as error:
                        self._attach_download_failure(url, error)
            except Exception as error:
                self._attach_download_failure("<result-json-path>", error)
            return response

        return wrapper  # type: ignore[return-value]

    def start_model_result_collection(self) -> Token[list[Path] | None]:
        return _MODEL_RESULT_FILES.set([])

    def stop_model_result_collection(self, token: Token[list[Path] | None]) -> list[Path]:
        file_paths = list(_MODEL_RESULT_FILES.get() or [])
        _MODEL_RESULT_FILES.reset(token)
        return file_paths

    def attach_model_result_file(self, file_path: Path) -> None:
        allure.attach.file(
            str(file_path),
            name=MODEL_RESULT_ATTACHMENT_NAME,
            attachment_type=self._attachment_type_for_file(file_path),
        )

    def _record_model_result_file(self, file_path: Path) -> None:
        file_paths = _MODEL_RESULT_FILES.get()
        if file_paths is None:
            self.attach_model_result_file(file_path)
            return

        file_paths.append(file_path)

    def _build_step_title(
        self,
        func: Callable[..., Any],
        title: str | None,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> str:
        if title is None:
            return func.__name__

        try:
            signature = inspect.signature(func)
            bound_arguments = signature.bind_partial(*args, **kwargs)
            bound_arguments.apply_defaults()
            return title.format(**bound_arguments.arguments)
        except Exception:
            return title

    def _extract_urls(self, value: Any) -> list[str]:
        urls: list[str] = []
        self._collect_urls(value, urls)
        return list(dict.fromkeys(urls))

    @staticmethod
    def _extract_json_path_value(response: requests.Response, json_path: str) -> Any:
        from jsonpath_ng.ext import parse

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

    def _collect_urls(self, value: Any, urls: list[str]) -> None:
        if isinstance(value, str):
            urls.extend(URL_PATTERN.findall(value))
            return

        if isinstance(value, dict):
            for item in value.values():
                self._collect_urls(item, urls)
            return

        if isinstance(value, Iterable) and not isinstance(value, (bytes, bytearray)):
            for item in value:
                self._collect_urls(item, urls)

    def _download_url(
        self,
        url: str,
        download_dir: Path,
        *,
        max_bytes: int | None = None,
    ) -> Path:
        return download_url(
            url,
            download_dir,
            fallback_name="download",
            timeout=600,
            max_bytes=max_bytes,
        )

    def _filename_from_url(self, url: str) -> str:
        return filename_from_url(url, fallback_name="download")

    @staticmethod
    def _sanitize_filename(filename: str) -> str:
        return sanitize_filename(filename, fallback_name="download")

    @staticmethod
    def _unique_file_path(file_path: Path) -> Path:
        return unique_file_path(file_path)

    @staticmethod
    def _attachment_type_for_file(file_path: Path) -> AttachmentType | str:
        return attachment_type_for_file(file_path)

    @staticmethod
    def _attach_download_failure(url: str, error: Exception) -> None:
        try:
            allure.attach(
                f"url: {url}\nerror: {type(error).__name__}: {error}",
                name="模型结果下载失败",
                attachment_type=AttachmentType.TEXT,
            )
        except Exception:
            return


_default_decorators = BaseDecorators()


def allure_step(title: str | None = None) -> Callable[[AnyCallable], AnyCallable]:
    return _default_decorators.allure_step(title)


def download_links_from_poll_get(func: F) -> F:
    return _default_decorators.download_links_from_poll_get(func)


def start_model_result_collection() -> Token[list[Path] | None]:
    return _default_decorators.start_model_result_collection()


def stop_model_result_collection(token: Token[list[Path] | None]) -> list[Path]:
    return _default_decorators.stop_model_result_collection(token)


def attach_model_result_file(file_path: Path) -> None:
    _default_decorators.attach_model_result_file(file_path)
