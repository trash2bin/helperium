"""Health check endpoint."""

from __future__ import annotations
import os
from fastapi import APIRouter, Response
from api_service.http_models import HealthResponse
from ..deps import get_agent

router = APIRouter()


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Health check",
)
async def health_endpoint(response: Response):
    return await get_health(response)


async def get_health(response: Response):
    # Skip LLM health check in test environments (CI/E2E with ScriptedLLMProvider)
    is_test_env = os.environ.get("USE_SCRIPTED_LLM") == "1"
    skip_llm = os.environ.get("HEALTH_CHECK_SKIP_LLM", "false").lower() == "true"

    if is_test_env or skip_llm:
        return HealthResponse(
            api="ok", ollama={"status": "skipped", "reason": "test environment"}
        )

    # In production, try to check LLM but NEVER fail the health check
    try:
        ollama_status = await get_agent().health()
        return HealthResponse(api="ok", ollama=ollama_status)
    except Exception as exc:
        # Return 200 OK with degraded status - Docker healthcheck only cares about HTTP code
        return HealthResponse(
            api="ok", ollama={"status": "degraded", "error": str(exc)}
        )
