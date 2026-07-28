from __future__ import annotations

import pytest
import requests

from util.curl_builder import build_curl


class TestBuildCurl:
    def test_builds_multiline_post_json_curl_with_redacted_headers(self):
        prepared_request = requests.Request(
            "POST",
            "https://example.com/v1/media/generations",
            headers={
                "Authorization": "Bearer secret-token",
                "Content-Type": "application/json",
            },
            json={"model": "wan2.7-t2v", "input": {"prompt": "hello"}},
        ).prepare()

        curl = build_curl(prepared_request)

        assert curl.startswith("curl -X POST 'https://example.com/v1/media/generations'")
        assert "\\\n  -H 'Authorization: <redacted>'" in curl
        assert "\\\n  -H 'Content-Type: application/json'" in curl
        assert """--data-raw '{"model": "wan2.7-t2v", "input": {"prompt": "hello"}}'""" in curl

    def test_builds_single_line_get_curl_without_body(self):
        prepared_request = requests.Request(
            "GET",
            "https://example.com/v1/media/tasks/task-1?expand=result",
            headers={"Accept": "application/json"},
        ).prepare()

        curl = build_curl(prepared_request, multiline=False)

        assert curl == (
            "curl -X GET 'https://example.com/v1/media/tasks/task-1?expand=result' "
            "-H 'Accept: application/json'"
        )

    def test_can_disable_header_redaction(self):
        prepared_request = requests.Request(
            "GET",
            "https://example.com/v1/media/tasks/task-1",
            headers={"Authorization": "Bearer secret-token"},
        ).prepare()

        curl = build_curl(prepared_request, redact_headers=set(), multiline=False)

        assert "-H 'Authorization: Bearer secret-token'" in curl

    def test_escapes_single_quotes(self):
        prepared_request = requests.Request(
            "POST",
            "https://example.com/messages",
            data="owner's guide",
        ).prepare()

        curl = build_curl(prepared_request, multiline=False)

        assert "--data-raw 'owner'\"'\"'s guide'" in curl

    def test_formats_json_body_as_readable_utf8_text(self):
        prepared_request = requests.Request(
            "POST",
            "https://example.com/v1/images",
            headers={"Content-Type": "application/json"},
            data='{"prompt": "\\u672a\\u6765\\u57ce\\u5e02", "watermark": false}',
        ).prepare()

        curl = build_curl(prepared_request, multiline=False)

        assert """--data-raw '{"prompt": "未来城市", "watermark": false}'""" in curl
        assert "\\u672a" not in curl

    def test_redacts_sensitive_query_and_json_body_fields(self):
        prepared_request = requests.Request(
            "POST",
            "https://example.com/v1/chat/completions?api_key=query-secret&model=test",
            headers={"Content-Type": "application/json"},
            json={
                "model": "test",
                "token": "body-secret",
                "nested": {"password": "nested-secret"},
            },
        ).prepare()

        curl = build_curl(prepared_request, multiline=False)

        assert "query-secret" not in curl
        assert "body-secret" not in curl
        assert "nested-secret" not in curl
        assert "<redacted>" in curl

    def test_redacts_sensitive_form_body_fields(self):
        prepared_request = requests.Request(
            "POST",
            "https://example.com/oauth/token",
            data={
                "token": "form-secret",
                "scope": "read",
            },
        ).prepare()

        curl = build_curl(prepared_request, multiline=False)

        assert "form-secret" not in curl
        assert "scope=read" in curl
        assert "%3Credacted%3E" in curl

    def test_requires_prepared_request(self):
        with pytest.raises(TypeError):
            build_curl(object())  # type: ignore[arg-type]
