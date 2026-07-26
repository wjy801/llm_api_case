from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from common.base_assertions import (
        BaseAssertions,
        assert_json_path_exists,
        assert_schema,
        assert_json_value,
        assert_status_code,
        async_assert_json_path_exists,
        async_assert_schema,
        async_assert_json_value,
        async_assert_status_code,
    )
    from common.base_request import BaseRequest
    from common.base_task import BaseTask
    from common.polling import DEFAULT_MEDIA_POLLING_POLICY, PollingPolicy, PollingState, PollingTimeoutError
    from common.retry import RetryPolicy
    from common.test_context import (
        ContextCleanupError,
        ContextExtractionError,
        ContextVariableError,
        ContextVariableNotFound,
        ContextVariableTypeError,
        TestContext,
        TestContextError,
    )
    from common.base_decorators import (
        BaseDecorators,
        attach_model_result_file,
        allure_step,
        download_links_from_poll_get,
        start_model_result_collection,
        stop_model_result_collection,
    )

__all__ = [
    "BaseAssertions",
    "BaseDecorators",
    "BaseRequest",
    "BaseTask",
    "PollingPolicy",
    "PollingState",
    "PollingTimeoutError",
    "DEFAULT_MEDIA_POLLING_POLICY",
    "RetryPolicy",
    "ContextCleanupError",
    "ContextExtractionError",
    "ContextVariableError",
    "ContextVariableNotFound",
    "ContextVariableTypeError",
    "TestContext",
    "TestContextError",
    "attach_model_result_file",
    "allure_step",
    "assert_json_path_exists",
    "assert_schema",
    "assert_json_value",
    "assert_status_code",
    "async_assert_json_path_exists",
    "async_assert_schema",
    "async_assert_json_value",
    "async_assert_status_code",
    "download_links_from_poll_get",
    "start_model_result_collection",
    "stop_model_result_collection",
]


def __getattr__(name: str):
    if name == "BaseRequest":
        from common.base_request import BaseRequest

        return BaseRequest
    if name == "BaseTask":
        from common.base_task import BaseTask

        return BaseTask
    if name == "BaseAssertions":
        from common.base_assertions import BaseAssertions

        return BaseAssertions
    if name == "BaseDecorators":
        from common.base_decorators import BaseDecorators

        return BaseDecorators
    if name == "RetryPolicy":
        from common.retry import RetryPolicy

        return RetryPolicy
    if name == "PollingPolicy":
        from common.polling import PollingPolicy

        return PollingPolicy
    if name == "DEFAULT_MEDIA_POLLING_POLICY":
        from common.polling import DEFAULT_MEDIA_POLLING_POLICY

        return DEFAULT_MEDIA_POLLING_POLICY
    if name == "PollingState":
        from common.polling import PollingState

        return PollingState
    if name == "PollingTimeoutError":
        from common.polling import PollingTimeoutError

        return PollingTimeoutError
    if name == "TestContext":
        from common.test_context import TestContext

        return TestContext
    if name == "TestContextError":
        from common.test_context import TestContextError

        return TestContextError
    if name == "ContextVariableError":
        from common.test_context import ContextVariableError

        return ContextVariableError
    if name == "ContextVariableNotFound":
        from common.test_context import ContextVariableNotFound

        return ContextVariableNotFound
    if name == "ContextVariableTypeError":
        from common.test_context import ContextVariableTypeError

        return ContextVariableTypeError
    if name == "ContextExtractionError":
        from common.test_context import ContextExtractionError

        return ContextExtractionError
    if name == "ContextCleanupError":
        from common.test_context import ContextCleanupError

        return ContextCleanupError
    if name == "assert_json_value":
        from common.base_assertions import assert_json_value

        return assert_json_value
    if name == "assert_json_path_exists":
        from common.base_assertions import assert_json_path_exists

        return assert_json_path_exists
    if name == "assert_schema":
        from common.base_assertions import assert_schema

        return assert_schema
    if name == "assert_status_code":
        from common.base_assertions import assert_status_code

        return assert_status_code
    if name == "async_assert_json_value":
        from common.base_assertions import async_assert_json_value

        return async_assert_json_value
    if name == "async_assert_json_path_exists":
        from common.base_assertions import async_assert_json_path_exists

        return async_assert_json_path_exists
    if name == "async_assert_schema":
        from common.base_assertions import async_assert_schema

        return async_assert_schema
    if name == "async_assert_status_code":
        from common.base_assertions import async_assert_status_code

        return async_assert_status_code
    if name == "download_links_from_poll_get":
        from common.base_decorators import download_links_from_poll_get

        return download_links_from_poll_get
    if name == "start_model_result_collection":
        from common.base_decorators import start_model_result_collection

        return start_model_result_collection
    if name == "stop_model_result_collection":
        from common.base_decorators import stop_model_result_collection

        return stop_model_result_collection
    if name == "attach_model_result_file":
        from common.base_decorators import attach_model_result_file

        return attach_model_result_file
    if name == "allure_step":
        from common.base_decorators import allure_step

        return allure_step
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
