#!/usr/bin/env python3
"""
Domestic Volcengine Asset Library — Management API Smoke Test.

Covers the 7 management endpoints NOT exercised by volc_cn_asset_pipeline_smoke.py
(which only covers the happy-path create→upload→poll→video pipeline):

  1. POST /v1/volc/assets/groups/list         查询素材组列表
  2. GET  /v1/volc/assets/groups/{group_id}    查询素材组
  3. POST /v1/volc/assets/groups/{group_id}/update  更新素材组
  4. POST /v1/volc/assets/groups/{group_id}/delete  删除素材组 (+ 幂等 + 删除后 404)
  5. POST /v1/volc/assets/list                查询素材列表
  6. POST /v1/volc/assets/{asset_id}/update    更新素材
  7. POST /v1/volc/assets/{asset_id}/delete    删除素材 (+ 幂等 + 删除后 404)

Strategy: create a throwaway AIGC group + image asset, exercise every management
endpoint against them (including idempotent re-delete and post-delete 404 checks),
plus a negative case (deleting a non-empty group is rejected upstream), then clean up.

Usage:
  python3 one-api/scripts/ops/volc_cn_asset_mgmt_smoke.py
  python3 one-api/scripts/ops/volc_cn_asset_mgmt_smoke.py --base-url https://pre.juhemoxing.com --insecure
"""

from __future__ import annotations

import argparse
import os
import json
import ssl
import sys
import time
import urllib.error
import urllib.request
import uuid
from typing import Any, Dict, List, Optional, Tuple

# ==============================================================================
# 配置区
# ==============================================================================
BASE_URL = "https://pre.juhemoxing.com"  # 本地开发服务器；预发用 https://pre.juhemoxing.com --insecure
API_KEY = os.getenv("CHINA_API_KEY", "")
DEFAULT_SAMPLE_IMAGE_URL = "https://ark-project.tos-cn-beijing.volces.com/doc_image/r2v_tea_pic1.jpg"
PROJECT_NAME = "default"
POLL_INTERVAL_SEC = 3
POLL_ASSET_TIMEOUT_SEC = 60  # 素材激活轮询上限；update/delete 不要求 Active，超时也继续
INSECURE_SKIP_VERIFY = False
VERBOSE_LOG = True
# ==============================================================================


# ------------------------------------------------------------------------------
# 日志
# ------------------------------------------------------------------------------
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


# ------------------------------------------------------------------------------
# 结果汇总（断言框架）
# ------------------------------------------------------------------------------
# 每条记录: (用例名, PASS/FAIL/SKIP, 说明)
_RESULTS: List[Tuple[str, str, str]] = []


def record(case: str, status: str, note: str = ""):
    _RESULTS.append((case, status, note))
    icon = {"PASS": "✅", "FAIL": "❌", "SKIP": "⏭️"}.get(status, "•")
    line = f"{icon} [{status}] {case}"
    if note:
        line += f" — {note}"
    print(line)


def check(case: str, condition: bool, label: str, detail: str = ""):
    """硬断言：condition 为 False 计为 FAIL。"""
    if condition:
        record(case, "PASS", label)
    else:
        record(case, "FAIL", label + (f" ({detail})" if detail else ""))


# ------------------------------------------------------------------------------
# HTTP
# ------------------------------------------------------------------------------
def _log_response(method: str, url: str, status: int, res: Dict[str, Any]):
    """每次接口调用结束后，无条件打印其输出结果，方便逐条查看。"""
    print(f"📤 [接口输出] {method} {url}  →  HTTP {status}")
    try:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    except Exception:
        print(str(res))
    print("-" * 64)


