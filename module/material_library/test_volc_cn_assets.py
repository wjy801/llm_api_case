from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field

import pytest

from module.material_library import (
    MaterialLibraryAssertions,
    MaterialLibraryRequest,
    MaterialLibraryTask,
)
from module.material_library.task import (
    VOLC_AIGC_GROUP_TYPE,
    VOLC_ASSET_ID_PREFIX,
    VOLC_GROUP_ID_PREFIX,
)


VOLC_PROJECT_NAME = "default"
VOLC_VIRTUAL_IMAGE_URL = (
    "https://ark-project.tos-cn-beijing.volces.com/doc_image/r2v_tea_pic1.jpg"
)
VOLC_VIRTUAL_VIDEO_MODELS = (
    "doubao-seedance-2-5-260628",
)
VOLC_VIRTUAL_VIDEO_PROMPT = (
    "图片1中的人物在海边自然行走，保持主体一致，电影感镜头。"
)
VOLC_VIRTUAL_VIDEO_DURATION = 5
VOLC_VIRTUAL_VIDEO_RESOLUTION = "720p"
VOLC_VIRTUAL_VIDEO_RATIO = "16:9"
VOLC_VIRTUAL_VIDEO_REFERENCE_ROLE = "reference_image"
VOLC_VIRTUAL_VIDEO_GENERATE_AUDIO = True
VOLC_VIRTUAL_VIDEO_WATERMARK = False


@dataclass
class VolcCnVirtualAssetFlowState:
    group_id: str | None = None
    asset_id: str | None = None
    task_ids: dict[str, str] = field(default_factory=dict)


@dataclass
class VolcCnVirtualAssetRuntime:
    request: MaterialLibraryRequest
    assertions: MaterialLibraryAssertions
    task: MaterialLibraryTask
    state: VolcCnVirtualAssetFlowState


@pytest.fixture(scope="module")
def volc_cn_virtual_asset_runtime() -> Iterator[VolcCnVirtualAssetRuntime]:
    runtime = VolcCnVirtualAssetRuntime(
        request=MaterialLibraryRequest(),
        assertions=MaterialLibraryAssertions(),
        task=MaterialLibraryTask(),
        state=VolcCnVirtualAssetFlowState(),
    )
    try:
        yield runtime
    finally:
        if runtime.state.asset_id:
            try:
                response = runtime.task.delete_asset_if_exists(
                    runtime.request,
                    runtime.state.asset_id,
                )
                if response.status_code not in (200, 404):
                    print(
                        "cleanup Volc virtual asset returned "
                        f"HTTP {response.status_code}: {response.text}"
                    )
            except Exception as error:
                print(f"cleanup Volc virtual asset failed: {error}")
        if runtime.state.group_id:
            try:
                response = runtime.task.delete_asset_group_if_exists(
                    runtime.request,
                    runtime.state.group_id,
                )
                if response.status_code not in (200, 404):
                    print(
                        "cleanup Volc virtual group returned "
                        f"HTTP {response.status_code}: {response.text}"
                    )
            except Exception as error:
                print(f"cleanup Volc virtual group failed: {error}")
        runtime.request.close()


