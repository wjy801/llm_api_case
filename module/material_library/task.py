from __future__ import annotations

import time
import uuid
from typing import Any

import requests

from common import BaseTask, allure_step
from module.material_library.request import MaterialLibraryRequest


PROJECT_NAME = "default"
VOLC_AIGC_GROUP_TYPE = "AIGC"
VOLC_IMAGE_ASSET_TYPE = "Image"
VOLC_GROUP_ID_PREFIX = "group-volc-cn-"
VOLC_ASSET_ID_PREFIX = "asset-volc-cn-"
VOLC_VISUAL_VALIDATE_SESSION_ID_PREFIX = "session-volc-cn-"
VOLC_LIVENESS_GROUP_TYPE = "LivenessFace"
DEFAULT_VOLC_LIVENESS_IMAGE_URL = "https://ark-project.tos-cn-beijing.volces.com/doc_image/r2v_tea_pic2.jpg"
DEFAULT_VOLC_SAMPLE_IMAGE_URL = "https://ark-project.tos-cn-beijing.volces.com/doc_image/r2v_tea_pic1.jpg"
DEFAULT_VOLC_FAST_VIDEO_MODEL = "doubao-seedance-2-0-fast-260128"
DEFAULT_VOLC_MINI_VIDEO_MODEL = "doubao-seedance-2-0-mini-260615"
DEFAULT_VOLC_SEEDANCE_2_5_VIDEO_MODEL = "doubao-seedance-2-0-mini-260615"
VOLC_ASSET_POLL_INTERVAL_SECONDS = 3
VOLC_ASSET_POLL_TIMEOUT_SECONDS = 180
VOLC_VISUAL_VALIDATE_POLL_INTERVAL_SECONDS = 3
VOLC_VISUAL_VALIDATE_POLL_TIMEOUT_SECONDS = 1200
VOLC_VISUAL_VALIDATE_PENDING_STATUS = "pending"
VOLC_VISUAL_VALIDATE_READY_STATUSES = {"callback_received", "group_ready"}
VOLC_VISUAL_VALIDATE_FAILURE_STATUSES = {"failed"}
VOLC_VIDEO_POLL_INTERVAL_SECONDS = 5
VOLC_VIDEO_POLL_TIMEOUT_SECONDS = 1500
VOLC_MEDIA_SUCCESS_STATUSES = {"succeeded", "success", "completed", "finished"}
VOLC_MEDIA_FAILURE_STATUSES = {"failed", "failure", "rejected", "error"}


