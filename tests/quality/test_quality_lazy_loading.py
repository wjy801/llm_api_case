from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
import subprocess
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _quality_modules(statement: str) -> list[str]:
    environment = os.environ.copy()
    environment["QUALITY_ENABLE"] = "FALSE"
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import json, sys; "
                f"{statement}; "
                "print(json.dumps(sorted(name for name in sys.modules "
                "if name == 'quality' or name.startswith('quality.'))))"
            ),
        ],
        cwd=PROJECT_ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout.splitlines()[-1])


def test_import_quality_loads_only_the_root_package():
    assert _quality_modules("import quality") == ["quality"]


def test_import_quality_config_does_not_load_heavy_implementations():
    assert _quality_modules("import quality.config") == [
        "quality",
        "quality.config",
    ]


def test_import_lightweight_pytest_plugin_does_not_load_runtime():
    assert _quality_modules("import quality.pytest_plugin") == [
        "quality",
        "quality.pytest_plugin",
    ]


def test_runner_import_does_not_load_quality():
    assert _quality_modules("import run_orchestration.runner") == []


def test_pipeline_reporting_cli_import_does_not_load_quality():
    assert _quality_modules("import pipeline_reporting.__main__") == []


def test_all_public_exports_resolve_to_the_defining_object():
    import quality

    assert set(quality._EXPORTS) == set(quality.__all__)
    for name in quality.__all__:
        module_name, attribute_name = quality._EXPORTS[name]
        assert getattr(quality, name) is getattr(
            importlib.import_module(module_name),
            attribute_name,
        )


def test_public_export_is_cached_and_unknown_names_fail_normally():
    import quality

    value = quality.QualityRuntimeConfig
    assert quality.__dict__["QualityRuntimeConfig"] is value
    assert quality.QualityRuntimeConfig is value

    try:
        quality.not_a_public_export
    except AttributeError as error:
        assert "not_a_public_export" in str(error)
    else:
        raise AssertionError("unknown quality export must raise AttributeError")


def test_star_import_keeps_the_existing_public_surface():
    import quality

    namespace: dict[str, object] = {}
    exec("from quality import *", namespace)

    assert set(quality.__all__) <= namespace.keys()