def make_request(
    endpoint: str,
    method: str = "GET",
    headers: Optional[Dict[str, str]] = None,
    payload: Optional[Dict[str, Any]] = None,
    base_url: str = BASE_URL,
    api_key: str = API_KEY,
    timeout: int = 30,
) -> Tuple[int, Dict[str, Any]]:
    """发送 HTTP 请求并解析 JSON 响应，与 pipeline 冒烟脚本保持一致。"""
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

    # 请求 URL 常显；payload 仅在 verbose 模式打印
    print(f"🌐 [接口请求] {method} {url}")
    if VERBOSE_LOG and payload is not None:
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
            _log_response(method, url, status_code, res_json)
            return status_code, res_json
    except urllib.error.HTTPError as e:
        status_code = e.code
        body_text = e.read().decode("utf-8")
        try:
            res_json = json.loads(body_text)
        except Exception:
            res_json = {"error": {"message": body_text or str(e)}}
        _log_response(method, url, status_code, res_json)
        return status_code, res_json
    except urllib.error.URLError as e:
        res_json = {"error": {"message": f"Network Error: {e.reason}"}}
        _log_response(method, url, 500, res_json)
        return 500, res_json
    except Exception as e:
        res_json = {"error": {"message": f"Unexpected Error: {str(e)}"}}
        _log_response(method, url, 500, res_json)
        return 500, res_json


def err_code(res: Dict[str, Any]) -> str:
    err = res.get("error")
    if isinstance(err, dict):
        return str(err.get("code") or "")
    return ""


# ------------------------------------------------------------------------------
# Setup helpers（复用主流程接口造数据）
# ------------------------------------------------------------------------------
def create_asset_group(name: str, description: str, base_url: str, api_key: str) -> str:
    payload = {
        "Name": name,
        "Description": description,
        "GroupType": "AIGC",
        "ProjectName": PROJECT_NAME,
    }
    headers = {"Idempotency-Key": f"mgmt-smoke-group-{uuid.uuid4().hex}"}
    status, res = make_request("/v1/volc/assets/groups", method="POST", payload=payload, headers=headers, base_url=base_url, api_key=api_key)
    if status != 200:
        raise RuntimeError(f"setup create group failed (HTTP {status}): {json.dumps(res, ensure_ascii=False)}")
    group_id = res.get("Result", {}).get("Id")
    if not group_id:
        raise RuntimeError(f"setup create group missing Result.Id: {res}")
    return str(group_id)


def upload_asset(group_id: str, image_url: str, name: str, base_url: str, api_key: str) -> str:
    payload = {
        "GroupId": group_id,
        "URL": image_url,
        "Name": name,
        "AssetType": "Image",
        "ProjectName": PROJECT_NAME,
    }
    headers = {"Idempotency-Key": f"mgmt-smoke-asset-{uuid.uuid4().hex}"}
    status, res = make_request("/v1/volc/assets", method="POST", payload=payload, headers=headers, base_url=base_url, api_key=api_key)
    if status != 200:
        raise RuntimeError(f"setup upload asset failed (HTTP {status}): {json.dumps(res, ensure_ascii=False)}")
    asset_id = res.get("Result", {}).get("Id")
    if not asset_id:
        raise RuntimeError(f"setup upload asset missing Result.Id: {res}")
    return str(asset_id)


def get_asset_details(asset_id: str, base_url: str, api_key: str) -> Dict[str, Any]:
    endpoint = f"/v1/volc/assets/{asset_id}?ProjectName={PROJECT_NAME}"
    status, res = make_request(endpoint, method="GET", base_url=base_url, api_key=api_key)
    return status, res.get("Result", {}) if status == 200 else {}


def poll_asset_until_active_or_timeout(asset_id: str, base_url: str, api_key: str) -> str:
    """轮询素材状态，最多等待 POLL_ASSET_TIMEOUT_SEC。返回最终状态（可能仍为 Processing）。"""
    start = time.time()
    poll_count = 0
    while True:
        poll_count += 1
        status, result = get_asset_details(asset_id, base_url, api_key)
        asset_status = result.get("Status", "Processing") if status == 200 else f"HTTP_{status}"
        elapsed = int(time.time() - start)
        log_info(f"[素材轮询第 {poll_count} 次 | {asset_id}] Status={asset_status} (已耗时 {elapsed}s)")
        if asset_status == "Active":
            return "Active"
        if asset_status == "Failed":
            return "Failed"
        if elapsed >= POLL_ASSET_TIMEOUT_SEC:
            return asset_status
        time.sleep(POLL_INTERVAL_SEC)


