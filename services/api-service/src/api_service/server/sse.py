"""SSE formatting utilities — shared across route handlers."""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import Request

from api_service.agent.types import AgentEventData

logger = logging.getLogger("api_service.server")


def _sse(payload: dict[str, Any]) -> str:
    """Format a payload as a Server-Sent Event."""
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def _single_error(text: str, correlation_id: str | None = None):
    """Yield a single error event."""
    payload: dict[str, Any] = {"type": "error", "text": text}
    if correlation_id is not None:
        payload["correlation_id"] = correlation_id
    yield _sse(payload)


def _event_payload(event_type: str, data: AgentEventData) -> dict[str, Any] | None:
    """Convert internal agent events to the browser-facing SSE payload."""
    if event_type in ("tool_call", "tool_result", "final", "error", "done"):
        logger.info(f"[SERVER] event_type={event_type}, data={str(data)[:200]}")
    else:
        logger.debug(f"[SERVER] event_type={event_type}, data={str(data)[:200]}")
    if event_type == "final":
        text = data.get("content") if isinstance(data, dict) else ""
        return {"type": "final", "text": text}
    if event_type == "tool_call":
        name = data.get("name", "") if isinstance(data, dict) else ""
        display_name = data.get("display_name", "") or name
        call_id = data.get("id", "") if isinstance(data, dict) else ""
        arguments = data.get("arguments", {}) if isinstance(data, dict) else {}
        payload: dict[str, Any] = {
            "type": "tool_call",
            "id": call_id,
            "name": name,
            "display_name": display_name,
            "arguments": arguments,
        }
        return payload
    if event_type == "tool_result":
        name = data.get("name", "") if isinstance(data, dict) else ""
        display_name = data.get("display_name", "") or name
        result = data.get("result") if isinstance(data, dict) else None
        payload: dict[str, Any] = {
            "type": "tool_result",
            "name": name,
            "display_name": display_name,
        }
        if result is not None:
            payload["result"] = result
        return payload
    if event_type == "error":
        text = data.get("message") if isinstance(data, dict) else data
        return {"type": "error", "text": text}
    if event_type == "audio":
        audio_data = data.get("data", "") if isinstance(data, dict) else ""
        return {"type": "audio", "data": audio_data}
    return None


def _get_lang(request: Request) -> str:
    """Extract language from Accept-Language header."""
    accept = request.headers.get("Accept-Language", "")
    if accept.startswith("ru"):
        return "ru"
    return "en"
