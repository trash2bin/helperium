"""Tests for AgentStore — SQLite agent registry with widget_config and llm_config."""

import tempfile
from pathlib import Path

import pytest

from api_service.agent_store import AgentStore


# ── Helpers ──

SAMPLE_WIDGET = {
    "title": "Test Helper",
    "greeting": "How can I help?",
    "accent_color": "#2563eb",
    "position": "left",
}

SAMPLE_LLM = {
    "provider": "ollama",
    "model": "qwen2.5:0.5b",
    "temperature": 0.3,
    "system_prompt": "You are a test assistant.",
}

UPDATED_WIDGET = {
    "title": "Updated Helper",
    "greeting": "What do you need?",
    "accent_color": "#0f766e",
    "position": "right",
}

UPDATED_LLM = {
    "provider": "mistral",
    "model": "mistral/mistral-small",
    "temperature": 0.7,
    "system_prompt": "You are an updated assistant.",
}


@pytest.fixture
def agent_store():
    """AgentStore backed by a temporary SQLite file."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    store = AgentStore(path)
    yield store
    Path(path).unlink(missing_ok=True)


# ── Tests ──


class TestCreateAgent:
    """Agent creation — basic and with configs."""

    def test_create_agent_basic(self, agent_store):
        """Create an agent without any configs."""
        agent = agent_store.create_agent("test-agent", "A test agent", ["tenant-a"])
        assert agent["name"] == "test-agent"
        assert agent["description"] == "A test agent"
        assert agent["tenant_ids"] == ["tenant-a"]
        assert agent["widget_config"] is None
        assert agent["llm_config"] is None
        assert agent["created_at"] is not None
        assert agent["updated_at"] is not None

    def test_create_agent_with_widget_config(self, agent_store):
        """Create an agent with only widget_config."""
        agent = agent_store.create_agent(
            "widget-agent",
            widget_config=SAMPLE_WIDGET,
        )
        assert agent["widget_config"] == SAMPLE_WIDGET
        assert agent["llm_config"] is None

    def test_create_agent_with_llm_config(self, agent_store):
        """Create an agent with only llm_config."""
        agent = agent_store.create_agent(
            "llm-agent",
            llm_config=SAMPLE_LLM,
        )
        assert agent["llm_config"] == SAMPLE_LLM
        assert agent["widget_config"] is None

    def test_create_agent_duplicate(self, agent_store):
        """Duplicate name raises ValueError."""
        agent_store.create_agent("unique", "original")
        with pytest.raises(ValueError, match="Agent 'unique' already exists"):
            agent_store.create_agent("unique", "duplicate")


class TestGetAgent:
    """Agent retrieval."""

    def test_get_agent_not_found(self, agent_store):
        """None for non-existent agent."""
        assert agent_store.get_agent("nonexistent") is None

    def test_get_agent_with_configs(self, agent_store):
        """Retrieve an agent and verify all fields."""
        agent_store.create_agent(
            "full-agent",
            description="Full agent",
            tenant_ids=["t1", "t2"],
            widget_config=SAMPLE_WIDGET,
            llm_config=SAMPLE_LLM,
        )
        got = agent_store.get_agent("full-agent")
        assert got is not None
        assert got["name"] == "full-agent"
        assert got["description"] == "Full agent"
        assert got["tenant_ids"] == ["t1", "t2"]
        assert got["widget_config"] == SAMPLE_WIDGET
        assert got["llm_config"] == SAMPLE_LLM
        assert got["created_at"] is not None
        assert got["updated_at"] is not None


class TestUpdateAgent:
    """Partial updates — each config field independently."""

    def test_update_agent_widget_only(self, agent_store):
        """Update only widget_config; llm_config stays None."""
        agent_store.create_agent("updatable", "original")
        updated = agent_store.update_agent(
            "updatable",
            description="updated desc",
            widget_config=UPDATED_WIDGET,
        )
        assert updated is not None
        assert updated["description"] == "updated desc"
        assert updated["widget_config"] == UPDATED_WIDGET
        assert updated["llm_config"] is None  # unchanged

    def test_update_agent_llm_only(self, agent_store):
        """Update only llm_config; widget_config stays as created."""
        agent_store.create_agent(
            "llm-updatable",
            widget_config=SAMPLE_WIDGET,
            llm_config=SAMPLE_LLM,
        )
        updated = agent_store.update_agent(
            "llm-updatable",
            llm_config=UPDATED_LLM,
        )
        assert updated is not None
        assert updated["llm_config"] == UPDATED_LLM
        assert updated["widget_config"] == SAMPLE_WIDGET  # unchanged

    def test_update_agent_not_found(self, agent_store):
        """Updating non-existent returns None."""
        result = agent_store.update_agent("ghost", description="nope")
        assert result is None


class TestListAgents:
    """Agent listing."""

    def test_list_agents(self, agent_store):
        """List orders by created_at DESC."""
        a1 = agent_store.create_agent("alpha")
        a2 = agent_store.create_agent("beta")
        a3 = agent_store.create_agent("gamma")
        agents = agent_store.list_agents()
        assert len(agents) == 3
        # created_at DESC → gamma, beta, alpha
        assert agents[0]["name"] == "gamma"
        assert agents[1]["name"] == "beta"
        assert agents[2]["name"] == "alpha"


class TestDeleteAgent:
    """Agent deletion."""

    def test_delete_agent(self, agent_store):
        """Delete an existing agent."""
        agent_store.create_agent("delete-me")
        assert agent_store.delete_agent("delete-me") is True
        assert agent_store.get_agent("delete-me") is None

    def test_delete_agent_not_found(self, agent_store):
        """Delete non-existent returns False."""
        assert agent_store.delete_agent("ghost") is False


class TestBackwardCompat:
    """Agents created without configs should return None for both fields."""

    def test_backward_compat(self, agent_store):
        """No configs → widget_config=None, llm_config=None."""
        agent_store.create_agent("legacy", "old-style agent", ["tenant-a"])
        got = agent_store.get_agent("legacy")
        assert got is not None
        assert got["widget_config"] is None
        assert got["llm_config"] is None
        assert got["description"] == "old-style agent"
        assert got["tenant_ids"] == ["tenant-a"]
