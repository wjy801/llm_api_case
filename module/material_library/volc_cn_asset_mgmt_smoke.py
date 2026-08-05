#!/usr/bin/env python3
"""Domestic Volcengine asset-library management smoke CLI."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
import json
import sys
import uuid
from typing import Any, Callable, Iterator

import requests

from config import Settings
from module.material_library.assertions import MaterialLibraryAssertions
from module.material_library.request import MaterialLibraryRequest
from module.material_library.task import MaterialLibraryTask


BASE_URL = "https://pre.juhemoxing.com"
API_KEY = "REDACTED_SECRET_REMOVED"
DEFAULT_SAMPLE_IMAGE_URL = "https://ark-project.tos-cn-beijing.volces.com/doc_image/r2v_tea_pic1.jpg"
PROJECT_NAME = "default"
POLL_INTERVAL_SEC = 3
POLL_ASSET_TIMEOUT_SEC = 60
INSECURE_SKIP_VERIFY = False
VERBOSE_LOG = True
_RESULTS: list[tuple[str, str, str]] = []


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


def record(case: str, status: str, note: str = ""):
    _RESULTS.append((case, status, note))
    icon = {"PASS": "✅", "FAIL": "❌", "SKIP": "⏭️"}.get(status, "•")
    print(f"{icon} [{status}] {case}" + (f" — {note}" if note else ""))


def check(case: str, condition: bool, label: str, detail: str = ""):
    record(case, "PASS" if condition else "FAIL", label if condition else label + (f" ({detail})" if detail else ""))


def _record_assertion(case: str, label: str, assertion: Callable[[], Any]) -> None:
    try:
        assertion()
    except AssertionError as exc:
        record(case, "FAIL", f"{label} ({exc})")
    else:
        record(case, "PASS", label)


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
    return _MaterialRuntime(request_client, MaterialLibraryTask(), MaterialLibraryAssertions())


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


def _log_response(method: str, url: str, status: int, res: dict[str, Any]):
    print(f"📤 [接口输出] {method} {url}  →  HTTP {status}")
    print(json.dumps(res, ensure_ascii=False, indent=2))
    print("-" * 64)


def _emit_response(response: requests.Response, payload: dict[str, Any] | None = None) -> None:
    request = getattr(response, "request", None)
    method = getattr(request, "method", "HTTP")
    url = getattr(request, "url", "")
    print(f"🌐 [接口请求] {method} {url}".rstrip())
    if VERBOSE_LOG and payload is not None:
        print(f"   Payload: {json.dumps(payload, ensure_ascii=False)}")
    _log_response(method, url, response.status_code, _response_json(response))


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
            _emit_response(response, payload)
            return response.status_code, _response_json(response)
    except requests.RequestException as exc:
        body = {"error": {"message": f"Network Error: {exc}"}}
        _log_response(method, endpoint, 500, body)
        return 500, body
    except Exception as exc:
        body = {"error": {"message": f"Unexpected Error: {exc}"}}
        _log_response(method, endpoint, 500, body)
        return 500, body


def err_code(res: dict[str, Any]) -> str:
    error = res.get("error") or res.get("Error")
    if isinstance(error, dict):
        return str(error.get("code") or error.get("Code") or "")
    return ""


def create_asset_group(
    name: str,
    description: str,
    base_url: str,
    api_key: str,
    *,
    _runtime: _MaterialRuntime | None = None,
) -> str:
    with _runtime_scope(base_url, api_key, _runtime) as runtime:
        response = runtime.task.create_asset_group(
            runtime.request,
            name=name,
            description=description,
        )
        _emit_response(response, runtime.task.build_create_asset_group_payload(name=name, description=description))
        if response.status_code != 200:
            raise RuntimeError(f"setup create group failed (HTTP {response.status_code}): {json.dumps(_response_json(response), ensure_ascii=False)}")
        runtime.assertions.assert_status_code(response, 200)
        return runtime.task.extract_group_id(response)


def upload_asset(
    group_id: str,
    image_url: str,
    name: str,
    base_url: str,
    api_key: str,
    *,
    _runtime: _MaterialRuntime | None = None,
) -> str:
    with _runtime_scope(base_url, api_key, _runtime) as runtime:
        response = runtime.task.upload_image_asset(
            runtime.request,
            group_id,
            image_url=image_url,
            name=name,
        )
        _emit_response(response, runtime.task.build_create_asset_payload(group_id=group_id, image_url=image_url, name=name))
        if response.status_code != 200:
            raise RuntimeError(f"setup upload asset failed (HTTP {response.status_code}): {json.dumps(_response_json(response), ensure_ascii=False)}")
        runtime.assertions.assert_status_code(response, 200)
        return runtime.task.extract_asset_id(response)


def get_asset_details(
    asset_id: str,
    base_url: str,
    api_key: str,
    *,
    _runtime: _MaterialRuntime | None = None,
) -> tuple[int, dict[str, Any]]:
    with _runtime_scope(base_url, api_key, _runtime) as runtime:
        response = runtime.task.get_asset(runtime.request, asset_id)
        _emit_response(response)
        result = _response_json(response).get("Result", {}) if response.status_code == 200 else {}
        return response.status_code, result if isinstance(result, dict) else {}


def poll_asset_until_active_or_timeout(
    asset_id: str,
    base_url: str,
    api_key: str,
    *,
    _runtime: _MaterialRuntime | None = None,
) -> str:
    with _runtime_scope(base_url, api_key, _runtime) as runtime:
        response = runtime.task.poll_asset_until_active_or_timeout(
            runtime.request,
            asset_id,
            poll_interval=POLL_INTERVAL_SEC,
            poll_timeout=POLL_ASSET_TIMEOUT_SEC,
        )
        _emit_response(response)
        if response.status_code != 200:
            return f"HTTP_{response.status_code}"
        return str(runtime.task.extract_json_path(response, ["Result", "Status"]) or "Processing")


def test_list_groups(
    group_id: str,
    group_name: str,
    base_url: str,
    api_key: str,
    *,
    _runtime: _MaterialRuntime | None = None,
):
    with _runtime_scope(base_url, api_key, _runtime) as runtime:
        case = "4.2 查询素材组列表 POST /groups/list"
        log_step("Test 1/7: 查询素材组列表", f"Filter.GroupType=AIGC, 期望包含 {group_id}")
        response = runtime.task.list_asset_groups(runtime.request)
        _emit_response(response)
        if response.status_code != 200:
            check(case, False, "列表请求应返回 200", f"实际 HTTP {response.status_code}: {json.dumps(_response_json(response), ensure_ascii=False)}")
            return
        _record_assertion(case, f"新建组 {group_id} 应出现在 Items 中且 TotalCount >= 1", lambda: runtime.assertions.assert_group_list_contains(response, group_id=group_id))

        case_b = "4.2 查询素材组列表(按 GroupIds 过滤)"
        response_b = runtime.task.list_asset_groups(runtime.request, group_ids=[group_id])
        _emit_response(response_b)
        _record_assertion(case_b, "按 GroupIds 过滤应返回该组", lambda: runtime.assertions.assert_group_list_contains(response_b, group_id=group_id))

        case_c = "4.2 查询素材组列表(按 Name 模糊搜索)"
        keyword = group_name[:8]
        response_c = runtime.task.list_asset_groups(runtime.request, name=keyword)
        _emit_response(response_c)
        _record_assertion(case_c, f"按 Name='{keyword}' 搜索应包含新建组", lambda: runtime.assertions.assert_group_list_contains(response_c, group_id=group_id))


def test_get_group(
    group_id: str,
    expected_name: str,
    base_url: str,
    api_key: str,
    *,
    _runtime: _MaterialRuntime | None = None,
):
    with _runtime_scope(base_url, api_key, _runtime) as runtime:
        case = "4.3 查询素材组 GET /groups/{id}"
        log_step("Test 2/7: 查询素材组", f"GET /groups/{group_id}")
        response = runtime.task.get_asset_group(runtime.request, group_id)
        _emit_response(response)
        _record_assertion(case, "素材组详情与期望一致", lambda: runtime.assertions.assert_group_detail_matches(response, group_id=group_id, name=expected_name))


def test_update_group(
    group_id: str,
    base_url: str,
    api_key: str,
    *,
    _runtime: _MaterialRuntime | None = None,
) -> str:
    with _runtime_scope(base_url, api_key, _runtime) as runtime:
        case = "4.4 更新素材组 POST /groups/{id}/update"
        new_name = f"mgmt-smoke-renamed-{uuid.uuid4().hex[:8]}"
        log_step("Test 3/7: 更新素材组", f"rename -> {new_name}")
        response = runtime.task.update_asset_group(runtime.request, group_id, name=new_name, description="已更新描述")
        _emit_response(response)
        check(case, response.status_code == 200, "更新素材组返回 200", f"实际 HTTP {response.status_code}")
        response_get = runtime.task.get_asset_group(runtime.request, group_id)
        _emit_response(response_get)
        _record_assertion(case + "(二次Get校验)", "二次 Get 确认 Name 已持久化", lambda: runtime.assertions.assert_group_detail_matches(response_get, group_id=group_id, name=new_name))
        return new_name


def test_list_assets(
    group_id: str,
    asset_id: str,
    base_url: str,
    api_key: str,
    *,
    _runtime: _MaterialRuntime | None = None,
):
    with _runtime_scope(base_url, api_key, _runtime) as runtime:
        case = "5.3 查询素材列表 POST /assets/list"
        log_step("Test 4/7: 查询素材列表", f"Filter.GroupIds=[{group_id}], 期望包含 {asset_id}")
        response = runtime.task.list_assets(
            runtime.request,
            group_ids=[group_id],
            group_type="AIGC",
            statuses=["Active", "Processing"],
        )
        _emit_response(response)
        _record_assertion(case, f"新建素材 {asset_id} 应出现在 Items 中", lambda: runtime.assertions.assert_asset_list_contains(response, asset_id=asset_id))
        check(case, asset_id.startswith("asset-volc-cn-"), "Items[].Id 为平台 ID 前缀", f"Id={asset_id}")


def test_update_asset(
    asset_id: str,
    base_url: str,
    api_key: str,
    *,
    _runtime: _MaterialRuntime | None = None,
) -> str:
    with _runtime_scope(base_url, api_key, _runtime) as runtime:
        case = "5.4 更新素材 POST /assets/{id}/update"
        new_name = f"mgmt-smoke-asset-renamed-{uuid.uuid4().hex[:8]}"
        log_step("Test 5/7: 更新素材", f"rename -> {new_name}")
        response = runtime.task.update_asset(runtime.request, asset_id, name=new_name)
        _emit_response(response)
        check(case, response.status_code == 200, "更新素材返回 200", f"实际 HTTP {response.status_code}")
        response_get = runtime.task.get_asset(runtime.request, asset_id)
        _emit_response(response_get)
        _record_assertion(case + "(二次Get校验)", "二次 Get 确认 Name 已持久化", lambda: runtime.assertions.assert_asset_name(response_get, asset_id=asset_id, name=new_name))
        return new_name


def _record_delete_contract(
    runtime: _MaterialRuntime,
    case: str,
    response: requests.Response,
    label: str,
) -> None:
    _emit_response(response)
    _record_assertion(case, label, lambda: runtime.assertions.assert_status_code(response, 200))
    if response.status_code == 200:
        _record_assertion(case, "删除 Result 为空", lambda: runtime.assertions.assert_delete_result_empty(response))


def test_delete_asset(
    asset_id: str,
    base_url: str,
    api_key: str,
    *,
    _runtime: _MaterialRuntime | None = None,
):
    with _runtime_scope(base_url, api_key, _runtime) as runtime:
        case = "5.5 删除素材 POST /assets/{id}/delete"
        log_step("Test 6/7: 删除素材", f"DELETE {asset_id}")
        _record_delete_contract(runtime, case, runtime.task.delete_asset_if_exists(runtime.request, asset_id), "首次删除返回 200")

        case_404 = "5.5 删除素材(删除后 Get 应 404)"
        response_get = runtime.task.get_asset(runtime.request, asset_id)
        _emit_response(response_get)
        check(case_404, response_get.status_code == 404, "删除后 Get 返回 404", f"实际 HTTP {response_get.status_code}")
        _record_assertion(case_404, "响应包含错误码", lambda: runtime.assertions.assert_error_code_present(response_get))
        check(case_404, err_code(_response_json(response_get)) == "resource_not_found", "错误码为 resource_not_found", f"error={_response_json(response_get).get('error')}")

        case_idem = "5.5 删除素材(幂等再删)"
        _record_delete_contract(runtime, case_idem, runtime.task.delete_asset_if_exists(runtime.request, asset_id), "重复删除返回 200")


def observe_delete_nonempty_group(
    base_url: str,
    api_key: str,
    sample_image_url: str,
    *,
    _runtime: _MaterialRuntime | None = None,
):
    with _runtime_scope(base_url, api_key, _runtime) as runtime:
        case = "4.5 删除素材组(非空组删除行为观察)"
        log_step("Test [obs]: 删除非空素材组行为观察(隔离资源)", "opt-in, 会创建并销毁独立 group+asset")
        try:
            group_id = create_asset_group(f"mgmt-smoke-neg-{uuid.uuid4().hex[:8]}", "nonempty delete observe", base_url, api_key, _runtime=runtime)
            asset_id = upload_asset(group_id, sample_image_url, "neg-asset", base_url, api_key, _runtime=runtime)
            poll_asset_until_active_or_timeout(asset_id, base_url, api_key, _runtime=runtime)
        except Exception as exc:
            record(case, "FAIL", f"隔离资源创建失败: {exc}")
            return

        delete_response = runtime.task.delete_asset_group_if_exists(runtime.request, group_id)
        _emit_response(delete_response)
        if delete_response.status_code == 200:
            record(case, "PASS", "非空组删除上游未拒绝(返回 200)——与文档'应被拒绝'不符，疑似级联删除")
        else:
            record(case, "PASS", f"非空组删除被上游拒绝(HTTP {delete_response.status_code})——符合文档")

        get_response = runtime.task.get_asset(runtime.request, asset_id)
        _emit_response(get_response)
        case_cascade = "4.5 删除素材组(级联影响观察)"
        if delete_response.status_code == 200 and get_response.status_code != 200:
            record(case_cascade, "PASS", f"组删除后素材上游不可访问(HTTP {get_response.status_code}, {err_code(_response_json(get_response))})，确认为级联删除；本地映射或泄漏")
        elif delete_response.status_code == 200:
            record(case_cascade, "SKIP", f"组删除后素材仍可访问(HTTP {get_response.status_code})，未级联或本地映射仍在")
        else:
            record(case_cascade, "SKIP", f"组删除被拒，不适用级联观察(Get HTTP {get_response.status_code})")
        _emit_response(runtime.task.delete_asset_if_exists(runtime.request, asset_id))
        _emit_response(runtime.task.delete_asset_group_if_exists(runtime.request, group_id))


def test_delete_group(
    group_id: str,
    base_url: str,
    api_key: str,
    *,
    _runtime: _MaterialRuntime | None = None,
):
    with _runtime_scope(base_url, api_key, _runtime) as runtime:
        case = "4.5 删除素材组 POST /groups/{id}/delete"
        log_step("Test 7b/7: 删除素材组(已清空)", f"DELETE {group_id}")
        _record_delete_contract(runtime, case, runtime.task.delete_asset_group_if_exists(runtime.request, group_id), "删除空组返回 200")

        case_404 = "4.5 删除素材组(删除后 Get 应 404)"
        response_get = runtime.task.get_asset_group(runtime.request, group_id)
        _emit_response(response_get)
        check(case_404, response_get.status_code == 404, "删除后 Get 返回 404", f"实际 HTTP {response_get.status_code}")
        _record_assertion(case_404, "响应包含错误码", lambda: runtime.assertions.assert_error_code_present(response_get))
        check(case_404, err_code(_response_json(response_get)) == "resource_not_found", "错误码为 resource_not_found", f"error={_response_json(response_get).get('error')}")

        case_idem = "4.5 删除素材组(幂等再删)"
        _record_delete_contract(runtime, case_idem, runtime.task.delete_asset_group_if_exists(runtime.request, group_id), "重复删除返回 200")


def run_management_suite(
    base_url: str,
    api_key: str,
    keep_resources: bool,
    sample_image_url: str,
    observe_nonempty: bool,
    *,
    _runtime: _MaterialRuntime | None = None,
):
    with _runtime_scope(base_url, api_key, _runtime) as runtime:
        group_name = f"mgmt-smoke-group-{uuid.uuid4().hex[:8]}"
        log_step("Setup: 创建临时素材组 + 上传素材", f"name={group_name}")
        group_id = create_asset_group(group_name, "mgmt smoke 临时组", base_url, api_key, _runtime=runtime)
        log_success(f"临时组创建成功: {group_id}")
        asset_id = upload_asset(group_id, sample_image_url, "mgmt-smoke-asset", base_url, api_key, _runtime=runtime)
        log_success(f"临时素材上传成功: {asset_id}")
        final_status = poll_asset_until_active_or_timeout(asset_id, base_url, api_key, _runtime=runtime)
        log_info(f"素材轮询结束，最终状态: {final_status}（update/delete 不要求 Active，继续）")
        try:
            test_list_groups(group_id, group_name, base_url, api_key, _runtime=runtime)
            test_get_group(group_id, group_name, base_url, api_key, _runtime=runtime)
            test_update_group(group_id, base_url, api_key, _runtime=runtime)
            test_list_assets(group_id, asset_id, base_url, api_key, _runtime=runtime)
            test_update_asset(asset_id, base_url, api_key, _runtime=runtime)
            test_delete_asset(asset_id, base_url, api_key, _runtime=runtime)
            test_delete_group(group_id, base_url, api_key, _runtime=runtime)
            if observe_nonempty:
                observe_delete_nonempty_group(base_url, api_key, sample_image_url, _runtime=runtime)
        finally:
            if keep_resources:
                log_warning(f"--keep-resources 已开启，跳过清理。group={group_id} asset={asset_id}")
            else:
                log_step("Cleanup: 兜底清理", "确保临时资源已删除")
                _emit_response(runtime.task.delete_asset_if_exists(runtime.request, asset_id))
                _emit_response(runtime.task.delete_asset_group_if_exists(runtime.request, group_id))
                log_success("兜底清理完成")


def print_summary():
    print("\n=======================================================")
    print("📋 管理接口测试结果汇总")
    print("=======================================================")
    fail = sum(1 for _, status, _ in _RESULTS if status == "FAIL")
    skip = sum(1 for _, status, _ in _RESULTS if status == "SKIP")
    passed = sum(1 for _, status, _ in _RESULTS if status == "PASS")
    for case, status, note in _RESULTS:
        icon = {"PASS": "✅", "FAIL": "❌", "SKIP": "⏭️"}.get(status, "•")
        print(f"{icon} [{status:4}] {case}" + (f" — {note}" if note else ""))
    print("-------------------------------------------------------")
    print(f"PASS {passed} | FAIL {fail} | SKIP {skip}")
    return fail


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="国内官key素材库管理接口冒烟测试")
    parser.add_argument("--base-url", default=BASE_URL, help=f"API 基础 URL，默认 {BASE_URL}")
    parser.add_argument("--api-key", default=API_KEY, help="平台 API Key (Bearer Token)")
    parser.add_argument("--sample-image-url", default=DEFAULT_SAMPLE_IMAGE_URL, help="测试图片 URL")
    parser.add_argument("--quiet", action="store_true", help="仅关闭请求 Payload 打印；接口输出始终打印")
    parser.add_argument("--insecure", action="store_true", help="跳过 SSL 证书校验")
    parser.add_argument("--keep-resources", action="store_true", help="跳过最终兜底清理（调试用）")
    parser.add_argument("--observe-nonempty-delete", action="store_true", help="额外运行隔离的「删除非空组」观察用例(会泄漏本地映射, 默认关闭)")
    return parser


def main(argv: list[str] | None = None):
    args = build_parser().parse_args(argv)
    global VERBOSE_LOG, INSECURE_SKIP_VERIFY
    VERBOSE_LOG = not args.quiet
    INSECURE_SKIP_VERIFY = bool(args.insecure)
    base_url = args.base_url or BASE_URL
    api_key = args.api_key or API_KEY
    if not api_key or api_key.startswith("sk-xxx"):
        log_error("未提供有效 API_KEY！")
        raise SystemExit(1)

    _RESULTS.clear()
    print("=======================================================")
    print("🚀 国内官key素材库 — 管理接口冒烟测试")
    print(f"   Base URL: {base_url}")
    print(f"   Insecure: {INSECURE_SKIP_VERIFY}")
    print("   覆盖: 列组/查组/改组/删组 + 列素材/改素材/删素材 (含幂等与删除后404)")
    print("=======================================================")
    runtime = _create_runtime(base_url, api_key)
    try:
        run_management_suite(
            base_url,
            api_key,
            args.keep_resources,
            args.sample_image_url,
            args.observe_nonempty_delete,
            _runtime=runtime,
        )
    except Exception as exc:
        log_error(f"测试链路出现异常: {exc}")
        print_summary()
        raise SystemExit(2) from exc
    finally:
        runtime.close()

    fail = print_summary()
    print("\n🎉 全部管理接口测试完成" + ("（存在失败用例，见上）" if fail else "，全部通过！"))
    raise SystemExit(1 if fail else 0)


if __name__ == "__main__":
    main()
