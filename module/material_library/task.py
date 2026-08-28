from __future__ import annotations

from dataclasses import dataclass
import time
import uuid
from typing import Any

import requests

from common import BaseTask, PollingPolicy, allure_step
from module.material_library.request import MaterialLibraryRequest


PROJECT_NAME = "default"
ARK_GROUP_ID_PREFIX = "group-"
ARK_ASSET_ID_PREFIX = "asset-"
ARK_ASSET_POLL_INTERVAL_SECONDS = 5
ARK_ASSET_POLL_TIMEOUT_SECONDS = 180
ARK_MEDIA_POLL_INTERVAL_SECONDS = 5
ARK_MEDIA_POLL_TIMEOUT_SECONDS = 1500
ARK_MEDIA_SUCCESS_STATUSES = {"succeeded", "success", "completed", "finished"}
ARK_MEDIA_FAILURE_STATUSES = {"failed", "failure", "rejected", "error"}
ARK_MEDIA_POLLING_POLICY = PollingPolicy(
    status_json_path="$.status",
    pending=frozenset({"queued", "running", "pending", "processing"}),
    success=frozenset(ARK_MEDIA_SUCCESS_STATUSES),
    failure=frozenset(ARK_MEDIA_FAILURE_STATUSES | {"cancelled", "canceled"}),
    result_json_path="$.content.video_url",
    error_json_path="$.error",
)
ARK_VISUAL_VALIDATE_POLL_INTERVAL_SECONDS = 3
ARK_VISUAL_VALIDATE_POLL_TIMEOUT_SECONDS = 1800
ARK_VISUAL_VALIDATE_READY_STATUS = "group_ready"
ARK_VISUAL_VALIDATE_FAILURE_STATUSES = {"failed", "expired"}
VOLC_AIGC_GROUP_TYPE = "AIGC"
VOLC_IMAGE_ASSET_TYPE = "Image"
VOLC_GROUP_ID_PREFIX = "group-volc-cn-"
VOLC_ASSET_ID_PREFIX = "asset-volc-cn-"
VOLC_VISUAL_VALIDATE_SESSION_ID_PREFIX = "session-volc-cn-"
VOLC_LIVENESS_GROUP_TYPE = "LivenessFace"
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


@dataclass
class _PollingBoundaryLogger:
    label: str
    first_value: str | None = None
    last_value: str | None = None
    finished: bool = False

    def observe(self, value: Any) -> None:
        normalized = str(value or "<empty>")
        if self.first_value is None:
            self.first_value = normalized
            print(f"{self.label} first: {normalized}", flush=True)
        self.last_value = normalized

    def finish(self, value: Any | None = None) -> None:
        if self.finished:
            return
        if value is not None:
            self.last_value = str(value or "<empty>")
        if self.last_value is not None:
            print(f"{self.label} final: {self.last_value}", flush=True)
        self.finished = True


