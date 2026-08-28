from __future__ import annotations

from typing import Any

import requests

from common import BaseRequest, PollingPolicy


class MaterialLibraryRequest(BaseRequest):
    ark_asset_groups_path = "/v1/ark/assets/groups"
    ark_asset_group_path_template = "/v1/ark/assets/groups/{group_id}"
    ark_asset_group_delete_path_template = "/v1/ark/assets/groups/{group_id}/delete"
    ark_assets_path = "/v1/ark/assets"
    ark_asset_path_template = "/v1/ark/assets/{asset_id}"
    ark_asset_delete_path_template = "/v1/ark/assets/{asset_id}/delete"
    ark_media_generations_path = "/v1/ark/media/generations"
    ark_media_task_path_template = "/v1/ark/media/tasks/{task_id}"
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
    media_generations_path = "/v1/media/generations"
    media_task_path_template = "/v1/media/tasks/{task_id}"

    def create_ark_asset_group(self, payload: dict[str, Any], **kwargs: Any) -> requests.Response:
        return self.post(self.ark_asset_groups_path, json=payload, **kwargs)

    def get_ark_asset_group(self, group_id: str, **kwargs: Any) -> requests.Response:
        return self.get(
            self.ark_asset_group_path_template.format(group_id=group_id),
            **kwargs,
        )

    def delete_ark_asset_group(self, group_id: str, **kwargs: Any) -> requests.Response:
        return self.post(
            self.ark_asset_group_delete_path_template.format(group_id=group_id),
            json={},
            **kwargs,
        )

    def create_ark_asset(self, payload: dict[str, Any], **kwargs: Any) -> requests.Response:
        return self.post(self.ark_assets_path, json=payload, **kwargs)

    def get_ark_asset(self, asset_id: str, **kwargs: Any) -> requests.Response:
        return self.get(
            self.ark_asset_path_template.format(asset_id=asset_id),
            **kwargs,
        )

    def delete_ark_asset(self, asset_id: str, **kwargs: Any) -> requests.Response:
        return self.post(
            self.ark_asset_delete_path_template.format(asset_id=asset_id),
            json={},
            **kwargs,
        )

    def create_ark_media_generation(
        self,
        payload: dict[str, Any],
        **kwargs: Any,
    ) -> requests.Response:
        return self.post(self.ark_media_generations_path, json=payload, **kwargs)

    def get_ark_media_generation_task(self, task_id: str, **kwargs: Any) -> requests.Response:
        return self.get(
            self.ark_media_task_path_template.format(task_id=task_id),
            **kwargs,
        )

    def poll_ark_media_generation_task(
        self,
        task_id: str,
        *,
        poll_interval: float,
        poll_timeout: float,
        polling_policy: PollingPolicy,
    ) -> requests.Response:
        return self.poll_get(
            self.ark_media_task_path_template.format(task_id=task_id),
            poll_interval=poll_interval,
            poll_timeout=poll_timeout,
            polling_policy=polling_policy,
        )

    def create_volc_asset_group(self, payload: dict[str, Any], **kwargs: Any) -> requests.Response:
        return self.post(self.volc_asset_groups_path, json=payload, **kwargs)

    def list_volc_asset_groups(self, payload: dict[str, Any], **kwargs: Any) -> requests.Response:
        return self.post(self.volc_asset_groups_list_path, json=payload, **kwargs)

    def get_volc_asset_group(
        self,
        group_id: str,
        **kwargs: Any,
    ) -> requests.Response:
        return self.get(
            self.volc_asset_group_path_template.format(group_id=group_id),
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
        **kwargs: Any,
    ) -> requests.Response:
        return self.post(
            self.volc_asset_group_delete_path_template.format(group_id=group_id),
            json={},
            **kwargs,
        )

    def create_volc_asset(self, payload: dict[str, Any], **kwargs: Any) -> requests.Response:
        return self.post(self.volc_assets_path, json=payload, **kwargs)

    def list_volc_assets(self, payload: dict[str, Any], **kwargs: Any) -> requests.Response:
        return self.post(self.volc_assets_list_path, json=payload, **kwargs)

    def get_volc_asset(
        self,
        asset_id: str,
        **kwargs: Any,
    ) -> requests.Response:
        return self.get(
            self.volc_asset_path_template.format(asset_id=asset_id),
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
        **kwargs: Any,
    ) -> requests.Response:
        return self.post(
            self.volc_asset_delete_path_template.format(asset_id=asset_id),
            json={},
            **kwargs,
        )

    def create_media_generation(self, payload: dict[str, Any], **kwargs: Any) -> requests.Response:
        return self.post(self.media_generations_path, json=payload, **kwargs)

    def get_media_generation_task(self, task_id: str, **kwargs: Any) -> requests.Response:
        return self.get(self.media_task_path_template.format(task_id=task_id), **kwargs)
