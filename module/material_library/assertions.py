from __future__ import annotations

import sys
from typing import Any

import requests

from common import BaseAssertions


class MaterialLibraryAssertions(BaseAssertions):
    def assert_result_id_prefix(
        self,
        response: requests.Response,
        expected_prefix: str,
        *,
        field_label: str = "Result.Id",
    ) -> requests.Response:
        result_id = self.get_required_nested_value(response, ["Result", "Id"], field_label)
        sys.__stdout__.write(f"{field_label}: {result_id}\n")
        sys.__stdout__.flush()
        assert str(result_id).startswith(expected_prefix), (
            f"{field_label} should start with {expected_prefix!r}, actual: {result_id!r}. "
            f"Response body: {response.text}"
        )
        return response

    def assert_no_upstream_secret_leaked(self, response: requests.Response) -> requests.Response:
        forbidden_values = ["AK", "SK", "AccessKey", "SecretKey", "BytedToken", "bytedToken"]
        response_text = response.text
        leaked = [value for value in forbidden_values if value in response_text]
        assert not leaked, (
            f"Response should not expose upstream credentials or tokens, found: {leaked!r}. "
            f"Response body: {response_text}"
        )
        return response

    def assert_asset_status(self, response: requests.Response, expected_status: str) -> requests.Response:
        actual_status = self.get_required_nested_value(response, ["Result", "Status"], "Result.Status")
        assert actual_status == expected_status, (
            f"Result.Status should be {expected_status!r}, actual: {actual_status!r}. "
            f"Response body: {response.text}"
        )
        return response

    def assert_asset_detail_matches(
        self,
        response: requests.Response,
        *,
        asset_id: str,
        group_id: str,
        asset_type: str = "Image",
    ) -> requests.Response:
        body = self.json_body(response)
        result = body.get("Result")
        assert isinstance(result, dict), f"Response Result should be an object. Response body: {response.text}"
        assert str(result.get("Id")) == asset_id, (
            f"Result.Id mismatch, expected {asset_id!r}, actual {result.get('Id')!r}. "
            f"Response body: {response.text}"
        )
        assert str(result.get("GroupId")) == group_id, (
            f"Result.GroupId mismatch, expected {group_id!r}, actual {result.get('GroupId')!r}. "
            f"Response body: {response.text}"
        )
        assert result.get("AssetType") == asset_type, (
            f"Result.AssetType mismatch, expected {asset_type!r}, actual {result.get('AssetType')!r}. "
            f"Response body: {response.text}"
        )
        return response

    def assert_group_list_contains(
        self,
        response: requests.Response,
        *,
        group_id: str,
    ) -> requests.Response:
        items = self.get_result_items(response)
        group_ids = [str(item.get("Id")) for item in items if isinstance(item, dict)]
        assert group_id in group_ids, (
            f"Result.Items should contain group id {group_id!r}, actual ids: {group_ids!r}. "
            f"Response body: {response.text}"
        )
        total_count = self.get_nested_value(self.json_body(response), ["Result", "TotalCount"])
        assert isinstance(total_count, int) and total_count >= 1, (
            f"Result.TotalCount should be an integer >= 1, actual: {total_count!r}. "
            f"Response body: {response.text}"
        )
        return response

    def assert_group_detail_matches(
        self,
        response: requests.Response,
        *,
        group_id: str,
        name: str,
        group_type: str = "AIGC",
    ) -> requests.Response:
        body = self.json_body(response)
        result = body.get("Result")
        assert isinstance(result, dict), f"Response Result should be an object. Response body: {response.text}"
        assert str(result.get("Id")) == group_id, (
            f"Result.Id mismatch, expected {group_id!r}, actual {result.get('Id')!r}. "
            f"Response body: {response.text}"
        )
        assert result.get("Name") == name, (
            f"Result.Name mismatch, expected {name!r}, actual {result.get('Name')!r}. "
            f"Response body: {response.text}"
        )
        assert result.get("GroupType") == group_type, (
            f"Result.GroupType mismatch, expected {group_type!r}, actual {result.get('GroupType')!r}. "
            f"Response body: {response.text}"
        )
        return response

    def assert_delete_result_empty(self, response: requests.Response) -> requests.Response:
        result = self.get_nested_value(self.json_body(response), ["Result"])
        assert result in ({}, None), (
            f"Delete response Result should be empty, actual: {result!r}. "
            f"Response body: {response.text}"
        )
        return response

    def assert_visual_validate_session_created(self, response: requests.Response) -> requests.Response:
        result = self.get_required_result_object(response)
        session_id = result.get("SessionId")
        h5_link = result.get("H5Link")
        assert isinstance(session_id, str) and session_id.startswith("session-volc-cn-"), (
            "Result.SessionId should be a platform visual validate session id, "
            f"actual: {session_id!r}. Response body: {response.text}"
        )
        assert isinstance(h5_link, str) and h5_link.startswith("http"), (
            f"Result.H5Link should be an HTTP URL, actual: {h5_link!r}. "
            f"Response body: {response.text}"
        )
        return response

    def assert_visual_validate_session_status(
        self,
        response: requests.Response,
        expected_statuses: set[str],
    ) -> requests.Response:
        result = self.get_required_result_object(response)
        status = result.get("Status")
        assert status in expected_statuses, (
            f"Result.Status should be one of {sorted(expected_statuses)!r}, actual: {status!r}. "
            f"Response body: {response.text}"
        )
        assert result.get("SessionId"), f"Result.SessionId should exist. Response body: {response.text}"
        assert result.get("ProjectName"), f"Result.ProjectName should exist. Response body: {response.text}"
        assert result.get("CreateTime"), f"Result.CreateTime should exist. Response body: {response.text}"
        assert result.get("UpdateTime"), f"Result.UpdateTime should exist. Response body: {response.text}"
        return response

    def assert_visual_validate_result_group_id(self, response: requests.Response) -> requests.Response:
        group_id = self.get_required_nested_value(response, ["Result", "GroupId"], "Result.GroupId")
        assert str(group_id).startswith("group-volc-cn-"), (
            f"Result.GroupId should start with 'group-volc-cn-', actual: {group_id!r}. "
            f"Response body: {response.text}"
        )
        return response

    def get_result_items(self, response: requests.Response) -> list[dict[str, Any]]:
        items = self.get_nested_value(self.json_body(response), ["Result", "Items"])
        assert isinstance(items, list), f"Result.Items should be a list. Response body: {response.text}"
        return items

    def assert_media_task_has_video_url(self, response: requests.Response) -> requests.Response:
        body = self.json_body(response)
        result = body.get("result")
        if not isinstance(result, dict):
            result = self.get_nested_value(body, ["data", "result"])

        assert isinstance(result, dict), f"Media task response should contain result object. Response body: {response.text}"

        primary_url = result.get("primary_url") or result.get("video_url") or result.get("url")
        urls = result.get("urls")
        has_url_list = isinstance(urls, list) and any(str(url).startswith("http") for url in urls)
        assert (isinstance(primary_url, str) and primary_url.startswith("http")) or has_url_list, (
            "Media task result should contain result.primary_url or result.urls with an HTTP URL. "
            f"Response body: {response.text}"
        )
        return response

    def assert_media_generation_submit_succeeded(self, response: requests.Response) -> requests.Response:
        assert response.status_code in (200, 201, 202), (
            f"Media generation submit should return 200, 201, or 202, actual: {response.status_code}. "
            f"Response body: {response.text}"
        )
        return response

    def get_required_nested_value(
        self,
        response: requests.Response,
        path: list[str],
        field_label: str,
    ) -> Any:
        value = self.get_nested_value(self.json_body(response), path)
        assert value, f"Response missing {field_label}. Response body: {response.text}"
        return value

    def get_required_result_object(self, response: requests.Response) -> dict[str, Any]:
        result = self.json_body(response).get("Result")
        assert isinstance(result, dict), f"Response Result should be an object. Response body: {response.text}"
        return result

    @staticmethod
    def get_nested_value(value: Any, path: list[str]) -> Any:
        current = value
        for key in path:
            if not isinstance(current, dict):
                return None
            current = current.get(key)
        return current

    @staticmethod
    def json_body(response: requests.Response) -> dict[str, Any]:
        try:
            body = response.json()
        except ValueError as exc:
            raise AssertionError(f"Response body is not valid JSON. Response body: {response.text}") from exc
        assert isinstance(body, dict), f"Response body should be a JSON object. Response body: {response.text}"
        return body
