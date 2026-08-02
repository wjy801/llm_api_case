from __future__ import annotations

from collections.abc import Iterator

import requests

from common.runtime_hooks import (
    RuntimeStreamOutcome,
    finish_stream,
    get_stream_lease,
    observe_stream_line,
)


def iter_sse_lines(response: requests.Response) -> Iterator[str]:
    """Iterate decoded SSE lines while emitting fail-open lifecycle facts."""
    stream_lease = get_stream_lease(response)
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
            observe_stream_line(stream_lease, line)
            if line.strip() == "data: [DONE]":
                completed = True
            yield line
        exhausted = True
    except GeneratorExit:
        raise
    except (KeyboardInterrupt, SystemExit):
        finish_stream(stream_lease, RuntimeStreamOutcome.INTERRUPTED)
        raise
    except BaseException:
        finish_stream(stream_lease, RuntimeStreamOutcome.ERROR)
        raise
    finally:
        if completed:
            finish_stream(stream_lease, RuntimeStreamOutcome.COMPLETE)
        elif exhausted:
            finish_stream(stream_lease, RuntimeStreamOutcome.INTERRUPTED)
        else:
            finish_stream(stream_lease, RuntimeStreamOutcome.INTERRUPTED)