# ------------------------------------------------------------------------------
# 待测的 7 个管理接口
# ------------------------------------------------------------------------------
def test_list_groups(group_id: str, group_name: str, base_url: str, api_key: str):
    case = "4.2 查询素材组列表 POST /groups/list"
    log_step("Test 1/7: 查询素材组列表", f"Filter.GroupType=AIGC, 期望包含 {group_id}")

    # 1a. 按 GroupType 列出，断言新建组出现在结果中
    payload = {
        "Filter": {"GroupType": "AIGC"},
        "PageNumber": 1,
        "PageSize": 20,
        "SortBy": "CreateTime",
        "SortOrder": "Desc",
        "ProjectName": PROJECT_NAME,
    }
    status, res = make_request("/v1/volc/assets/groups/list", method="POST", payload=payload, base_url=base_url, api_key=api_key)
    if status != 200:
        check(case, False, "列表请求应返回 200", f"实际 HTTP {status}: {json.dumps(res, ensure_ascii=False)}")
        return
    result = res.get("Result", {})
    items = result.get("Items", []) or []
    ids = [str(it.get("Id")) for it in items if isinstance(it, dict)]
    check(case, status == 200, "列表请求返回 200")
    check(case, group_id in ids, f"新建组 {group_id} 应出现在 Items 中", f"Items={ids}")
    check(case, isinstance(result.get("TotalCount"), int) and result.get("TotalCount", 0) >= 1, "TotalCount >= 1", f"TotalCount={result.get('TotalCount')}")

    # 1b. 用 Filter.GroupIds 精确过滤
    case_b = "4.2 查询素材组列表(按 GroupIds 过滤)"
    payload_b = {
        "Filter": {"GroupType": "AIGC", "GroupIds": [group_id]},
        "PageNumber": 1,
        "PageSize": 20,
        "ProjectName": PROJECT_NAME,
    }
    status_b, res_b = make_request("/v1/volc/assets/groups/list", method="POST", payload=payload_b, base_url=base_url, api_key=api_key)
    items_b = (res_b.get("Result", {}) or {}).get("Items", []) or []
    ids_b = [str(it.get("Id")) for it in items_b if isinstance(it, dict)]
    check(case_b, status_b == 200 and group_id in ids_b, "按 GroupIds 过滤应只返回该组", f"HTTP {status_b}, Items={ids_b}")

    # 1c. 用 Filter.Name 模糊搜索新建组名
    case_c = "4.2 查询素材组列表(按 Name 模糊搜索)"
    name_keyword = group_name[:8]
    payload_c = {
        "Filter": {"GroupType": "AIGC", "Name": name_keyword},
        "PageNumber": 1,
        "PageSize": 20,
        "ProjectName": PROJECT_NAME,
    }
    status_c, res_c = make_request("/v1/volc/assets/groups/list", method="POST", payload=payload_c, base_url=base_url, api_key=api_key)
    items_c = (res_c.get("Result", {}) or {}).get("Items", []) or []
    ids_c = [str(it.get("Id")) for it in items_c if isinstance(it, dict)]
    check(case_c, status_c == 200 and group_id in ids_c, f"按 Name='{name_keyword}' 搜索应包含新建组", f"HTTP {status_c}, Items={ids_c}")


def test_get_group(group_id: str, expected_name: str, base_url: str, api_key: str):
    case = "4.3 查询素材组 GET /groups/{id}"
    log_step("Test 2/7: 查询素材组", f"GET /groups/{group_id}")
    endpoint = f"/v1/volc/assets/groups/{group_id}?ProjectName={PROJECT_NAME}"
    status, res = make_request(endpoint, method="GET", base_url=base_url, api_key=api_key)
    result = res.get("Result", {}) if status == 200 else {}
    check(case, status == 200, "查询素材组返回 200", f"实际 HTTP {status}")
    check(case, str(result.get("Id")) == group_id, "Result.Id 与路径一致", f"Result={result}")
    check(case, result.get("GroupType") == "AIGC", "GroupType == AIGC", f"GroupType={result.get('GroupType')}")
    check(case, result.get("Name") == expected_name, f"Name == '{expected_name}'", f"Name={result.get('Name')}")