@pytest.mark.serial
class TestVolcCnVirtualAssetsFlow:
    def test_vc_aigc_001_create_virtual_asset_group(
        self,
        volc_cn_virtual_asset_runtime: VolcCnVirtualAssetRuntime,
    ):
        runtime = volc_cn_virtual_asset_runtime

        response = runtime.task.create_aigc_asset_group(
            runtime.request,
            project_name=VOLC_PROJECT_NAME,
        )
        group_id = runtime.task.extract_group_id(response)
        runtime.state.group_id = group_id

        runtime.assertions.assert_status_code(response, 200)
        runtime.assertions.assert_result_id_prefix(response, VOLC_GROUP_ID_PREFIX)
        runtime.assertions.assert_no_upstream_secret_leaked(response)

    def test_vc_aigc_002_upload_virtual_image_asset(
        self,
        volc_cn_virtual_asset_runtime: VolcCnVirtualAssetRuntime,
    ):
        runtime = volc_cn_virtual_asset_runtime
        group_id = self._require(runtime.state.group_id, "group_id")

        response = runtime.task.upload_image_asset(
            runtime.request,
            group_id,
            image_url=VOLC_VIRTUAL_IMAGE_URL,
            project_name=VOLC_PROJECT_NAME,
        )
        asset_id = runtime.task.extract_asset_id(response)
        runtime.state.asset_id = asset_id

        runtime.assertions.assert_status_code(response, 200)
        runtime.assertions.assert_result_id_prefix(response, VOLC_ASSET_ID_PREFIX)
        runtime.assertions.assert_no_upstream_secret_leaked(response)

    def test_vc_aigc_003_wait_for_virtual_asset_active(
        self,
        volc_cn_virtual_asset_runtime: VolcCnVirtualAssetRuntime,
    ):
        runtime = volc_cn_virtual_asset_runtime
        asset_id = self._require(runtime.state.asset_id, "asset_id")
        group_id = self._require(runtime.state.group_id, "group_id")

        response = runtime.task.poll_asset_until_active(
            runtime.request,
            asset_id,
        )

        runtime.assertions.assert_status_code(response, 200)
        runtime.assertions.assert_asset_status(response, "Active")
        runtime.assertions.assert_asset_detail_matches(
            response,
            asset_id=asset_id,
            group_id=group_id,
        )

    @pytest.mark.parametrize("model_id", VOLC_VIRTUAL_VIDEO_MODELS)
    def test_vc_aigc_004_create_asset_video_generation(
        self,
        volc_cn_virtual_asset_runtime: VolcCnVirtualAssetRuntime,
        model_id: str,
    ):
        runtime = volc_cn_virtual_asset_runtime
        asset_id = self._require(runtime.state.asset_id, "asset_id")

        response = runtime.task.create_asset_video_generation(
            runtime.request,
            asset_id,
            model=model_id,
            prompt=VOLC_VIRTUAL_VIDEO_PROMPT,
            duration=VOLC_VIRTUAL_VIDEO_DURATION,
            resolution=VOLC_VIRTUAL_VIDEO_RESOLUTION,
            ratio=VOLC_VIRTUAL_VIDEO_RATIO,
            reference_role=VOLC_VIRTUAL_VIDEO_REFERENCE_ROLE,
            generate_audio=VOLC_VIRTUAL_VIDEO_GENERATE_AUDIO,
            watermark=VOLC_VIRTUAL_VIDEO_WATERMARK,
        )
        task_id = runtime.task.extract_media_task_id(response)
        runtime.state.task_ids[model_id] = task_id

        runtime.assertions.assert_media_generation_submit_succeeded(response)

    @pytest.mark.parametrize("model_id", VOLC_VIRTUAL_VIDEO_MODELS)
    def test_vc_aigc_005_wait_for_asset_video_succeeded(
        self,
        volc_cn_virtual_asset_runtime: VolcCnVirtualAssetRuntime,
        model_id: str,
    ):
        runtime = volc_cn_virtual_asset_runtime
        task_id = self._require(runtime.state.task_ids.get(model_id), "task_id")

        response = runtime.task.poll_media_generation_until_finished(
            runtime.request,
            task_id,
        )

        runtime.assertions.assert_status_code(response, 200)
        runtime.assertions.assert_media_task_has_video_url(response)

    def test_vc_aigc_006_delete_virtual_asset(
        self,
        volc_cn_virtual_asset_runtime: VolcCnVirtualAssetRuntime,
    ):
        runtime = volc_cn_virtual_asset_runtime
        asset_id = self._require(runtime.state.asset_id, "asset_id")

        response = runtime.task.delete_asset_if_exists(runtime.request, asset_id)

        runtime.assertions.assert_status_code(response, 200)
        runtime.assertions.assert_delete_result_empty(response)
        runtime.state.asset_id = None

    def test_vc_aigc_007_delete_virtual_asset_group(
        self,
        volc_cn_virtual_asset_runtime: VolcCnVirtualAssetRuntime,
    ):
        runtime = volc_cn_virtual_asset_runtime
        group_id = self._require(runtime.state.group_id, "group_id")

        response = runtime.task.delete_asset_group_if_exists(runtime.request, group_id)

        runtime.assertions.assert_status_code(response, 200)
        runtime.assertions.assert_delete_result_empty(response)
        runtime.state.group_id = None

    @staticmethod
    def _require(value: str | None, label: str) -> str:
        if not value:
            pytest.fail(
                f"缺少流程状态 {label}；请按顺序执行完整国内虚拟素材文件，"
                "不要单独从中间节点启动"
            )
        return value


