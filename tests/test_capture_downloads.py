from __future__ import annotations

from pathlib import Path

import pytest
import requests

from common.base_decorators import BaseDecorators
from common.capture import CapturePolicy
from util import media_resources
from util.downloads import DownloadLimitExceeded, download_url


class _Response:
    headers: dict[str, str] = {}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def raise_for_status(self) -> None:
        return None

    def iter_content(self, chunk_size: int):
        yield b"1234"
        yield b"5678"


def test_disabled_input_capture_starts_no_download_thread(monkeypatch) -> None:
    monkeypatch.setattr(
        media_resources.threading,
        "Thread",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("thread must not start")
        ),
    )

    tasks = media_resources.start_media_downloads(
        {"input": {"media": {"type": "image", "url": "https://x/a.png"}}},
        policy=CapturePolicy.disabled(),
    )

    assert tasks == []


def test_output_download_failure_does_not_replace_polling_response(monkeypatch) -> None:
    decorators = BaseDecorators()
    response = requests.Response()
    response.status_code = 200
    response._content = b'{"result": {"urls": ["https://x/a.png"]}}'
    response.headers["Content-Type"] = "application/json"

    class Client:
        capture_policy = CapturePolicy.output_only()

    def poll(_client, _path, **_kwargs):
        return response

    monkeypatch.setattr(
        decorators,
        "_download_url",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            requests.Timeout("capture timeout")
        ),
    )
    monkeypatch.setattr("common.base_decorators.allure.attach", lambda *_args, **_kwargs: None)
    wrapped = decorators.download_links_from_poll_get(poll)

    result = wrapped(
        Client(),
        "/v1/tasks/1",
        polling_policy=type(
            "Policy",
            (),
            {"result_json_path": "$.result.urls"},
        )(),
    )

    assert result is response


def test_download_limit_removes_partial_file(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("util.downloads.requests.get", lambda *_args, **_kwargs: _Response())

    with pytest.raises(DownloadLimitExceeded):
        download_url(
            "https://x/result.bin",
            tmp_path,
            max_bytes=5,
        )

    assert list(tmp_path.iterdir()) == []


def test_input_and_output_compatibility_helpers_share_naming_rules() -> None:
    decorators = BaseDecorators()

    assert decorators._sanitize_filename('a:b?.png') == "a_b_.png"
    assert media_resources._sanitize_filename('a:b?.png') == "a_b_.png"
    assert decorators._filename_from_url("https://x/a%20b.png") == "a b.png"
    assert media_resources._filename_from_url("https://x/a%20b.png") == "a b.png"
