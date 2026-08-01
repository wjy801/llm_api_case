from __future__ import annotations

from collections.abc import Iterator

import requests

from quality.semantic_context import finish_stream, observe_stream_line, stream_operation_id
from quality.semantic_models import StreamOutcome


def iter_sse_lines(response: requests.Response) -> Iterator[str]:
    """Iterate decoded SSE lines while emitting fail-open lifecycle facts."""
    operation_id = stream_operation_id(response)
    completed = False
    exhausted = False
    try:
        for raw_line in response.iter_lines(decode_unicode=False):
            if not raw_line:
                continue
            line = (
                raw_line.decode("utf-8", errors="replace")
                if isinstance(raw_line, bytes)
                else str(raw_line)
            )
            observe_stream_line(operation_id, line)
            if line.strip() == "data: [DONE]":
                completed = True
            yield line
        exhausted = True
    except GeneratorExit:
        raise
    except (KeyboardInterrupt, SystemExit):
        finish_stream(operation_id, StreamOutcome.INTERRUPTED)
        raise
    except BaseException:
        finish_stream(operation_id, StreamOutcome.ERROR)
        raise
    finally:
        if completed:
            finish_stream(operation_id, StreamOutcome.COMPLETE)
        elif exhausted:
            finish_stream(operation_id, StreamOutcome.INTERRUPTED)
        else:
            finish_stream(operation_id, StreamOutcome.INTERRUPTED)
