from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.mark.parametrize("case_index", range(6))
def test_flaky_enforce_canary(case_index: int) -> None:
    """Offline canary: Enforce skips it; kill-switch execution leaves a marker."""

    marker_root = os.environ.get("QUALITY_FLAKY_ENFORCE_CANARY_MARKER_DIR")
    if not marker_root:
        return
    root = Path(marker_root)
    if not root.is_absolute():
        raise AssertionError("canary marker directory must be absolute")
    root.mkdir(parents=True, exist_ok=True)
    (root / f"case-{case_index}.executed").write_text("executed\n", encoding="utf-8")
