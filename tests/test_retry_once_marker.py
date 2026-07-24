from __future__ import annotations

import pytest

from common.markers import retry_once


pytestmark = pytest.mark.flaky_governance


class TestRetryOnceMarker:
    def test_retry_once_uses_single_rerun(self):
        mark = retry_once.mark

        assert mark.name == "flaky"
        assert mark.kwargs == {"reruns": 1, "reruns_delay": 2}
