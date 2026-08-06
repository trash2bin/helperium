"""Backlog endpoints — session history, stats, export."""

from __future__ import annotations
import json
import logging
from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
from api_service.http_models import (
    BacklogDetailResponse,
    BacklogEvent,
    BacklogListResponse,
    BacklogSessionMetadata,
    SessionHistoryResponse,
    ChatMessage,
)
from api_service.backlog import backlog
from api_service.sessions import session_store
import asyncio

logger = logging.getLogger("api_service.server")
router = APIRouter()


async def get_backlog_list():
    sessions = backlog.list_sessions()
    return BacklogListResponse(sessions=[BacklogSessionMetadata(**s) for s in sessions])


async def get_backlog_detail(session_id, limit=500, offset=0):
    records = backlog.read_session(session_id, limit=limit, offset=offset)
    return BacklogDetailResponse(
        records=[BacklogEvent(**r) for r in records],
        session_id=session_id,
        count=len(records),
    )


async def get_session_history(session_id="default"):
    history = await asyncio.to_thread(session_store.history_messages, session_id)
    return SessionHistoryResponse(messages=[ChatMessage(**m) for m in history])


@router.get(
    "/api/backlog",
    response_model=BacklogListResponse,
)
async def backlog_list_endpoint():
    return await get_backlog_list()


@router.get(
    "/api/backlog/{session_id}",
    response_model=BacklogDetailResponse,
)
async def backlog_detail_endpoint(
    session_id: str, limit: int = Query(500, ge=1), offset: int = Query(0, ge=0)
):
    return await get_backlog_detail(session_id, limit, offset)


@router.get("/api/backlog/stats/{session_id}")
async def backlog_stats_endpoint(session_id: str):
    stats = backlog.get_session_stats(session_id)
    return stats


@router.get("/api/backlog/errors")
async def backlog_errors_endpoint(limit: int = Query(50, ge=1, le=200)):
    errors = backlog.get_recent_errors(limit=limit)
    return {"errors": errors, "total": len(errors)}


@router.get(
    "/api/backlog/export/{session_id}",
    response_class=StreamingResponse,
)
async def export_backlog(session_id: str):
    async def generate():
        records = backlog._read_records(session_id)
        for r in records:
            event = r.get("event")
            if event == "turn_start":
                msg = {
                    "role": "user",
                    "content": r.get("data", {}).get("user_message", ""),
                }
                yield json.dumps(msg, ensure_ascii=False) + "\n"
            elif event == "model_response":
                data = r.get("data", {})
                content = data.get("content", "")
                if content:
                    msg = {"role": "assistant", "content": content}
                    yield json.dumps(msg, ensure_ascii=False) + "\n"
            elif event == "tool_call":
                data = r.get("data", {})
                msg = {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "type": "function",
                            "function": {
                                "name": data.get("name", ""),
                                "arguments": json.dumps(
                                    data.get("arguments", {}), ensure_ascii=False
                                ),
                            },
                        }
                    ],
                }
                yield json.dumps(msg, ensure_ascii=False) + "\n"
            elif event == "tool_result":
                data = r.get("data", {})
                msg = {
                    "role": "tool",
                    "tool_call_id": f"call_{data.get('name', '')}",
                    "content": data.get("result", ""),
                }
                yield json.dumps(msg, ensure_ascii=False) + "\n"

    return StreamingResponse(
        generate(),
        media_type="application/x-ndjson",
        headers={"Content-Disposition": f'attachment; filename="{session_id}.jsonl"'},
    )


@router.get("/api/session/history", response_model=SessionHistoryResponse)
async def session_history_endpoint(
    session_id: str = Query("default"), agent_name: str = Query(None)
):
    effective = f"agent:{agent_name}:{session_id}" if agent_name else session_id
    return await get_session_history(effective)
