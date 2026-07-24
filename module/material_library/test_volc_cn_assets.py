from __future__ import annotations

import os

import pytest

from module.material_library import (
    MaterialLibraryAssertions,
    MaterialLibraryRequest,
    MaterialLibraryTask,
)
from module.material_library.task import (
    DEFAULT_VOLC_FAST_VIDEO_MODEL,
    DEFAULT_VOLC_LIVENESS_IMAGE_URL,
    DEFAULT_VOLC_MINI_VIDEO_MODEL,
    PROJECT_NAME,
    VOLC_AIGC_GROUP_TYPE,
    VOLC_ASSET_ID_PREFIX,
    VOLC_GROUP_ID_PREFIX,
    VOLC_VISUAL_VALIDATE_PENDING_STATUS,
    VOLC_VISUAL_VALIDATE_READY_STATUSES,
)


COMPLETED_VISUAL_VALIDATE_SESSION_ID_ENV = "VOLC_CN_VISUAL_VALIDATE_SESSION_ID"
LIVENESS_GROUP_ID_ENV = "VOLC_CN_LIVENESS_GROUP_ID"


class TestVolcCnAssetsPositiveFlow:
    def setup_method(self):
        self.material_library_request = MaterialLibraryRequest()
        self.material_library_assertions = MaterialLibraryAssertions()
        self.material_library_task = MaterialLibraryTask()
        self.created_asset_ids: list[str] = []
        self.created_group_ids: list[str] = []

    def teardown_method(self):
        for asset_id in reversed(self.created_asset_ids):
            try:
                self.material_library_task.delete_asset_if_exists(self.material_library_request, asset_id)
            except Exception as error:
                print(f"cleanup asset failed: asset_id={asset_id}, error={error}")
        for group_id in reversed(self.created_group_ids):
            try:
                self.material_library_task.delete_asset_group_if_exists(self.material_library_request, group_id)
            except Exception as error:
                print(f"cleanup group failed: group_id={group_id}, error={error}")
        self.material_library_request.close()

    def test_vc_aigc_001_create_aigc_asset_group(self):
        response = self.material_library_task.create_aigc_asset_group(self.material_library_request)

        self.material_library_assertions.assert_status_code(response, 200)
        self.material_library_assertions.assert_result_id_prefix(response, VOLC_GROUP_ID_PREFIX)
        self.material_library_assertions.assert_no_upstream_secret_leaked(response)

        group_id = self.material_library_task.extract_group_id(response)
        self.created_group_ids.append(group_id)

    def test_vc_aigc_002_upload_image_asset(self):
        group_id = self._create_group()

        response = self.material_library_task.upload_image_asset(self.material_library_request, group_id)

        self.material_library_assertions.assert_status_code(response, 200)
        self.material_library_assertions.assert_result_id_prefix(response, VOLC_ASSET_ID_PREFIX)
        self.material_library_assertions.assert_no_upstream_secret_leaked(response)

        asset_id = self.material_library_task.extract_asset_id(response)
        self.created_asset_ids.append(asset_id)

    def test_vc_aigc_003_poll_asset_status_until_active(self):
        group_id = self._create_group()
        asset_id = self._upload_asset(group_id)

        active_response = self.material_library_task.poll_asset_until_active(
            self.material_library_request,
            asset_id,
        )

        self.material_library_assertions.assert_status_code(active_response, 200)
        self.material_library_assertions.assert_asset_status(active_response, "Active")
        self.material_library_assertions.assert_asset_detail_matches(
            active_response,
            asset_id=asset_id,
            group_id=group_id,
        )

    def test_vc_aigc_004_asset_can_be_used_for_video_generation(self):
        group_id = self._create_group()
        asset_id = self._upload_asset(group_id)
        self.material_library_task.poll_asset_until_active(self.material_library_request, asset_id)

        create_response = self.material_library_task.create_asset_video_generation(
            self.material_library_request,
            asset_id,
            model=DEFAULT_VOLC_FAST_VIDEO_MODEL,
        )
        self.material_library_assertions.assert_media_generation_submit_succeeded(create_response)
        task_id = self.material_library_task.extract_media_task_id(create_response)

        result_response = self.material_library_task.poll_media_generation_until_finished(
            self.material_library_request,
            task_id,
        )

        self.material_library_assertions.assert_status_code(result_response, 200)
        self.material_library_assertions.assert_media_task_has_video_url(result_response)

    @pytest.mark.parametrize(
        "model_id",
        [
            DEFAULT_VOLC_FAST_VIDEO_MODEL,
            DEFAULT_VOLC_MINI_VIDEO_MODEL,
        ],
    )
    def test_vc_aigc_005_asset_id_can_be_reused_between_fast_and_mini_models(self, model_id: str):
        group_id = self._create_group()
        asset_id = self._upload_asset(group_id)
        self.material_library_task.poll_asset_until_active(self.material_library_request, asset_id)

        response = self.material_library_task.create_asset_video_generation(
            self.material_library_request,
            asset_id,
            model=model_id,
        )

        self.material_library_assertions.assert_media_generation_submit_succeeded(response)
        task_id = self.material_library_task.extract_media_task_id(response)
        assert task_id, f"Video generation response should contain a task id. Response body: {response.text}"

    def _create_group(self) -> str:
        response = self.material_library_task.create_aigc_asset_group(self.material_library_request)
        self.material_library_assertions.assert_status_code(response, 200)
        self.material_library_assertions.assert_result_id_prefix(response, VOLC_GROUP_ID_PREFIX)
        group_id = self.material_library_task.extract_group_id(response)
        self.created_group_ids.append(group_id)
        return group_id

    def _upload_asset(self, group_id: str) -> str:
        response = self.material_library_task.upload_image_asset(
            self.material_library_request,
            group_id,
            project_name=PROJECT_NAME,
        )
        self.material_library_assertions.assert_status_code(response, 200)
        self.material_library_assertions.assert_result_id_prefix(response, VOLC_ASSET_ID_PREFIX)
        asset_id = self.material_library_task.extract_asset_id(response)
        self.created_asset_ids.append(asset_id)
        return asset_id


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
            project_name=PROJECT_NAME,
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


