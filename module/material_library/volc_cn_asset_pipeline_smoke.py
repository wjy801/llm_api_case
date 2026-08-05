#!/usr/bin/env python3
"""Domestic Volcengine asset-library and video-generation pipeline smoke CLI."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
import json
import sys
from typing import Any, Iterator

import requests

from config import Settings
from module.material_library.assertions import MaterialLibraryAssertions
from module.material_library.request import MaterialLibraryRequest
from module.material_library.task import MaterialLibraryTask


BASE_URL = "https://pre.juhemoxing.com"
API_KEY = "sk-mxai-d78130c737fe8caa201a6246cb2f45b7d0ef8e4a002f97745f49fbc529672fcd"
MODEL_ID = "doubao-seedance-2-0-fast-260128"
DEFAULT_SAMPLE_IMAGE_URL = "https://ark-project.tos-cn-beijing.volces.com/doc_image/r2v_tea_pic1.jpg"
DEFAULT_LIVENESS_IMAGE_URL = "https://ark-project.tos-cn-beijing.volces.com/doc_image/r2v_tea_pic2.jpg"
POLL_INTERVAL_SEC = 3
INSECURE_SKIP_VERIFY = False
VERBOSE_LOG = True


@dataclass(frozen=True)
class _MaterialRuntime:
    request: MaterialLibraryRequest
    task: MaterialLibraryTask
    assertions: MaterialLibraryAssertions

    def close(self) -> None:
        self.request.close()


def log_step(step_name: str, detail: str = ""):
    print("\n=======================================================")
    print(f"📌 {step_name}")
    if detail:
        print(f"   {detail}")
    print("=======================================================")


def log_info(message: str):
    print(f"ℹ️  {message}")


def log_success(message: str):
    print(f"✅ {message}")


def log_warning(message: str):
    print(f"⚠️  {message}")


def log_error(message: str):
    print(f"❌ {message}")


def _create_runtime(base_url: str, api_key: str, *, timeout: float = 30) -> _MaterialRuntime:
    config = Settings(
        timeout=timeout,
        generate_allure_report=False,
        generate_history_report=False,
        history_report_keep_limit=1,
        base_url=base_url.rstrip("/"),
        api_key=api_key,
        environment_name="china",
    )
    request_client = MaterialLibraryRequest(config=config, middlewares=[])
    request_client.session.verify = not INSECURE_SKIP_VERIFY
    if not api_key:
        request_client.remove_header("Authorization")
    return _MaterialRuntime(
        request=request_client,
        task=MaterialLibraryTask(),
        assertions=MaterialLibraryAssertions(),
    )


@contextmanager
def _runtime_scope(
    base_url: str,
    api_key: str,
    runtime: _MaterialRuntime | None,
) -> Iterator[_MaterialRuntime]:
    owned = runtime is None
    active = runtime or _create_runtime(base_url, api_key)
    try:
        yield active
    finally:
        if owned:
            active.close()


def _response_json(response: requests.Response) -> dict[str, Any]:
    try:
        value = response.json()
    except ValueError:
        return {"error": {"message": response.text}}
    return value if isinstance(value, dict) else {"result": value}


def _format_response(response: requests.Response) -> str:
    return json.dumps(_response_json(response), ensure_ascii=False)


def _log_http_response(response: requests.Response) -> None:
    if not VERBOSE_LOG:
        return
    request = getattr(response, "request", None)
    method = getattr(request, "method", "HTTP")
    url = getattr(request, "url", "")
    print(f"🌐 [HTTP Request] {method} {url}".rstrip())
    print(f"📩 [HTTP Response] Status {response.status_code}")
    print(f"   Response Body: {_format_response(response)}")


def _require_status(
    runtime: _MaterialRuntime,
    response: requests.Response,
    expected: int,
    operation: str,
) -> requests.Response:
    _log_http_response(response)
    try:
        return runtime.assertions.assert_status_code(response, expected)
    except AssertionError as exc:
        raise RuntimeError(
            f"Failed to {operation} (HTTP {response.status_code}): {_format_response(response)}"
        ) from exc


def make_request(
    endpoint: str,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    payload: dict[str, Any] | None = None,
    base_url: str = BASE_URL,
    api_key: str = API_KEY,
    timeout: int = 30,
) -> tuple[int, dict[str, Any]]:
    """Backward-compatible generic helper delegated to MaterialLibraryRequest."""
    try:
        with _runtime_scope(base_url, api_key, None) as runtime:
            response = runtime.request.request(
                method,
                endpoint,
                headers=headers,
                json=payload,
                timeout=timeout,
                _attach_log=False,
            )
            _log_http_response(response)
            return response.status_code, _response_json(response)
    except requests.RequestException as exc:
        return 500, {"error": {"message": f"Network Error: {exc}"}}
    except Exception as exc:
        return 500, {"error": {"message": f"Unexpected Error: {exc}"}}


def create_asset_group(
    name: str = "测试普通素材库",
    description: str = "包含AIGC角色素材",
    group_type: str = "AIGC",
    project_name: str = "default",
    base_url: str = BASE_URL,
    api_key: str = API_KEY,
    *,
    _runtime: _MaterialRuntime | None = None,
) -> str:
    with _runtime_scope(base_url, api_key, _runtime) as runtime:
        response = runtime.task.create_asset_group(
            runtime.request,
            name=name,
            description=description,
            group_type=group_type,
            project_name=project_name,
        )
        _require_status(runtime, response, 200, "create asset group")
        try:
            return runtime.task.extract_group_id(response)
        except AssertionError as exc:
            raise RuntimeError(f"Response missing Group Result.Id: {_response_json(response)}") from exc


def upload_asset(
    group_id: str,
    image_url: str,
    name: str = "测试素材图片",
    asset_type: str = "Image",
    project_name: str = "default",
    base_url: str = BASE_URL,
    api_key: str = API_KEY,
    return_upstream: bool = False,
    *,
    _runtime: _MaterialRuntime | None = None,
) -> Any:
    with _runtime_scope(base_url, api_key, _runtime) as runtime:
        response = runtime.task.upload_image_asset(
            runtime.request,
            group_id,
            image_url=image_url,
            name=name,
            asset_type=asset_type,
            project_name=project_name,
        )
        _require_status(runtime, response, 200, "upload asset")
        try:
            asset_id = runtime.task.extract_asset_id(response)
            upstream_id = runtime.task.extract_upstream_asset_id(response)
        except AssertionError as exc:
            raise RuntimeError(f"Response missing Asset Result.Id: {_response_json(response)}") from exc
        return (asset_id, upstream_id) if return_upstream else asset_id


def get_asset_details(
    asset_id: str,
    project_name: str = "default",
    base_url: str = BASE_URL,
    api_key: str = API_KEY,
    *,
    _runtime: _MaterialRuntime | None = None,
) -> dict[str, Any]:
    with _runtime_scope(base_url, api_key, _runtime) as runtime:
        response = runtime.task.get_asset(runtime.request, asset_id, project_name=project_name)
        _require_status(runtime, response, 200, "get asset details")
        return runtime.assertions.get_required_result_object(response)


def poll_asset_until_active(
    asset_id: str,
    project_name: str = "default",
    interval_sec: int = POLL_INTERVAL_SEC,
    base_url: str = BASE_URL,
    api_key: str = API_KEY,
    *,
    _runtime: _MaterialRuntime | None = None,
) -> dict[str, Any]:
    with _runtime_scope(base_url, api_key, _runtime) as runtime:
        try:
            response = runtime.task.poll_asset_until_active(
                runtime.request,
                asset_id,
                project_name=project_name,
                poll_interval=interval_sec,
                poll_timeout=None,
            )
            runtime.assertions.assert_asset_status(response, "Active")
            return runtime.assertions.get_required_result_object(response)
        except AssertionError as exc:
            raise RuntimeError(f"Asset processing failed: {exc}") from exc


def create_visual_validate_session(
    project_name: str = "default",
    base_url: str = BASE_URL,
    api_key: str = API_KEY,
    *,
    _runtime: _MaterialRuntime | None = None,
) -> tuple[str, str]:
    with _runtime_scope(base_url, api_key, _runtime) as runtime:
        response = runtime.task.create_visual_validate_session(
            runtime.request,
            project_name=project_name,
        )
        _require_status(runtime, response, 200, "create visual validate session")
        try:
            runtime.assertions.assert_visual_validate_session_created(response)
            return (
                runtime.task.extract_visual_validate_session_id(response),
                runtime.task.extract_visual_validate_h5_link(response),
            )
        except AssertionError as exc:
            raise RuntimeError(f"Session response missing SessionId or H5Link: {_response_json(response)}") from exc


def get_visual_validate_session(
    session_id: str,
    base_url: str = BASE_URL,
    api_key: str = API_KEY,
    *,
    _runtime: _MaterialRuntime | None = None,
) -> dict[str, Any]:
    with _runtime_scope(base_url, api_key, _runtime) as runtime:
        response = runtime.task.get_visual_validate_session(runtime.request, session_id)
        _require_status(runtime, response, 200, "get visual validate session")
        return runtime.assertions.get_required_result_object(response)


def get_visual_validate_result(
    session_id: str,
    base_url: str = BASE_URL,
    api_key: str = API_KEY,
    *,
    _runtime: _MaterialRuntime | None = None,
) -> str:
    with _runtime_scope(base_url, api_key, _runtime) as runtime:
        response = runtime.task.get_visual_validate_result(runtime.request, session_id)
        _require_status(runtime, response, 200, "get visual validate result")
        try:
            runtime.assertions.assert_visual_validate_result_group_id(response)
            return runtime.task.extract_visual_validate_group_id(response)
        except AssertionError as exc:
            raise RuntimeError(f"Visual validate result missing GroupId: {_response_json(response)}") from exc


def submit_video_generation_task(
    asset_id: str,
    model: str = MODEL_ID,
    prompt: str = "图片1中的人物自然转身并看向镜头，阳光明媚，电影质感",
    duration: int = 5,
    resolution: str = "720p",
    ratio: str = "16:9",
    base_url: str = BASE_URL,
    api_key: str = API_KEY,
    *,
    _runtime: _MaterialRuntime | None = None,
) -> str:
    with _runtime_scope(base_url, api_key, _runtime) as runtime:
        response = runtime.task.create_asset_video_generation(
            runtime.request,
            asset_id,
            model=model,
            prompt=prompt,
            duration=duration,
            resolution=resolution,
            ratio=ratio,
            reference_role="first_frame",
        )
        _log_http_response(response)
        try:
            runtime.assertions.assert_media_generation_submit_succeeded(response)
            return runtime.task.extract_media_task_id(response)
        except AssertionError as exc:
            raise RuntimeError(
                f"Failed to submit video generation task (HTTP {response.status_code}): {_format_response(response)}"
            ) from exc


def poll_video_task_until_completion(
    task_id: str,
    interval_sec: int = POLL_INTERVAL_SEC,
    base_url: str = BASE_URL,
    api_key: str = API_KEY,
    *,
    _runtime: _MaterialRuntime | None = None,
) -> str:
    with _runtime_scope(base_url, api_key, _runtime) as runtime:
        try:
            response = runtime.task.poll_media_generation_until_finished(
                runtime.request,
                task_id,
                poll_interval=interval_sec,
                poll_timeout=None,
            )
            runtime.assertions.assert_media_task_has_video_url(response)
            video_url = runtime.task.extract_media_video_url(response)
            log_success(f"已成功获取生成的视频链接: {video_url}")
            return video_url
        except AssertionError as exc:
            raise RuntimeError(f"Video task {task_id} failed: {exc}") from exc


def run_normal_asset_flow(
    image_url: str = DEFAULT_SAMPLE_IMAGE_URL,
    model: str = MODEL_ID,
    base_url: str = BASE_URL,
    api_key: str = API_KEY,
    *,
    _runtime: _MaterialRuntime | None = None,
) -> dict[str, Any]:
    with _runtime_scope(base_url, api_key, _runtime) as runtime:
        print("\n=======================================================")
        print("🚀 [流程 1/2] 运行普通素材库 (AIGC Group) 全链路测试")
        print("=======================================================")
        log_step("Step 1/5: 创建普通素材组 (GroupType=AIGC)")
        group_id = create_asset_group(
            name="自动化测试-普通素材组",
            description="用于 Seedance 2.0 视频生成测试",
            group_type="AIGC",
            base_url=base_url,
            api_key=api_key,
            _runtime=runtime,
        )
        log_success(f"普通素材组创建成功! Platform Group ID = {group_id}")

        log_step("Step 2/5: 上传参考图片素材", f"GroupId={group_id}, ImageURL={image_url}")
        asset_id, upstream_asset_id = upload_asset(
            group_id,
            image_url,
            name="普通参考图片素材",
            base_url=base_url,
            api_key=api_key,
            return_upstream=True,
            _runtime=runtime,
        )
        log_success(
            f"素材上传提交成功! Platform Asset ID = {asset_id}, Upstream Asset ID = {upstream_asset_id}"
        )

        log_step("Step 3/5: 轮询素材解析状态", f"AssetID={asset_id}")
        asset_info = poll_asset_until_active(
            asset_id,
            base_url=base_url,
            api_key=api_key,
            _runtime=runtime,
        )
        log_success(f"素材处理完毕并已激活! Status = {asset_info.get('Status')}")

        log_step("Step 4/5: 提交视频生成任务", f"Model={model}, AssetURL=asset://{asset_id}")
        task_id = submit_video_generation_task(
            asset_id,
            model=model,
            prompt="参考图片1的人物在秋天公园的林荫道上自然行走，阳光洒落，电影画质",
            base_url=base_url,
            api_key=api_key,
            _runtime=runtime,
        )
        log_success(f"视频任务提交成功! Task ID = {task_id}")

        log_step(
            "Step 5/5: 持续无限轮询视频生成任务结果 (直到 SUCCESS 或 FAILED 终态)",
            f"TaskID={task_id}",
        )
        video_url = poll_video_task_until_completion(
            task_id,
            base_url=base_url,
            api_key=api_key,
            _runtime=runtime,
        )
        log_success("视频生成成功! 最终生成的视频下载/播放链接为:")
        print(f"🎬 {video_url}")
        return {"group_id": group_id, "asset_id": asset_id, "task_id": task_id, "video_url": video_url}


def run_liveness_asset_flow(
    image_url: str = DEFAULT_LIVENESS_IMAGE_URL,
    model: str = MODEL_ID,
    base_url: str = BASE_URL,
    api_key: str = API_KEY,
    auto_mock_callback: bool = False,
    mock_byted_token: str = "",
    *,
    _runtime: _MaterialRuntime | None = None,
) -> dict[str, Any]:
    with _runtime_scope(base_url, api_key, _runtime) as runtime:
        print("\n=======================================================")
        print("🚀 [流程 2/2] 运行真人素材库 (LivenessFace Group) 全链路测试")
        print("=======================================================")
        log_step("Step 1/7: 创建真人活体认证会话 (Visual Validate Session)")
        session_id, h5_link = create_visual_validate_session(
            base_url=base_url,
            api_key=api_key,
            _runtime=runtime,
        )
        log_success(f"认证会话创建成功! Session ID = {session_id}")
        print("🔗 请在手机/浏览器中打开 H5 认证链接完成人脸扫脸授权:")
        print(f"   {h5_link}\n")

        if auto_mock_callback and mock_byted_token:
            log_info("[Mock Action] 模拟触发生物认证回调 (Callback)...")
            callback = runtime.task.trigger_visual_validate_callback(
                runtime.request,
                session_id,
                mock_byted_token,
            )
            _log_http_response(callback)
            log_info(
                f"[Mock Action] Callback Status: {callback.status_code}, Response: {_response_json(callback)}"
            )

        log_step("Step 2/7: 轮询真人认证完成状态", f"SessionID={session_id}")
        session_response = runtime.task.poll_visual_validate_session_until_ready(
            runtime.request,
            session_id,
            poll_interval=POLL_INTERVAL_SEC,
            poll_timeout=None,
        )
        runtime.assertions.assert_visual_validate_session_status(
            session_response,
            {"callback_received", "group_ready"},
        )

        log_step("Step 3/7: 获取真人认证产生的素材组 ID", f"SessionID={session_id}")
        group_id = get_visual_validate_result(
            session_id,
            base_url=base_url,
            api_key=api_key,
            _runtime=runtime,
        )
        log_success(f"获取成功! 真人素材组 Liveness Group ID = {group_id}")

        log_step("Step 4/7: 在真人素材组中上传真人参考图片", f"GroupId={group_id}, ImageURL={image_url}")
        asset_id, upstream_asset_id = upload_asset(
            group_id,
            image_url,
            name="真人参考图片素材",
            base_url=base_url,
            api_key=api_key,
            return_upstream=True,
            _runtime=runtime,
        )
        log_success(
            f"真人素材上传提交成功! Platform Asset ID = {asset_id}, Upstream Asset ID = {upstream_asset_id}"
        )

        log_step("Step 5/7: 轮询真人素材处理与人脸一致性比对状态", f"AssetID={asset_id}")
        asset_info = poll_asset_until_active(
            asset_id,
            base_url=base_url,
            api_key=api_key,
            _runtime=runtime,
        )
        log_success(f"真人素材一致性校验通过并就绪! Status = {asset_info.get('Status')}")

        log_step("Step 6/7: 提交真人视频生成任务", f"Model={model}, AssetURL=asset://{asset_id}")
        task_id = submit_video_generation_task(
            asset_id,
            model=model,
            prompt="参考真人图片1的人物在镜头前自然微笑并招手，温暖光线，电影画质",
            base_url=base_url,
            api_key=api_key,
            _runtime=runtime,
        )
        log_success(f"真人视频任务提交成功! Task ID = {task_id}")

        log_step(
            "Step 7/7: 持续无限轮询真人视频生成任务结果 (直到 SUCCESS 或 FAILED 终态)",
            f"TaskID={task_id}",
        )
        video_url = poll_video_task_until_completion(
            task_id,
            base_url=base_url,
            api_key=api_key,
            _runtime=runtime,
        )
        log_success("真人视频生成成功! 最终生成的视频下载/播放链接为:")
        print(f"🎬 {video_url}")
        return {
            "session_id": session_id,
            "group_id": group_id,
            "asset_id": asset_id,
            "task_id": task_id,
            "video_url": video_url,
        }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="国内官key素材库与视频生成全链路测试脚本")
    parser.add_argument("--base-url", default=BASE_URL, help=f"API 基础 URL，默认 {BASE_URL}")
    parser.add_argument("--api-key", default=API_KEY, help="平台 API Key (Bearer Token)")
    parser.add_argument("--model", default=MODEL_ID, help=f"Seedance 2.0 模型 ID，默认 {MODEL_ID}")
    parser.add_argument(
        "--flow",
        choices=["normal", "liveness", "all"],
        default="all",
        help="选择测试流程: normal(普通素材库), liveness(真人素材库), all(全部测试)",
    )
    parser.add_argument("--sample-image-url", default=DEFAULT_SAMPLE_IMAGE_URL, help="普通素材测试图片 URL")
    parser.add_argument("--liveness-image-url", default=DEFAULT_LIVENESS_IMAGE_URL, help="真人素材测试图片 URL")
    parser.add_argument("--auto-mock-callback", action="store_true", help="是否在测试模式下自动模拟触发 H5 认证回调")
    parser.add_argument("--mock-byted-token", default="", help="模拟回调所需的 bytedToken")
    parser.add_argument("--quiet", action="store_true", help="关闭详细 HTTP 请求/响应 JSON 日志")
    parser.add_argument("--insecure", action="store_true", help="跳过 SSL 证书校验")
    return parser


def main(argv: list[str] | None = None):
    args = build_parser().parse_args(argv)
    global VERBOSE_LOG, INSECURE_SKIP_VERIFY
    VERBOSE_LOG = not args.quiet
    INSECURE_SKIP_VERIFY = bool(args.insecure)

    base_url = args.base_url or BASE_URL
    api_key = args.api_key or API_KEY
    model_id = args.model or MODEL_ID
    if not api_key or api_key == "sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx":
        log_error("未提供 --api-key 或未在脚本顶部设置有效的 API_KEY！")
        raise SystemExit(1)

    print("=======================================================")
    print("🚀 国内官key素材库 API 与视频生成全链路联调测试")
    print(f"   Base URL: {base_url}")
    print(f"   Model:    {model_id}")
    print(f"   Flow:     {args.flow}")
    print("=======================================================")
    results: dict[str, Any] = {}
    runtime = _create_runtime(base_url, api_key)
    try:
        if args.flow in {"normal", "all"}:
            results["normal_flow"] = run_normal_asset_flow(
                image_url=args.sample_image_url,
                model=model_id,
                base_url=base_url,
                api_key=api_key,
                _runtime=runtime,
            )
        if args.flow in {"liveness", "all"}:
            results["liveness_flow"] = run_liveness_asset_flow(
                image_url=args.liveness_image_url,
                model=model_id,
                base_url=base_url,
                api_key=api_key,
                auto_mock_callback=args.auto_mock_callback,
                mock_byted_token=args.mock_byted_token,
                _runtime=runtime,
            )
        print("\n=======================================================")
        print("🎉 所有测试流程执行完毕，测试成功！")
        print("=======================================================")
        print(json.dumps(results, indent=2, ensure_ascii=False))
    except Exception as exc:
        log_error(f"测试链路出现异常: {exc}")
        raise SystemExit(1) from exc
    finally:
        runtime.close()


if __name__ == "__main__":
    main()