def test_update_group(group_id: str, base_url: str, api_key: str) -> str:
    """更新组名并返回新名，供后续 Get 校验。"""
    case = "4.4 更新素材组 POST /groups/{id}/update"
    new_name = f"mgmt-smoke-renamed-{uuid.uuid4().hex[:8]}"
    log_step("Test 3/7: 更新素材组", f"rename -> {new_name}")
    endpoint = f"/v1/volc/assets/groups/{group_id}/update"
    payload = {"Name": new_name, "Description": "已更新描述", "ProjectName": PROJECT_NAME}
    status, res = make_request(endpoint, method="POST", payload=payload, base_url=base_url, api_key=api_key)
    check(case, status == 200, "更新素材组返回 200", f"实际 HTTP {status}: {json.dumps(res, ensure_ascii=False)}")
    # Update 接口上游仅回 {Id}，不回显 Name；用 Id 一致性 + 二次 Get 校验持久化
    result = res.get("Result", {}) if status == 200 else {}
    check(case, str(result.get("Id")) == group_id, "更新响应 Result.Id 与路径一致", f"Result={result}")
    # 再 Get 一次确认 Name 落库
    g_status, g_res = make_request(f"/v1/volc/assets/groups/{group_id}?ProjectName={PROJECT_NAME}", method="GET", base_url=base_url, api_key=api_key)
    g_name = (g_res.get("Result", {}) or {}).get("Name") if g_status == 200 else None
    check(case + "(二次Get校验)", g_status == 200 and g_name == new_name, "二次 Get 确认 Name 已持久化", f"Name={g_name}")
    return new_name


def test_list_assets(group_id: str, asset_id: str, base_url: str, api_key: str):
    case = "5.3 查询素材列表 POST /assets/list"
    log_step("Test 4/7: 查询素材列表", f"Filter.GroupIds=[{group_id}], 期望包含 {asset_id}")
    payload = {
        "Filter": {"GroupIds": [group_id], "GroupType": "AIGC", "Statuses": ["Active", "Processing"]},
        "PageNumber": 1,
        "PageSize": 20,
        "SortBy": "CreateTime",
        "SortOrder": "Desc",
        "ProjectName": PROJECT_NAME,
    }
    status, res = make_request("/v1/volc/assets/list", method="POST", payload=payload, base_url=base_url, api_key=api_key)
    if status != 200:
        check(case, False, "列表请求应返回 200", f"实际 HTTP {status}: {json.dumps(res, ensure_ascii=False)}")
        return
    result = res.get("Result", {})
    items = result.get("Items", []) or []
    ids = [str(it.get("Id")) for it in items if isinstance(it, dict)]
    check(case, status == 200, "列表请求返回 200")
    check(case, asset_id in ids, f"新建素材 {asset_id} 应出现在 Items 中", f"Items={ids}")
    # 校验 Id 已被平台改写为平台 ID（asset-volc-cn-*）而非上游 ID
    if asset_id in ids:
        check(case, asset_id.startswith("asset-volc-cn-"), "Items[].Id 为平台 ID 前缀", f"Id={asset_id}")


def test_update_asset(asset_id: str, base_url: str, api_key: str) -> str:
    case = "5.4 更新素材 POST /assets/{id}/update"
    new_name = f"mgmt-smoke-asset-renamed-{uuid.uuid4().hex[:8]}"
    log_step("Test 5/7: 更新素材", f"rename -> {new_name}")
    endpoint = f"/v1/volc/assets/{asset_id}/update"
    payload = {"Name": new_name, "ProjectName": PROJECT_NAME}
    status, res = make_request(endpoint, method="POST", payload=payload, base_url=base_url, api_key=api_key)
    check(case, status == 200, "更新素材返回 200", f"实际 HTTP {status}: {json.dumps(res, ensure_ascii=False)}")
    # Update 接口上游仅回 {Id}，不回显 Name；用 Id 一致性 + 二次 Get 校验持久化
    result = res.get("Result", {}) if status == 200 else {}
    check(case, str(result.get("Id")) == asset_id, "更新响应 Result.Id 与路径一致", f"Result={result}")
    # 二次 Get 确认（注意：上游 URL 为临时地址，可能为空，仅校验 Name）
    g_status, g_res = make_request(f"/v1/volc/assets/{asset_id}?ProjectName={PROJECT_NAME}", method="GET", base_url=base_url, api_key=api_key)
    g_name = (g_res.get("Result", {}) or {}).get("Name") if g_status == 200 else None
    check(case + "(二次Get校验)", g_status == 200 and g_name == new_name, "二次 Get 确认 Name 已持久化", f"Name={g_name}")
    return new_name


