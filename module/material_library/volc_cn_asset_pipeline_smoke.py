#!/usr/bin/env python3
"""
Domestic Volcengine Asset Library & Video Generation Full Pipeline Smoke Test.

Usage:
  1. Edit BASE_URL, API_KEY, and MODEL_ID directly below.
  2. Run: python3 one-api/scripts/ops/volc_cn_asset_pipeline_smoke.py
"""

from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Dict, Optional, Tuple

# ==============================================================================
# 配置文件区 (请直接在此填写您的配置)
# ==============================================================================
BASE_URL = "https://pre.juhemoxing.com"  # 目标服务地址，例如: https://pre.juhemoxing.com 或 http://localhost:3001
API_KEY = "REMOVED_CREDENTIAL"  # 您的平台 API Key (Bearer Token)
MODEL_ID = "doubao-seedance-2-0-fast-260128"  # 可选: doubao-seedance-2-0-fast-260128 或 doubao-seedance-2-0-mini-260615

# 测试参考图片 URL
DEFAULT_SAMPLE_IMAGE_URL = (
    "https://ark-project.tos-cn-beijing.volces.com/doc_image/r2v_tea_pic1.jpg"
)
DEFAULT_LIVENESS_IMAGE_URL = (
    "https://ark-project.tos-cn-beijing.volces.com/doc_image/r2v_tea_pic2.jpg"
)

POLL_INTERVAL_SEC = 3  # 素材与视频任务轮询间隔 (秒)
INSECURE_SKIP_VERIFY = False  # 如果是自签名 HTTPS 证书可设为 True
VERBOSE_LOG = True  # 是否详细打印每个步骤的 HTTP 请求与响应 JSON 详情
# ==============================================================================


def log_step(step_name: str, detail: str = ""):
    print(f"\n=======================================================")
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


def make_request(
    endpoint: str,
    method: str = "GET",
    headers: Optional[Dict[str, str]] = None,
    payload: Optional[Dict[str, Any]] = None,
    base_url: str = BASE_URL,
    api_key: str = API_KEY,
    timeout: int = 30,
) -> Tuple[int, Dict[str, Any]]:
    """Helper to send HTTP request and parse JSON response with complete step logging."""
    url = f"{base_url.rstrip('/')}{endpoint}" if endpoint.startswith("/") else f"{base_url.rstrip('/')}/{endpoint}"
    req_headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if api_key:
        req_headers["Authorization"] = f"Bearer {api_key}"
    if headers:
        req_headers.update(headers)

    data_bytes = None
    if payload is not None:
        data_bytes = json.dumps(payload).encode("utf-8")

    if VERBOSE_LOG:
        print(f"🌐 [HTTP Request] {method} {url}")
        if payload is not None:
            print(f"   Payload: {json.dumps(payload, ensure_ascii=False)}")

    req = urllib.request.Request(url, data=data_bytes, headers=req_headers, method=method)

    ctx = None
    if INSECURE_SKIP_VERIFY or url.startswith("https://localhost") or url.startswith("https://127.0.0.1"):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as response:
            status_code = response.getcode()
            body_text = response.read().decode("utf-8")
            res_json = json.loads(body_text) if body_text else {}
            if VERBOSE_LOG:
                print(f"📩 [HTTP Response] Status {status_code}")
                print(f"   Response Body: {json.dumps(res_json, ensure_ascii=False)}")
            return status_code, res_json
    except urllib.error.HTTPError as e:
        status_code = e.code
        body_text = e.read().decode("utf-8")
        try:
            res_json = json.loads(body_text)
        except Exception:
            res_json = {"error": {"message": body_text or str(e)}}
        if VERBOSE_LOG:
            print(f"📩 [HTTP Response Error] Status {status_code}")
            print(f"   Response Body: {json.dumps(res_json, ensure_ascii=False)}")
        return status_code, res_json
    except urllib.error.URLError as e:
        res_json = {"error": {"message": f"Network Error: {e.reason}"}}
        if VERBOSE_LOG:
            print(f"📩 [HTTP Request Exception] Network Error: {e.reason}")
        return 500, res_json
    except Exception as e:
        res_json = {"error": {"message": f"Unexpected Error: {str(e)}"}}
        if VERBOSE_LOG:
            print(f"📩 [HTTP Request Exception] Unexpected Error: {str(e)}")
        return 500, res_json


# -----------------------------------------------------------------------------
# Asset Library Helper APIs
# -----------------------------------------------------------------------------

