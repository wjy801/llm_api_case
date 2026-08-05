from __future__ import annotations

from typing import Any

import requests

from common import BaseRequest


class MaterialLibraryRequest(BaseRequest):
    volc_asset_groups_path = "/v1/volc/assets/groups"
    volc_asset_groups_list_path = "/v1/volc/assets/groups/list"
    volc_asset_group_path_template = "/v1/volc/assets/groups/{group_id}"
    volc_asset_group_update_path_template = "/v1/volc/assets/groups/{group_id}/update"
    volc_asset_group_delete_path_template = "/v1/volc/assets/groups/{group_id}/delete"
    volc_assets_path = "/v1/volc/assets"
    volc_assets_list_path = "/v1/volc/assets/list"
    volc_asset_path_template = "/v1/volc/assets/{asset_id}"
    volc_asset_update_path_template = "/v1/volc/assets/{asset_id}/update"
    volc_asset_delete_path_template = "/v1/volc/assets/{asset_id}/delete"
    volc_visual_validate_sessions_path = "/v1/volc/assets/visual-validate/sessions"
    volc_visual_validate_session_path_template = "/v1/volc/assets/visual-validate/sessions/{session_id}"
    volc_visual_validate_result_path_template = "/v1/volc/assets/visual-validate/results/{session_id}"
    volc_visual_validate_callback_path = "/v1/volc/assets/visual-validate/callback"
    media_generations_path = "/v1/media/generations"
    media_task_path_template = "/v1/media/tasks/{task_id}"

    def create_volc_asset_group(self, payload: dict[str, Any], **kwargs: Any) -> requests.Response:
        return self.post(self.volc_asset_groups_path, json=payload, **kwargs)

    def list_volc_asset_groups(self, payload: dict[str, Any], **kwargs: Any) -> requests.Response:
        return self.post(self.volc_asset_groups_list_path, json=payload, **kwargs)

    def get_volc_asset_group(
        self,
        group_id: str,
        *,
        project_name: str = "default",
        **kwargs: Any,
    ) -> requests.Response:
        return self.get(
            self.volc_asset_group_path_template.format(group_id=group_id),
            params={"ProjectName": project_name},
            **kwargs,
        )

    def update_volc_asset_group(
        self,
        group_id: str,
        payload: dict[str, Any],
        **kwargs: Any,
    ) -> requests.Response:
        return self.post(
            self.volc_asset_group_update_path_template.format(group_id=group_id),
            json=payload,
            **kwargs,
        )

    def delete_volc_asset_group(
        self,
        group_id: str,
        payload: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> requests.Response:
        return self.post(
            self.volc_asset_group_delete_path_template.format(group_id=group_id),
            json=payload or {"ProjectName": "default"},
            **kwargs,
        )

    def create_volc_asset(self, payload: dict[str, Any], **kwargs: Any) -> requests.Response:
        return self.post(self.volc_assets_path, json=payload, **kwargs)

    def list_volc_assets(self, payload: dict[str, Any], **kwargs: Any) -> requests.Response:
        return self.post(self.volc_assets_list_path, json=payload, **kwargs)

    def get_volc_asset(
        self,
        asset_id: str,
        *,
        project_name: str = "default",
        **kwargs: Any,
    ) -> requests.Response:
        return self.get(
            self.volc_asset_path_template.format(asset_id=asset_id),
            params={"ProjectName": project_name},
            **kwargs,
        )

    def update_volc_asset(
        self,
        asset_id: str,
        payload: dict[str, Any],
        **kwargs: Any,
    ) -> requests.Response:
        return self.post(
            self.volc_asset_update_path_template.format(asset_id=asset_id),
            json=payload,
            **kwargs,
        )

    def delete_volc_asset(
        self,
        asset_id: str,
        payload: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> requests.Response:
        return self.post(
            self.volc_asset_delete_path_template.format(asset_id=asset_id),
            json=payload or {"ProjectName": "default"},
            **kwargs,
        )

    def create_volc_visual_validate_session(
        self,
        payload: dict[str, Any],
        **kwargs: Any,
    ) -> requests.Response:
        return self.post(self.volc_visual_validate_sessions_path, json=payload, **kwargs)

    def get_volc_visual_validate_session(self, session_id: str, **kwargs: Any) -> requests.Response:
        return self.get(
            self.volc_visual_validate_session_path_template.format(session_id=session_id),
            **kwargs,
        )

    def get_volc_visual_validate_result(self, session_id: str, **kwargs: Any) -> requests.Response:
        return self.get(
            self.volc_visual_validate_result_path_template.format(session_id=session_id),
            **kwargs,
        )

    def trigger_volc_visual_validate_callback(
        self,
        session_id: str,
        byted_token: str,
        *,
        result_code: str = "10000",
        **kwargs: Any,
    ) -> requests.Response:
        return self.get(
            self.volc_visual_validate_callback_path,
            params={
                "platform_session": session_id,
                "bytedToken": byted_token,
                "resultCode": result_code,
            },
            _inherit_session_headers=False,
            **kwargs,
        )

    def create_media_generation(self, payload: dict[str, Any], **kwargs: Any) -> requests.Response:
        return self.post(self.media_generations_path, json=payload, **kwargs)

    def get_media_generation_task(self, task_id: str, **kwargs: Any) -> requests.Response:
        return self.get(self.media_task_path_template.format(task_id=task_id), **kwargs)
