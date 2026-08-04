from __future__ import annotations

from pathlib import Path
import re
from threading import Event
from urllib.parse import unquote, urlparse
import uuid

from allure_commons.types import AttachmentType
import requests


class DownloadCancelled(Exception):
    pass


class DownloadLimitExceeded(Exception):
    pass


def download_url(
    url: str,
    download_dir: Path,
    *,
    fallback_name: str = "download",
    timeout: float | tuple[float, float] = 600,
    cancel_event: Event | None = None,
    max_bytes: int | None = None,
) -> Path:
    if cancel_event is not None and cancel_event.is_set():
        raise DownloadCancelled()

    download_dir.mkdir(parents=True, exist_ok=True)
    file_path = unique_file_path(
        download_dir / filename_from_url(url, fallback_name=fallback_name)
    )
    temporary_path = file_path.with_name(
        f".{file_path.name}.{uuid.uuid4().hex}.part"
    )
    written = 0
    try:
        with requests.get(url, stream=True, timeout=timeout) as response:
            response.raise_for_status()
            content_length = response.headers.get("Content-Length")
            if (
                max_bytes is not None
                and content_length
                and int(content_length) > max_bytes
            ):
                raise DownloadLimitExceeded(
                    f"download exceeds {max_bytes} bytes"
                )
            with temporary_path.open("wb") as output:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if cancel_event is not None and cancel_event.is_set():
                        raise DownloadCancelled()
                    if not chunk:
                        continue
                    written += len(chunk)
                    if max_bytes is not None and written > max_bytes:
                        raise DownloadLimitExceeded(
                            f"download exceeds {max_bytes} bytes"
                        )
                    output.write(chunk)
        temporary_path.replace(file_path)
        return file_path
    except Exception:
        temporary_path.unlink(missing_ok=True)
        file_path.unlink(missing_ok=True)
        raise


def filename_from_url(url: str, *, fallback_name: str = "download") -> str:
    parsed = urlparse(url)
    name = Path(unquote(parsed.path)).name
    if name:
        return sanitize_filename(name, fallback_name=fallback_name)
    return fallback_name


def sanitize_filename(filename: str, *, fallback_name: str = "download") -> str:
    sanitized = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", filename).strip(". ")
    return sanitized or fallback_name


def unique_file_path(file_path: Path) -> Path:
    if not file_path.exists():
        return file_path
    stem = file_path.stem
    suffix = file_path.suffix
    for index in range(1, 1_000_000):
        candidate = file_path.parent / f"{stem}_{index}{suffix}"
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"unable to allocate unique download path: {file_path}")


def attachment_type_for_file(file_path: Path) -> AttachmentType | str:
    suffix = file_path.suffix.lower()
    mapping: dict[str, AttachmentType | str] = {
        ".jpg": AttachmentType.JPG,
        ".jpeg": AttachmentType.JPG,
        ".png": AttachmentType.PNG,
        ".gif": AttachmentType.GIF,
        ".svg": AttachmentType.SVG,
        ".txt": AttachmentType.TEXT,
        ".log": AttachmentType.TEXT,
        ".json": AttachmentType.TEXT,
        ".csv": AttachmentType.TEXT,
        ".xml": AttachmentType.TEXT,
        ".html": AttachmentType.TEXT,
        ".pdf": AttachmentType.PDF,
        ".mp4": AttachmentType.MP4,
        ".webm": AttachmentType.WEBM,
        ".ogg": AttachmentType.OGG,
        ".mov": "video/quicktime",
        ".avi": "video/x-msvideo",
        ".mkv": "video/x-matroska",
        ".m4v": "video/mp4",
        ".3gp": "video/mp4",
    }
    return mapping.get(suffix, "application/octet-stream")
