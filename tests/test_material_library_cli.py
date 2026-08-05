from __future__ import annotations

import ast
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

from module.material_library import volc_cn_asset_mgmt_smoke as mgmt_cli
from module.material_library import volc_cn_asset_pipeline_smoke as pipeline_cli
from module.material_library.request import MaterialLibraryRequest
from module.material_library.task import MaterialLibraryTask


ROOT = Path(__file__).resolve().parents[1]


def _response(status: int, body: dict[str, object], method: str = "POST") -> requests.Response:
    response = requests.Response()
    response.status_code = status
    response._content = json.dumps(body).encode("utf-8")
    response.encoding = "utf-8"
    request = requests.Request(method, "https://example.test/v1/volc/assets").prepare()
    response.request = request
    return response


def test_pipeline_parser_preserves_public_options_and_flow_choices():
    parser = pipeline_cli.build_parser()
    args = parser.parse_args([])
    assert args.base_url == pipeline_cli.BASE_URL
    assert args.api_key == pipeline_cli.API_KEY
    assert args.model == pipeline_cli.MODEL_ID
    assert args.flow == "all"

    flow_action = next(action for action in parser._actions if action.dest == "flow")
    assert flow_action.choices == ["normal", "liveness", "all"]
    option_strings = {option for action in parser._actions for option in action.option_strings}
    assert {
        "--base-url",
        "--api-key",
        "--model",
        "--flow",
        "--sample-image-url",
        "--liveness-image-url",
        "--auto-mock-callback",
        "--mock-byted-token",
        "--quiet",
        "--insecure",
    } <= option_strings


def test_management_parser_preserves_public_options():
    parser = mgmt_cli.build_parser()
    args = parser.parse_args([])
    assert args.base_url == mgmt_cli.BASE_URL
    assert args.api_key == mgmt_cli.API_KEY
    option_strings = {option for action in parser._actions for option in action.option_strings}
    assert {
        "--base-url",
        "--api-key",
        "--sample-image-url",
        "--quiet",
        "--insecure",
        "--keep-resources",
        "--observe-nonempty-delete",
    } <= option_strings


