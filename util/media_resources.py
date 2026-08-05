from __future__ import annotations

from collections.abc import Iterable
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from pathlib import Path
import threading
from typing import Any

import allure
from allure_commons.types import AttachmentType

from common.capture import CapturePolicy, DEFAULT_CAPTURE_POLICY
from util.downloads import (
    DownloadCancelled,
    attachment_type_for_file,
    download_url,
    filename_from_url,
    sanitize_filename,
    unique_file_path,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MEDIA_DOWNLOAD_DIR = PROJECT_ROOT / "data" / "pre_data"
MEDIA_DOWNLOAD_TIMEOUT = (3.05, 5)
MEDIA_DOWNLOAD_PARENT_STEP_NAME = "前置资源"
MEDIA_DOWNLOAD_STEP_FALLBACK = "资源下载未完成"


class MediaDownloadCancelled(Exception):
    pass


@dataclass
class MediaDownloadTask:
    media_type: str
    url: str
    cancel_event: threading.Event = field(default_factory=threading.Event)
    done_event: threading.Event = field(default_factory=threading.Event)
    file_path: Path | None = None
    error: str | None = None
    max_bytes: int | None = None

    @property
    def is_done(self) -> bool:
        return self.done_event.is_set()


_MEDIA_DOWNLOAD_TASKS: ContextVar[list[MediaDownloadTask] | None] = ContextVar(
    "media_download_tasks",
    default=None,
)


def start_media_downloads(
    payload: Any,
    *,
    policy: CapturePolicy = DEFAULT_CAPTURE_POLICY,
) -> list[MediaDownloadTask]:
    if not policy.capture_input_media:
        return []
    tasks = []
    for media_type, url in _extract_media_entries(payload):
        task = MediaDownloadTask(
            media_type=media_type,
            url=url,
            max_bytes=policy.max_input_bytes,
        )
        thread = threading.Thread(
            target=_run_download,
            args=(task,),
            name=f"media-download-{media_type}",
            daemon=True,
        )
        thread.start()
        tasks.append(task)
    _record_media_download_tasks(tasks)
    return tasks


def start_media_download_collection() -> Token[list[MediaDownloadTask] | None]:
    return _MEDIA_DOWNLOAD_TASKS.set([])


def stop_media_download_collection(token: Token[list[MediaDownloadTask] | None]) -> list[MediaDownloadTask]:
    tasks = list(_MEDIA_DOWNLOAD_TASKS.get() or [])
    _MEDIA_DOWNLOAD_TASKS.reset(token)
    return tasks


def attach_media_download_steps(tasks: list[MediaDownloadTask]) -> None:
    if not tasks:
        return

    with allure.step(MEDIA_DOWNLOAD_PARENT_STEP_NAME):
        for task in tasks:
            if task.is_done and task.file_path is not None and task.file_path.exists():
                allure.attach.file(
                    str(task.file_path),
                    name=task.media_type,
                    attachment_type=_attachment_type_for_file(task.file_path),
                )
                continue

            if not task.is_done:
                task.cancel_event.set()
                status = MEDIA_DOWNLOAD_STEP_FALLBACK
            else:
                status = "资源下载失败"

            allure.attach(
                _fallback_text(task, status),
                name=task.media_type,
                attachment_type=AttachmentType.TEXT,
            )


def _record_media_download_tasks(tasks: list[MediaDownloadTask]) -> None:
    if not tasks:
        return

    current_tasks = _MEDIA_DOWNLOAD_TASKS.get()
    if current_tasks is None:
        return

    current_tasks.extend(tasks)


def _run_download(task: MediaDownloadTask) -> None:
    try:
        task.file_path = _download_media_url(
            task.url,
            MEDIA_DOWNLOAD_DIR,
            task.cancel_event,
            max_bytes=task.max_bytes,
        )
    except (MediaDownloadCancelled, DownloadCancelled):
        task.error = MEDIA_DOWNLOAD_STEP_FALLBACK
    except Exception as error:
        task.error = f"{type(error).__name__}: {error}"
    finally:
        task.done_event.set()


def _extract_media_entries(payload: Any) -> list[tuple[str, str]]:
    if not isinstance(payload, dict):
        return []

    input_value = payload.get("input")
    if not isinstance(input_value, dict):
        return []

    media_value = input_value.get("media")
    if isinstance(media_value, dict):
        media_items: Iterable[Any] = [media_value]
    elif isinstance(media_value, list):
        media_items = media_value
    else:
        return []

    entries = []
    for media in media_items:
        if not isinstance(media, dict):
            continue

        url = media.get("url")
        if not isinstance(url, str) or not url.strip():
            continue

        media_type = media.get("type")
        if not isinstance(media_type, str) or not media_type.strip():
            media_type = "media"

        entries.append((media_type.strip(), url.strip()))
    return entries


def _download_media_url(
    url: str,
    download_dir: Path,
    cancel_event: threading.Event,
    *,
    max_bytes: int | None = None,
) -> Path:
    try:
        return download_url(
            url,
            download_dir,
            fallback_name="media",
            timeout=MEDIA_DOWNLOAD_TIMEOUT,
            cancel_event=cancel_event,
            max_bytes=max_bytes,
        )
    except DownloadCancelled as error:
        raise MediaDownloadCancelled() from error


def _fallback_text(task: MediaDownloadTask, status: str) -> str:
    lines = [
        f"media.type: {task.media_type}",
        f"media.url: {task.url}",
        f"状态: {status}",
    ]
    if task.error:
        lines.append(f"错误: {task.error}")
    return "\n".join(lines)


def _filename_from_url(url: str) -> str:
    return filename_from_url(url, fallback_name="media")


def _sanitize_filename(filename: str) -> str:
    return sanitize_filename(filename, fallback_name="media")


def _unique_file_path(file_path: Path) -> Path:
    return unique_file_path(file_path)


def _attachment_type_for_file(file_path: Path) -> AttachmentType | str:
    return attachment_type_for_file(file_path)
