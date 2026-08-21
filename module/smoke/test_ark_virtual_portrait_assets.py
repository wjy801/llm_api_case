from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

import pytest

from module.material_library import (
    MaterialLibraryAssertions,
    MaterialLibraryRequest,
    MaterialLibraryTask,
)
from module.material_library.task import (
    ARK_ASSET_ID_PREFIX,
    ARK_GROUP_ID_PREFIX,
)


ARK_VIRTUAL_PORTRAIT_IMAGE_URL = (
    "https://ark-project.tos-cn-beijing.volces.com/doc_image/r2v_tea_pic1.jpg"
)
ARK_VIRTUAL_PORTRAIT_VIDEO_MODEL_ID = "dreamina-seedance-2-0-260128"
ARK_VIRTUAL_PORTRAIT_VIDEO_PROMPT = "参考图主体自然微笑并看向镜头"
ARK_VIRTUAL_PORTRAIT_VIDEO_DURATION = 5
ARK_VIRTUAL_PORTRAIT_VIDEO_RESOLUTION = "720p"
ARK_VIRTUAL_PORTRAIT_VIDEO_RATIO = "16:9"
ARK_VIRTUAL_PORTRAIT_VIDEO_REFERENCE_ROLE = "reference_image"
ARK_VIRTUAL_PORTRAIT_VIDEO_GENERATE_AUDIO = True


pytestmark = pytest.mark.serial


@dataclass
class ArkVirtualPortraitFlowState:
    group_id: str | None = None
    asset_id: str | None = None
    task_id: str | None = None


@dataclass
class ArkVirtualPortraitRuntime:
    request: MaterialLibraryRequest
    assertions: MaterialLibraryAssertions
    task: MaterialLibraryTask
    state: ArkVirtualPortraitFlowState


@pytest.fixture(scope="module")
def ark_virtual_portrait_runtime() -> Iterator[ArkVirtualPortraitRuntime]:
    runtime = ArkVirtualPortraitRuntime(
        request=MaterialLibraryRequest(),
        assertions=MaterialLibraryAssertions(),
        task=MaterialLibraryTask(),
        state=ArkVirtualPortraitFlowState(),
    )
    try:
        yield runtime
    finally:
        if runtime.state.asset_id:
            try:
                response = runtime.task.delete_ark_asset_if_exists(
                    runtime.request,
                    runtime.state.asset_id,
                )
                if response.status_code not in (204, 404):
                    print(
                        "cleanup Ark virtual portrait asset returned "
                        f"HTTP {response.status_code}: {response.text}"
                    )
            except Exception as error:
                print(f"cleanup Ark virtual portrait asset failed: {error}")
        if runtime.state.group_id:
            try:
                response = runtime.task.delete_ark_asset_group_if_exists(
                    runtime.request,
                    runtime.state.group_id,
                )
                if response.status_code not in (204, 404):
                    print(
                        "cleanup Ark virtual portrait group returned "
                        f"HTTP {response.status_code}: {response.text}"
                    )
            except Exception as error:
                print(f"cleanup Ark virtual portrait group failed: {error}")
        runtime.request.close()


