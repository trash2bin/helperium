"""Admin endpoints — guardrails, spending, abuse, LLM providers."""

from __future__ import annotations
import logging
from fastapi import APIRouter, HTTPException
from api_service.guardrails import get_guard_checker
from api_service.spending import get_spending_checker
from api_service.provider_store import get_provider_store
from api_service.abuse_live import get_live_abuse_provider

logger = logging.getLogger("api_service.server")
router = APIRouter()


@router.get("/admin/guardrails")
async def get_guardrails():
    """Get current guard config."""
    checker = get_guard_checker()
    return {
        "enabled": checker.config.enabled,
        "block_on_match": checker.config.block_on_match,
        "blocked_count": checker.config.blocked_count,
    }


@router.post("/admin/guardrails")
async def update_guardrails(config: dict):
    """Update guard config. Accepts: enabled, block_on_match."""
    checker = get_guard_checker()
    if "enabled" in config:
        checker.config.enabled = bool(config["enabled"])
    if "block_on_match" in config:
        val = str(config["block_on_match"])
        if val in ("block", "warn"):
            checker.config.block_on_match = val
    return {
        "enabled": checker.config.enabled,
        "block_on_match": checker.config.block_on_match,
        "blocked_count": checker.config.blocked_count,
    }


# ── Spending Admin API ──


@router.get("/admin/spending")
async def get_all_spending():
    """Get spending overview."""
    checker = get_spending_checker()
    return {
        "enabled": checker.config.enabled,
        "default_budget": checker.config.default_budget,
        "period": checker.config.period,
    }


@router.get("/admin/spending/{tenant_id}")
async def get_tenant_spending(tenant_id: str):
    """Get spending for a specific tenant."""
    checker = get_spending_checker()
    return checker.get_spending(tenant_id)


@router.post("/admin/spending/{tenant_id}")
async def set_tenant_budget(tenant_id: str, config: dict):
    """Set per-tenant budget override.

    Body: {"budget": 100.0}
    """
    budget = float(config.get("budget", 0))
    if budget < 0:
        raise HTTPException(status_code=400, detail="Budget must be >= 0")
    checker = get_spending_checker()
    checker.set_budget(tenant_id, budget)
    return checker.get_spending(tenant_id)


# ── Live Abuse Config Admin API ──


@router.get("/admin/abuse-config")
async def get_abuse_config():
    """Get the current effective abuse configuration (from file + env)."""
    provider = get_live_abuse_provider()
    cfg = provider.get_config()
    from api_service.abuse_live import _serialize_config

    return _serialize_config(cfg)


@router.post("/admin/abuse-config/reload")
async def reload_abuse_config():
    """Reload abuse config from disk and apply runtime settings.

    Called by admin-dashboard after saving new config.
    Applies runtime settings (history, loops) to the live settings object.
    """
    provider = get_live_abuse_provider()
    cfg = provider.reload()
    provider.apply_runtime_settings()
    from api_service.abuse_live import _serialize_config

    return {"status": "ok", "config": _serialize_config(cfg)}


@router.post("/admin/abuse-config")
async def save_abuse_config(data: dict):
    """Save new abuse config directly (admin dashboard alternative endpoint)."""
    provider = get_live_abuse_provider()
    cfg = provider.save_config(data)
    provider.apply_runtime_settings()
    from api_service.abuse_live import _serialize_config

    return {"status": "ok", "config": _serialize_config(cfg)}


# ── LLM Providers Admin API ──


@router.get("/admin/llm-providers")
async def list_llm_providers():
    """List all LLM providers with masked API keys."""
    store = get_provider_store()
    return {
        "providers": await store.list_providers(),
        "fallback_enabled": store.get_fallback_enabled(),
    }


@router.get("/admin/llm-provider-list")
async def list_litellm_providers():
    """List all available providers from LiteLLM (live, no hardcode)."""
    from api_service.provider_store import get_litellm_provider_list

    providers = get_litellm_provider_list()
    return {
        "providers": providers,
        "count": len(providers),
    }


@router.get("/admin/llm-providers/{name}")
async def get_llm_provider(name: str):
    """Get a single LLM provider with masked API key."""

    store = get_provider_store()
    provider = await store.get_provider(name)
    if not provider:
        raise HTTPException(status_code=404, detail=f"Provider '{name}' not found")
    return provider


@router.post("/admin/llm-providers", status_code=201)
async def add_llm_provider(body: dict):
    """Add a new LLM provider.

    Body:
        name (required): unique provider name
        model (required): model identifier (e.g. openai/gpt-4o, anthropic/claude-3-sonnet)
        provider: provider type — auto-detected from model prefix if omitted
        api_key: API key (will not be returned in full)
        api_base: custom API base URL
        enabled: whether the provider is active (default: true)
    """

    store = get_provider_store()
    try:
        result = await store.add_provider(
            name=body["name"],
            model=body["model"],
            provider=body.get("provider", ""),
            api_key=body.get("api_key"),
            api_base=body.get("api_base"),
            enabled=body.get("enabled", True),
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=f"Missing required field: {exc}")
    return result


@router.put("/admin/llm-providers/{name}")
async def update_llm_provider(name: str, body: dict):
    """Update an existing LLM provider.

    Omitted fields keep their current value.
    Set api_key="" to keep existing key (not change it).
    Set api_key="__clear__" to clear the key.
    """

    store = get_provider_store()
    result = await store.update_provider(
        name=name,
        model=body.get("model"),
        provider=body.get("provider"),
        api_key=body.get("api_key"),
        api_base=body.get("api_base"),
        enabled=body.get("enabled"),
        label=body.get("label"),
    )
    if not result:
        raise HTTPException(status_code=404, detail=f"Provider '{name}' not found")
    return result


@router.delete("/admin/llm-providers/{name}")
async def delete_llm_provider(name: str):
    """Delete an LLM provider."""

    store = get_provider_store()
    if not await store.delete_provider(name):
        raise HTTPException(status_code=404, detail=f"Provider '{name}' not found")
    return {"deleted": True}


@router.post("/admin/llm-providers/{name}/toggle")
async def toggle_llm_provider(name: str):
    """Toggle a provider on/off."""

    store = get_provider_store()
    provider_data = await store.get_provider(name)
    if not provider_data:
        raise HTTPException(status_code=404, detail=f"Provider '{name}' not found")
    new_enabled = not provider_data["enabled"]
    result = await store.set_enabled(name, new_enabled)
    return result


# ── LLM Config Admin API (legacy, from env vars) ──


@router.get("/admin/llm-config")
async def get_llm_config():
    """Get current LLM provider fallback configuration."""
    from api_service.provider_store import get_provider_store

    store = get_provider_store()
    providers = await store.list_providers()

    return {
        "fallback_enabled": store.get_fallback_enabled(),
        "providers": providers,
        "num_models": len(providers),
    }
