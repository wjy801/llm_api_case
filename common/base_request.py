from __future__ import annotations

from copy import deepcopy
import time
from typing import Any
from urllib.parse import urljoin

from jsonpath_ng.ext import parse
import requests

from config import Settings, settings
from common.base_decorators import download_links_from_poll_get
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
    calculate_retry_delay,
    is_method_retry_allowed,
    retry_reason_for_exception,
    retry_reason_for_response,
    should_retry_exception,
    should_retry_response,
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
    ):
        self.config = config
        self.session = requests.Session()
        self.default_headers = self._build_default_headers()
        self.session.headers.update(self.default_headers)
        self.middlewares = list(self._default_middlewares() if middlewares is None else middlewares)

    def _default_middlewares(self) -> list[RequestMiddleware]:
        return default_request_middlewares()

    def request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        attach_log = kwargs.pop("_attach_log", True)
        retry_policy = kwargs.pop("retry_policy", None)
        if retry_policy is not None:
            return self._send_with_retry(method, path, retry_policy, attach_log=attach_log, **kwargs)

        context = self._build_request_context(method, path, attach_log=attach_log, **kwargs)
        return self._send(context)

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
        success_json_path: str | None = None,
        failure_json_path: str | None = None,
        polling_policy: PollingPolicy | None = None,
        retry_policy: RetryPolicy | None = None,
        **kwargs: Any,
    ) -> requests.Response:
        if poll_interval <= 0:
            raise ValueError("poll_interval must be greater than 0")

        timeout = self.config.timeout if poll_timeout is None else poll_timeout
        if timeout <= 0:
            raise ValueError("poll_timeout must be greater than 0")

        if polling_policy is not None:
            return self._poll_get_with_policy(
                path,
                poll_interval=poll_interval,
                timeout=timeout,
                polling_policy=polling_policy,
                retry_policy=retry_policy,
                **kwargs,
            )

        deadline = time.monotonic() + timeout
        last_response: requests.Response
        last_status: Any
        last_logger: ApiCallLogger | None = None

        while True:
            last_response, last_logger = self._request_without_attach(
                "GET",
                path,
                step_name=POLL_GET_REQUEST_STEP_NAME,
                response_step_name=POLL_GET_RESPONSE_STEP_NAME,
                retry_policy=retry_policy,
                **kwargs,
            )
            failure_status = None
            try:
                if failure_json_path is not None:
                    failure_status = self._extract_json_path_value(last_response, failure_json_path)
                last_status = self._extract_json_path_value(last_response, success_json_path)
            except Exception:
                last_logger.attach_success(last_response)
                raise

            if failure_json_path is not None and failure_status is not None:
                last_logger.attach_success(last_response)
                raise AssertionError(
                    f"poll_get failed: path={path!r}, "
                    f"{failure_json_path}={failure_status!r}, "
                    f"response={last_response.text}"
                )

            if last_status is not None:
                last_logger.attach_success(last_response)
                return last_response

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                last_logger.attach_success(last_response)
                raise TimeoutError(
                    f"poll_get timed out after {timeout} seconds: path={path!r}, "
                    f"last {success_json_path}={last_status!r}, "
                    f"last response={last_response.text if last_response is not None else '<empty>'}"
                )

            time.sleep(min(poll_interval, remaining))

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

    def _merge_headers(self, headers: dict[str, str]) -> dict[str, str]:
        merged = dict(self.session.headers)
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
        **kwargs: Any,
    ) -> RequestContext:
        url = self._build_url(path)
        request_kwargs = self._copy_request_kwargs(kwargs)
        request_kwargs.setdefault("timeout", self.config.timeout)

        headers = request_kwargs.pop("headers", None)
        if headers:
            request_kwargs["headers"] = self._merge_headers(headers)

        return RequestContext(
            method=method.upper(),
            path=path,
            url=url,
            kwargs=request_kwargs,
            attach_log=attach_log,
            request_step_name=request_step_name,
            response_step_name=response_step_name,
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

    def _send_with_retry(
        self,
        method: str,
        path: str,
        retry_policy: RetryPolicy,
        *,
        attach_log: bool = True,
        request_step_name: str = API_REQUEST_STEP_NAME,
        response_step_name: str = API_RESPONSE_STEP_NAME,
        context_recorder: list[RequestContext] | None = None,
        **kwargs: Any,
    ) -> requests.Response:
        first_context = self._build_request_context(
            method,
            path,
            attach_log=attach_log,
            request_step_name=request_step_name,
            response_step_name=response_step_name,
            **kwargs,
        )
        if context_recorder is not None:
            context_recorder[:] = [first_context]
        if not is_method_retry_allowed(
            first_context.method,
            self._kwargs_with_session_headers(first_context.kwargs),
            retry_policy,
        ):
            return self._send(first_context)

        started_at = time.monotonic()
        retry_records: list[RetryAttemptRecord] = []
        last_response: requests.Response | None = None

        for attempt_index in range(1, retry_policy.max_attempts + 1):
            context = self._build_request_context(
                method,
                path,
                attach_log=attach_log,
                request_step_name=request_step_name,
                response_step_name=response_step_name,
                **kwargs,
            )
            context.attributes["attempt_index"] = attempt_index
            context.attributes["max_attempts"] = retry_policy.max_attempts
            context.attributes["retry_records"] = retry_records
            if context_recorder is not None:
                context_recorder[:] = [context]

            try:
                response = self._send(context)
            except Exception as error:
                if (
                    attempt_index >= retry_policy.max_attempts
                    or not should_retry_exception(error, retry_policy)
                ):
                    self._attach_retry_records(context, retry_records)
                    raise

                wait_seconds = self._retry_wait_seconds(
                    retry_policy,
                    attempt_index,
                    started_at=started_at,
                )
                retry_records.append(
                    RetryAttemptRecord(
                        attempt_index=attempt_index,
                        max_attempts=retry_policy.max_attempts,
                        reason=retry_reason_for_exception(error),
                        wait_seconds=wait_seconds,
                        exception_type=type(error).__name__,
                        exception_message=str(error),
                    )
                )
                self._attach_retry_records(context, retry_records)
                if not self._can_retry_within_elapsed(retry_policy, started_at, wait_seconds):
                    raise
                time.sleep(wait_seconds)
                continue

            last_response = response
            if attempt_index >= retry_policy.max_attempts or not should_retry_response(response, retry_policy):
                self._attach_retry_records(context, retry_records)
                return response

            wait_seconds = self._retry_wait_seconds(
                retry_policy,
                attempt_index,
                started_at=started_at,
                response=response,
            )
            retry_records.append(
                RetryAttemptRecord(
                    attempt_index=attempt_index,
                    max_attempts=retry_policy.max_attempts,
                    reason=retry_reason_for_response(response),
                    wait_seconds=wait_seconds,
                    response_status_code=response.status_code,
                )
            )
            self._attach_retry_records(context, retry_records)
            if not self._can_retry_within_elapsed(retry_policy, started_at, wait_seconds):
                return response
            time.sleep(wait_seconds)

        if last_response is not None:
            return last_response
        raise RuntimeError("retry loop ended without response or exception")

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
        **kwargs: Any,
    ) -> tuple[requests.Response, ApiCallLogger]:
        context = self._build_request_context(
            method,
            path,
            attach_log=False,
            request_step_name=step_name,
            response_step_name=response_step_name or API_RESPONSE_STEP_NAME,
            **kwargs,
        )
        try:
            if retry_policy is None:
                response = self._send(context)
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
                    context_recorder=context_recorder,
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
        **kwargs: Any,
    ) -> requests.Response:
        deadline = time.monotonic() + timeout
        started_at = time.monotonic()
        transitions: list[PollingTransition] = []
        last_response: requests.Response | None = None
        last_status: Any = None
        last_logger: ApiCallLogger | None = None
        attempt_index = 0

        while True:
            attempt_index += 1
            last_response, last_logger = self._request_without_attach(
                "GET",
                path,
                step_name=POLL_GET_REQUEST_STEP_NAME,
                response_step_name=POLL_GET_RESPONSE_STEP_NAME,
                retry_policy=retry_policy,
                **kwargs,
            )
            evaluation = evaluate_polling_response(last_response, polling_policy)
            last_status = evaluation.raw_status
            transitions.append(
                PollingTransition(
                    attempt_index=attempt_index,
                    elapsed_seconds=round(time.monotonic() - started_at, 3),
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

            remaining = deadline - time.monotonic()
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

            time.sleep(min(poll_interval, remaining))

    @staticmethod
    def _get_optional_api_call_logger(context: RequestContext) -> ApiCallLogger:
        try:
            return LoggingMiddleware.get_logger(context)
        except RuntimeError:
            return NoopApiCallLogger()

    @staticmethod
    def _extract_json_path_value(response: requests.Response, json_path: str) -> Any:
        if not json_path.startswith("$"):
            raise ValueError(f"json_path must start with '$', current value: {json_path!r}")

        try:
            body = response.json()
        except ValueError as exc:
            raise AssertionError(f"response body is not valid JSON: {response.text}") from exc

        matches = [match.value for match in parse(json_path).find(body)]
        if not matches:
            return None
        return matches[0] if len(matches) == 1 else matches

    @staticmethod
    def _retry_wait_seconds(
        retry_policy: RetryPolicy,
        attempt_index: int,
        *,
        started_at: float,
        response: requests.Response | None = None,
    ) -> float:
        return calculate_retry_delay(retry_policy, attempt_index, response=response)

    @staticmethod
    def _can_retry_within_elapsed(
        retry_policy: RetryPolicy,
        started_at: float,
        wait_seconds: float,
    ) -> bool:
        if retry_policy.max_elapsed is None:
            return True
        return (time.monotonic() - started_at + wait_seconds) <= retry_policy.max_elapsed

    def _kwargs_with_session_headers(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        retry_kwargs = dict(kwargs)
        merged_headers = dict(self.session.headers)
        merged_headers.update(dict(kwargs.get("headers") or {}))
        retry_kwargs["headers"] = merged_headers
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