class MaterialLibraryTask(BaseTask):
    @allure_step("创建国内官key素材组")
    def create_asset_group(
        self,
        request_client: MaterialLibraryRequest,
        *,
        name: str,
        description: str,
        group_type: str = VOLC_AIGC_GROUP_TYPE,
        project_name: str = PROJECT_NAME,
    ) -> requests.Response:
        return request_client.create_volc_asset_group(
            self.build_create_asset_group_payload(
                name=name,
                description=description,
                group_type=group_type,
                project_name=project_name,
            ),
            headers={"Idempotency-Key": f"api-case-volc-group-{uuid.uuid4().hex}"},
        )

    @allure_step("创建国内官key AIGC 素材组")
    def create_aigc_asset_group(
        self,
        request_client: MaterialLibraryRequest,
        *,
        name: str | None = None,
        description: str = "api-case positive flow AIGC group",
        project_name: str = PROJECT_NAME,
    ) -> requests.Response:
        return self.create_asset_group(
            request_client,
            name=name or self.unique_group_name(),
            description=description,
            group_type=VOLC_AIGC_GROUP_TYPE,
            project_name=project_name,
        )

    @allure_step("上传国内官key图片素材到素材组: {group_id}")
    def upload_image_asset(
        self,
        request_client: MaterialLibraryRequest,
        group_id: str,
        *,
        image_url: str = DEFAULT_VOLC_SAMPLE_IMAGE_URL,
        name: str | None = None,
        asset_type: str = VOLC_IMAGE_ASSET_TYPE,
        project_name: str = PROJECT_NAME,
    ) -> requests.Response:
        return request_client.create_volc_asset(
            self.build_create_asset_payload(
                group_id=group_id,
                image_url=image_url,
                name=name or self.unique_asset_name(),
                asset_type=asset_type,
                project_name=project_name,
            ),
            headers={"Idempotency-Key": f"api-case-volc-asset-{uuid.uuid4().hex}"},
        )

    @allure_step("查询国内官key素材详情: {asset_id}")
    def get_asset(
        self,
        request_client: MaterialLibraryRequest,
        asset_id: str,
        *,
        project_name: str = PROJECT_NAME,
    ) -> requests.Response:
        return request_client.get_volc_asset(asset_id, project_name=project_name)

    @allure_step("查询国内官key素材列表")
    def list_assets(
        self,
        request_client: MaterialLibraryRequest,
        *,
        group_ids: list[str] | None = None,
        asset_ids: list[str] | None = None,
        name: str | None = None,
        group_type: str | None = None,
        statuses: list[str] | None = None,
        page_number: int = 1,
        page_size: int = 20,
        sort_by: str = "CreateTime",
        sort_order: str = "Desc",
        project_name: str = PROJECT_NAME,
    ) -> requests.Response:
        return request_client.list_volc_assets(
            self.build_list_assets_payload(
                group_ids=group_ids,
                asset_ids=asset_ids,
                name=name,
                group_type=group_type,
                statuses=statuses,
                page_number=page_number,
                page_size=page_size,
                sort_by=sort_by,
                sort_order=sort_order,
                project_name=project_name,
            )
        )

    @allure_step("更新国内官key素材: {asset_id}")
    def update_asset(
        self,
        request_client: MaterialLibraryRequest,
        asset_id: str,
        *,
        name: str,
        description: str = "",
        project_name: str = PROJECT_NAME,
    ) -> requests.Response:
        return request_client.update_volc_asset(
            asset_id,
            self.build_update_asset_payload(
                name=name,
                description=description,
                project_name=project_name,
            ),
        )

    @allure_step("查询国内官key素材组列表")
    def list_asset_groups(
        self,
        request_client: MaterialLibraryRequest,
        *,
        group_type: str = VOLC_AIGC_GROUP_TYPE,
        group_ids: list[str] | None = None,
        name: str | None = None,
        page_number: int = 1,
        page_size: int = 20,
        sort_by: str = "CreateTime",
        sort_order: str = "Desc",
        project_name: str = PROJECT_NAME,
    ) -> requests.Response:
        return request_client.list_volc_asset_groups(
            self.build_list_asset_groups_payload(
                group_type=group_type,
                group_ids=group_ids,
                name=name,
                page_number=page_number,
                page_size=page_size,
                sort_by=sort_by,
                sort_order=sort_order,
                project_name=project_name,
            )
        )

    @allure_step("查询国内官key素材组详情: {group_id}")
    def get_asset_group(
        self,
        request_client: MaterialLibraryRequest,
        group_id: str,
        *,
        project_name: str = PROJECT_NAME,
    ) -> requests.Response:
        return request_client.get_volc_asset_group(group_id, project_name=project_name)

    @allure_step("更新国内官key素材组: {group_id}")
    def update_asset_group(
        self,
        request_client: MaterialLibraryRequest,
        group_id: str,
        *,
        name: str,
        description: str,
        project_name: str = PROJECT_NAME,
    ) -> requests.Response:
        return request_client.update_volc_asset_group(
            group_id,
            self.build_update_asset_group_payload(
                name=name,
                description=description,
                project_name=project_name,
            ),
        )

    @allure_step("创建国内官key真人认证会话")
    def create_visual_validate_session(
        self,
        request_client: MaterialLibraryRequest,
        *,
        project_name: str = PROJECT_NAME,
    ) -> requests.Response:
        return request_client.create_volc_visual_validate_session(
            self.build_create_visual_validate_session_payload(project_name=project_name),
            headers={"Idempotency-Key": f"api-case-volc-visual-{uuid.uuid4().hex}"},
        )

    @allure_step("查询国内官key真人认证会话: {session_id}")
    def get_visual_validate_session(
        self,
        request_client: MaterialLibraryRequest,
        session_id: str,
    ) -> requests.Response:
        return request_client.get_volc_visual_validate_session(session_id)

    @allure_step("获取国内官key真人认证结果: {session_id}")
    def get_visual_validate_result(
        self,
        request_client: MaterialLibraryRequest,
        session_id: str,
    ) -> requests.Response:
        return request_client.get_volc_visual_validate_result(session_id)

    @allure_step("模拟国内官key真人认证回调: {session_id}")
    def trigger_visual_validate_callback(
        self,
        request_client: MaterialLibraryRequest,
        session_id: str,
        byted_token: str,
    ) -> requests.Response:
        return request_client.trigger_volc_visual_validate_callback(
            session_id,
            byted_token,
        )

    @allure_step("轮询国内官key真人认证会话完成: {session_id}")
    def poll_visual_validate_session_until_ready(
        self,
        request_client: MaterialLibraryRequest,
        session_id: str,
        *,
        poll_interval: float = VOLC_VISUAL_VALIDATE_POLL_INTERVAL_SECONDS,
        poll_timeout: float | None = VOLC_VISUAL_VALIDATE_POLL_TIMEOUT_SECONDS,
    ) -> requests.Response:
        deadline = None if poll_timeout is None else time.monotonic() + poll_timeout
        last_response: requests.Response | None = None

        while True:
            last_response = self.get_visual_validate_session(request_client, session_id)
            if last_response.status_code == 200:
                status = self.extract_visual_validate_status(last_response)
                print(f"volc visual validate session {session_id} status: {status}")
                if status in VOLC_VISUAL_VALIDATE_READY_STATUSES:
                    return last_response
                if status in VOLC_VISUAL_VALIDATE_FAILURE_STATUSES:
                    raise AssertionError(f"Volc visual validate session failed. Response body: {last_response.text}")

            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                raise TimeoutError(
                    f"Timed out waiting for Volc visual validate session {session_id} to become ready. "
                    f"Last response: {last_response.text if last_response is not None else '<none>'}"
                )
            time.sleep(poll_interval if remaining is None else min(poll_interval, remaining))

    @allure_step("轮询国内官key真人认证结果生成真人素材组: {session_id}")
    def poll_visual_validate_result_until_group_ready(
        self,
        request_client: MaterialLibraryRequest,
        session_id: str,
        *,
        poll_interval: float = VOLC_VISUAL_VALIDATE_POLL_INTERVAL_SECONDS,
        poll_timeout: float | None = VOLC_VISUAL_VALIDATE_POLL_TIMEOUT_SECONDS,
    ) -> requests.Response:
        deadline = None if poll_timeout is None else time.monotonic() + poll_timeout
        last_response: requests.Response | None = None

        while True:
            last_response = self.get_visual_validate_result(request_client, session_id)
            if last_response.status_code == 200:
                group_id = self.extract_json_path(last_response, ["Result", "GroupId"])
                print(f"volc visual validate result {session_id} group_id: {group_id or '<not-ready>'}")
                if group_id:
                    return last_response

            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                raise TimeoutError(
                    f"Timed out waiting for Volc visual validate result {session_id} to return GroupId. "
                    f"Last response: {last_response.text if last_response is not None else '<none>'}"
                )
            time.sleep(poll_interval if remaining is None else min(poll_interval, remaining))

    @allure_step("轮询国内官key素材到 Active: {asset_id}")
    def poll_asset_until_active(
        self,
        request_client: MaterialLibraryRequest,
        asset_id: str,
        *,
        project_name: str = PROJECT_NAME,
        poll_interval: float = VOLC_ASSET_POLL_INTERVAL_SECONDS,
        poll_timeout: float | None = VOLC_ASSET_POLL_TIMEOUT_SECONDS,
    ) -> requests.Response:
        deadline = None if poll_timeout is None else time.monotonic() + poll_timeout
        last_response: requests.Response | None = None

        while True:
            last_response = self.get_asset(request_client, asset_id, project_name=project_name)
            if last_response.status_code == 200:
                status = str(self.extract_json_path(last_response, ["Result", "Status"]) or "")
                print(f"volc asset {asset_id} status: {status}")
                if status == "Active":
                    return last_response
                if status == "Failed":
                    raise AssertionError(f"Volc asset processing failed. Response body: {last_response.text}")

            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                raise TimeoutError(
                    f"Timed out waiting for Volc asset {asset_id} to become Active. "
                    f"Last response: {last_response.text if last_response is not None else '<none>'}"
                )
            time.sleep(poll_interval if remaining is None else min(poll_interval, remaining))

    @allure_step("限时观察国内官key素材状态: {asset_id}")
    def poll_asset_until_active_or_timeout(
        self,
        request_client: MaterialLibraryRequest,
        asset_id: str,
        *,
        project_name: str = PROJECT_NAME,
        poll_interval: float = VOLC_ASSET_POLL_INTERVAL_SECONDS,
        poll_timeout: float = VOLC_ASSET_POLL_TIMEOUT_SECONDS,
    ) -> requests.Response:
        deadline = time.monotonic() + poll_timeout
        last_response: requests.Response | None = None
        while True:
            last_response = self.get_asset(request_client, asset_id, project_name=project_name)
            if last_response.status_code == 200:
                status = str(self.extract_json_path(last_response, ["Result", "Status"]) or "")
                if status in {"Active", "Failed"}:
                    return last_response
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                assert last_response is not None
                return last_response
            time.sleep(min(poll_interval, remaining))

    @allure_step("提交 asset:// 国内官key视频生成任务: {asset_id}")
    def create_asset_video_generation(
        self,
        request_client: MaterialLibraryRequest,
        asset_id: str,
        *,
        model: str = DEFAULT_VOLC_FAST_VIDEO_MODEL,
        prompt: str = "图片1中的人物在海边自然行走，保持主体一致，电影感镜头。",
        duration: int = 5,
        resolution: str = "720p",
        ratio: str = "16:9",
        reference_role: str = "reference_image",
    ) -> requests.Response:
        return request_client.create_media_generation(
            self.build_asset_video_generation_payload(
                asset_id=asset_id,
                model=model,
                prompt=prompt,
                duration=duration,
                resolution=resolution,
                ratio=ratio,
                reference_role=reference_role,
            ),
        )

    @allure_step("查询国内官key素材视频任务: {task_id}")
    def get_media_generation_task(
        self,
        request_client: MaterialLibraryRequest,
        task_id: str,
    ) -> requests.Response:
        return request_client.get_media_generation_task(task_id)

    @allure_step("轮询国内官key素材视频任务完成: {task_id}")
    def poll_media_generation_until_finished(
        self,
        request_client: MaterialLibraryRequest,
        task_id: str,
        *,
        poll_interval: float = VOLC_VIDEO_POLL_INTERVAL_SECONDS,
        poll_timeout: float | None = VOLC_VIDEO_POLL_TIMEOUT_SECONDS,
    ) -> requests.Response:
        deadline = None if poll_timeout is None else time.monotonic() + poll_timeout
        last_response: requests.Response | None = None

        while True:
            last_response = self.get_media_generation_task(request_client, task_id)
            if last_response.status_code in (200, 202):
                status = self.extract_media_task_status(last_response)
                print(f"volc media task {task_id} status: {status}")
                if status in VOLC_MEDIA_SUCCESS_STATUSES:
                    return last_response
                if status in VOLC_MEDIA_FAILURE_STATUSES:
                    raise AssertionError(f"Volc media task failed. Response body: {last_response.text}")

            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                raise TimeoutError(
                    f"Timed out waiting for Volc media task {task_id} to finish. "
                    f"Last response: {last_response.text if last_response is not None else '<none>'}"
                )
            time.sleep(poll_interval if remaining is None else min(poll_interval, remaining))

    @allure_step("删除国内官key素材: {asset_id}")
    def delete_asset_if_exists(
        self,
        request_client: MaterialLibraryRequest,
        asset_id: str,
        *,
        project_name: str = PROJECT_NAME,
    ) -> requests.Response:
        return request_client.delete_volc_asset(asset_id, {"ProjectName": project_name})

    @allure_step("删除国内官key素材组: {group_id}")
    def delete_asset_group_if_exists(
        self,
        request_client: MaterialLibraryRequest,
        group_id: str,
        *,
        project_name: str = PROJECT_NAME,
    ) -> requests.Response:
        return request_client.delete_volc_asset_group(group_id, {"ProjectName": project_name})

    @staticmethod
    def build_create_asset_group_payload(
        *,
        name: str,
        description: str,
        group_type: str = VOLC_AIGC_GROUP_TYPE,
        project_name: str = PROJECT_NAME,
    ) -> dict[str, Any]:
        return {
            "Name": name,
            "Description": description,
            "GroupType": group_type,
            "ProjectName": project_name,
        }

    @staticmethod
    def build_create_asset_payload(
        *,
        group_id: str,
        image_url: str,
        name: str,
        asset_type: str = VOLC_IMAGE_ASSET_TYPE,
        project_name: str = PROJECT_NAME,
    ) -> dict[str, Any]:
        return {
            "GroupId": group_id,
            "URL": image_url,
            "Name": name,
            "AssetType": asset_type,
            "ProjectName": project_name,
        }

    @staticmethod
    def build_list_asset_groups_payload(
        *,
        group_type: str = VOLC_AIGC_GROUP_TYPE,
        group_ids: list[str] | None = None,
        name: str | None = None,
        page_number: int = 1,
        page_size: int = 20,
        sort_by: str = "CreateTime",
        sort_order: str = "Desc",
        project_name: str = PROJECT_NAME,
    ) -> dict[str, Any]:
        filter_value: dict[str, Any] = {"GroupType": group_type}
        if group_ids is not None:
            filter_value["GroupIds"] = group_ids
        if name is not None:
            filter_value["Name"] = name

        return {
            "Filter": filter_value,
            "PageNumber": page_number,
            "PageSize": page_size,
            "SortBy": sort_by,
            "SortOrder": sort_order,
            "ProjectName": project_name,
        }

    @staticmethod
    def build_list_assets_payload(
        *,
        group_ids: list[str] | None = None,
        asset_ids: list[str] | None = None,
        name: str | None = None,
        group_type: str | None = None,
        statuses: list[str] | None = None,
        page_number: int = 1,
        page_size: int = 20,
        sort_by: str = "CreateTime",
        sort_order: str = "Desc",
        project_name: str = PROJECT_NAME,
    ) -> dict[str, Any]:
        filter_value: dict[str, Any] = {}
        if group_ids is not None:
            filter_value["GroupIds"] = group_ids
        if asset_ids is not None:
            filter_value["AssetIds"] = asset_ids
        if name is not None:
            filter_value["Name"] = name
        if group_type is not None:
            filter_value["GroupType"] = group_type
        if statuses is not None:
            filter_value["Statuses"] = statuses
        return {
            "Filter": filter_value,
            "PageNumber": page_number,
            "PageSize": page_size,
            "SortBy": sort_by,
            "SortOrder": sort_order,
            "ProjectName": project_name,
        }

    @staticmethod
    def build_update_asset_payload(
        *,
        name: str,
        description: str = "",
        project_name: str = PROJECT_NAME,
    ) -> dict[str, Any]:
        return {
            "Name": name,
            "Description": description,
            "ProjectName": project_name,
        }

    @staticmethod
    def build_update_asset_group_payload(
        *,
        name: str,
        description: str,
        project_name: str = PROJECT_NAME,
    ) -> dict[str, Any]:
        return {
            "Name": name,
            "Description": description,
            "ProjectName": project_name,
        }

    @staticmethod
    def build_create_visual_validate_session_payload(
        *,
        project_name: str = PROJECT_NAME,
    ) -> dict[str, Any]:
        return {"ProjectName": project_name}

    @staticmethod
    def build_asset_video_generation_payload(
        *,
        asset_id: str,
        model: str = DEFAULT_VOLC_FAST_VIDEO_MODEL,
        prompt: str = "图片1中的人物在海边自然行走，保持主体一致，电影感镜头。",
        duration: int = 5,
        resolution: str = "720p",
        ratio: str = "16:9",
        reference_role: str = "reference_image",
    ) -> dict[str, Any]:
        return {
            "model": model,
            "content": [
                {
                    "type": "text",
                    "text": prompt,
                },
                {
                    "type": "image_url",
                    "role": reference_role,
                    "image_url": {"url": f"asset://{asset_id}"},
                },
            ],
            "duration": duration,
            "resolution": resolution,
            "ratio": ratio,
            "generate_audio": True,
            "watermark": False,
        }

    @staticmethod
    def extract_group_id(response: requests.Response) -> str:
        return MaterialLibraryTask.extract_required_json_path(response, ["Result", "Id"], "Result.Id")

    @staticmethod
    def extract_asset_id(response: requests.Response) -> str:
        return MaterialLibraryTask.extract_required_json_path(response, ["Result", "Id"], "Result.Id")

    @staticmethod
    def extract_upstream_asset_id(response: requests.Response) -> str:
        value = MaterialLibraryTask.extract_json_path(response, ["Result", "UpstreamId"])
        return str(value or MaterialLibraryTask.extract_asset_id(response))

    @staticmethod
    def extract_visual_validate_session_id(response: requests.Response) -> str:
        return MaterialLibraryTask.extract_required_json_path(response, ["Result", "SessionId"], "Result.SessionId")

    @staticmethod
    def extract_visual_validate_h5_link(response: requests.Response) -> str:
        return MaterialLibraryTask.extract_required_json_path(response, ["Result", "H5Link"], "Result.H5Link")

    @staticmethod
    def extract_visual_validate_group_id(response: requests.Response) -> str:
        return MaterialLibraryTask.extract_required_json_path(response, ["Result", "GroupId"], "Result.GroupId")

    @staticmethod
    def extract_visual_validate_status(response: requests.Response) -> str:
        status = MaterialLibraryTask.extract_json_path(response, ["Result", "Status"])
        return str(status or "")

    @staticmethod
    def extract_media_task_id(response: requests.Response) -> str:
        body = MaterialLibraryTask.json_body(response)
        for path in (["task_id"], ["id"], ["data", "id"]):
            value = MaterialLibraryTask.get_nested_value(body, path)
            if value:
                return str(value)
        raise AssertionError(f"Media generation response missing task id. Response body: {response.text}")

    @staticmethod
    def extract_media_task_status(response: requests.Response) -> str:
        body = MaterialLibraryTask.json_body(response)
        for path in (["status"], ["task_status"], ["state"], ["data", "status"]):
            value = MaterialLibraryTask.get_nested_value(body, path)
            if value:
                return str(value).lower()
        return ""

    @staticmethod
    def extract_media_video_url(response: requests.Response) -> str:
        body = MaterialLibraryTask.json_body(response)
        candidates = (
            ["result", "primary_url"],
            ["result", "video_url"],
            ["result", "url"],
            ["data", "result", "primary_url"],
            ["data", "result", "video_url"],
            ["data", "result", "url"],
        )
        for path in candidates:
            value = MaterialLibraryTask.get_nested_value(body, path)
            if isinstance(value, str) and value.startswith("http"):
                return value
        for path in (["result", "urls"], ["data", "result", "urls"]):
            values = MaterialLibraryTask.get_nested_value(body, path)
            if isinstance(values, list):
                for value in values:
                    if isinstance(value, str) and value.startswith("http"):
                        return value
        results = body.get("results") or MaterialLibraryTask.get_nested_value(body, ["data", "results"])
        if isinstance(results, list) and results:
            first = results[0]
            if isinstance(first, dict) and first.get("url"):
                return str(first["url"])
            if isinstance(first, str) and first:
                return first
        direct_url = body.get("video_url") or body.get("url")
        if not direct_url:
            direct_url = MaterialLibraryTask.get_nested_value(body, ["data", "video_url"])
        if direct_url:
            return str(direct_url)
        raise AssertionError(
            f"Media task response missing video URL. Response body: {response.text}"
        )

    @staticmethod
    def extract_json_path(response: requests.Response, path: list[str]) -> Any:
        return MaterialLibraryTask.get_nested_value(MaterialLibraryTask.json_body(response), path)

    @staticmethod
    def extract_required_json_path(response: requests.Response, path: list[str], label: str) -> str:
        value = MaterialLibraryTask.extract_json_path(response, path)
        assert value, f"Response missing {label}. Response body: {response.text}"
        return str(value)

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

    @staticmethod
    def unique_group_name() -> str:
        return f"api-case-volc-group-{uuid.uuid4().hex[:8]}"

    @staticmethod
    def unique_asset_name() -> str:
        return f"api-case-volc-asset-{uuid.uuid4().hex[:8]}"
