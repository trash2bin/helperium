"""Anti-abuse and rate-limiting checks — extracted from chat routes."""

from __future__ import annotations

import asyncio
import time

from fastapi.responses import StreamingResponse

from api_service.abuse_live import get_live_abuse_provider
from api_service.sessions import session_store
from .sse import _single_error


def _get_lang_from_request(request) -> str:
    """Extract language from Accept-Language header."""
    accept = request.headers.get("Accept-Language", "") or ""
    return "ru" if accept.startswith("ru") else "en"


def _make_error_message(request, retry_after: float) -> str:
    """Build localized rate-limit error message."""
    lang = _get_lang_from_request(request)
    if lang == "ru":
        return f"Слишком много запросов. Повторите через {int(retry_after)}с."
    return f"Too many requests. Retry after {retry_after:.0f}s."


async def check_abuse(request, session_id, message, agent_abuse_config=None):
    live = get_live_abuse_provider()
    checker, token_bucket = live.get_enforcers(agent_abuse_config)

    user_agent = request.headers.get("User-Agent", "") or ""
    ip = request.client.host if request.client else "127.0.0.1"
    safe_id = session_id or "unknown"

    allowed, ctx = token_bucket.allow(safe_id, ip, user_agent)
    if not allowed:
        retry_after = ctx.get("retry_after", 1.0)
        msg = _make_error_message(request, retry_after)
        return StreamingResponse(
            _single_error(msg),
            media_type="text/event-stream",
            status_code=429,
            headers={"Retry-After": str(int(retry_after))},
        )

    history = await asyncio.to_thread(session_store.history_messages, safe_id)
    n_msg = len(history)
    last_msg_time = None
    if history:
        last_msg = history[-1]
        ts = last_msg.get("timestamp") or last_msg.get("created_at")
        if ts:
            try:
                last_msg_time = (
                    time.time() - float(ts) if isinstance(ts, (int, float)) else None
                )
            except (ValueError, TypeError):
                pass

    check_result = checker.check(
        session_id=safe_id,
        ip=ip,
        user_agent=user_agent,
        message=message,
        n_msg=n_msg,
        last_msg_time_since=last_msg_time,
    )
    if not check_result.allowed:
        lang = _get_lang_from_request(request)
        msg = (
            f"Запрос заблокирован: {check_result.reason}"
            if lang == "ru"
            else f"Request blocked: {check_result.reason}"
        )
        return StreamingResponse(
            _single_error(msg),
            media_type="text/event-stream",
        )
    return None
