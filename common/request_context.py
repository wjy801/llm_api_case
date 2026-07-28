from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

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
    attributes: dict[str, Any] = field(default_factory=dict)
