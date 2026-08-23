from __future__ import annotations

from helperium_sdk.settings import DemoSettings


def test_llm_retry_budget_defaults_to_sixty_seconds(monkeypatch) -> None:
    monkeypatch.delenv("LLM_RETRY_MAX_ELAPSED_SECONDS", raising=False)

    assert DemoSettings().llm_retry_max_elapsed_seconds == 60.0


def test_llm_retry_budget_accepts_explicit_environment_override(monkeypatch) -> None:
    monkeypatch.setenv("LLM_RETRY_MAX_ELAPSED_SECONDS", "12.5")

    assert DemoSettings().llm_retry_max_elapsed_seconds == 12.5
