from __future__ import annotations

from collections.abc import Callable, Iterable
from contextvars import ContextVar, Token
from functools import wraps
import inspect
from pathlib import Path
import re
from typing import Any, TypeVar
from urllib.parse import unquote, urlparse

import allure
from allure_commons.types import AttachmentType
import requests


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
            success_json_path = kwargs.get("success_json_path", "$.result.urls")
            response = func(instance, path, *args, **kwargs)
            link_value = instance._extract_json_path_value(response, success_json_path)
            for url in self._extract_urls(link_value):
                file_path = self._download_url(url, DOWNLOAD_DIR)
                self._record_model_result_file(file_path)
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

    def _download_url(self, url: str, download_dir: Path) -> Path:
        download_dir.mkdir(parents=True, exist_ok=True)
        file_path = self._unique_file_path(download_dir / self._filename_from_url(url))

        with requests.get(url, stream=True, timeout=600) as response:
            response.raise_for_status()
            with file_path.open("wb") as file:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        file.write(chunk)

        return file_path

    def _filename_from_url(self, url: str) -> str:
        parsed = urlparse(url)
        name = Path(unquote(parsed.path)).name
        if name:
            return self._sanitize_filename(name)
        return "download"

    @staticmethod
    def _sanitize_filename(filename: str) -> str:
        sanitized = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", filename).strip(". ")
        return sanitized or "download"

    @staticmethod
    def _unique_file_path(file_path: Path) -> Path:
        if not file_path.exists():
            return file_path

        stem = file_path.stem
        suffix = file_path.suffix
        parent = file_path.parent
        index = 1
        while True:
            candidate = parent / f"{stem}_{index}{suffix}"
            if not candidate.exists():
                return candidate
            index += 1

    @staticmethod
    def _attachment_type_for_file(file_path: Path) -> AttachmentType | str:
        suffix = file_path.suffix.lower()
        if suffix in {".jpg", ".jpeg"}:
            return AttachmentType.JPG
        if suffix == ".png":
            return AttachmentType.PNG
        if suffix == ".gif":
            return AttachmentType.GIF
        if suffix == ".svg":
            return AttachmentType.SVG
        if suffix in {".txt", ".log", ".json", ".csv", ".xml", ".html"}:
            return AttachmentType.TEXT
        if suffix == ".pdf":
            return AttachmentType.PDF
        if suffix == ".mp4":
            return AttachmentType.MP4
        if suffix == ".webm":
            return AttachmentType.WEBM
        if suffix == ".ogg":
            return AttachmentType.OGG
        if suffix == ".mov":
            return "video/quicktime"
        if suffix == ".avi":
            return "video/x-msvideo"
        if suffix == ".mkv":
            return "video/x-matroska"
        if suffix in {".m4v", ".3gp"}:
            return "video/mp4"
        return "application/octet-stream"


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
