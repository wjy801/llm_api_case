from __future__ import annotations

from typing import Protocol, cast

import requests

from common.request_context import RequestContext
from util import ApiCallLogger, start_media_downloads
from util.redaction import redact_request_kwargs


class RequestMiddleware(Protocol):
    def before_request(self, context: RequestContext) -> None:
        ...

    def after_response(self, context: RequestContext, response: requests.Response) -> None:
        ...

    def on_exception(self, context: RequestContext, error: BaseException) -> None:
        ...


class RedactionMiddleware:
    REDACTED_KWARGS_ATTR = "redacted_kwargs"

    def before_request(self, context: RequestContext) -> None:
        context.attributes[self.REDACTED_KWARGS_ATTR] = redact_request_kwargs(context.kwargs)

    def after_response(self, context: RequestContext, response: requests.Response) -> None:
        return None

    def on_exception(self, context: RequestContext, error: BaseException) -> None:
        return None


class LoggingMiddleware:
    LOGGER_ATTR = "api_call_logger"

    def before_request(self, context: RequestContext) -> None:
        logger_kwargs = context.attributes.get(RedactionMiddleware.REDACTED_KWARGS_ATTR, context.kwargs)
        context.attributes[self.LOGGER_ATTR] = ApiCallLogger(
            context.method,
            context.url,
            logger_kwargs,
            step_name=context.request_step_name,
            response_step_name=context.response_step_name,
        )

    def after_response(self, context: RequestContext, response: requests.Response) -> None:
        if not context.attach_log:
            return
        self.get_logger(context).attach_success(response)

    def on_exception(self, context: RequestContext, error: BaseException) -> None:
        if not context.attach_log:
            return
        self.get_logger(context).attach_failure(error)

    @classmethod
    def get_logger(cls, context: RequestContext) -> ApiCallLogger:
        logger = context.attributes.get(cls.LOGGER_ATTR)
        if logger is None:
            raise RuntimeError("api call logger is missing from request context")
        return cast(ApiCallLogger, logger)


class MediaResourceMiddleware:
    def before_request(self, context: RequestContext) -> None:
        if context.method == "POST":
            start_media_downloads(context.kwargs.get("json"))

    def after_response(self, context: RequestContext, response: requests.Response) -> None:
        return None

    def on_exception(self, context: RequestContext, error: BaseException) -> None:
        return None


def default_request_middlewares() -> list[RequestMiddleware]:
    return [
        MediaResourceMiddleware(),
        RedactionMiddleware(),
        LoggingMiddleware(),
    ]
