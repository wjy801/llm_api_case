from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from threading import Event
from types import SimpleNamespace

import pytest
import requests

from common import BaseRequest
from common.polling import PollingPolicy, PollingTimeoutError
from common.retry_executor import RetryExecutor
from module.protocol_testing.request import (
    ANTHROPIC_API_KEY_HEADER,
    ANTHROPIC_VERSION_HEADER,
    ProtocolRequest,
)
from run_orchestration import pytest_execution, runner


POLLING_BUDGET_XFAIL = pytest.mark.xfail(
    strict=True,
    reason="P1: polling HTTP timeout must not exceed the remaining total budget",
)
HEADER_ISOLATION_XFAIL = pytest.mark.xfail(
    strict=True,
    reason="P1: per-request headers must not mutate a shared session concurrently",
)


def _assert_collects_one_case(test_path, pytest_args, capsys) -> None:
    result = runner.run(
        str(test_path),
        extra_pytest_args=["--collect-only", "-q", *pytest_args],
    )

    assert result == 0
    assert "Collected test cases: 1" in capsys.readouterr().out


def test_keyword_selection_is_applied_during_authoritative_collection(
    tmp_path, capsys
) -> None:
    (tmp_path / "test_keyword_selection.py").write_text(
        "def test_selected(): pass\n"
        "def test_other(): pass\n",
        encoding="utf-8",
    )

    _assert_collects_one_case(tmp_path, ["-k", "selected"], capsys)


def test_marker_selection_is_applied_during_authoritative_collection(
    tmp_path, capsys
) -> None:
    (tmp_path / "test_marker_selection.py").write_text(
        "import pytest\n"
        "@pytest.mark.serial\n"
        "def test_serial(): pass\n"
        "def test_parallel(): pass\n",
        encoding="utf-8",
    )

    _assert_collects_one_case(tmp_path, ["-m", "serial"], capsys)


def test_ignore_selection_is_applied_during_authoritative_collection(
    tmp_path, capsys
) -> None:
    (tmp_path / "test_kept.py").write_text("def test_kept(): pass\n", encoding="utf-8")
    ignored_path = tmp_path / "test_ignored.py"
    ignored_path.write_text("def test_ignored(): pass\n", encoding="utf-8")

    _assert_collects_one_case(tmp_path, [f"--ignore={ignored_path}"], capsys)


@pytest.mark.parametrize(
    ("exit_codes", "expected"),
    [
        ([0], 0),
        ([0, 0], 0),
        ([1, 0], 1),
        ([0, 1], 1),
    ],
)
def test_pool_exit_code_merge_keeps_current_success_and_failure_contract(
    exit_codes, expected
) -> None:
    assert pytest_execution.merge_exit_codes(exit_codes) == expected


@pytest.mark.parametrize(
    ("exit_codes", "expected"),
    [
        ([1, 2], 2),
        ([2], 2),
        ([3], 3),
        ([4], 4),
        ([5], 5),
        ([5, 5], 5),
    ],
)
def test_pool_exit_code_merge_preserves_terminating_pytest_facts(
    exit_codes, expected
) -> None:
    assert pytest_execution.merge_exit_codes(exit_codes) == expected


class _BudgetConfig:
    base_url = "https://example.com"
    api_key = "test-key"
    timeout = 30


class _FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _pending_response() -> requests.Response:
    response = requests.Response()
    response.status_code = 200
    response.url = "https://example.com/v1/tasks/task-1"
    response._content = json.dumps({"status": "pending"}).encode("utf-8")
    response.headers["Content-Type"] = "application/json"
    return response


def test_polling_request_timeout_is_bounded_by_remaining_budget(monkeypatch) -> None:
    clock = _FakeClock()
    client = BaseRequest(
        config=_BudgetConfig(),
        retry_executor=RetryExecutor(
            sleeper=clock.advance,
            monotonic=clock.monotonic,
        ),
    )
    request_timeouts: list[float] = []

    def request(method, url, **kwargs):
        request_timeouts.append(float(kwargs["timeout"]))
        clock.advance(0.25)
        return _pending_response()

    client.session.request = request
    with pytest.raises(PollingTimeoutError):
        client.poll_get(
            "/v1/tasks/task-1",
            poll_interval=1,
            poll_timeout=1,
            polling_policy=PollingPolicy(pending=frozenset({"pending"})),
        )

    assert request_timeouts
    assert all(0 < timeout <= 1 for timeout in request_timeouts)


class _ConcurrentProtocolRequest(ProtocolRequest):
    def __init__(self) -> None:
        self.config = SimpleNamespace(api_key="test-key")
        self.session = SimpleNamespace(
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer test-key",
            }
        )
        self.message_started = Event()
        self.chat_finished = Event()
        self.headers_seen_by_chat: dict[str, str] | None = None

    def update_headers(self, headers: dict[str, str]) -> None:
        self.session.headers.update(headers)

    def post(self, path: str, **_kwargs):
        if path == self.messages_path:
            self.message_started.set()
            assert self.chat_finished.wait(timeout=2)
        elif path == self.chat_completions_path:
            self.headers_seen_by_chat = dict(self.session.headers)
            self.chat_finished.set()
        return object()


def test_anthropic_headers_do_not_leak_into_concurrent_openai_request() -> None:
    request = _ConcurrentProtocolRequest()

    with ThreadPoolExecutor(max_workers=2) as executor:
        message_future = executor.submit(
            request.create_message,
            {"model": "model-a", "messages": []},
        )
        assert request.message_started.wait(timeout=2)
        chat_future = executor.submit(
            request.create_chat_completion,
            {"model": "model-a", "messages": []},
        )
        chat_future.result(timeout=2)
        message_future.result(timeout=2)

    assert request.headers_seen_by_chat == {
        "Content-Type": "application/json",
        "Authorization": "Bearer test-key",
    }
    assert ANTHROPIC_API_KEY_HEADER not in request.headers_seen_by_chat
    assert ANTHROPIC_VERSION_HEADER not in request.headers_seen_by_chat
