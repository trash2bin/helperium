"""Agent CRUD endpoints."""

from __future__ import annotations
import asyncio
from fastapi import APIRouter, HTTPException
from api_service.http_models import (
    AgentCreateRequest,
    AgentUpdateRequest,
    AgentResponse,
    AgentListResponse,
)
from ..deps import get_agent_store

router = APIRouter()


@router.post("/api/agents", response_model=AgentResponse, status_code=201)
async def create_agent_endpoint(req: AgentCreateRequest) -> AgentResponse:
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
        return AgentResponse(**result)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.get("/api/agents", response_model=AgentListResponse)
async def list_agents_endpoint() -> AgentListResponse:
    agents = await asyncio.to_thread(get_agent_store().list_agents)
    return AgentListResponse(agents=[AgentResponse(**a) for a in agents])


@router.get("/api/agents/{name}", response_model=AgentResponse)
async def get_agent_endpoint(name: str) -> AgentResponse:
    agent = await asyncio.to_thread(get_agent_store().get_agent, name)
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")
    return AgentResponse(**agent)


@router.put("/api/agents/{name}", response_model=AgentResponse)
async def update_agent_endpoint(name: str, req: AgentUpdateRequest) -> AgentResponse:
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
    return AgentResponse(**result)


@router.delete("/api/agents/{name}", status_code=204)
async def delete_agent_endpoint(name: str):
    deleted = await asyncio.to_thread(get_agent_store().delete_agent, name)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")
    return None


@router.get("/api/agents/{name}/widget-config")
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