@pytest.mark.parametrize(
    "path",
    [
        ROOT / "module/material_library/volc_cn_asset_pipeline_smoke.py",
        ROOT / "module/material_library/volc_cn_asset_mgmt_smoke.py",
    ],
)
def test_cli_modules_do_not_own_urllib_or_ssl_http_implementation(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported.update(
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    )
    assert not {name for name in imported if name == "ssl" or name.startswith("urllib")}


def test_pipeline_runtime_scopes_insecure_to_current_request_session(monkeypatch):
    monkeypatch.setattr(pipeline_cli, "INSECURE_SKIP_VERIFY", True)
    unrelated = requests.Session()
    runtime = pipeline_cli._create_runtime("https://example.test", "key")
    try:
        assert isinstance(runtime.request, MaterialLibraryRequest)
        assert runtime.request.middlewares == []
        assert runtime.request.session.verify is False
        assert unrelated.verify is True
    finally:
        runtime.close()
        unrelated.close()


def test_pipeline_compatibility_helper_delegates_to_task_and_assertions():
    response = _response(200, {"Result": {"Id": "group-volc-cn-1"}})
    calls: list[tuple[str, object]] = []

    class FakeTask:
        def create_asset_group(self, request, **kwargs):
            calls.append(("task", kwargs))
            return response

        def extract_group_id(self, actual):
            assert actual is response
            calls.append(("extract", actual))
            return "group-volc-cn-1"

    class FakeAssertions:
        def assert_status_code(self, actual, expected):
            assert actual is response
            assert expected == 200
            calls.append(("assert", expected))
            return actual

    runtime = pipeline_cli._MaterialRuntime(SimpleNamespace(), FakeTask(), FakeAssertions())
    group_id = pipeline_cli.create_asset_group(
        name="group",
        description="description",
        base_url="https://example.test",
        api_key="key",
        _runtime=runtime,
    )
    assert group_id == "group-volc-cn-1"
    assert [name for name, _ in calls] == ["task", "assert", "extract"]


def test_material_task_keeps_cli_payload_semantics():
    task = MaterialLibraryTask()
    assert task.build_create_asset_group_payload(
        name="n",
        description="d",
        group_type="LivenessFace",
    )["GroupType"] == "LivenessFace"
    payload = task.build_list_assets_payload(
        group_ids=["g"],
        group_type="AIGC",
        statuses=["Active", "Processing"],
    )
    assert payload == {
        "Filter": {
            "GroupIds": ["g"],
            "GroupType": "AIGC",
            "Statuses": ["Active", "Processing"],
        },
        "PageNumber": 1,
        "PageSize": 20,
        "SortBy": "CreateTime",
        "SortOrder": "Desc",
        "ProjectName": "default",
    }
    video_payload = task.build_asset_video_generation_payload(
        asset_id="a",
        reference_role="first_frame",
    )
    assert video_payload["content"][1]["role"] == "first_frame"


def test_pipeline_main_success_uses_selected_flow_and_local_flags(monkeypatch, capsys):
    calls: list[tuple[str, dict[str, object]]] = []

    class FakeRuntime:
        def close(self):
            calls.append(("close", {}))

    monkeypatch.setattr(pipeline_cli, "_create_runtime", lambda *args, **kwargs: FakeRuntime())
    monkeypatch.setattr(
        pipeline_cli,
        "run_normal_asset_flow",
        lambda **kwargs: calls.append(("normal", kwargs)) or {"ok": True},
    )
    monkeypatch.setattr(
        pipeline_cli,
        "run_liveness_asset_flow",
        lambda **kwargs: calls.append(("liveness", kwargs)) or {"ok": True},
    )

    assert pipeline_cli.main(["--flow", "normal", "--quiet", "--insecure"]) is None
    assert [name for name, _ in calls] == ["normal", "close"]
    assert pipeline_cli.VERBOSE_LOG is False
    assert pipeline_cli.INSECURE_SKIP_VERIFY is True
    assert "所有测试流程执行完毕，测试成功" in capsys.readouterr().out


def test_pipeline_main_invalid_key_and_runtime_failure_exit_one(monkeypatch):
    with pytest.raises(SystemExit) as invalid:
        pipeline_cli.main(["--api-key", "sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"])
    assert invalid.value.code == 1

    runtime = SimpleNamespace(close=lambda: None)
    monkeypatch.setattr(pipeline_cli, "_create_runtime", lambda *args, **kwargs: runtime)
    monkeypatch.setattr(
        pipeline_cli,
        "run_normal_asset_flow",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    with pytest.raises(SystemExit) as failed:
        pipeline_cli.main(["--flow", "normal"])
    assert failed.value.code == 1


@pytest.mark.parametrize("mode, expected", [("pass", 0), ("fail", 1), ("error", 2)])
def test_management_main_exit_code_matrix(monkeypatch, mode: str, expected: int):
    runtime = SimpleNamespace(close=lambda: None)
    monkeypatch.setattr(mgmt_cli, "_create_runtime", lambda *args, **kwargs: runtime)

    def fake_suite(*args, **kwargs):
        if mode == "fail":
            mgmt_cli.record("case", "FAIL", "failed")
        elif mode == "error":
            raise RuntimeError("boom")

    monkeypatch.setattr(mgmt_cli, "run_management_suite", fake_suite)
    with pytest.raises(SystemExit) as result:
        mgmt_cli.main([])
    assert result.value.code == expected


def test_management_assertion_failure_is_recorded_and_does_not_stop_suite():
    mgmt_cli._RESULTS.clear()
    mgmt_cli._record_assertion("first", "failed assertion", lambda: (_ for _ in ()).throw(AssertionError("bad")))
    mgmt_cli._record_assertion("second", "passed assertion", lambda: None)
    assert [status for _, status, _ in mgmt_cli._RESULTS] == ["FAIL", "PASS"]