def test_delete_asset(asset_id: str, base_url: str, api_key: str):
    case = "5.5 删除素材 POST /assets/{id}/delete"
    log_step("Test 6/7: 删除素材", f"DELETE {asset_id}")
    endpoint = f"/v1/volc/assets/{asset_id}/delete"
    payload = {"ProjectName": PROJECT_NAME}

    # 首次删除：应返回 200 空结果
    status, res = make_request(endpoint, method="POST", payload=payload, base_url=base_url, api_key=api_key)
    check(case, status == 200, "首次删除返回 200", f"实际 HTTP {status}: {json.dumps(res, ensure_ascii=False)}")
    if status == 200:
        result = res.get("Result", {})
        check(case, result == {} or result is None, "首次删除 Result 为空", f"Result={result}")

    # 删除后 Get：本地映射已删 -> 应 404 resource_not_found
    case_404 = "5.5 删除素材(删除后 Get 应 404)"
    g_status, g_res = make_request(f"/v1/volc/assets/{asset_id}?ProjectName={PROJECT_NAME}", method="GET", base_url=base_url, api_key=api_key)
    check(case_404, g_status == 404, "删除后 Get 返回 404", f"实际 HTTP {g_status}: {json.dumps(g_res, ensure_ascii=False)}")
    check(case_404, err_code(g_res) == "resource_not_found", "错误码为 resource_not_found", f"error={g_res.get('error')}")

    # 幂等：再次删除同一 ID，本地映射已删，应仍返回 200 空结果（不调上游）
    case_idem = "5.5 删除素材(幂等再删)"
    status2, res2 = make_request(endpoint, method="POST", payload=payload, base_url=base_url, api_key=api_key)
    check(case_idem, status2 == 200, "重复删除返回 200", f"实际 HTTP {status2}: {json.dumps(res2, ensure_ascii=False)}")
    if status2 == 200:
        result2 = res2.get("Result", {})
        check(case_idem, result2 == {} or result2 is None, "重复删除 Result 为空", f"Result={result2}")


def observe_delete_nonempty_group(base_url: str, api_key: str, sample_image_url: str):
    """隔离观察用例(opt-in)：删除非空素材组的实际行为。

    文档声称「删除非空素材组时，火山会按官方规则拒绝」，但实测上游可能直接成功并级联
    删除组内素材。本用例用独立资源验证实际行为，避免副作用污染主生命周期。

    注意：若发生级联，被级联素材的平台本地映射会因上游 NotFound 而无法通过 DeleteAsset
    清理（平台返回 502 且不删本地映射），即每次运行会泄漏一行本地映射——这是平台侧缺陷，
    故本用例默认关闭，需 --observe-nonempty-delete 显式开启。
    """
    case = "4.5 删除素材组(非空组删除行为观察)"
    log_step("Test [obs]: 删除非空素材组行为观察(隔离资源)", "opt-in, 会创建并销毁独立 group+asset")
    g_name = f"mgmt-smoke-neg-{uuid.uuid4().hex[:8]}"
    try:
        group_id = create_asset_group(name=g_name, description="nonempty delete observe", base_url=base_url, api_key=api_key)
        asset_id = upload_asset(group_id=group_id, image_url=sample_image_url, name="neg-asset", base_url=base_url, api_key=api_key)
        poll_asset_until_active_or_timeout(asset_id, base_url, api_key)
    except Exception as e:
        record(case, "FAIL", f"隔离资源创建失败: {e}")
        return

    endpoint = f"/v1/volc/assets/groups/{group_id}/delete"
    status, res = make_request(endpoint, method="POST", payload={"ProjectName": PROJECT_NAME}, base_url=base_url, api_key=api_key)
    if status == 200:
        record(case, "PASS", "非空组删除上游未拒绝(返回 200)——与文档'应被拒绝'不符，疑似级联删除")
    else:
        record(case, "PASS", f"非空组删除被上游拒绝(HTTP {status})——符合文档")

    # 观察级联：组内素材上游是否随之消失
    case_cascade = "4.5 删除素材组(级联影响观察)"
    g_status, g_res = make_request(f"/v1/volc/assets/{asset_id}?ProjectName={PROJECT_NAME}", method="GET", base_url=base_url, api_key=api_key)
    if status == 200 and g_status != 200:
        record(case_cascade, "PASS", f"组删除后素材上游不可访问(HTTP {g_status}, {err_code(g_res)})，确认为级联删除；本地映射或泄漏")
    elif status == 200 and g_status == 200:
        record(case_cascade, "SKIP", f"组删除后素材仍可访问(HTTP {g_status})，未级联或本地映射仍在")
    else:
        record(case_cascade, "SKIP", f"组删除被拒，不适用级联观察(Get HTTP {g_status})")

    # 尽力清理残留（若级联，素材上游已不存在，DeleteAsset 会 502 且不删本地映射，忽略）
    make_request(f"/v1/volc/assets/{asset_id}/delete", method="POST", payload={"ProjectName": PROJECT_NAME}, base_url=base_url, api_key=api_key)
    make_request(endpoint, method="POST", payload={"ProjectName": PROJECT_NAME}, base_url=base_url, api_key=api_key)


