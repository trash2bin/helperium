"""Anti-abuse and rate-limiting checks — extracted from chat routes."""

from __future__ import annotations

import asyncio
import logging
import time

from .rate_limit import get_client_ip

from fastapi.responses import StreamingResponse

from api_service.abuse_live import get_live_abuse_provider
from api_service.sessions import session_store
from .sse import _single_error


logger = logging.getLogger("api_service.server.security")


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
    ip = get_client_ip(request)
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

    state = await asyncio.to_thread(session_store.abuse_state, safe_id)
    last_user_turn_since = (
        max(0.0, time.time() - state.last_user_turn_at)
        if isinstance(state.last_user_turn_at, (int, float))
        else None
    )

    check_result = checker.check(
        session_id=safe_id,
        ip=ip,
        user_agent=user_agent,
        message=message,
        user_turn_count=state.user_turn_count,
        last_msg_time_since=last_user_turn_since,
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
    accepted_at = time.time()
    try:
        await asyncio.to_thread(session_store.accept_user_turn, safe_id, accepted_at)
    except Exception:
        logger.exception("Failed to persist accepted user turn for session %s", safe_id)
        return StreamingResponse(
            _single_error(
                "Сервис сессий временно недоступен. Попробуйте ещё раз позже."
            ),
            media_type="text/event-stream",
            status_code=503,
        )
    return None
