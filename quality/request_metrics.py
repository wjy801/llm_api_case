from __future__ import annotations

from collections.abc import Mapping
import time
from typing import Any

import requests

from common.polling import PollingState, evaluate_polling_response
from common.request_context import RequestContext
from common.retry import (
    is_method_retry_allowed,
    should_retry_exception,
    should_retry_response,
)
from quality.collector import QualityCollector, get_collector
from quality.identifiers import build_interface_id, build_url_template, new_request_event_id
from quality.models import (
    BusinessStatus,
    IssueSeverity,
    Protocol,
    RequestMetric,
    RequestUsage,
)
from quality.runtime_context import get_case_context
from quality.semantic_context import observe_request_metric


REQUEST_EVENT_ID_ATTR = "quality_request_event_id"
REQUEST_STARTED_ATTR = "quality_request_started"
REQUEST_METRIC_WRITTEN_ATTR = "quality_request_metric_written"

_SERVER_REQUEST_ID_HEADERS = (
    "x-oneapi-request-id",
    "x-request-id",
    "request-id",
)


def start_request_capture(context: RequestContext) -> None:
    if get_collector() is None:
        return
    context.attributes[REQUEST_EVENT_ID_ATTR] = new_request_event_id()
    context.attributes[REQUEST_STARTED_ATTR] = time.perf_counter()
    context.attributes[REQUEST_METRIC_WRITTEN_ATTR] = False


def record_response(context: RequestContext, response: requests.Response) -> None:
    collector = get_collector()
    if collector is None or _already_written(context):
        return
    _mark_written(context)

    request_event_id = _request_event_id(context)
    case_context = get_case_context()
    if case_context is None:
        _record_missing_case(collector, request_event_id)
        return

    protocol = _protocol(context)
    response_body = _response_json(response, protocol)
    business_status, error_type = _response_business_status(
        context,
        response,
        protocol,
        collector,
        request_event_id,
    )
    run_context = collector.run_context
    metric = RequestMetric(
        run_id=run_context.run_id,
        execution_id=run_context.execution_id,
        worker_id=run_context.worker_id,
        case_id=case_context.case_id,
        invocation_id=case_context.invocation_id,
        request_event_id=request_event_id,
        server_request_id=_server_request_id(response, response_body, protocol),
        interface_id=build_interface_id(context.method, context.path, protocol),
        method=context.method,
        url_template=build_url_template(context.path),
        protocol=protocol,
        attempt_index=_attempt_index(context),
        status_code=response.status_code,
        business_status=business_status,
        duration_ms=_duration_ms(context),
        timeout=False,
        retryable=_response_retryable(context, response),
        error_type=error_type,
        usage=_usage(response_body, protocol),
    )
    collector.record_request(metric)
    _observe_semantic(context, metric)


def record_exception(context: RequestContext, error: BaseException) -> None:
    collector = get_collector()
    if collector is None or _already_written(context):
        return
    _mark_written(context)

    request_event_id = _request_event_id(context)
    case_context = get_case_context()
    if case_context is None:
        _record_missing_case(collector, request_event_id)
        return

    protocol = _protocol(context)
    run_context = collector.run_context
    metric = RequestMetric(
        run_id=run_context.run_id,
        execution_id=run_context.execution_id,
        worker_id=run_context.worker_id,
        case_id=case_context.case_id,
        invocation_id=case_context.invocation_id,
        request_event_id=request_event_id,
        interface_id=build_interface_id(context.method, context.path, protocol),
        method=context.method,
        url_template=build_url_template(context.path),
        protocol=protocol,
        attempt_index=_attempt_index(context),
        status_code=None,
        business_status=BusinessStatus.FAILED,
        duration_ms=_duration_ms(context),
        timeout=isinstance(error, requests.Timeout),
        retryable=_exception_retryable(context, error),
        error_type=type(error).__name__,
    )
    collector.record_request(metric)
    _observe_semantic(context, metric)


def _response_business_status(
    context: RequestContext,
    response: requests.Response,
    protocol: Protocol,
    collector: QualityCollector,
    request_event_id: str,
) -> tuple[BusinessStatus, str | None]:
    if not 200 <= response.status_code < 300:
        return BusinessStatus.FAILED, None
    if protocol is Protocol.HTTP:
        return BusinessStatus.SUCCESS, None
    if protocol is Protocol.SSE:
        return BusinessStatus.UNKNOWN, None
    if context.polling_policy is None:
        return BusinessStatus.FAILED, "MissingPollingPolicy"
    try:
        evaluation = evaluate_polling_response(response, context.polling_policy)
    except Exception as error:
        collector.capture_integrity(
            source="request_metrics",
            code="polling_metric_evaluation_failed",
            message=f"{type(error).__name__}: {error}",
            related_id=request_event_id,
            severity=IssueSeverity.WARN,
        )
        return BusinessStatus.FAILED, type(error).__name__

    if evaluation.state is PollingState.SUCCESS:
        return BusinessStatus.SUCCESS, None
    if evaluation.state is PollingState.PENDING:
        return BusinessStatus.UNKNOWN, None
    return BusinessStatus.FAILED, None


