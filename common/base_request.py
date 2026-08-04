from __future__ import annotations

from copy import deepcopy
import time
from typing import Any
from urllib.parse import urljoin

import requests

from config import Settings, settings
from common.base_decorators import download_links_from_poll_get
from common.capture import CapturePolicy, DEFAULT_CAPTURE_POLICY
from common.polling import (
    PollingFailedError,
    PollingPolicy,
    PollingState,
    PollingTimeoutError,
    PollingTransition,
    PollingUnknownStateError,
    evaluate_polling_response,
    format_polling_transitions,
)
from common.request_context import RequestContext
from common.request_middleware import (
    LoggingMiddleware,
    RequestMiddleware,
    default_request_middlewares,
)
from common.retry import (
    RetryAttemptRecord,
    RetryPolicy,
)
from common.retry_executor import RetryDeadlineExceeded, RetryExecutor
from common.runtime_hooks import (
    RuntimeOperationKind,
    RuntimeOperationOutcome,
    RuntimeObserver,
    RuntimePollingObservation,
    operation_outcome_for_error,
)
from util import (
    API_REQUEST_STEP_NAME,
    ApiCallLogger,
    API_RESPONSE_STEP_NAME,
    POLL_GET_REQUEST_STEP_NAME,
    POLL_GET_RESPONSE_STEP_NAME,
)


