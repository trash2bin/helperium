"""Provider/model compatibility registry tests."""

from api_service.agent.provider_compatibility import find_provider_model_policy


def test_step37_nim_policy_shapes_reasoning_and_keeps_continuation_tools() -> None:
    policy = find_provider_model_policy(
        "nvidia_nim", "nvidia_nim/stepfun-ai/step-3.7-flash"
    )

    assert policy is not None
    assert policy.reasoning_body is not None
    assert policy.reasoning_body(False) == {"chat_template_kwargs": {"thinking": False}}
    assert policy.keep_tool_schemas_on_continuation is True


def test_unknown_nim_model_has_no_policy() -> None:
    assert (
        find_provider_model_policy("nvidia_nim", "nvidia_nim/new-vendor/new-model")
        is None
    )


def test_non_nim_provider_has_no_nim_policy() -> None:
    assert find_provider_model_policy("openai", "openai/gpt-4.1-mini") is None