def create_asset_group(
    name: str = "测试普通素材库",
    description: str = "包含AIGC角色素材",
    group_type: str = "AIGC",
    project_name: str = "default",
    base_url: str = BASE_URL,
    api_key: str = API_KEY,
) -> str:
    """POST /v1/volc/assets/groups"""
    payload = {
        "Name": name,
        "Description": description,
        "GroupType": group_type,
        "ProjectName": project_name,
    }
    status, res = make_request("/v1/volc/assets/groups", method="POST", payload=payload, base_url=base_url, api_key=api_key)
    if status != 200:
        raise RuntimeError(f"Failed to create asset group (HTTP {status}): {json.dumps(res, ensure_ascii=False)}")
    group_id = res.get("Result", {}).get("Id")
    if not group_id:
        raise RuntimeError(f"Response missing Group Result.Id: {res}")
    return group_id


def upload_asset(
    group_id: str,
    image_url: str,
    name: str = "测试素材图片",
    asset_type: str = "Image",
    project_name: str = "default",
    base_url: str = BASE_URL,
    api_key: str = API_KEY,
    return_upstream: bool = False,
) -> Any:
    """POST /v1/volc/assets"""
    payload = {
        "GroupId": group_id,
        "URL": image_url,
        "Name": name,
        "AssetType": asset_type,
        "ProjectName": project_name,
    }
    status, res = make_request("/v1/volc/assets", method="POST", payload=payload, base_url=base_url, api_key=api_key)
    if status != 200:
        raise RuntimeError(f"Failed to upload asset (HTTP {status}): {json.dumps(res, ensure_ascii=False)}")
    result = res.get("Result", {})
    asset_id = result.get("Id")
    upstream_asset_id = result.get("UpstreamId") or asset_id
    if not asset_id:
        raise RuntimeError(f"Response missing Asset Result.Id: {res}")
    return (asset_id, upstream_asset_id) if return_upstream else asset_id


def get_asset_details(
    asset_id: str,
    project_name: str = "default",
    base_url: str = BASE_URL,
    api_key: str = API_KEY,
) -> Dict[str, Any]:
    """GET /v1/volc/assets/{asset_id}"""
    endpoint = f"/v1/volc/assets/{asset_id}?ProjectName={project_name}"
    status, res = make_request(endpoint, method="GET", base_url=base_url, api_key=api_key)
    if status != 200:
        raise RuntimeError(f"Failed to get asset details (HTTP {status}): {json.dumps(res, ensure_ascii=False)}")
    return res.get("Result", {})


def poll_asset_until_active(
    asset_id: str,
    project_name: str = "default",
    interval_sec: int = POLL_INTERVAL_SEC,
    base_url: str = BASE_URL,
    api_key: str = API_KEY,
) -> Dict[str, Any]:
    """Poll GET /v1/volc/assets/{asset_id} continuously until Status == Active."""
    start_time = time.time()
    poll_count = 0
    while True:
        poll_count += 1
        elapsed = int(time.time() - start_time)
        details = get_asset_details(asset_id, project_name=project_name, base_url=base_url, api_key=api_key)
        asset_status = details.get("Status", "Processing")
        log_info(f"[轮询第 {poll_count} 次 | 素材 {asset_id}] 状态: {asset_status} (已耗时: {elapsed}s)")
        if asset_status == "Active":
            return details
        elif asset_status == "Failed":
            err_info = details.get("Error", {})
            raise RuntimeError(f"Asset processing failed: Code={err_info.get('Code')} Message={err_info.get('Message')}")
        time.sleep(interval_sec)


def create_visual_validate_session(
    project_name: str = "default",
    base_url: str = BASE_URL,
    api_key: str = API_KEY,
) -> Tuple[str, str]:
    """POST /v1/volc/assets/visual-validate/sessions -> (session_id, h5_link)"""
    payload = {"ProjectName": project_name}
    status, res = make_request("/v1/volc/assets/visual-validate/sessions", method="POST", payload=payload, base_url=base_url, api_key=api_key)
    if status != 200:
        raise RuntimeError(f"Failed to create visual validate session (HTTP {status}): {json.dumps(res, ensure_ascii=False)}")
    result = res.get("Result", {})
    session_id = result.get("SessionId")
    h5_link = result.get("H5Link")
    if not session_id or not h5_link:
        raise RuntimeError(f"Session response missing SessionId or H5Link: {res}")
    return session_id, h5_link