def test_delete_group(group_id: str, base_url: str, api_key: str):
    case = "4.5 删除素材组 POST /groups/{id}/delete"
    log_step("Test 7b/7: 删除素材组(已清空)", f"DELETE {group_id}")
    endpoint = f"/v1/volc/assets/groups/{group_id}/delete"
    payload = {"ProjectName": PROJECT_NAME}

    status, res = make_request(endpoint, method="POST", payload=payload, base_url=base_url, api_key=api_key)
    result = res.get("Result", {}) if status == 200 else res.get("Result")
    check(case, status == 200, "删除空组返回 200", f"实际 HTTP {status}: {json.dumps(res, ensure_ascii=False)}")
    check(case, result == {} or result is None, "删除空组 Result 为空", f"Result={result}")

    # 删除后 Get：应 404
    case_404 = "4.5 删除素材组(删除后 Get 应 404)"
    g_status, g_res = make_request(f"/v1/volc/assets/groups/{group_id}?ProjectName={PROJECT_NAME}", method="GET", base_url=base_url, api_key=api_key)
    check(case_404, g_status == 404, "删除后 Get 返回 404", f"实际 HTTP {g_status}")
    check(case_404, err_code(g_res) == "resource_not_found", "错误码为 resource_not_found", f"error={g_res.get('error')}")

    # 幂等再删
    case_idem = "4.5 删除素材组(幂等再删)"
    status2, res2 = make_request(endpoint, method="POST", payload=payload, base_url=base_url, api_key=api_key)
    result2 = res2.get("Result", {}) if status2 == 200 else res2.get("Result")
    check(case_idem, status2 == 200, "重复删除返回 200", f"实际 HTTP {status2}: {json.dumps(res2, ensure_ascii=False)}")
    check(case_idem, result2 == {} or result2 is None, "重复删除 Result 为空", f"Result={result2}")


# ------------------------------------------------------------------------------
# 主流程
# ------------------------------------------------------------------------------
def run_management_suite(base_url: str, api_key: str, keep_resources: bool, sample_image_url: str, observe_nonempty: bool):
    group_name = f"mgmt-smoke-group-{uuid.uuid4().hex[:8]}"
    log_step("Setup: 创建临时素材组 + 上传素材", f"name={group_name}")
    group_id = create_asset_group(name=group_name, description="mgmt smoke 临时组", base_url=base_url, api_key=api_key)
    log_success(f"临时组创建成功: {group_id}")
    asset_id = upload_asset(group_id=group_id, image_url=sample_image_url, name="mgmt-smoke-asset", base_url=base_url, api_key=api_key)
    log_success(f"临时素材上传成功: {asset_id}")

    final_status = poll_asset_until_active_or_timeout(asset_id, base_url, api_key)
    log_info(f"素材轮询结束，最终状态: {final_status}（update/delete 不要求 Active，继续）")

    try:
        test_list_groups(group_id, group_name, base_url, api_key)
        test_get_group(group_id, group_name, base_url, api_key)
        group_name = test_update_group(group_id, base_url, api_key)
        test_list_assets(group_id, asset_id, base_url, api_key)
        test_update_asset(asset_id, base_url, api_key)
        test_delete_asset(asset_id, base_url, api_key)
        test_delete_group(group_id, base_url, api_key)
        if observe_nonempty:
            observe_delete_nonempty_group(base_url, api_key, sample_image_url)
    finally:
        if keep_resources:
            log_warning(f"--keep-resources 已开启，跳过清理。group={group_id} asset={asset_id}")
        else:
            log_step("Cleanup: 兜底清理", "确保临时资源已删除")
            for aid in [asset_id]:
                make_request(f"/v1/volc/assets/{aid}/delete", method="POST", payload={"ProjectName": PROJECT_NAME}, base_url=base_url, api_key=api_key)
            make_request(f"/v1/volc/assets/groups/{group_id}/delete", method="POST", payload={"ProjectName": PROJECT_NAME}, base_url=base_url, api_key=api_key)
            log_success("兜底清理完成")


