from __future__ import annotations

import requests

from util.api_call_logger import ApiCallLogger, REQUEST_CURL_ATTACHMENT_NAME


class TestApiCallLogger:
    def test_attach_success_adds_redacted_curl_attachment(self, monkeypatch):
        attachments: list[tuple[str, str]] = []
        response = requests.Response()
        response.status_code = 200
        response.reason = "OK"
        response._content = b'{"ok": true}'
        response.headers["Content-Type"] = "application/json"
        response.request = requests.Request(
            "POST",
            "https://example.com/v1/media/generations",
            headers={
                "Authorization": "Bearer secret-token",
                "Content-Type": "application/json",
            },
            json={"model": "wan2.7-t2v"},
        ).prepare()

        monkeypatch.setattr(
            "util.api_call_logger.allure.attach",
            lambda body, name, attachment_type: attachments.append((name, body)),
        )

        logger = ApiCallLogger("POST", "https://example.com/v1/media/generations", {})
        logger.attach_success(response)

        curl_attachment = _attachment_body(attachments, REQUEST_CURL_ATTACHMENT_NAME)
        assert "curl -X POST 'https://example.com/v1/media/generations'" in curl_attachment
        assert "-H 'Authorization: <redacted>'" in curl_attachment
        assert "secret-token" not in curl_attachment

    def test_attach_success_redacts_url_headers_body_and_response(self, monkeypatch):
        attachments: list[tuple[str, str]] = []
        response = requests.Response()
        response.status_code = 200
        response.reason = "OK"
        response._content = b'{"token": "response-secret", "ok": true}'
        response.headers["Content-Type"] = "application/json"
        response.headers["Set-Cookie"] = "session=response-cookie-secret"
        response.request = requests.Request(
            "POST",
            "https://example.com/v1/chat/completions?api_key=query-secret&model=test",
            headers={
                "Authorization": "Bearer header-secret",
                "Content-Type": "application/json",
            },
            json={
                "model": "test",
                "token": "body-secret",
                "nested": {"password": "nested-secret"},
            },
        ).prepare()

        monkeypatch.setattr(
            "util.api_call_logger.allure.attach",
            lambda body, name, attachment_type: attachments.append((name, body)),
        )

        logger = ApiCallLogger(
            "POST",
            "https://example.com/v1/chat/completions?api_key=query-secret&model=test",
            {},
        )
        logger.attach_success(response)

        attachment_text = "\n".join(body for _, body in attachments)
        assert "query-secret" not in attachment_text
        assert "header-secret" not in attachment_text
        assert "body-secret" not in attachment_text
        assert "nested-secret" not in attachment_text
        assert "response-secret" not in attachment_text
        assert "response-cookie-secret" not in attachment_text
        assert "<redacted>" in attachment_text

    def test_attach_failure_uses_request_from_request_exception(self, monkeypatch):
        attachments: list[tuple[str, str]] = []
        prepared_request = requests.Request(
            "GET",
            "https://example.com/v1/media/tasks/task-1",
            headers={"Authorization": "Bearer secret-token"},
        ).prepare()
        error = requests.RequestException("connection failed", request=prepared_request)

        monkeypatch.setattr(
            "util.api_call_logger.allure.attach",
            lambda body, name, attachment_type: attachments.append((name, body)),
        )

        logger = ApiCallLogger("GET", "https://example.com/v1/media/tasks/task-1", {})
        logger.attach_failure(error)

        curl_attachment = _attachment_body(attachments, REQUEST_CURL_ATTACHMENT_NAME)
        assert "curl -X GET 'https://example.com/v1/media/tasks/task-1'" in curl_attachment
        assert "-H 'Authorization: <redacted>'" in curl_attachment

    def test_attach_failure_redacts_sensitive_error_text(self, monkeypatch):
        attachments: list[tuple[str, str]] = []
        error = requests.RequestException(
            "connection failed api_key=exception-secret token='token-secret' Authorization: Bearer auth-secret"
        )

        monkeypatch.setattr(
            "util.api_call_logger.allure.attach",
            lambda body, name, attachment_type: attachments.append((name, body)),
        )

        logger = ApiCallLogger("GET", "https://example.com/v1/tasks/task-1", {})
        logger.attach_failure(error)

        attachment_text = "\n".join(body for _, body in attachments)
        assert "exception-secret" not in attachment_text
        assert "token-secret" not in attachment_text
        assert "auth-secret" not in attachment_text
        assert "api_key=<redacted>" in attachment_text
        assert "token='<redacted>'" in attachment_text
        assert "Authorization: <redacted>" in attachment_text


def _attachment_body(attachments: list[tuple[str, str]], name: str) -> str:
    for attachment_name, body in attachments:
        if attachment_name == name:
            return body
    raise AssertionError(f"attachment not found: {name}")