def get_visual_validate_session(
    session_id: str,
    base_url: str = BASE_URL,
    api_key: str = API_KEY,
) -> Dict[str, Any]:
    """GET /v1/volc/assets/visual-validate/sessions/{session_id}"""
    endpoint = f"/v1/volc/assets/visual-validate/sessions/{session_id}"
    status, res = make_request(endpoint, method="GET", base_url=base_url, api_key=api_key)
    if status != 200:
        raise RuntimeError(f"Failed to get visual validate session (HTTP {status}): {json.dumps(res, ensure_ascii=False)}")
    return res.get("Result", {})


def get_visual_validate_result(
    session_id: str,
    base_url: str = BASE_URL,
    api_key: str = API_KEY,
) -> str:
    """GET /v1/volc/assets/visual-validate/results/{session_id} -> liveness group_id"""
    endpoint = f"/v1/volc/assets/visual-validate/results/{session_id}"
    status, res = make_request(endpoint, method="GET", base_url=base_url, api_key=api_key)
    if status != 200:
        raise RuntimeError(f"Failed to get visual validate result (HTTP {status}): {json.dumps(res, ensure_ascii=False)}")
    group_id = res.get("Result", {}).get("GroupId")
    if not group_id:
        raise RuntimeError(f"Visual validate result missing GroupId: {res}")
    return group_id


# -----------------------------------------------------------------------------
# Video Generation & Polling APIs (无时间限制循环轮询)
# -----------------------------------------------------------------------------

def submit_video_generation_task(
    asset_id: str,
    model: str = MODEL_ID,
    prompt: str = "图片1中的人物自然转身并看向镜头，阳光明媚，电影质感",
    duration: int = 5,
    resolution: str = "720p",
    ratio: str = "16:9",
    base_url: str = BASE_URL,
    api_key: str = API_KEY,
) -> str:
    """POST /v1/media/generations using asset://{asset_id}"""
    payload = {
        "model": model,
        "content": [
            {"type": "text", "text": prompt},
            {
                "type": "image_url",
                "role": "first_frame",
                "image_url": {"url": f"asset://{asset_id}"},
            },
        ],
        "duration": duration,
        "resolution": resolution,
        "ratio": ratio,
        "generate_audio": True,
        "watermark": False,
    }
    status, res = make_request("/v1/media/generations", method="POST", payload=payload, base_url=base_url, api_key=api_key)
    if status not in {200, 201, 202}:
        raise RuntimeError(f"Failed to submit video generation task (HTTP {status}): {json.dumps(res, ensure_ascii=False)}")

    task_id = res.get("id") or res.get("task_id") or res.get("data", {}).get("id")
    if not task_id:
        raise RuntimeError(f"Video task submission response missing task ID: {res}")
    return str(task_id)