class MaterialLibraryTask(BaseTask):
    @allure_step("创建 Ark 虚拟人像素材组")
    def create_ark_virtual_portrait_group(
        self,
        request_client: MaterialLibraryRequest,
        *,
        name: str | None = None,
        description: str = "api-case Ark virtual portrait group",
    ) -> requests.Response:
        return request_client.create_ark_asset_group(
            self.build_create_ark_asset_group_payload(
                name=name or self.unique_ark_group_name(),
                description=description,
            )
        )

    @allure_step("上传 Ark 虚拟人像图片素材到素材组: {group_id}")
    def upload_ark_virtual_portrait_image(
        self,
        request_client: MaterialLibraryRequest,
        group_id: str,
        *,
        image_url: str,
        name: str | None = None,
    ) -> requests.Response:
        return request_client.create_ark_asset(
            self.build_create_ark_asset_payload(
                group_id=group_id,
                image_url=image_url,
                name=name or self.unique_ark_asset_name(),
            )
        )

    @allure_step("上传 Ark 真人图片素材到认证素材组: {group_id}")
    def upload_ark_real_person_image(
        self,
        request_client: MaterialLibraryRequest,
        group_id: str,
        *,
        image_url: str,
        name: str | None = None,
    ) -> requests.Response:
        return request_client.create_ark_asset(
            self.build_create_ark_asset_payload(
                group_id=group_id,
                image_url=image_url,
                name=name or self.unique_ark_asset_name(),
            )
        )

    @allure_step("查询 Ark 素材详情: {asset_id}")
    def get_ark_asset(
        self,
        request_client: MaterialLibraryRequest,
        asset_id: str,
    ) -> requests.Response:
        return request_client.get_ark_asset(asset_id)

    @allure_step("创建 Ark 真人认证 H5 会话")
    def create_ark_visual_validate_session(
        self,
        request_client: MaterialLibraryRequest,
        *,
        client_redirect_url: str,
        project_name: str = PROJECT_NAME,
    ) -> requests.Response:
        return request_client.create_ark_visual_validate_session(
            self.build_create_ark_visual_validate_session_payload(
                client_redirect_url=client_redirect_url,
                project_name=project_name,
            )
        )

    @allure_step("查询 Ark 真人认证会话: {session_id}")
    def get_ark_visual_validate_session(
        self,
        request_client: MaterialLibraryRequest,
        session_id: str,
    ) -> requests.Response:
        return request_client.get_ark_visual_validate_session(session_id)

    @allure_step("轮询 Ark 真人认证完成: {session_id}")
    def poll_ark_visual_validate_session_until_ready(
        self,
        request_client: MaterialLibraryRequest,
        session_id: str,
        *,
        poll_interval: float = ARK_VISUAL_VALIDATE_POLL_INTERVAL_SECONDS,
        poll_timeout: float = ARK_VISUAL_VALIDATE_POLL_TIMEOUT_SECONDS,
    ) -> requests.Response:
        deadline = time.monotonic() + poll_timeout
        last_response: requests.Response | None = None
        boundary = _PollingBoundaryLogger(
            f"ark visual validate session {session_id}"
        )
        while True:
            last_response = self.get_ark_visual_validate_session(
                request_client,
                session_id,
            )
            if last_response.status_code == 200:
                status = self.extract_ark_visual_validate_status(last_response)
                boundary.observe(status)
                if status == ARK_VISUAL_VALIDATE_READY_STATUS:
                    boundary.finish()
                    return last_response
                if status in ARK_VISUAL_VALIDATE_FAILURE_STATUSES:
                    boundary.finish()
                    raise AssertionError(
                        "Ark visual validation failed. "
                        f"Response body: {last_response.text}"
                    )

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                boundary.finish()
                raise TimeoutError(
                    f"Timed out waiting for Ark visual validation {session_id}. "
                    f"Last response: {last_response.text if last_response is not None else '<none>'}"
                )
            time.sleep(min(poll_interval, remaining))

    @allure_step("获取 Ark 真人认证结果: {session_id}")
    def get_ark_visual_validate_result(
        self,
        request_client: MaterialLibraryRequest,
        session_id: str,
    ) -> requests.Response:
        return request_client.get_ark_visual_validate_result(session_id)

    @allure_step("轮询 Ark 素材到 Active: {asset_id}")
    def poll_ark_asset_until_active(
        self,
        request_client: MaterialLibraryRequest,
        asset_id: str,
        *,
        poll_interval: float = ARK_ASSET_POLL_INTERVAL_SECONDS,
        poll_timeout: float = ARK_ASSET_POLL_TIMEOUT_SECONDS,
    ) -> requests.Response:
        deadline = time.monotonic() + poll_timeout
        last_response: requests.Response | None = None
        boundary = _PollingBoundaryLogger(f"ark asset {asset_id}")
        while True:
            last_response = self.get_ark_asset(request_client, asset_id)
            if last_response.status_code == 200:
                status = str(self.json_body(last_response).get("Status") or "")
                boundary.observe(status)
                if status == "Active":
                    boundary.finish()
                    return last_response
                if status == "Failed":
                    boundary.finish()
                    raise AssertionError(
                        f"Ark asset processing failed. Response body: {last_response.text}"
                    )

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                boundary.finish()
                raise TimeoutError(
                    f"Timed out waiting for Ark asset {asset_id} to become Active. "
                    f"Last response: {last_response.text if last_response is not None else '<none>'}"
                )
            time.sleep(min(poll_interval, remaining))

    @allure_step("提交 Ark 虚拟人像视频生成任务: {asset_id}")
    def create_ark_virtual_portrait_video(
        self,
        request_client: MaterialLibraryRequest,
        asset_id: str,
        *,
        model: str,
        prompt: str,
        duration: int,
        resolution: str,
        ratio: str,
        reference_role: str,
        generate_audio: bool,
    ) -> requests.Response:
        return request_client.create_ark_media_generation(
            self.build_ark_virtual_portrait_video_payload(
                asset_id=asset_id,
                model=model,
                prompt=prompt,
                duration=duration,
                resolution=resolution,
                ratio=ratio,
                reference_role=reference_role,
                generate_audio=generate_audio,
            )
        )

    @allure_step("提交 Ark 真人素材视频生成任务: {asset_id}")
    def create_ark_real_person_video(
        self,
        request_client: MaterialLibraryRequest,
        asset_id: str,
        *,
        model: str,
        prompt: str,
        duration: int,
        resolution: str,
        ratio: str,
        reference_role: str,
        generate_audio: bool,
    ) -> requests.Response:
        return request_client.create_ark_media_generation(
            self.build_ark_virtual_portrait_video_payload(
                asset_id=asset_id,
                model=model,
                prompt=prompt,
                duration=duration,
                resolution=resolution,
                ratio=ratio,
                reference_role=reference_role,
                generate_audio=generate_audio,
            )
        )

    @allure_step("查询 Ark 视频生成任务: {task_id}")
    def get_ark_media_generation_task(
        self,
        request_client: MaterialLibraryRequest,
        task_id: str,
    ) -> requests.Response:
        return request_client.get_ark_media_generation_task(task_id)

    @allure_step("轮询 Ark 视频生成任务完成: {task_id}")
    def poll_ark_media_generation_until_finished(
        self,
        request_client: MaterialLibraryRequest,
        task_id: str,
        *,
        poll_interval: float = ARK_MEDIA_POLL_INTERVAL_SECONDS,
        poll_timeout: float = ARK_MEDIA_POLL_TIMEOUT_SECONDS,
    ) -> requests.Response:
        boundary = _PollingBoundaryLogger(f"ark media task {task_id}")
        first_response = self.get_ark_media_generation_task(request_client, task_id)
        first_status = self.extract_media_task_status(first_response)
        boundary.observe(first_status or f"HTTP {first_response.status_code}")

        try:
            final_response = request_client.poll_ark_media_generation_task(
                task_id,
                poll_interval=poll_interval,
                poll_timeout=poll_timeout,
                polling_policy=ARK_MEDIA_POLLING_POLICY,
            )
        except Exception as error:
            boundary.finish(getattr(error, "last_status", None))
            raise

        boundary.observe(self.extract_media_task_status(final_response))
        boundary.finish()
        return final_response

    @allure_step("删除 Ark 素材: {asset_id}")
    def delete_ark_asset_if_exists(
        self,
        request_client: MaterialLibraryRequest,
        asset_id: str,
    ) -> requests.Response:
        return request_client.delete_ark_asset(asset_id)

    @allure_step("删除 Ark 素材组: {group_id}")
    def delete_ark_asset_group_if_exists(
        self,
        request_client: MaterialLibraryRequest,
        group_id: str,
    ) -> requests.Response:
        return request_client.delete_ark_asset_group(group_id)

    @allure_step("创建国内官key素材组")
    def create_asset_group(
        self,
        request_client: MaterialLibraryRequest,
        *,
        name: str,
        description: str,
        group_type: str = VOLC_AIGC_GROUP_TYPE,
    ) -> requests.Response:
        return request_client.create_volc_asset_group(
            self.build_create_asset_group_payload(
                name=name,
                description=description,
                group_type=group_type,
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
    ) -> requests.Response:
        return self.create_asset_group(
            request_client,
            name=name or self.unique_group_name(),
            description=description,
            group_type=VOLC_AIGC_GROUP_TYPE,
        )

    @allure_step("上传国内官key图片素材到素材组: {group_id}")
    def upload_image_asset(
        self,
        request_client: MaterialLibraryRequest,
        group_id: str,
        *,
        image_url: str,
        name: str | None = None,
        asset_type: str = VOLC_IMAGE_ASSET_TYPE,
    ) -> requests.Response:
        return request_client.create_volc_asset(
            self.build_create_asset_payload(
                group_id=group_id,
                image_url=image_url,
                name=name or self.unique_asset_name(),
                asset_type=asset_type,
            ),
            headers={"Idempotency-Key": f"api-case-volc-asset-{uuid.uuid4().hex}"},
        )

    @allure_step("查询国内官key素材详情: {asset_id}")
    def get_asset(
        self,
        request_client: MaterialLibraryRequest,
        asset_id: str,
    ) -> requests.Response:
        return request_client.get_volc_asset(asset_id)

    @allure_step("查询国内官key素材列表")
    def list_assets(
        self,
        request_client: MaterialLibraryRequest,
        *,
        group_ids: list[str] | None = None,
        name: str | None = None,
        statuses: list[str] | None = None,
        page_number: int = 1,
        page_size: int = 20,
        sort_by: str = "CreateTime",
        sort_order: str = "Desc",
    ) -> requests.Response:
        return request_client.list_volc_assets(
            self.build_list_assets_payload(
                group_ids=group_ids,
                name=name,
                statuses=statuses,
                page_number=page_number,
                page_size=page_size,
                sort_by=sort_by,
                sort_order=sort_order,
            )
        )

    @allure_step("更新国内官key素材: {asset_id}")
    def update_asset(
        self,
        request_client: MaterialLibraryRequest,
        asset_id: str,
        *,
        name: str,
    ) -> requests.Response:
        return request_client.update_volc_asset(
            asset_id,
            self.build_update_asset_payload(
                name=name,
            ),
        )

    @allure_step("查询国内官key素材组列表")
    def list_asset_groups(
        self,
        request_client: MaterialLibraryRequest,
        *,
        group_type: str | None = None,
        group_ids: list[str] | None = None,
        name: str | None = None,
        page_number: int = 1,
        page_size: int = 20,
        sort_by: str = "CreateTime",
        sort_order: str = "Desc",
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
            )
        )

    @allure_step("查询国内官key素材组详情: {group_id}")
    def get_asset_group(
        self,
        request_client: MaterialLibraryRequest,
        group_id: str,
    ) -> requests.Response:
        return request_client.get_volc_asset_group(group_id)

    @allure_step("更新国内官key素材组: {group_id}")
    def update_asset_group(
        self,
        request_client: MaterialLibraryRequest,
        group_id: str,
        *,
        name: str,
        description: str,
    ) -> requests.Response:
        return request_client.update_volc_asset_group(
            group_id,
            self.build_update_asset_group_payload(
                name=name,
                description=description,
            ),
        )

    @allure_step("创建国内官key真人认证会话")
    def create_visual_validate_session(
        self,
        request_client: MaterialLibraryRequest,
    ) -> requests.Response:
        return request_client.create_volc_visual_validate_session(
            self.build_create_visual_validate_session_payload(),
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
        poll_interval: float = VOLC_ASSET_POLL_INTERVAL_SECONDS,
        poll_timeout: float | None = VOLC_ASSET_POLL_TIMEOUT_SECONDS,
    ) -> requests.Response:
        deadline = None if poll_timeout is None else time.monotonic() + poll_timeout
        last_response: requests.Response | None = None

        while True:
            last_response = self.get_asset(request_client, asset_id)
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
        poll_interval: float = VOLC_ASSET_POLL_INTERVAL_SECONDS,
        poll_timeout: float = VOLC_ASSET_POLL_TIMEOUT_SECONDS,
    ) -> requests.Response:
        deadline = time.monotonic() + poll_timeout
        last_response: requests.Response | None = None
        while True:
            last_response = self.get_asset(request_client, asset_id)
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
        model: str,
        prompt: str,
        duration: int,
        resolution: str,
        ratio: str,
        reference_role: str,
        generate_audio: bool,
        watermark: bool,
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
                generate_audio=generate_audio,
                watermark=watermark,
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
    ) -> requests.Response:
        return request_client.delete_volc_asset(asset_id)

    @allure_step("删除国内官key素材组: {group_id}")
    def delete_asset_group_if_exists(
        self,
        request_client: MaterialLibraryRequest,
        group_id: str,
    ) -> requests.Response:
        return request_client.delete_volc_asset_group(group_id)

    @staticmethod
    def build_create_ark_asset_group_payload(
        *,
        name: str,
        description: str,
    ) -> dict[str, Any]:
        return {
            "Name": name,
            "Description": description,
            "GroupType": VOLC_AIGC_GROUP_TYPE,
        }

    @staticmethod
    def build_create_ark_asset_payload(
        *,
        group_id: str,
        image_url: str,
        name: str,
    ) -> dict[str, Any]:
        return {
            "GroupId": group_id,
            "URL": image_url,
            "AssetType": VOLC_IMAGE_ASSET_TYPE,
            "Name": name,
        }

    @staticmethod
    def build_create_ark_visual_validate_session_payload(
        *,
        client_redirect_url: str,
        project_name: str = PROJECT_NAME,
    ) -> dict[str, Any]:
        return {
            "client_redirect_url": client_redirect_url,
            "project_name": project_name,
        }

    @staticmethod
    def build_ark_virtual_portrait_video_payload(
        *,
        asset_id: str,
        model: str,
        prompt: str,
        duration: int,
        resolution: str,
        ratio: str,
        reference_role: str,
        generate_audio: bool,
    ) -> dict[str, Any]:
        # 模板只映射接口字段；模型与生成参数由具体测试场景显式提供。
        return {
            "model": model,
            "duration": duration,
            "resolution": resolution,
            "ratio": ratio,
            "generate_audio": generate_audio,
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
        }

    @staticmethod
    def build_create_asset_group_payload(
        *,
        name: str,
        description: str,
        group_type: str = VOLC_AIGC_GROUP_TYPE,
    ) -> dict[str, Any]:
        return {
            "Name": name,
            "Description": description,
            "GroupType": group_type,
        }

    @staticmethod
    def build_create_asset_payload(
        *,
        group_id: str,
        image_url: str,
        name: str,
        asset_type: str = VOLC_IMAGE_ASSET_TYPE,
    ) -> dict[str, Any]:
        return {
            "GroupId": group_id,
            "URL": image_url,
            "Name": name,
            "AssetType": asset_type,
        }

    @staticmethod
    def build_list_asset_groups_payload(
        *,
        group_type: str | None = None,
        group_ids: list[str] | None = None,
        name: str | None = None,
        page_number: int = 1,
        page_size: int = 20,
        sort_by: str = "CreateTime",
        sort_order: str = "Desc",
    ) -> dict[str, Any]:
        filter_value: dict[str, Any] = {}
        if group_type is not None:
            filter_value["GroupType"] = group_type
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
        }

    @staticmethod
    def build_list_assets_payload(
        *,
        group_ids: list[str] | None = None,
        name: str | None = None,
        statuses: list[str] | None = None,
        page_number: int = 1,
        page_size: int = 20,
        sort_by: str = "CreateTime",
        sort_order: str = "Desc",
    ) -> dict[str, Any]:
        filter_value: dict[str, Any] = {}
        if group_ids is not None:
            filter_value["GroupIds"] = group_ids
        if name is not None:
            filter_value["Name"] = name
        if statuses is not None:
            filter_value["Statuses"] = statuses
        return {
            "Filter": filter_value,
            "PageNumber": page_number,
            "PageSize": page_size,
            "SortBy": sort_by,
            "SortOrder": sort_order,
        }

    @staticmethod
    def build_update_asset_payload(
        *,
        name: str,
    ) -> dict[str, Any]:
        return {"Name": name}

    @staticmethod
    def build_update_asset_group_payload(
        *,
        name: str,
        description: str,
    ) -> dict[str, Any]:
        return {
            "Name": name,
            "Description": description,
        }

    @staticmethod
    def build_create_visual_validate_session_payload(
    ) -> dict[str, Any]:
        return {}

    @staticmethod
    def build_asset_video_generation_payload(
        *,
        asset_id: str,
        model: str,
        prompt: str,
        duration: int,
        resolution: str,
        ratio: str,
        reference_role: str,
        generate_audio: bool,
        watermark: bool,
    ) -> dict[str, Any]:
        # 模板只映射接口字段；模型与生成参数由具体测试场景显式提供。
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
            "generate_audio": generate_audio,
            "watermark": watermark,
        }

    @staticmethod
    def extract_root_id(response: requests.Response) -> str:
        value = MaterialLibraryTask.json_body(response).get("Id")
        assert value, f"Response missing Id. Response body: {response.text}"
        return str(value)

    @staticmethod
    def extract_ark_visual_validate_session_id(response: requests.Response) -> str:
        value = MaterialLibraryTask.json_body(response).get("session_id")
        assert value, f"Response missing session_id. Response body: {response.text}"
        return str(value)

    @staticmethod
    def extract_ark_visual_validate_h5_link(response: requests.Response) -> str:
        value = MaterialLibraryTask.json_body(response).get("h5_link")
        assert value, f"Response missing h5_link. Response body: {response.text}"
        return str(value)

    @staticmethod
    def extract_ark_visual_validate_status(response: requests.Response) -> str:
        value = MaterialLibraryTask.json_body(response).get("status")
        return str(value or "")

    @staticmethod
    def extract_ark_visual_validate_group_id(response: requests.Response) -> str:
        value = MaterialLibraryTask.json_body(response).get("group_id")
        assert value, f"Response missing group_id. Response body: {response.text}"
        return str(value)

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

    @staticmethod
    def unique_ark_group_name() -> str:
        return f"api-case-ark-group-{uuid.uuid4().hex[:8]}"

    @staticmethod
    def unique_ark_asset_name() -> str:
        return f"api-case-ark-asset-{uuid.uuid4().hex[:8]}"
