"""SSE (Server-Sent Events) formatting utilities.

Pure functions that convert agent events into SSE-wire-format strings
and handle the backward-compatible token-stream suffix logic.
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


def unstreamed_suffix(streamed_text: str, final_text: str) -> str:
    """Return the portion of *final_text* not yet emitted via streaming.

    Used by ``stream_answer()`` — the backward-compat helper that
    converts the full event stream into a plain token stream.
    """
    if not streamed_text:
        return final_text
    if final_text.startswith(streamed_text):
        return final_text[len(streamed_text) :]
    return ""
