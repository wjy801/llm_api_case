from __future__ import annotations

import pytest


retry_once = pytest.mark.flaky(reruns=1, reruns_delay=2)
