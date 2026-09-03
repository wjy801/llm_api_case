"""Select exactly one Probe nodeid without access to controller credentials."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

from quality.flaky_probe import ProbePlan, select_probe_nodeid
from quality.pytest_identity import build_pytest_item_identity


class _Collector:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.items: list[object] = []

    def pytest_collection_finish(self, session) -> None:
        self.items = [build_pytest_item_identity(item, self.root) for item in session.items]


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("usage: python -m quality.probe_target <plan.json>", file=sys.stderr)
        return 2
    root = Path.cwd().resolve()
    try:
        plan = ProbePlan.model_validate_json(Path(args[0]).read_text(encoding="utf-8"))
        collector = _Collector(root)
        import pytest

        pytest.main([plan.case_id.split("::", 1)[0], "--collect-only", "-q"], plugins=[collector])
        nodeid = select_probe_nodeid(
            plan, collector.items, execution_profile=plan.execution_profile
        )
    except Exception as error:
        code = getattr(error, "code", "probe_target_selection_failed")
        print(json.dumps({"status": "NON_COUNTING", "diagnostic_code": code}, sort_keys=True))
        return 3
    print(json.dumps({"status": "SELECTED", "nodeid": nodeid}, ensure_ascii=False, sort_keys=True))
    output_dir = os.environ.get("QUALITY_OUTPUT_DIR", "reports/quality")
    command = [sys.executable, "run_master.py", nodeid]
    if plan.execution_profile == "parallel":
        command.extend(["-n", "auto"])
    elif plan.execution_profile != "serial":
        print(json.dumps({"status": "NON_COUNTING", "diagnostic_code": "probe_profile_mismatch"}, sort_keys=True))
        return 3
    command.extend(["-q", f"--junitxml={Path(output_dir).parent / 'probe-tests.xml'}"])
    completed = subprocess.run(
        command,
        cwd=root,
        check=False,
    )
    return int(completed.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