class TestVolcCnVisualValidate:
    def setup_method(self):
        self.material_library_request = MaterialLibraryRequest()
        self.material_library_assertions = MaterialLibraryAssertions()
        self.material_library_task = MaterialLibraryTask()
        self.created_asset_ids: list[str] = []

    def teardown_method(self):
        for asset_id in reversed(self.created_asset_ids):
            try:
                self.material_library_task.delete_asset_if_exists(self.material_library_request, asset_id)
            except Exception as error:
                print(f"cleanup liveness asset failed: asset_id={asset_id}, error={error}")
        self.material_library_request.close()

    def test_vc_vv_001_create_visual_validate_session(self):
        response = self.material_library_task.create_visual_validate_session(
            self.material_library_request,
            project_name=PROJECT_NAME,
        )

        self.material_library_assertions.assert_status_code(response, 200)
        self.material_library_assertions.assert_visual_validate_session_created(response)
        self.material_library_assertions.assert_no_upstream_secret_leaked(response)

    def test_vc_vv_002_get_pending_visual_validate_session(self):
        create_response = self.material_library_task.create_visual_validate_session(
            self.material_library_request,
            project_name=PROJECT_NAME,
        )
        session_id = self.material_library_task.extract_visual_validate_session_id(create_response)

        response = self.material_library_task.get_visual_validate_session(
            self.material_library_request,
            session_id,
        )

        self.material_library_assertions.assert_status_code(response, 200)
        self.material_library_assertions.assert_visual_validate_session_status(
            response,
            {VOLC_VISUAL_VALIDATE_PENDING_STATUS},
        )
        self.material_library_assertions.assert_no_upstream_secret_leaked(response)

    def test_vc_vv_003_completed_visual_validate_session_reaches_ready_status(self):
        session_id = self._required_completed_session_id()

        response = self.material_library_task.poll_visual_validate_session_until_ready(
            self.material_library_request,
            session_id,
        )

        self.material_library_assertions.assert_status_code(response, 200)
        self.material_library_assertions.assert_visual_validate_session_status(
            response,
            VOLC_VISUAL_VALIDATE_READY_STATUSES,
        )

    def test_vc_vv_004_get_visual_validate_result_group(self):
        session_id = self._required_completed_session_id()

        response = self.material_library_task.get_visual_validate_result(
            self.material_library_request,
            session_id,
        )

        self.material_library_assertions.assert_status_code(response, 200)
        self.material_library_assertions.assert_visual_validate_result_group_id(response)
        self.material_library_assertions.assert_no_upstream_secret_leaked(response)

    def test_vc_vv_005_upload_asset_to_liveness_group(self):
        group_id = self._required_liveness_group_id()

        upload_response = self.material_library_task.upload_image_asset(
            self.material_library_request,
            group_id,
            image_url=DEFAULT_VOLC_LIVENESS_IMAGE_URL,
            project_name=PROJECT_NAME,
        )
        self.material_library_assertions.assert_status_code(upload_response, 200)
        self.material_library_assertions.assert_result_id_prefix(upload_response, VOLC_ASSET_ID_PREFIX)
        asset_id = self.material_library_task.extract_asset_id(upload_response)
        self.created_asset_ids.append(asset_id)

        active_response = self.material_library_task.poll_asset_until_active(
            self.material_library_request,
            asset_id,
        )

        self.material_library_assertions.assert_status_code(active_response, 200)
        self.material_library_assertions.assert_asset_status(active_response, "Active")
        self.material_library_assertions.assert_asset_detail_matches(
            active_response,
            asset_id=asset_id,
            group_id=group_id,
        )

    @staticmethod
    def _required_completed_session_id() -> str:
        session_id = os.getenv(COMPLETED_VISUAL_VALIDATE_SESSION_ID_ENV, "").strip()
        if not session_id:
            pytest.skip(
                f"Please complete H5 visual validate first and configure "
                f"{COMPLETED_VISUAL_VALIDATE_SESSION_ID_ENV}."
            )
        return session_id

    @staticmethod
    def _required_liveness_group_id() -> str:
        group_id = os.getenv(LIVENESS_GROUP_ID_ENV, "").strip()
        if not group_id:
            pytest.skip(
                f"Please configure {LIVENESS_GROUP_ID_ENV} from "
                f"GET /v1/volc/assets/visual-validate/results/{{session_id}} first."
            )
        return group_id
