from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from common.polling import PollingPolicy
from common.retry import RetryPolicy
from util import API_REQUEST_STEP_NAME, API_RESPONSE_STEP_NAME


@dataclass
class RequestContext:
    method: str
    path: str
    url: str
    kwargs: dict[str, Any]
    attach_log: bool = True
    request_step_name: str = API_REQUEST_STEP_NAME
    response_step_name: str = API_RESPONSE_STEP_NAME
    protocol: str = "http"
    retry_policy: RetryPolicy | None = None
    polling_policy: PollingPolicy | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
