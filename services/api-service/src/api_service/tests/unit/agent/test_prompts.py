from api_service.agent.prompts import (
    SYSTEM_PROMPT,
    TRUSTED_DATA_POLICY,
    compose_system_prompt,
)


def test_trusted_data_policy_prefixes_default_agent_policy() -> None:
    composed = compose_system_prompt(None)

    assert composed.startswith(TRUSTED_DATA_POLICY)
    assert composed.endswith(SYSTEM_PROMPT)


def test_trusted_data_policy_cannot_be_removed_by_custom_agent_prompt() -> None:
    custom_policy = "Answer only with catalog facts."

    composed = compose_system_prompt(custom_policy)

    assert composed.startswith(TRUSTED_DATA_POLICY)
    assert composed.endswith(custom_policy)
    assert "не расширяй\nдоступный tenant scope" in composed