class TestVolcCnAssetGroupsManagement:
    def setup_method(self):
        self.material_library_request = MaterialLibraryRequest()
        self.material_library_assertions = MaterialLibraryAssertions()
        self.material_library_task = MaterialLibraryTask()
        self.created_group_ids: list[str] = []

    def teardown_method(self):
        for group_id in reversed(self.created_group_ids):
            try:
                self.material_library_task.delete_asset_group_if_exists(self.material_library_request, group_id)
            except Exception as error:
                print(f"cleanup group failed: group_id={group_id}, error={error}")
        self.material_library_request.close()

    def test_vc_grp_001_list_asset_groups_by_group_type(self):
        group_id, _ = self._create_group()

        response = self.material_library_task.list_asset_groups(
            self.material_library_request,
            group_type=VOLC_AIGC_GROUP_TYPE,
        )

        self.material_library_assertions.assert_status_code(response, 200)
        self.material_library_assertions.assert_group_list_contains(response, group_id=group_id)

    def test_vc_grp_002_list_asset_groups_by_group_ids(self):
        group_id, _ = self._create_group()

        response = self.material_library_task.list_asset_groups(
            self.material_library_request,
            group_ids=[group_id],
        )

        self.material_library_assertions.assert_status_code(response, 200)
        self.material_library_assertions.assert_group_list_contains(response, group_id=group_id)

    def test_vc_grp_003_list_asset_groups_by_name_keyword(self):
        group_id, group_name = self._create_group()
        name_keyword = group_name[:12]

        response = self.material_library_task.list_asset_groups(
            self.material_library_request,
            name=name_keyword,
        )

        self.material_library_assertions.assert_status_code(response, 200)
        self.material_library_assertions.assert_group_list_contains(response, group_id=group_id)

    def test_vc_grp_004_get_asset_group_detail(self):
        group_id, group_name = self._create_group()

        response = self.material_library_task.get_asset_group(self.material_library_request, group_id)

        self.material_library_assertions.assert_status_code(response, 200)
        self.material_library_assertions.assert_group_detail_matches(
            response,
            group_id=group_id,
            name=group_name,
            group_type=VOLC_AIGC_GROUP_TYPE,
        )

    def test_vc_grp_005_update_asset_group_name_and_description(self):
        group_id, _ = self._create_group()
        new_name = self.material_library_task.unique_group_name()
        new_description = "api-case updated AIGC group description"

        update_response = self.material_library_task.update_asset_group(
            self.material_library_request,
            group_id,
            name=new_name,
            description=new_description,
        )
        detail_response = self.material_library_task.get_asset_group(self.material_library_request, group_id)

        self.material_library_assertions.assert_status_code(update_response, 200)
        self.material_library_assertions.assert_result_id_prefix(update_response, VOLC_GROUP_ID_PREFIX)
        self.material_library_assertions.assert_status_code(detail_response, 200)
        self.material_library_assertions.assert_group_detail_matches(
            detail_response,
            group_id=group_id,
            name=new_name,
            group_type=VOLC_AIGC_GROUP_TYPE,
        )

    def test_vc_grp_006_delete_empty_asset_group(self):
        group_id, _ = self._create_group()

        delete_response = self.material_library_task.delete_asset_group_if_exists(
            self.material_library_request,
            group_id,
        )
        self._forget_group(group_id)
        get_response = self.material_library_task.get_asset_group(self.material_library_request, group_id)

        self.material_library_assertions.assert_status_code(delete_response, 200)
        self.material_library_assertions.assert_delete_result_empty(delete_response)
        self.material_library_assertions.assert_status_code(get_response, 404)

    def test_vc_grp_007_delete_asset_group_is_idempotent(self):
        group_id, _ = self._create_group()

        first_delete_response = self.material_library_task.delete_asset_group_if_exists(
            self.material_library_request,
            group_id,
        )
        self._forget_group(group_id)
        second_delete_response = self.material_library_task.delete_asset_group_if_exists(
            self.material_library_request,
            group_id,
        )

        self.material_library_assertions.assert_status_code(first_delete_response, 200)
        self.material_library_assertions.assert_delete_result_empty(first_delete_response)
        self.material_library_assertions.assert_status_code(second_delete_response, 200)
        self.material_library_assertions.assert_delete_result_empty(second_delete_response)

    def _create_group(self) -> tuple[str, str]:
        group_name = self.material_library_task.unique_group_name()
        response = self.material_library_task.create_aigc_asset_group(
            self.material_library_request,
            name=group_name,
            description="api-case group management test",
            project_name=VOLC_PROJECT_NAME,
        )
        self.material_library_assertions.assert_status_code(response, 200)
        self.material_library_assertions.assert_result_id_prefix(response, VOLC_GROUP_ID_PREFIX)
        group_id = self.material_library_task.extract_group_id(response)
        self.created_group_ids.append(group_id)
        return group_id, group_name

    def _forget_group(self, group_id: str) -> None:
        self.created_group_ids = [
            created_group_id
            for created_group_id in self.created_group_ids
            if created_group_id != group_id
        ]