def poll_video_task_until_completion(
    task_id: str,
    interval_sec: int = POLL_INTERVAL_SEC,
    base_url: str = BASE_URL,
    api_key: str = API_KEY,
) -> str:
    """
    无时间限制循环轮询 GET /v1/media/tasks/{task_id}，直到任务到达终态 (SUCCESS / SUCCEEDED 或 FAILED)。

    终态处理逻辑:
      - 成功终态 (SUCCESS / SUCCEEDED / COMPLETED / FINISHED): 提取并打印输出视频播放/下载 URL
      - 失败终态 (FAILED / FAILURE / REJECTED / ERROR): 抛出包含失败原因的异常并中断流程
      - 中间状态 (QUEUED / PENDING / PROCESSING / RUNNING 等): 打印日志并 sleep interval_sec 后继续下一次轮询
    """
    endpoint = f"/v1/media/tasks/{task_id}"
    start_time = time.time()
    poll_count = 0

    while True:
        poll_count += 1
        elapsed = int(time.time() - start_time)
        status_code, res = make_request(endpoint, method="GET", base_url=base_url, api_key=api_key)

        if status_code not in {200, 202}:
            log_warning(f"[视频任务 {task_id}] 轮询请求返回 HTTP {status_code}: {res}")
        else:
            raw_status = (
                res.get("status")
                or res.get("task_status")
                or res.get("state")
                or res.get("data", {}).get("status")
                or ""
            )
            task_status = str(raw_status).upper()
            log_info(f"[轮询第 {poll_count} 次 | 视频任务 {task_id}] 当前状态: {task_status or 'PROCESSING'} (已耗时: {elapsed}s)")

            # 1. 成功终态：提取视频 URL 并退出循环
            if task_status in {"SUCCESS", "SUCCEEDED", "COMPLETED", "FINISHED"}:
                log_success(f"🎉 视频生成任务处理完成! (总耗时: {elapsed}s)")

                # 优先解析 媒体任务协议 V1 标准字段: res["result"]
                result_obj = res.get("result") or res.get("data", {}).get("result")
                if isinstance(result_obj, dict):
                    if result_obj.get("primary_url"):
                        video_url = str(result_obj["primary_url"])
                        log_success(f"已成功获取生成的视频链接: {video_url}")
                        return video_url
                    urls = result_obj.get("urls")
                    if isinstance(urls, list) and len(urls) > 0 and urls[0]:
                        video_url = str(urls[0])
                        log_success(f"已成功获取生成的视频链接: {video_url}")
                        return video_url
                    if result_obj.get("video_url"):
                        video_url = str(result_obj["video_url"])
                        log_success(f"已成功获取生成的视频链接: {video_url}")
                        return video_url
                    if result_obj.get("url"):
                        video_url = str(result_obj["url"])
                        log_success(f"已成功获取生成的视频链接: {video_url}")
                        return video_url

                # 兼容旧版本/自定义字段: res["results"]
                results = res.get("results") or res.get("data", {}).get("results")
                if isinstance(results, list) and len(results) > 0:
                    first_item = results[0]
                    if isinstance(first_item, dict) and first_item.get("url"):
                        video_url = str(first_item["url"])
                        log_success(f"已成功获取生成的视频链接: {video_url}")
                        return video_url
                    if isinstance(first_item, str) and first_item:
                        log_success(f"已成功获取生成的视频链接: {first_item}")
                        return first_item

                # 兜底直接字段: res["video_url"] 或 res["url"]
                video_url = res.get("video_url") or res.get("url") or res.get("data", {}).get("video_url")
                if video_url:
                    video_url_str = str(video_url)
                    log_success(f"已成功获取生成的视频链接: {video_url_str}")
                    return video_url_str

                return json.dumps(res, ensure_ascii=False)

            # 2. 失败终态：抛出详细失败原因并中断流程
            elif task_status in {"FAILED", "FAILURE", "REJECTED", "ERROR"}:
                err_obj = (
                    res.get("error")
                    or res.get("error_message")
                    or res.get("fail_reason")
                    or res.get("data", {}).get("fail_reason")
                )
                err_msg = json.dumps(err_obj, ensure_ascii=False) if isinstance(err_obj, (dict, list)) else str(err_obj)
                log_error(f"❌ 视频生成任务终态判定为失败! 原因: {err_msg}")
                raise RuntimeError(f"Video task {task_id} failed with status {task_status}: {err_msg}")

            # 3. 中间状态 (QUEUED / PENDING / RUNNING / PROCESSING 等): 继续循环等待
            else:
                log_info(f"   ⏳ 任务尚在处理中，等待 {interval_sec}s 后进行下一次轮询...")

        time.sleep(interval_sec)


def run_normal_asset_flow(
    image_url: str = DEFAULT_SAMPLE_IMAGE_URL,
    model: str = MODEL_ID,
    base_url: str = BASE_URL,
    api_key: str = API_KEY,
) -> Dict[str, Any]:
    """Execute complete flow for Normal Asset Group (AIGC)."""
    print("\n=======================================================")
    print("🚀 [流程 1/2] 运行普通素材库 (AIGC Group) 全链路测试")
    print("=======================================================")

    # Step 1: Create Group
    log_step("Step 1/5: 创建普通素材组 (GroupType=AIGC)")
    group_id = create_asset_group(
        name="自动化测试-普通素材组",
        description="用于 Seedance 2.0 视频生成测试",
        group_type="AIGC",
        base_url=base_url,
        api_key=api_key,
    )
    log_success(f"普通素材组创建成功! Platform Group ID = {group_id}")

    # Step 2: Upload Asset
    log_step("Step 2/5: 上传参考图片素材", f"GroupId={group_id}, ImageURL={image_url}")
    asset_id, upstream_asset_id = upload_asset(
        group_id=group_id,
        image_url=image_url,
        name="普通参考图片素材",
        asset_type="Image",
        base_url=base_url,
        api_key=api_key,
        return_upstream=True,
    )
    log_success(f"素材上传提交成功! Platform Asset ID = {asset_id}, Upstream Asset ID = {upstream_asset_id}")

    # Step 3: Poll Asset Status until Active
    log_step("Step 3/5: 轮询素材解析状态", f"AssetID={asset_id}")
    asset_info = poll_asset_until_active(asset_id, base_url=base_url, api_key=api_key)
    log_success(f"素材处理完毕并已激活! Status = {asset_info.get('Status')}")

    # Step 4: Submit Video Task using asset://{asset_id}
    log_step("Step 4/5: 提交视频生成任务", f"Model={model}, AssetURL=asset://{asset_id}")
    task_id = submit_video_generation_task(
        asset_id=asset_id,
        model=model,
        prompt="参考图片1的人物在秋天公园的林荫道上自然行走，阳光洒落，电影画质",
        base_url=base_url,
        api_key=api_key,
    )
    log_success(f"视频任务提交成功! Task ID = {task_id}")

    # Step 5: Poll Video Task and Get Video URL until Success or Failure
    log_step("Step 5/5: 持续无限轮询视频生成任务结果 (直到 SUCCESS 或 FAILED 终态)", f"TaskID={task_id}")
    video_url = poll_video_task_until_completion(task_id, base_url=base_url, api_key=api_key)
    log_success(f"视频生成成功! 最终生成的视频下载/播放链接为:")
    print(f"🎬 {video_url}")

    return {
        "group_id": group_id,
        "asset_id": asset_id,
        "task_id": task_id,
        "video_url": video_url,
    }


