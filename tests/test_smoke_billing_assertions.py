from __future__ import annotations

import pytest

from module.smoke import SmokeAssertions
from tests.mock_helpers import make_response


@pytest.mark.parametrize("status", ["success", "succeeded", "completed"])
def test_successful_usage_record_accepts_positive_request_scoped_charge(status):
    assertions = SmokeAssertions()
    response = _usage_response(
        request_id="request-001",
        status=status,
        quota_yuan="1.23",
    )

    assert (
        assertions.assert_successful_usage_record(
            response,
            expected_request_id="request-001",
        )
        is response
    )


@pytest.mark.parametrize("quota_yuan", ["0", "-0.01"])
def test_successful_usage_record_rejects_non_positive_charge(quota_yuan):
    assertions = SmokeAssertions()

    with pytest.raises(AssertionError, match="positive request-scoped charge"):
        assertions.assert_successful_usage_record(
            _usage_response(
                request_id="request-001",
                status="success",
                quota_yuan=quota_yuan,
            ),
            expected_request_id="request-001",
        )


def test_successful_usage_record_rejects_non_success_status():
    assertions = SmokeAssertions()

    with pytest.raises(AssertionError, match="successful usage record"):
        assertions.assert_successful_usage_record(
            _usage_response(
                request_id="request-001",
                status="failed",
                quota_yuan="1.23",
            ),
            expected_request_id="request-001",
        )


def test_usage_record_rejects_different_request_id():
    assertions = SmokeAssertions()

    with pytest.raises(AssertionError, match="request_id mismatch"):
        assertions.assert_successful_usage_record(
            _usage_response(
                request_id="request-002",
                status="success",
                quota_yuan="1.23",
            ),
            expected_request_id="request-001",
        )


def test_usage_record_not_charged_accepts_zero_charge():
    assertions = SmokeAssertions()
    response = _usage_response(
        request_id="request-001",
        status="failed",
        quota_yuan="0",
    )

    assert (
        assertions.assert_usage_record_not_charged(
            response,
            expected_request_id="request-001",
        )
        is response
    )


def test_usage_record_not_charged_rejects_positive_charge():
    assertions = SmokeAssertions()

    with pytest.raises(AssertionError, match="should not produce a request-scoped charge"):
        assertions.assert_usage_record_not_charged(
            _usage_response(
                request_id="request-001",
                status="failed",
                quota_yuan="0.01",
            ),
            expected_request_id="request-001",
        )


def test_usage_record_requires_object_data():
    assertions = SmokeAssertions()

    with pytest.raises(AssertionError, match="Usage response data should be an object"):
        assertions.assert_successful_usage_record(
            make_response(
                "https://example.com/v1/account/usage-records",
                json_body={"data": []},
            ),
            expected_request_id="request-001",
        )


def _usage_response(*, request_id: str, status: str, quota_yuan: str):
    return make_response(
        "https://example.com/v1/account/usage-records",
        json_body={
            "data": {
                "request_id": request_id,
                "status": status,
                "quota_yuan": quota_yuan,
            }
        },
    )
