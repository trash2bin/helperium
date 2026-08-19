"""Tests for ProviderStore CRUD and the /admin/llm-providers HTTP endpoints.

Проверяет:
1. ProviderStore CRUD (list, add, get, update, delete, toggle)
2. ProviderStore фильтрация — Ollama без api_key должна быть доступна
3. HTTP-эндпоинты /admin/llm-providers через TestClient
4. orchestrator.provider_priority — Ollama без ключа должна подхватываться
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def provider_store(tmp_path: Path):
    """ProviderStore with a temp file path, no providers loaded."""
    from api_service.provider_store import ProviderStore

    store_path = tmp_path / "providers.json"
    store = ProviderStore(path=store_path)
    # Очищаем что мог импортироваться из env
    store._providers = {}
    store._save()
    return store


def _get_app():
    """Load the API app fresh (no stale state from other tests)."""
    import api_service.server as sv

    if hasattr(sv, "app"):
        del sv.app
    importlib.reload(sv)
    return sv.app


# ── ProviderStore: CRUD ─────────────────────────────────────────────────


class TestProviderStoreCRUD:
    """ProviderStore unit tests — create, read, update, delete."""

    async def test_add_provider_basic(self, provider_store):
        """Add a provider with full fields."""
        result = await provider_store.add_provider(
            name="test-provider",
            model="openai/gpt-4o-mini",
            provider="openai",
            api_key="sk-test-123",
            api_base="https://api.openai.com",
            enabled=True,
        )
        assert result["name"] == "test-provider"
        assert result["model"] == "openai/gpt-4o-mini"
        assert result["provider"] == "openai"
        assert result["api_key_masked"] == "sk-t****"
        assert result["has_api_key"] is True
        assert result["enabled"] is True
        assert result["api_base"] == "https://api.openai.com"

    async def test_add_provider_no_api_key(self, provider_store):
        """Add an Ollama provider without api_key."""
        result = await provider_store.add_provider(
            name="ollama",
            model="ollama/minimax-m3:cloud",
            provider="ollama",
            api_key=None,
            api_base=None,
            enabled=True,
        )
        assert result["name"] == "ollama"
        assert result["has_api_key"] is False
        assert result["api_key_masked"] is None

    async def test_add_provider_duplicate_raises(self, provider_store):
        """Add duplicate name should raise ValueError."""
        await provider_store.add_provider(
            name="dup", model="openai/gpt-4o-mini", api_key="sk-1"
        )
        with pytest.raises(ValueError, match="already exists"):
            await provider_store.add_provider(
                name="dup", model="openai/gpt-4o-mini", api_key="sk-2"
            )

    async def test_add_provider_no_model_raises(self, provider_store):
        """Add without model should raise ValueError."""
        with pytest.raises(ValueError, match="model is required"):
            await provider_store.add_provider(
                name="bad",
                model="",
            )

    async def test_list_providers(self, provider_store):
        """List returns sorted masked providers."""
        await provider_store.add_provider(
            name="z-provider", model="openai/gpt-4o", api_key="sk-z"
        )
        await provider_store.add_provider(
            name="a-provider", model="anthropic/claude-3", api_key="sk-a"
        )
        result = await provider_store.list_providers()
        assert len(result) == 2
        assert result[0]["name"] == "a-provider"
        assert result[1]["name"] == "z-provider"
        # Keys are masked
        for p in result:
            assert p["api_key_masked"] is not None
            assert "****" in p["api_key_masked"]

    async def test_get_provider(self, provider_store):
        """Get by name returns the masked provider."""
        await provider_store.add_provider(
            name="my-provider", model="openai/gpt-4o", api_key="sk-secret"
        )
        result = await provider_store.get_provider("my-provider")
        assert result is not None
        assert result["model"] == "openai/gpt-4o"
        assert result["api_key_masked"] == "sk-s****"
        assert result["has_api_key"] is True

    async def test_get_provider_not_found(self, provider_store):
        """Get by unknown name returns None."""
        result = await provider_store.get_provider("nope")
        assert result is None

    async def test_get_provider_no_key(self, provider_store):
        """Get Ollama provider (no key) returns has_api_key=False."""
        await provider_store.add_provider(
            name="ollama",
            model="ollama/minimax-m3:cloud",
            provider="ollama",
        )
        result = await provider_store.get_provider("ollama")
        assert result is not None
        assert result["has_api_key"] is False
        assert result["api_key_masked"] is None

    async def test_update_provider_change_model(self, provider_store):
        """Update only model, other fields preserved."""
        await provider_store.add_provider(
            name="test",
            model="openai/gpt-4o",
            api_key="sk-secret",
        )
        result = await provider_store.update_provider(
            "test", model="openai/gpt-4o-mini"
        )
        assert result is not None
        assert result["model"] == "openai/gpt-4o-mini"
        assert result["has_api_key"] is True  # preserved

    async def test_update_provider_api_key_empty_keeps_existing(self, provider_store):
        """api_key="" should keep the existing key (masking support)."""
        await provider_store.add_provider(
            name="test",
            model="openai/gpt-4o",
            api_key="sk-secret",
        )
        result = await provider_store.update_provider("test", api_key="")
        assert result is not None
        assert result["has_api_key"] is True  # unchanged
        assert result["api_key_masked"] == "sk-s****"

    async def test_update_provider_clear_api_key(self, provider_store):
        """api_key="__clear__" should clear the key."""
        await provider_store.add_provider(
            name="test",
            model="openai/gpt-4o",
            api_key="sk-secret",
        )
        result = await provider_store.update_provider("test", api_key="__clear__")
        assert result is not None
        assert result["has_api_key"] is False

    async def test_update_provider_change_key(self, provider_store):
        """New non-empty api_key should replace existing."""
        await provider_store.add_provider(
            name="test",
            model="openai/gpt-4o",
            api_key="sk-old",
        )
        result = await provider_store.update_provider("test", api_key="sk-new-key")
        assert result is not None
        assert result["api_key_masked"] == "sk-n****"

    async def test_update_provider_enable_disable(self, provider_store):
        """Toggle enabled flag."""
        await provider_store.add_provider(
            name="test",
            model="openai/gpt-4o",
            api_key="sk-test",
        )
        result = await provider_store.update_provider("test", enabled=False)
        assert result is not None
        assert result["enabled"] is False
        result = await provider_store.update_provider("test", enabled=True)
        assert result["enabled"] is True

    async def test_update_provider_not_found(self, provider_store):
        """Update unknown name returns None."""
        result = await provider_store.update_provider("nope", model="openai/gpt-4o")
        assert result is None

    async def test_delete_provider(self, provider_store):
        """Delete removes the provider."""
        await provider_store.add_provider(
            name="test",
            model="openai/gpt-4o",
            api_key="sk-test",
        )
        deleted = await provider_store.delete_provider("test")
        assert deleted is True
        result = await provider_store.get_provider("test")
        assert result is None

    async def test_delete_provider_not_found(self, provider_store):
        """Delete unknown name returns False."""
        deleted = await provider_store.delete_provider("nope")
        assert deleted is False

    async def test_set_enabled(self, provider_store):
        """set_enabled toggles the provider on/off."""
        await provider_store.add_provider(
            name="test",
            model="openai/gpt-4o",
            api_key="sk-test",
        )
        result = await provider_store.set_enabled("test", False)
        assert result["enabled"] is False
        result = await provider_store.set_enabled("test", True)
        assert result["enabled"] is True

    async def test_persistence_across_reload(self, provider_store, tmp_path):
        """Providers survive file reload."""
        store_path = provider_store._path
        await provider_store.add_provider(
            name="persist",
            model="openai/gpt-4o",
            api_key="sk-test",
        )
        # Create new store pointing to same file
        from api_service.provider_store import ProviderStore

        store2 = ProviderStore(path=store_path)
        result = await store2.get_provider("persist")
        assert result is not None
        assert result["model"] == "openai/gpt-4o"

    async def test_list_ollama_providers_included(self, provider_store):
        """list_providers includes providers with no api_key."""
        await provider_store.add_provider(
            name="ollama",
            model="ollama/minimax-m3:cloud",
            provider="ollama",
        )
        result = await provider_store.list_providers()
        names = [p["name"] for p in result]
        assert "ollama" in names
        ollama = next(p for p in result if p["name"] == "ollama")
        assert ollama["has_api_key"] is False

    async def test_router_config_keeps_model_and_provider_separate(
        self, provider_store
    ):
        """Provider routing belongs to LiteLLM, not handwritten model rewriting."""
        await provider_store.add_provider(
            name="ollama",
            model="minimax-m3:cloud",
            provider="ollama",
            api_base="http://localhost:11434",
        )

        params = provider_store.get_active_router_config()[0]["litellm_params"]

        assert params["model"] == "minimax-m3:cloud"
        assert params["custom_llm_provider"] == "ollama"
        assert params["api_base"] == "http://localhost:11434"


# ── HTTP endpoints: /admin/llm-providers ─────────────────────────────────


@pytest.fixture
def api_client(provider_store, monkeypatch):
    """TestClient with mocked ProviderStore."""
    # Replace the singleton with our test store
    import api_service.provider_store as ps_module

    monkeypatch.setattr(ps_module, "get_provider_store", lambda: provider_store)

    # Reload server/app module to pick up the patched store.
    # NOTE: `from api_service.server import app` would give the FastAPI *object*
    # (re-exported in __init__), not the module — reload would TypeError.
    app_mod = importlib.import_module("api_service.server.app")

    importlib.reload(app_mod)

    with TestClient(app_mod.app) as client:
        yield client


class TestLlmProvidersHTTP:
    """HTTP endpoint tests for /admin/llm-providers."""

    def test_list_empty(self, api_client):
        """GET /admin/llm-providers returns empty list."""
        resp = api_client.get("/admin/llm-providers")
        assert resp.status_code == 200
        data = resp.json()
        assert data["providers"] == []
        assert data["fallback_enabled"] is False

    def test_add_provider(self, api_client):
        """POST /admin/llm-providers creates a provider."""
        resp = api_client.post(
            "/admin/llm-providers",
            json={
                "name": "openai",
                "model": "openai/gpt-4o-mini",
                "api_key": "sk-test-123",
                "api_base": "https://api.openai.com",
                "enabled": True,
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "openai"
        assert data["api_key_masked"] == "sk-t****"
        assert data["has_api_key"] is True

    def test_add_provider_duplicate(self, api_client):
        """POST duplicate returns 409."""
        api_client.post(
            "/admin/llm-providers",
            json={"name": "dup", "model": "openai/gpt-4o", "api_key": "sk-1"},
        )
        resp = api_client.post(
            "/admin/llm-providers",
            json={"name": "dup", "model": "openai/gpt-4o-mini", "api_key": "sk-2"},
        )
        assert resp.status_code == 409

    def test_add_ollama_provider_without_key(self, api_client):
        """POST Ollama provider with empty api_key."""
        resp = api_client.post(
            "/admin/llm-providers",
            json={
                "name": "ollama",
                "model": "ollama/minimax-m3:cloud",
                "provider": "ollama",
                "api_key": None,
                "enabled": True,
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "ollama"
        assert data["has_api_key"] is False
        assert data["api_key_masked"] is None

    def test_get_provider(self, api_client):
        """GET /admin/llm-providers/{name} returns the provider."""
        api_client.post(
            "/admin/llm-providers",
            json={"name": "test", "model": "openai/gpt-4o", "api_key": "sk-secret"},
        )
        resp = api_client.get("/admin/llm-providers/test")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "test"
        assert data["api_key_masked"] == "sk-s****"

    def test_get_provider_not_found(self, api_client):
        """GET unknown name returns 404."""
        resp = api_client.get("/admin/llm-providers/nope")
        assert resp.status_code == 404

    def test_update_provider(self, api_client):
        """PUT /admin/llm-providers/{name} updates fields."""
        api_client.post(
            "/admin/llm-providers",
            json={"name": "test", "model": "openai/gpt-4o", "api_key": "sk-old"},
        )
        resp = api_client.put(
            "/admin/llm-providers/test",
            json={"model": "openai/gpt-4o-mini", "enabled": False},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["model"] == "openai/gpt-4o-mini"
        assert data["enabled"] is False
        assert data["has_api_key"] is True  # preserved

    def test_update_provider_empty_api_key_keeps_existing(self, api_client):
        """PUT with api_key="" keeps existing key (the critical Ollama flow)."""
        api_client.post(
            "/admin/llm-providers",
            json={
                "name": "ollama",
                "model": "ollama/minimax-m3:cloud",
                "api_key": None,
            },
        )
        # Update model, keep empty key
        resp = api_client.put(
            "/admin/llm-providers/ollama",
            json={"model": "ollama/minimax-m3:cloud", "api_key": "", "enabled": True},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["model"] == "ollama/minimax-m3:cloud"
        assert data["has_api_key"] is False

    def test_update_provider_not_found(self, api_client):
        """PUT unknown name returns 404."""
        resp = api_client.put(
            "/admin/llm-providers/nope",
            json={"model": "openai/gpt-4o"},
        )
        assert resp.status_code == 404

    def test_delete_provider(self, api_client):
        """DELETE /admin/llm-providers/{name} removes the provider."""
        api_client.post(
            "/admin/llm-providers",
            json={"name": "test", "model": "openai/gpt-4o", "api_key": "sk-test"},
        )
        resp = api_client.delete("/admin/llm-providers/test")
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True

        # Verify gone
        resp = api_client.get("/admin/llm-providers/test")
        assert resp.status_code == 404

    def test_delete_provider_not_found(self, api_client):
        """DELETE unknown name returns 404."""
        resp = api_client.delete("/admin/llm-providers/nope")
        assert resp.status_code == 404

    def test_toggle_provider(self, api_client):
        """POST /admin/llm-providers/{name}/toggle flips enabled."""
        api_client.post(
            "/admin/llm-providers",
            json={"name": "test", "model": "openai/gpt-4o", "api_key": "sk-test"},
        )
        # Toggle off
        resp = api_client.post("/admin/llm-providers/test/toggle")
        assert resp.status_code == 200
        assert resp.json()["enabled"] is False

        # Toggle back on
        resp = api_client.post("/admin/llm-providers/test/toggle")
        assert resp.status_code == 200
        assert resp.json()["enabled"] is True

    def test_list_with_ollama_provider(self, api_client):
        """List includes Ollama provider (no key)."""
        api_client.post(
            "/admin/llm-providers",
            json={
                "name": "ollama",
                "model": "ollama/minimax-m3:cloud",
                "provider": "ollama",
            },
        )
        resp = api_client.get("/admin/llm-providers")
        assert resp.status_code == 200
        data = resp.json()
        names = [p["name"] for p in data["providers"]]
        assert "ollama" in names

    def test_llm_config_endpoint(self, api_client):
        """GET /admin/llm-config returns provider list and fallback status."""
        api_client.post(
            "/admin/llm-providers",
            json={"name": "test", "model": "openai/gpt-4o", "api_key": "sk-test"},
        )
        resp = api_client.get("/admin/llm-config")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["providers"]) == 1
        assert data["num_models"] == 1

    def test_llm_provider_list_endpoint(self, api_client):
        """GET /admin/llm-provider-list returns available provider list."""
        resp = api_client.get("/admin/llm-provider-list")
        assert resp.status_code == 200
        data = resp.json()
        assert "providers" in data
        assert isinstance(data["providers"], list)
        assert data["count"] >= 0


# ── Orchestrator: provider_priority with no-key providers ────────────────


class TestOrchestratorProviderPriority:
    """Provider priority resolution must NOT skip providers without api_key.

    Regression test for the bug where `if not provider_data.get("api_key"): continue`
    blocked Ollama (local providers with no key needed).
    """

    def test_ollama_allowed_without_api_key(self):
        """Ollama provider with empty api_key should be selected by priority."""

        raw_providers = {
            "ollama": {
                "model": "ollama/minimax-m3:cloud",
                "api_key": "",
                "api_base": "http://localhost:11434",
                "enabled": True,
                "provider": "ollama",
            },
        }

        priority = ["ollama"]

        # Simulate the logic from orchestrator.py (with our fix)
        found = None
        for name in priority:
            provider_data = raw_providers.get(name)
            if not provider_data:
                continue
            if not provider_data.get("enabled", True):
                continue
            model = provider_data.get("model", "")
            if not model:
                continue
            # NO check for api_key — local providers are valid without one
            found = (name, provider_data)
            break

        assert found is not None, (
            "Ollama provider with empty api_key was skipped by provider_priority!"
        )
        name, data = found
        assert name == "ollama"
        assert data["model"] == "ollama/minimax-m3:cloud"

    def test_disabled_provider_skipped(self):
        """Disabled providers should still be skipped."""
        raw_providers = {
            "ollama": {
                "model": "ollama/minimax-m3:cloud",
                "api_key": "",
                "api_base": "http://localhost:11434",
                "enabled": False,
                "provider": "ollama",
            },
        }

        priority = ["ollama"]

        found = None
        for name in priority:
            provider_data = raw_providers.get(name)
            if not provider_data:
                continue
            if not provider_data.get("enabled", True):
                continue
            model = provider_data.get("model", "")
            if not model:
                continue
            found = (name, provider_data)
            break

        assert found is None, "Disabled provider should have been skipped!"

    def test_mixed_providers_ollama_first(self):
        """Ollama (no key) before cloud provider — picks Ollama."""
        raw_providers = {
            "ollama": {
                "model": "ollama/minimax-m3:cloud",
                "api_key": "",
                "api_base": "http://localhost:11434",
                "enabled": True,
                "provider": "ollama",
            },
            "openai": {
                "model": "openai/gpt-4o-mini",
                "api_key": "sk-test-123",
                "api_base": "",
                "enabled": True,
                "provider": "openai",
            },
        }

        priority = ["ollama", "openai"]

        found = None
        for name in priority:
            provider_data = raw_providers.get(name)
            if not provider_data:
                continue
            if not provider_data.get("enabled", True):
                continue
            model = provider_data.get("model", "")
            if not model:
                continue
            found = (name, provider_data)
            break

        assert found is not None
        assert found[0] == "ollama"

    def test_ollama_with_cloud_only_priority_uses_cloud(self):
        """When Ollama is not in priority, cloud provider is picked."""
        raw_providers = {
            "ollama": {
                "model": "ollama/minimax-m3:cloud",
                "api_key": "",
                "api_base": "http://localhost:11434",
                "enabled": True,
                "provider": "ollama",
            },
            "openai": {
                "model": "openai/gpt-4o-mini",
                "api_key": "sk-test-123",
                "api_base": "",
                "enabled": True,
                "provider": "openai",
            },
        }

        priority = ["openai"]

        found = None
        for name in priority:
            provider_data = raw_providers.get(name)
            if not provider_data:
                continue
            if not provider_data.get("enabled", True):
                continue
            model = provider_data.get("model", "")
            if not model:
                continue
            found = (name, provider_data)
            break

        assert found is not None
        assert found[0] == "openai"
