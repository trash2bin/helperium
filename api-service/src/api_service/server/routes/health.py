"""Health check endpoint."""

from __future__ import annotations
from fastapi import APIRouter
from api_service.http_models import HealthResponse
from ..deps import get_agent

router = APIRouter()


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Health check",
)
async def health_endpoint():
    return await get_health()


async def get_health():
    try:
        ollama_status = await get_agent().health()
    except Exception as exc:
        ollama_status = {"status": "error", "error": str(exc)}
    return HealthResponse(api="ok", ollama=ollama_status)
