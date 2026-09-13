"""The default prompt is a minimal stub: admin-configured agents carry real
prompts, and model behavior is controlled structurally, not by prompt text."""

from api_service.agent.prompts import DEFAULT_SYSTEM_PROMPT


def test_default_system_prompt_is_a_non_empty_stub() -> None:
    assert DEFAULT_SYSTEM_PROMPT.strip()
    assert "MCP" in DEFAULT_SYSTEM_PROMPT