def _response_retryable(context: RequestContext, response: requests.Response) -> bool:
    if context.retry_policy is None:
        return False
    if not is_method_retry_allowed(context.method, context.kwargs, context.retry_policy):
        return False
    return should_retry_response(response, context.retry_policy)


def _exception_retryable(context: RequestContext, error: BaseException) -> bool:
    if context.retry_policy is None:
        return False
    if not is_method_retry_allowed(context.method, context.kwargs, context.retry_policy):
        return False
    return should_retry_exception(error, context.retry_policy)


def _server_request_id(
    response: requests.Response,
    body: Mapping[str, Any] | None,
    protocol: Protocol,
) -> str | None:
    for header in _SERVER_REQUEST_ID_HEADERS:
        value = response.headers.get(header)
        if value and value.strip():
            return value.strip()
    if protocol is Protocol.SSE or body is None:
        return None
    value = body.get("request_id")
    return value.strip() if isinstance(value, str) and value.strip() else None


def _usage(body: Mapping[str, Any] | None, protocol: Protocol) -> RequestUsage:
    if protocol is Protocol.SSE or body is None:
        return RequestUsage()
    raw_usage = body.get("usage")
    if not isinstance(raw_usage, Mapping):
        raw_usage = {}
    return RequestUsage(
        input_tokens=_first_value(
            _non_negative_int(raw_usage.get("input_tokens")),
            _non_negative_int(raw_usage.get("prompt_tokens")),
        ),
        output_tokens=_first_value(
            _non_negative_int(raw_usage.get("output_tokens")),
            _non_negative_int(raw_usage.get("completion_tokens")),
        ),
        media_count=_media_count(body),
    )


def _media_count(body: Mapping[str, Any]) -> int | None:
    data = body.get("data")
    if isinstance(data, list):
        media_items = [
            item
            for item in data
            if isinstance(item, Mapping)
            and any(name in item for name in ("url", "b64_json", "image_url", "video_url"))
        ]
        if media_items:
            return len(media_items)
    result = body.get("result")
    if not isinstance(result, Mapping):
        return None
    urls = result.get("urls")
    if isinstance(urls, list):
        return len(urls)
    if any(result.get(name) for name in ("url", "b64_json", "image_url", "video_url")):
        return 1
    return None


def _response_json(
    response: requests.Response,
    protocol: Protocol,
) -> Mapping[str, Any] | None:
    if protocol is Protocol.SSE:
        return None
    try:
        body = response.json()
    except Exception:
        return None
    return body if isinstance(body, Mapping) else None


def _protocol(context: RequestContext) -> Protocol:
    return Protocol(context.protocol)


def _attempt_index(context: RequestContext) -> int:
    value = context.attributes.get("attempt_index", 1)
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 1 else 1


def _duration_ms(context: RequestContext) -> float:
    started_at = context.attributes.get(REQUEST_STARTED_ATTR)
    if not isinstance(started_at, (int, float)):
        return 0.0
    return max((time.perf_counter() - float(started_at)) * 1000, 0.0)


def _request_event_id(context: RequestContext) -> str:
    value = context.attributes.get(REQUEST_EVENT_ID_ATTR)
    if isinstance(value, str) and value.strip():
        return value
    value = new_request_event_id()
    context.attributes[REQUEST_EVENT_ID_ATTR] = value
    return value


def _non_negative_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _first_value(primary: int | None, fallback: int | None) -> int | None:
    return primary if primary is not None else fallback


def _record_missing_case(collector: QualityCollector, request_event_id: str) -> None:
    collector.capture_integrity(
        source="request_metrics",
        code="missing_case_context",
        message="request metric skipped because case context is missing",
        related_id=request_event_id,
        severity=IssueSeverity.WARN,
    )


def _already_written(context: RequestContext) -> bool:
    return bool(context.attributes.get(REQUEST_METRIC_WRITTEN_ATTR, False))


def _mark_written(context: RequestContext) -> None:
    context.attributes[REQUEST_METRIC_WRITTEN_ATTR] = True


def _observe_semantic(context: RequestContext, metric: RequestMetric) -> None:
    try:
        observe_request_metric(context, metric)
    except Exception:
        return