class TestArkVirtualPortraitAssetsFlow:
    def test_01_create_virtual_portrait_group(
        self,
        ark_virtual_portrait_runtime: ArkVirtualPortraitRuntime,
    ):
        runtime = ark_virtual_portrait_runtime

        response = runtime.task.create_ark_virtual_portrait_group(runtime.request)
        group_id = runtime.task.extract_root_id(response)
        runtime.state.group_id = group_id

        runtime.assertions.assert_status_code(response, 201)
        runtime.assertions.assert_root_id_prefix(
            response,
            ARK_GROUP_ID_PREFIX,
        )

    def test_02_upload_virtual_portrait_image(
        self,
        ark_virtual_portrait_runtime: ArkVirtualPortraitRuntime,
    ):
        runtime = ark_virtual_portrait_runtime
        group_id = self._require(runtime.state.group_id, "group_id")

        response = runtime.task.upload_ark_virtual_portrait_image(
            runtime.request,
            group_id,
            image_url=ARK_VIRTUAL_PORTRAIT_IMAGE_URL,
        )
        asset_id = runtime.task.extract_root_id(response)
        runtime.state.asset_id = asset_id

        runtime.assertions.assert_status_code(response, 202)
        runtime.assertions.assert_root_id_prefix(
            response,
            ARK_ASSET_ID_PREFIX,
        )

    def test_03_wait_for_virtual_portrait_asset_active(
        self,
        ark_virtual_portrait_runtime: ArkVirtualPortraitRuntime,
    ):
        runtime = ark_virtual_portrait_runtime
        asset_id = self._require(runtime.state.asset_id, "asset_id")

        response = runtime.task.poll_ark_asset_until_active(
            runtime.request,
            asset_id,
        )

        runtime.assertions.assert_status_code(response, 200)
        runtime.assertions.assert_root_status(response, "Active")

    def test_04_create_virtual_portrait_video_generation(
        self,
        ark_virtual_portrait_runtime: ArkVirtualPortraitRuntime,
    ):
        runtime = ark_virtual_portrait_runtime
        asset_id = self._require(runtime.state.asset_id, "asset_id")

        response = runtime.task.create_ark_virtual_portrait_video(
            runtime.request,
            asset_id,
            model=ARK_VIRTUAL_PORTRAIT_VIDEO_MODEL_ID,
            prompt=ARK_VIRTUAL_PORTRAIT_VIDEO_PROMPT,
            duration=ARK_VIRTUAL_PORTRAIT_VIDEO_DURATION,
            resolution=ARK_VIRTUAL_PORTRAIT_VIDEO_RESOLUTION,
            ratio=ARK_VIRTUAL_PORTRAIT_VIDEO_RATIO,
            reference_role=ARK_VIRTUAL_PORTRAIT_VIDEO_REFERENCE_ROLE,
            generate_audio=ARK_VIRTUAL_PORTRAIT_VIDEO_GENERATE_AUDIO,
        )
        task_id = runtime.task.extract_media_task_id(response)
        runtime.state.task_id = task_id

        runtime.assertions.assert_media_generation_submit_succeeded(response)

    def test_05_wait_for_virtual_portrait_video_succeeded(
        self,
        ark_virtual_portrait_runtime: ArkVirtualPortraitRuntime,
    ):
        runtime = ark_virtual_portrait_runtime
        task_id = self._require(runtime.state.task_id, "task_id")

        response = runtime.task.poll_ark_media_generation_until_finished(
            runtime.request,
            task_id,
        )

        runtime.assertions.assert_status_code(response, 200)
        runtime.assertions.assert_media_task_has_video_url(response)

    def test_06_delete_virtual_portrait_asset(
        self,
        ark_virtual_portrait_runtime: ArkVirtualPortraitRuntime,
    ):
        runtime = ark_virtual_portrait_runtime
        asset_id = self._require(runtime.state.asset_id, "asset_id")

        response = runtime.task.delete_ark_asset_if_exists(
            runtime.request,
            asset_id,
        )

        runtime.assertions.assert_status_code(response, 204)
        runtime.state.asset_id = None

    def test_07_delete_virtual_portrait_group(
        self,
        ark_virtual_portrait_runtime: ArkVirtualPortraitRuntime,
    ):
        runtime = ark_virtual_portrait_runtime
        group_id = self._require(runtime.state.group_id, "group_id")

        response = runtime.task.delete_ark_asset_group_if_exists(
            runtime.request,
            group_id,
        )

        runtime.assertions.assert_status_code(response, 204)
        runtime.state.group_id = None

    @staticmethod
    def _require(value: str | None, label: str) -> str:
        if not value:
            pytest.fail(
                f"缺少流程状态 {label}；请按顺序执行完整虚拟人像文件，"
                "不要单独从中间节点启动"
            )
        return value
