from __future__ import annotations

import asyncio

import common
from common.base_assertions import BaseAssertions
from module.image_model import ImageAssertions
from module.video_model import VideoAssertions
from tests.mock_helpers import make_response


def test_sync_async_and_module_status_entrypoints_share_the_sync_method(monkeypatch):
    response = make_response("https://example.com/items", status_code=204)
    calls = []

    def canonical(self, actual_response, expected):
        calls.append((self, actual_response, expected))
        return actual_response

    monkeypatch.setattr(BaseAssertions, "assert_status_code", canonical)

    instance = BaseAssertions()
    assert instance.assert_status_code(response, 204) is response
    assert asyncio.run(instance.async_assert_status_code(response, 204)) is response
    assert common.assert_status_code(response, 204) is response
    assert asyncio.run(common.async_assert_status_code(response, 204)) is response

    assert len(calls) == 4
    assert all(call[1:] == (response, 204) for call in calls)


def test_thin_domain_assertions_keep_real_class_identity_and_mro():
    assert ImageAssertions is not BaseAssertions
    assert VideoAssertions is not BaseAssertions
    assert ImageAssertions.__name__ == "ImageAssertions"
    assert VideoAssertions.__name__ == "VideoAssertions"
    assert ImageAssertions.__mro__[1] is BaseAssertions
    assert VideoAssertions.__mro__[1] is BaseAssertions