class BaseRequest:
    def __init__(
        self,
        config: Settings = settings,
        middlewares: list[RequestMiddleware] | None = None,
        retry_executor: RetryExecutor | None = None,
        capture_policy: CapturePolicy | None = None,
    ):
        self.config = config
        self.session = requests.Session()
        self.default_headers = self._build_default_headers()
        self.session.headers.update(self.default_headers)
        self.capture_policy = capture_policy or DEFAULT_CAPTURE_POLICY
        self.middlewares = list(self._default_middlewares() if middlewares is None else middlewares)
        self.retry_executor = retry_executor or RetryExecutor(sleeper=time.sleep, monotonic=time.monotonic)
        self._runtime_observer = RuntimeObserver()

    def _default_middlewares(self) -> list[RequestMiddleware]:
        return default_request_middlewares(self.capture_policy)

    def request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        attach_log = kwargs.pop("_attach_log", True)
        retry_policy = kwargs.pop("retry_policy", None)
        inherit_session_headers = bool(kwargs.pop("_inherit_session_headers", True))
        stream = bool(kwargs.get("stream"))
        operation_kind = RuntimeOperationKind.SSE if stream else RuntimeOperationKind.HTTP
        metadata = self._runtime_observer.normalize_metadata(
            kwargs,
            kind=operation_kind,
            default_name="sse_request" if stream else "http_request",
        )
        operation = self._runtime_observer.start_operation(metadata)
        try:
            if retry_policy is not None:
                response = self._send_with_retry(
                    method,
                    path,
                    retry_policy,
                    attach_log=attach_log,
                    inherit_session_headers=inherit_session_headers,
                    **kwargs,
                )
            else:
                context = self._build_request_context(
                    method,
                    path,
                    attach_log=attach_log,
                    inherit_session_headers=inherit_session_headers,
                    **kwargs,
                )
                response = self._send_single_group(context)
        except BaseException as error:
            operation.finish_error(error)
            raise
        operation.finish_response(response, stream=stream)
        return response

    def set_header(self, name: str, value: str) -> None:
        self.session.headers[name] = value

    def update_headers(self, headers: dict[str, str]) -> None:
        self.session.headers.update(headers)

    def remove_header(self, name: str) -> None:
        self.session.headers.pop(name, None)

    def reset_headers(self) -> None:
        self.session.headers.clear()
        self.session.headers.update(self.default_headers)

    def get(self, path: str, **kwargs: Any) -> requests.Response:
        return self.request("GET", path, **kwargs)

    @download_links_from_poll_get
    def poll_get(
        self,
        path: str,
        *,
        poll_interval: float = 2,
        poll_timeout: float | None = None,
        polling_policy: PollingPolicy,
        retry_policy: RetryPolicy | None = None,
        **kwargs: Any,
    ) -> requests.Response:
        if poll_interval <= 0:
            raise ValueError("poll_interval must be greater than 0")

        timeout = self.config.timeout if poll_timeout is None else poll_timeout
        if timeout <= 0:
            raise ValueError("poll_timeout must be greater than 0")

        metadata = self._runtime_observer.normalize_metadata(
            kwargs,
            kind=RuntimeOperationKind.POLLING,
            default_name="polling",
        )
        polling = self._runtime_observer.start_polling(metadata)
        try:
            response = self._poll_get_with_policy(
                path,
                poll_interval=poll_interval,
                timeout=timeout,
                polling_policy=polling_policy,
                retry_policy=retry_policy,
                runtime_polling=polling,
                **kwargs,
            )
        except BaseException as error:
            polling.finish_error(error)
            raise
        polling.finish_success()
        return response

    def post(self, path: str, **kwargs: Any) -> requests.Response:
        return self.request("POST", path, **kwargs)

    def put(self, path: str, **kwargs: Any) -> requests.Response:
        return self.request("PUT", path, **kwargs)

    def patch(self, path: str, **kwargs: Any) -> requests.Response:
        return self.request("PATCH", path, **kwargs)

    def delete(self, path: str, **kwargs: Any) -> requests.Response:
        return self.request("DELETE", path, **kwargs)

    def close(self) -> None:
        self.session.close()

    def _build_url(self, path: str) -> str:
        if path.startswith(("http://", "https://")):
            return path
        return urljoin(f"{self.config.base_url}/", path.lstrip("/"))

    def _build_default_headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "api-v1_chat_completions-framework",
            "Authorization": f"Bearer {self.config.api_key}",
        }
        return headers

    def _merge_headers(
        self,
        headers: dict[str, Any],
        *,
        inherit_session_headers: bool = True,
    ) -> dict[str, Any]:
        merged: dict[str, Any]
        if inherit_session_headers:
            merged = dict(self.session.headers)
        else:
            merged = {str(name): None for name in self.session.headers}
        merged.update(headers)
        return merged

    @staticmethod
    def _copy_request_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
        copied_kwargs: dict[str, Any] = {}
        for name, value in kwargs.items():
            try:
                copied_kwargs[name] = deepcopy(value)
            except Exception:
                copied_kwargs[name] = value
        return copied_kwargs

    def _build_request_context(
        self,
        method: str,
        path: str,
        *,
        attach_log: bool = True,
        request_step_name: str = API_REQUEST_STEP_NAME,
        response_step_name: str = API_RESPONSE_STEP_NAME,
        protocol: str | None = None,
        retry_policy: RetryPolicy | None = None,
        polling_policy: PollingPolicy | None = None,
        inherit_session_headers: bool = True,
        deadline: float | None = None,
        **kwargs: Any,
    ) -> RequestContext:
        url = self._build_url(path)
        request_kwargs = self._copy_request_kwargs(kwargs)
        request_kwargs.setdefault("timeout", self.config.timeout)
        request_kwargs["timeout"] = self.retry_executor.clamp_timeout(
            request_kwargs["timeout"],
            deadline,
        )

        headers = dict(request_kwargs.pop("headers", None) or {})
        request_kwargs["headers"] = self._merge_headers(
            headers,
            inherit_session_headers=inherit_session_headers,
        )

        return RequestContext(
            method=method.upper(),
            path=path,
            url=url,
            kwargs=request_kwargs,
            attach_log=attach_log,
            request_step_name=request_step_name,
            response_step_name=response_step_name,
            protocol=protocol or ("sse" if request_kwargs.get("stream") else "http"),
            retry_policy=retry_policy,
            polling_policy=polling_policy,
        )

    def _send(self, context: RequestContext) -> requests.Response:
        self._run_before_middlewares(context)

        try:
            response = self.session.request(
                method=context.method,
                url=context.url,
                **context.kwargs,
            )
        except Exception as error:
            self._run_exception_middlewares(context, error)
            raise

        self._run_after_middlewares(context, response)
        return response

    def _send_single_group(self, context: RequestContext) -> requests.Response:
        group = self._runtime_observer.start_request_group(
            method=context.method,
            path=context.path,
            protocol=context.protocol,
            configured_max_attempts=1,
        )
        group.bind(context)
        try:
            return self._send(context)
        finally:
            group.finish()

    def _send_with_retry(
        self,
        method: str,
        path: str,
        retry_policy: RetryPolicy,
        *,
        attach_log: bool = True,
        request_step_name: str = API_REQUEST_STEP_NAME,
        response_step_name: str = API_RESPONSE_STEP_NAME,
        protocol: str | None = None,
        polling_policy: PollingPolicy | None = None,
        context_recorder: list[RequestContext] | None = None,
        inherit_session_headers: bool = True,
        deadline: float | None = None,
        **kwargs: Any,
    ) -> requests.Response:
        first_context = self._build_request_context(
            method,
            path,
            attach_log=attach_log,
            request_step_name=request_step_name,
            response_step_name=response_step_name,
            protocol=protocol,
            retry_policy=retry_policy,
            polling_policy=polling_policy,
            inherit_session_headers=inherit_session_headers,
            deadline=deadline,
            **kwargs,
        )

        group = self._runtime_observer.start_request_group(
            method=first_context.method,
            path=first_context.path,
            protocol=first_context.protocol,
            configured_max_attempts=retry_policy.max_attempts,
        )
        def context_factory(attempt_index: int) -> RequestContext:
            context = self._build_request_context(
                method,
                path,
                attach_log=attach_log,
                request_step_name=request_step_name,
                response_step_name=response_step_name,
                protocol=protocol,
                retry_policy=retry_policy,
                polling_policy=polling_policy,
                inherit_session_headers=inherit_session_headers,
                deadline=deadline,
                **kwargs,
            )
            context.attributes["attempt_index"] = attempt_index
            context.attributes["max_attempts"] = retry_policy.max_attempts
            group.bind(context)
            return context
        try:
            return self.retry_executor.execute(
                method=first_context.method,
                request_kwargs=self._kwargs_with_session_headers(first_context.kwargs),
                policy=retry_policy,
                context_factory=context_factory,
                send_once=self._send,
                attach_records=self._attach_retry_records,
                context_recorder=context_recorder,
                on_wait=group.add_retry_wait,
                deadline=deadline,
            )
        finally:
            group.finish()

    def _run_before_middlewares(self, context: RequestContext) -> None:
        for middleware in self.middlewares:
            try:
                middleware.before_request(context)
            except Exception as error:
                raise RuntimeError(
                    f"Request middleware {type(middleware).__name__} failed in before_request"
                ) from error

    def _run_after_middlewares(self, context: RequestContext, response: requests.Response) -> None:
        for middleware in self.middlewares:
            try:
                middleware.after_response(context, response)
            except Exception as error:
                raise RuntimeError(
                    f"Request middleware {type(middleware).__name__} failed in after_response"
                ) from error

    def _run_exception_middlewares(self, context: RequestContext, request_error: BaseException) -> None:
        middleware_errors: list[RuntimeError] = []
        for middleware in self.middlewares:
            try:
                middleware.on_exception(context, request_error)
            except Exception as error:
                middleware_errors.append(
                    RuntimeError(
                        f"Request middleware {type(middleware).__name__} failed in on_exception"
                    )
                )
                middleware_errors[-1].__cause__ = error
        if middleware_errors:
            context.attributes["middleware_exception_errors"] = middleware_errors
            for middleware_error in middleware_errors:
                request_error.add_note(str(middleware_error))

    def _request_without_attach(
        self,
        method: str,
        path: str,
        *,
        step_name: str = API_REQUEST_STEP_NAME,
        response_step_name: str | None = None,
        retry_policy: RetryPolicy | None = None,
        protocol: str | None = None,
        polling_policy: PollingPolicy | None = None,
        inherit_session_headers: bool = True,
        deadline: float | None = None,
        **kwargs: Any,
    ) -> tuple[requests.Response, ApiCallLogger]:
        context = self._build_request_context(
            method,
            path,
            attach_log=False,
            request_step_name=step_name,
            response_step_name=response_step_name or API_RESPONSE_STEP_NAME,
            protocol=protocol,
            retry_policy=retry_policy,
            polling_policy=polling_policy,
            inherit_session_headers=inherit_session_headers,
            deadline=deadline,
            **kwargs,
        )
        try:
            if retry_policy is None:
                response = self._send_single_group(context)
                response_context = context
            else:
                context_recorder: list[RequestContext] = []
                response = self._send_with_retry(
                    method,
                    path,
                    retry_policy,
                    attach_log=False,
                    request_step_name=step_name,
                    response_step_name=response_step_name or API_RESPONSE_STEP_NAME,
                    protocol=protocol,
                    polling_policy=polling_policy,
                    context_recorder=context_recorder,
                    inherit_session_headers=inherit_session_headers,
                    deadline=deadline,
                    **kwargs,
                )
                response_context = context_recorder[-1] if context_recorder else context
        except Exception as error:
            logger_context = context_recorder[-1] if retry_policy is not None and context_recorder else context
            logger = self._get_optional_api_call_logger(logger_context)
            logger.attach_failure(error)
            raise
        logger = self._get_optional_api_call_logger(response_context)
        return response, logger

    def _poll_get_with_policy(
        self,
        path: str,
        *,
        poll_interval: float,
        timeout: float,
        polling_policy: PollingPolicy,
        retry_policy: RetryPolicy | None = None,
        runtime_polling: RuntimePollingObservation | None = None,
        **kwargs: Any,
    ) -> requests.Response:
        started_at = self.retry_executor.monotonic()
        deadline = started_at + timeout
        transitions: list[PollingTransition] = []
        last_response: requests.Response | None = None
        last_status: Any = None
        last_logger: ApiCallLogger | None = None
        attempt_index = 0

        while True:
            attempt_index += 1
            try:
                last_response, last_logger = self._request_without_attach(
                    "GET",
                    path,
                    step_name=POLL_GET_REQUEST_STEP_NAME,
                    response_step_name=POLL_GET_RESPONSE_STEP_NAME,
                    retry_policy=retry_policy,
                    protocol="polling",
                    polling_policy=polling_policy,
                    deadline=deadline,
                    **kwargs,
                )
            except RetryDeadlineExceeded as error:
                raise PollingTimeoutError(
                    path=path,
                    timeout=timeout,
                    last_status=last_status,
                    last_response=(
                        error.last_response
                        if error.last_response is not None
                        else last_response
                    ),
                    transitions=transitions,
                ) from error
            try:
                evaluation = evaluate_polling_response(last_response, polling_policy)
            except Exception:
                last_logger.attach_success(last_response)
                raise

            last_status = evaluation.raw_status
            if runtime_polling is not None:
                runtime_polling.observe_state(evaluation.state.value)
            transitions.append(
                PollingTransition(
                    attempt_index=attempt_index,
                    elapsed_seconds=round(self.retry_executor.monotonic() - started_at, 3),
                    state=evaluation.state,
                    raw_status=evaluation.raw_status,
                    response_status_code=last_response.status_code,
                )
            )

            if evaluation.state is PollingState.SUCCESS:
                self._attach_polling_transitions(last_logger, transitions)
                last_logger.attach_success(last_response)
                return last_response

            if evaluation.state is PollingState.FAILURE:
                self._attach_polling_transitions(last_logger, transitions)
                last_logger.attach_success(last_response)
                raise PollingFailedError(
                    path=path,
                    last_status=last_status,
                    last_response=last_response,
                    transitions=transitions,
                    error_value=evaluation.error_value,
                )

            if evaluation.state is PollingState.UNKNOWN:
                self._attach_polling_transitions(last_logger, transitions)
                last_logger.attach_success(last_response)
                raise PollingUnknownStateError(
                    path=path,
                    last_status=last_status,
                    last_response=last_response,
                    transitions=transitions,
                )

            remaining = deadline - self.retry_executor.monotonic()
            if remaining <= 0:
                self._attach_polling_transitions(last_logger, transitions)
                last_logger.attach_success(last_response)
                raise PollingTimeoutError(
                    path=path,
                    timeout=timeout,
                    last_status=last_status,
                    last_response=last_response,
                    transitions=transitions,
                )

            sleep_seconds = min(poll_interval, remaining)
            sleep_started_at = self.retry_executor.monotonic()
            try:
                self.retry_executor.sleeper(sleep_seconds)
            finally:
                if runtime_polling is not None:
                    runtime_polling.add_sleep(
                        self.retry_executor.monotonic() - sleep_started_at,
                    )

    @staticmethod
    def _get_optional_api_call_logger(context: RequestContext) -> ApiCallLogger:
        try:
            return LoggingMiddleware.get_logger(context)
        except RuntimeError:
            return NoopApiCallLogger()

    def _kwargs_with_session_headers(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        retry_kwargs = dict(kwargs)
        retry_kwargs["headers"] = dict(kwargs.get("headers") or {})
        return retry_kwargs

    @staticmethod
    def _attach_retry_records(context: RequestContext, records: list[RetryAttemptRecord]) -> None:
        if not records:
            return
        logger = BaseRequest._get_optional_api_call_logger(context)
        logger.attach_retry_records(records)

    @staticmethod
    def _attach_polling_transitions(
        logger: ApiCallLogger,
        transitions: list[PollingTransition],
    ) -> None:
        logger.attach_polling_transitions(format_polling_transitions(transitions))

    @staticmethod
    def _operation_outcome_for_error(error: BaseException) -> RuntimeOperationOutcome:
        return operation_outcome_for_error(error)


class NoopApiCallLogger(ApiCallLogger):
    def __init__(self) -> None:
        pass

    def attach_success(self, response: requests.Response) -> None:
        return None

    def attach_failure(self, error: BaseException) -> None:
        return None

    def attach_retry_records(self, records: list[RetryAttemptRecord]) -> None:
        return None

    def attach_polling_transitions(self, transitions_text: str) -> None:
        return None