def run_liveness_asset_flow(
    image_url: str = DEFAULT_LIVENESS_IMAGE_URL,
    model: str = MODEL_ID,
    base_url: str = BASE_URL,
    api_key: str = API_KEY,
    auto_mock_callback: bool = False,
    mock_byted_token: str = "",
) -> Dict[str, Any]:
    """Execute complete flow for Real-Person Asset Group (LivenessFace)."""
    print("\n=======================================================")
    print("🚀 [流程 2/2] 运行真人素材库 (LivenessFace Group) 全链路测试")
    print("=======================================================")

    # Step 1: Create Visual Validate Session
    log_step("Step 1/7: 创建真人活体认证会话 (Visual Validate Session)")
    session_id, h5_link = create_visual_validate_session(base_url=base_url, api_key=api_key)
    log_success(f"认证会话创建成功! Session ID = {session_id}")
    print(f"🔗 请在手机/浏览器中打开 H5 认证链接完成人脸扫脸授权:")
    print(f"   {h5_link}\n")

    # Optional: Mock callback for testing environments
    if auto_mock_callback and mock_byted_token:
        log_info("[Mock Action] 模拟触发生物认证回调 (Callback)...")
        callback_url = f"/v1/volc/assets/visual-validate/callback?platform_session={session_id}&bytedToken={mock_byted_token}&resultCode=10000"
        status, cb_res = make_request(callback_url, method="GET", base_url=base_url, api_key="")
        log_info(f"[Mock Action] Callback Status: {status}, Response: {cb_res}")

    # Step 2: Poll Session & Get Resulting Group ID
    log_step("Step 2/7: 轮询真人认证完成状态", f"SessionID={session_id}")
    start_time = time.time()
    session_ready = False
    while True:
        sess_info = get_visual_validate_session(session_id, base_url=base_url, api_key=api_key)
        status_name = sess_info.get("Status", "pending")
        log_info(f"[轮询认证会话 {session_id}] Status: {status_name} (已耗时: {int(time.time() - start_time)}s)")
        if status_name in {"callback_received", "group_ready"}:
            session_ready = True
            break
        elif status_name == "failed":
            raise RuntimeError(f"Visual validate session failed: {sess_info}")
        time.sleep(POLL_INTERVAL_SEC)

    # Step 3: Fetch Liveness Group ID
    log_step("Step 3/7: 获取真人认证产生的素材组 ID", f"SessionID={session_id}")
    group_id = get_visual_validate_result(session_id, base_url=base_url, api_key=api_key)
    log_success(f"获取成功! 真人素材组 Liveness Group ID = {group_id}")

    # Step 4: Upload Real-Person Asset to Liveness Group
    log_step("Step 4/7: 在真人素材组中上传真人参考图片", f"GroupId={group_id}, ImageURL={image_url}")
    asset_id, upstream_asset_id = upload_asset(
        group_id=group_id,
        image_url=image_url,
        name="真人参考图片素材",
        asset_type="Image",
        base_url=base_url,
        api_key=api_key,
        return_upstream=True,
    )
    log_success(f"真人素材上传提交成功! Platform Asset ID = {asset_id}, Upstream Asset ID = {upstream_asset_id}")

    # Step 5: Poll Asset Status until Active
    log_step("Step 5/7: 轮询真人素材处理与人脸一致性比对状态", f"AssetID={asset_id}")
    asset_info = poll_asset_until_active(asset_id, base_url=base_url, api_key=api_key)
    log_success(f"真人素材一致性校验通过并就绪! Status = {asset_info.get('Status')}")

    # Step 6: Submit Video Task using真人 asset://{asset_id}
    log_step("Step 6/7: 提交真人视频生成任务", f"Model={model}, AssetURL=asset://{asset_id}")
    task_id = submit_video_generation_task(
        asset_id=asset_id,
        model=model,
        prompt="参考真人图片1的人物在镜头前自然微笑并招手，温暖光线，电影画质",
        base_url=base_url,
        api_key=api_key,
    )
    log_success(f"真人视频任务提交成功! Task ID = {task_id}")

    # Step 7: Poll Video Task and Get Video URL until Success or Failure
    log_step("Step 7/7: 持续无限轮询真人视频生成任务结果 (直到 SUCCESS 或 FAILED 终态)", f"TaskID={task_id}")
    video_url = poll_video_task_until_completion(task_id, base_url=base_url, api_key=api_key)
    log_success(f"真人视频生成成功! 最终生成的视频下载/播放链接为:")
    print(f"🎬 {video_url}")

    return {
        "session_id": session_id,
        "group_id": group_id,
        "asset_id": asset_id,
        "task_id": task_id,
        "video_url": video_url,
    }


