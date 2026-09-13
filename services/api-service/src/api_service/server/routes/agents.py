"""Agent CRUD endpoints."""

from __future__ import annotations
import asyncio
from fastapi import APIRouter, Header, HTTPException
from api_service.http_models import (
    AgentCreateRequest,
    AgentUpdateRequest,
    AgentResponse,
    AgentListResponse,
)
from ..deps import get_agent_store

router = APIRouter()
public_router = APIRouter()


# ── Pentest H1: secret masking ──
# demo/web проксирует /api/agents с admin-токеном наружу, поэтому ключи
# обязаны быть замаскированы ПО УМОЛЧАНИЮ. Полный ключ отдаём только при
# явном X-Full-Keys: 1 (admin-dashboard round-trip GET->PUT). Без заголовка
# ответ всегда маскируется — даже для валидного bearer.


def mask_api_key(key: str | None) -> str | None:
    """Заменить секрет маской (первые 4 + … + последние 4, либо звёздочки)."""
    if not key:
        return key
    if len(key) <= 10:
        return "********"
    return f"{key[:4]}…{key[-4:]}"


def _wants_full_keys(x_full_keys: str | None) -> bool:
    return (x_full_keys or "").strip().lower() in {"1", "true", "yes", "full"}


def _is_masked_key(value: str | None) -> bool:
    """Распознать выход mask_api_key: «********» либо «abcd…wxyz» (U+2026)."""
    if not value:
        return False
    return set(value) == {"*"} or "…" in value


def _reject_masked_llm_key(req: AgentCreateRequest | AgentUpdateRequest) -> None:
    """Pentest H1 follow-up: write-back маски.

    Round-trip GET-без-X-Full-Keys → PUT молча перезаписал бы реальный ключ
    замаскированным значением (update_agent заменяет llm_config целиком).
    """
    if req.llm_config and _is_masked_key(req.llm_config.api_key):
        raise HTTPException(
            status_code=400,
            detail=(
                "llm_config.api_key looks masked; fetch the agent with "
                "X-Full-Keys: 1 and resubmit the real key"
            ),
        )


def mask_agent(agent: dict, full_keys: bool = False) -> dict:
    """Вернуть копию агента с замаскированными api_key (если не full_keys)."""
    if full_keys:
        return agent
    agent = dict(agent)
    llm = agent.get("llm_config")
    if isinstance(llm, dict) and llm.get("api_key"):
        llm = dict(llm)
        llm["api_key"] = mask_api_key(llm.get("api_key"))
        agent["llm_config"] = llm
    voice = agent.get("voice_config")
    if isinstance(voice, dict) and voice.get("stt_providers"):
        voice = dict(voice)
        voice["stt_providers"] = [
            {**p, "api_key": mask_api_key(p.get("api_key"))}
            for p in voice["stt_providers"]
        ]
        agent["voice_config"] = voice
    return agent


@router.post(
    "/api/agents",
    response_model=AgentResponse,
    status_code=201,
)
async def create_agent_endpoint(
    req: AgentCreateRequest,
    x_full_keys: str | None = Header(default=None, alias="X-Full-Keys"),
) -> AgentResponse:
    _reject_masked_llm_key(req)
    try:
        result = await asyncio.to_thread(
            get_agent_store().create_agent,
            name=req.name,
            description=req.description,
            tenant_ids=req.tenant_ids,
            widget_config=req.widget_config.model_dump() if req.widget_config else None,
            llm_config=req.llm_config.model_dump() if req.llm_config else None,
            provider_priority=req.provider_priority or None,
            abuse_config=(
                req.abuse_config.model_dump(exclude_none=True)
                if req.abuse_config
                else None
            ),
            system_prompt=req.system_prompt,
            voice_config=req.voice_config.model_dump() if req.voice_config else None,
        )
        return AgentResponse(**mask_agent(result, _wants_full_keys(x_full_keys)))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.get(
    "/api/agents",
    response_model=AgentListResponse,
)
async def list_agents_endpoint(
    x_full_keys: str | None = Header(default=None, alias="X-Full-Keys"),
) -> AgentListResponse:
    agents = await asyncio.to_thread(get_agent_store().list_agents)
    full = _wants_full_keys(x_full_keys)
    return AgentListResponse(
        agents=[AgentResponse(**mask_agent(a, full)) for a in agents]
    )


@router.get(
    "/api/agents/{name}",
    response_model=AgentResponse,
)
async def get_agent_endpoint(
    name: str,
    x_full_keys: str | None = Header(default=None, alias="X-Full-Keys"),
) -> AgentResponse:
    agent = await asyncio.to_thread(get_agent_store().get_agent, name)
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")
    return AgentResponse(**mask_agent(agent, _wants_full_keys(x_full_keys)))


@router.put(
    "/api/agents/{name}",
    response_model=AgentResponse,
)
async def update_agent_endpoint(
    name: str,
    req: AgentUpdateRequest,
    x_full_keys: str | None = Header(default=None, alias="X-Full-Keys"),
) -> AgentResponse:
    _reject_masked_llm_key(req)
    result = await asyncio.to_thread(
        get_agent_store().update_agent,
        name=name,
        description=req.description,
        tenant_ids=req.tenant_ids,
        widget_config=req.widget_config.model_dump() if req.widget_config else None,
        llm_config=req.llm_config.model_dump() if req.llm_config else None,
        provider_priority=req.provider_priority,
        abuse_config=(
            req.abuse_config.model_dump(exclude_none=True) if req.abuse_config else None
        ),
        system_prompt=req.system_prompt,
        voice_config=req.voice_config.model_dump() if req.voice_config else None,
    )
    if not result:
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")
    return AgentResponse(**mask_agent(result, _wants_full_keys(x_full_keys)))


@router.delete(
    "/api/agents/{name}",
    status_code=204,
)
async def delete_agent_endpoint(name: str):
    deleted = await asyncio.to_thread(get_agent_store().delete_agent, name)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")
    return None


@public_router.get("/api/agents/{name}/widget-config")
async def agent_widget_config_endpoint(name: str) -> dict:
    agent = await asyncio.to_thread(get_agent_store().get_agent, name)
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")
    cfg = agent.get("widget_config") or {}
    return {
        "title": cfg.get("title", "Помощник"),
        "greeting": cfg.get("greeting", "Задайте вопрос"),
        "accent_color": cfg.get("accent_color", "#0f766e"),
        "position": cfg.get("position", "right"),
    }