def print_summary():
    print("\n=======================================================")
    print("📋 管理接口测试结果汇总")
    print("=======================================================")
    fail = sum(1 for _, s, _ in _RESULTS if s == "FAIL")
    skip = sum(1 for _, s, _ in _RESULTS if s == "SKIP")
    passed = sum(1 for _, s, _ in _RESULTS if s == "PASS")
    for case, status, note in _RESULTS:
        icon = {"PASS": "✅", "FAIL": "❌", "SKIP": "⏭️"}.get(status, "•")
        print(f"{icon} [{status:4}] {case}" + (f" — {note}" if note else ""))
    print("-------------------------------------------------------")
    print(f"PASS {passed} | FAIL {fail} | SKIP {skip}")
    return fail


def main():
    parser = argparse.ArgumentParser(description="国内官key素材库管理接口冒烟测试")
    parser.add_argument("--base-url", default=BASE_URL, help=f"API 基础 URL，默认 {BASE_URL}")
    parser.add_argument("--api-key", default=API_KEY, help="平台 API Key (Bearer Token)")
    parser.add_argument("--sample-image-url", default=DEFAULT_SAMPLE_IMAGE_URL, help="测试图片 URL")
    parser.add_argument("--quiet", action="store_true", help="仅关闭请求 Payload 打印；接口输出始终打印")
    parser.add_argument("--insecure", action="store_true", help="跳过 SSL 证书校验")
    parser.add_argument("--keep-resources", action="store_true", help="跳过最终兜底清理（调试用）")
    parser.add_argument("--observe-nonempty-delete", action="store_true", help="额外运行隔离的「删除非空组」观察用例(会泄漏本地映射, 默认关闭)")
    args = parser.parse_args()

    global VERBOSE_LOG, INSECURE_SKIP_VERIFY
    if args.quiet:
        VERBOSE_LOG = False
    if args.insecure:
        INSECURE_SKIP_VERIFY = True

    base_url = args.base_url or BASE_URL
    api_key = args.api_key or API_KEY
    sample_image_url = args.sample_image_url

    if not api_key or api_key.startswith("sk-xxx"):
        log_error("未提供有效 API_KEY！")
        sys.exit(1)

    print("=======================================================")
    print("🚀 国内官key素材库 — 管理接口冒烟测试")
    print(f"   Base URL: {base_url}")
    print(f"   Insecure: {INSECURE_SKIP_VERIFY}")
    print(f"   覆盖: 列组/查组/改组/删组 + 列素材/改素材/删素材 (含幂等与删除后404)")
    print("=======================================================")

    try:
        run_management_suite(base_url=base_url, api_key=api_key, keep_resources=args.keep_resources, sample_image_url=sample_image_url, observe_nonempty=args.observe_nonempty_delete)
    except Exception as e:
        log_error(f"测试链路出现异常: {e}")
        print_summary()
        sys.exit(2)

    fail = print_summary()
    print("\n🎉 全部管理接口测试完成" + ("（存在失败用例，见上）" if fail else "，全部通过！"))
    sys.exit(1 if fail else 0)


if __name__ == "__main__":
    main()