def main():
    parser = argparse.ArgumentParser(description="国内官key素材库与视频生成全链路测试脚本")
    parser.add_argument("--base-url", default=BASE_URL, help=f"API 基础 URL，默认 {BASE_URL}")
    parser.add_argument("--api-key", default=API_KEY, help="平台 API Key (Bearer Token)")
    parser.add_argument("--model", default=MODEL_ID, help=f"Seedance 2.0 模型 ID，默认 {MODEL_ID}")
    parser.add_argument("--flow", choices=["normal", "liveness", "all"], default="all", help="选择测试流程: normal(普通素材库), liveness(真人素材库), all(全部测试)")
    parser.add_argument("--sample-image-url", default=DEFAULT_SAMPLE_IMAGE_URL, help="普通素材测试图片 URL")
    parser.add_argument("--liveness-image-url", default=DEFAULT_LIVENESS_IMAGE_URL, help="真人素材测试图片 URL")
    parser.add_argument("--auto-mock-callback", action="store_true", help="是否在测试模式下自动模拟触发 H5 认证回调")
    parser.add_argument("--mock-byted-token", default="", help="模拟回调所需的 bytedToken")
    parser.add_argument("--quiet", action="store_true", help="关闭详细 HTTP 请求/响应 JSON 日志")
    parser.add_argument("--insecure", action="store_true", help="跳过 SSL 证书校验")

    args = parser.parse_args()

    global VERBOSE_LOG, INSECURE_SKIP_VERIFY
    if args.quiet:
        VERBOSE_LOG = False
    if args.insecure:
        INSECURE_SKIP_VERIFY = True

    base_url = args.base_url or BASE_URL
    api_key = args.api_key or API_KEY
    model_id = args.model or MODEL_ID

    if not api_key or api_key == "REMOVED_RESOURCE_ID":
        log_error("未提供 --api-key 或未在脚本顶部设置有效的 API_KEY！")
        sys.exit(1)

    print("=======================================================")
    print("🚀 国内官key素材库 API 与视频生成全链路联调测试")
    print(f"   Base URL: {base_url}")
    print(f"   Model:    {model_id}")
    print(f"   Flow:     {args.flow}")
    print("=======================================================")

    results = {}

    try:
        if args.flow in {"normal", "all"}:
            normal_res = run_normal_asset_flow(
                image_url=args.sample_image_url,
                model=model_id,
                base_url=base_url,
                api_key=api_key,
            )
            results["normal_flow"] = normal_res

        if args.flow in {"liveness", "all"}:
            liveness_res = run_liveness_asset_flow(
                image_url=args.liveness_image_url,
                model=model_id,
                base_url=base_url,
                api_key=api_key,
                auto_mock_callback=args.auto_mock_callback,
                mock_byted_token=args.mock_byted_token,
            )
            results["liveness_flow"] = liveness_res

        print("\n=======================================================")
        print("🎉 所有测试流程执行完毕，测试成功！")
        print("=======================================================")
        print(json.dumps(results, indent=2, ensure_ascii=False))

    except Exception as e:
        log_error(f"测试链路出现异常: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
