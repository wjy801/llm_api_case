from __future__ import annotations

import ast
from pathlib import Path
import subprocess
import sys
import textwrap


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_common_has_no_static_quality_imports():
    violations: list[str] = []
    for path in sorted((PROJECT_ROOT / "common").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            if any(name == "quality" or name.startswith("quality.") for name in names):
                violations.append(f"{path.relative_to(PROJECT_ROOT)}:{node.lineno}")

    assert violations == []


def test_common_imports_and_http_work_when_quality_is_unavailable():
    script = textwrap.dedent(
        """
        import importlib.abc
        import json
        import sys

        class BlockQuality(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname == "quality" or fullname.startswith("quality."):
                    raise ImportError(f"blocked dependency: {fullname}")
                return None

        sys.meta_path.insert(0, BlockQuality())

        import requests
        from common.base_request import BaseRequest
        from common.base_task import BaseTask
        from common.streaming import iter_sse_lines

        class Config:
            base_url = "https://example.com"
            api_key = "secret"
            timeout = 5

        response = requests.Response()
        response.status_code = 200
        response.url = "https://example.com/v1/items"
        response._content = json.dumps({"ok": True}).encode("utf-8")
        response.headers["Content-Type"] = "application/json"

        client = BaseRequest(config=Config())
        client.session.request = lambda method, url, **kwargs: response
        assert client.get("/v1/items", _attach_log=False).json() == {"ok": True}
        assert BaseTask is not None
        assert iter_sse_lines is not None
        assert not any(name == "quality" or name.startswith("quality.") for name in sys.modules)
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
