"""SSE (Server-Sent Events) formatting utilities.

Pure functions that convert agent events into SSE-wire-format strings.
"""

from __future__ import annotations

import json

from .types import AgentEvent


def format_sse_event(event: AgentEvent) -> str:
    """Format an AgentEvent as a Server-Sent Event (SSE) string.

    The result follows the SSE format:
        event: <type>\n
        data: <json>\n
        \n
    """
    payload = json.dumps(event.data, ensure_ascii=False)
    return f"event: {event.type}\ndata: {payload}\n\n"
