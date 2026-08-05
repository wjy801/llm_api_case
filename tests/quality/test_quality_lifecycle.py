from __future__ import annotations

from datetime import UTC, datetime
import json
import os
from pathlib import Path
import subprocess
import sys

from quality.config import QualityRuntimeConfig
from quality.models import RunStatus
from run_orchestration import quality_lifecycle, quality_pipeline
from run_orchestration.pytest_execution import (
    PoolExecutionResult,
    PoolExecutionStatus,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_disabled_factory_loads_only_lightweight_quality_config(tmp_path):
    environment = os.environ.copy()
    environment["QUALITY_ENABLE"] = "FALSE"
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import json, sys; "
                "from run_orchestration.quality_lifecycle import "
                "create_quality_run_lifecycle; "
                "lifecycle=create_quality_run_lifecycle(); "
                "print(json.dumps({'enabled': lifecycle.enabled, "
                "'modules': sorted(name for name in sys.modules "
                "if name == 'quality' or name.startswith('quality.'))}))"
            ),
        ],
        cwd=tmp_path,
        env={**environment, "PYTHONPATH": str(PROJECT_ROOT)},
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout.splitlines()[-1])
    assert payload == {
        "enabled": False,
        "modules": ["quality", "quality.config"],
    }


def test_noop_lifecycle_has_no_file_or_argument_side_effects(tmp_path):
    lifecycle = quality_lifecycle.NoopQualityRunLifecycle()
    start_time = datetime(2026, 8, 5, tzinfo=UTC)
    args = ["-q"]

    lifecycle.prepare(start_time)
    assert lifecycle.ensure_junit_args(args) == args
    with lifecycle.stage_environment("serial-pool"):
        assert not any(tmp_path.iterdir())
    lifecycle.finalize(
        start_time=start_time,
        expected_case_count=1,
        pool_results=(),
        status=quality_lifecycle.RunLifecycleStatus.FINISHED,
    )
    assert not any(tmp_path.iterdir())


def test_enabled_lifecycle_preserves_junit_environment_and_status_mapping(
    monkeypatch,
    tmp_path,
):
    runtime_config = QualityRuntimeConfig(
        enabled=True,
        run_id="run-1",
        execution_id=None,
        output_dir=tmp_path / "quality",
    )
    lifecycle = quality_lifecycle.EnabledQualityRunLifecycle(runtime_config)
    calls = []
    monkeypatch.setattr(
        quality_pipeline,
        "finalize_quality_run",
        lambda *args, **kwargs: calls.append(kwargs),
    )
    monkeypatch.setenv("QUALITY_EXECUTION_ID", "outside")

    generated = lifecycle.ensure_junit_args(["-q"])
    assert generated[-1] == f"--junitxml={tmp_path / 'quality/junit/quality.xml'}"
    custom = lifecycle.ensure_junit_args(["--junitxml=custom.xml"])
    assert custom == ["--junitxml=custom.xml"]

    with lifecycle.stage_environment("serial-pool"):
        assert os.environ["QUALITY_RUN_ID"] == "run-1"
        assert os.environ["QUALITY_EXECUTION_ID"] == "serial-pool"
    assert os.environ["QUALITY_EXECUTION_ID"] == "outside"

    result = PoolExecutionResult(
        stage_id="serial-pool",
        planned_nodeids=("test_sample.py::test_ok",),
        status=PoolExecutionStatus.COMPLETED,
        raw_pytest_exit_code=0,
        junit_path=tmp_path / "quality/junit/quality.xml",
    )
    lifecycle.finalize(
        start_time=datetime(2026, 8, 5, tzinfo=UTC),
        expected_case_count=1,
        pool_results=(result,),
        status=quality_lifecycle.RunLifecycleStatus.INTERRUPTED,
    )

    assert calls[0]["expected_execution_ids"] == ("serial-pool",)
    assert calls[0]["status"] is RunStatus.INTERRUPTED