class TestVolcCnAssetsManagement:
    def setup_method(self):
        self.material_library_request = MaterialLibraryRequest()
        self.material_library_assertions = MaterialLibraryAssertions()
        self.material_library_task = MaterialLibraryTask()
        self.created_asset_ids: list[str] = []
        self.created_group_ids: list[str] = []

    def teardown_method(self):
        for asset_id in reversed(self.created_asset_ids):
            try:
                self.material_library_task.delete_asset_if_exists(
                    self.material_library_request,
                    asset_id,
                )
            except Exception as error:
                print(f"cleanup asset failed: asset_id={asset_id}, error={error}")
        for group_id in reversed(self.created_group_ids):
            try:
                self.material_library_task.delete_asset_group_if_exists(
                    self.material_library_request,
                    group_id,
                )
            except Exception as error:
                print(f"cleanup group failed: group_id={group_id}, error={error}")
        self.material_library_request.close()

    def test_vc_ast_001_list_assets_by_group_ids(self):
        group_id, asset_id, _ = self._create_asset()

        response = self.material_library_task.list_assets(
            self.material_library_request,
            group_ids=[group_id],
        )

        self.material_library_assertions.assert_status_code(response, 200)
        self.material_library_assertions.assert_asset_list_contains(
            response,
            asset_id=asset_id,
        )

    def test_vc_ast_002_list_assets_by_status(self):
        _, asset_id, _ = self._create_asset()
        self.material_library_task.poll_asset_until_active(
            self.material_library_request,
            asset_id,
        )
        expected_statuses = {"Active", "Processing"}

        response = self.material_library_task.list_assets(
            self.material_library_request,
            statuses=sorted(expected_statuses),
        )

        self.material_library_assertions.assert_status_code(response, 200)
        self.material_library_assertions.assert_asset_list_contains(
            response,
            asset_id=asset_id,
        )
        self.material_library_assertions.assert_asset_list_statuses(
            response,
            expected_statuses,
        )

    def test_vc_ast_003_list_assets_by_name_keyword(self):
        _, asset_id, asset_name = self._create_asset()

        response = self.material_library_task.list_assets(
            self.material_library_request,
            name=asset_name[:12],
        )

        self.material_library_assertions.assert_status_code(response, 200)
        self.material_library_assertions.assert_asset_list_contains(
            response,
            asset_id=asset_id,
        )

    def test_vc_ast_004_get_asset_detail(self):
        group_id, asset_id, asset_name = self._create_asset()

        response = self.material_library_task.get_asset(
            self.material_library_request,
            asset_id,
        )

        self.material_library_assertions.assert_status_code(response, 200)
        self.material_library_assertions.assert_asset_detail_matches(
            response,
            asset_id=asset_id,
            group_id=group_id,
        )
        self.material_library_assertions.assert_asset_name(
            response,
            asset_id=asset_id,
            name=asset_name,
        )

    def test_vc_ast_005_update_asset_name(self):
        _, asset_id, _ = self._create_asset()
        new_name = self.material_library_task.unique_asset_name()

        update_response = self.material_library_task.update_asset(
            self.material_library_request,
            asset_id,
            name=new_name,
        )
        detail_response = self.material_library_task.get_asset(
            self.material_library_request,
            asset_id,
        )

        self.material_library_assertions.assert_status_code(update_response, 200)
        self.material_library_assertions.assert_status_code(detail_response, 200)
        self.material_library_assertions.assert_asset_name(
            detail_response,
            asset_id=asset_id,
            name=new_name,
        )

    def test_vc_ast_006_delete_asset(self):
        _, asset_id, _ = self._create_asset()

        delete_response = self.material_library_task.delete_asset_if_exists(
            self.material_library_request,
            asset_id,
        )
        self._forget_asset(asset_id)
        get_response = self.material_library_task.get_asset(
            self.material_library_request,
            asset_id,
        )

        self.material_library_assertions.assert_status_code(delete_response, 200)
        self.material_library_assertions.assert_delete_result_empty(delete_response)
        self.material_library_assertions.assert_status_code(get_response, 404)
        self.material_library_assertions.assert_error_code_present(get_response)

    def test_vc_ast_007_delete_asset_is_idempotent(self):
        _, asset_id, _ = self._create_asset()

        first_delete_response = self.material_library_task.delete_asset_if_exists(
            self.material_library_request,
            asset_id,
        )
        self._forget_asset(asset_id)
        second_delete_response = self.material_library_task.delete_asset_if_exists(
            self.material_library_request,
            asset_id,
        )

        self.material_library_assertions.assert_status_code(first_delete_response, 200)
        self.material_library_assertions.assert_delete_result_empty(first_delete_response)
        self.material_library_assertions.assert_status_code(second_delete_response, 200)
        self.material_library_assertions.assert_delete_result_empty(second_delete_response)

    def _create_asset(self) -> tuple[str, str, str]:
        group_response = self.material_library_task.create_aigc_asset_group(
            self.material_library_request,
            description="api-case asset management test",
            project_name=VOLC_PROJECT_NAME,
        )
        self.material_library_assertions.assert_status_code(group_response, 200)
        self.material_library_assertions.assert_result_id_prefix(
            group_response,
            VOLC_GROUP_ID_PREFIX,
        )
        group_id = self.material_library_task.extract_group_id(group_response)
        self.created_group_ids.append(group_id)

        asset_name = self.material_library_task.unique_asset_name()
        asset_response = self.material_library_task.upload_image_asset(
            self.material_library_request,
            group_id,
            image_url=VOLC_VIRTUAL_IMAGE_URL,
            name=asset_name,
            project_name=VOLC_PROJECT_NAME,
        )
        self.material_library_assertions.assert_status_code(asset_response, 200)
        self.material_library_assertions.assert_result_id_prefix(
            asset_response,
            VOLC_ASSET_ID_PREFIX,
        )
        asset_id = self.material_library_task.extract_asset_id(asset_response)
        self.created_asset_ids.append(asset_id)
        return group_id, asset_id, asset_name

    def _forget_asset(self, asset_id: str) -> None:
        self.created_asset_ids = [
            created_asset_id
            for created_asset_id in self.created_asset_ids
            if created_asset_id != asset_id
        ]
