"""Prompt injection guard layer.

Two directions:
1. Input guard — blocks messages with prompt injection before LLM.
2. Output guard — detects system prompt leaks in LLM response.

Configurable via env vars and admin API.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


# ── Default blocking patterns (input) ────────────────────────────────────────
# Each pattern is a tuple (regex, reason_tag).

DEFAULT_BLOCK_PATTERNS: list[tuple[str, str]] = [
    # Direct system prompt override
    (
        r"(?i)(?:ignore|forget|disregard|override|overwrite).{0,20}"
        r"(?:all|previous|prior).{0,20}"
        r"(?:instruction|prompt|directive|command|rule|guideline)",
        "inject_ignore_instructions",
    ),
    (
        r"(?i)(?:you are not|you aren't).{0,30}(?:assistant|ai|helper|chatbot)",
        "inject_role_override",
    ),
    (
        r"(?i)(?:new|updated).{0,10}(?:instruction|prompt|directive|command|rule).{0,5}:?\s*$",
        "inject_new_instruction",
    ),
    (
        r"(?i)(?:pretend|imagine|act as|from now on|henceforth).{0,30}"
        r"(?:you are|you're|your role|your name)",
        "inject_pretend",
    ),
    (
        r"(?i)(?:system prompt|initial prompt|original prompt|your prompt|my instructions are)",
        "inject_system_prompt_leak",
    ),
    (
        r"(?i)^(?:say|repeat|print|output|display|show|type|write).{0,20}"
        r"(?:the |your |system |above|previous).{0,20}"
        r"(?:prompt|instruction|directive|message)",
        "inject_leak_request",
    ),
    (
        r"(?i)(?:reveal|expose|leak|dump|extract|give me).{0,30}"
        r"(?:prompt|instruction|directive|system)",
        "inject_reveal_request",
    ),
    # Executive override
    (
        r"(?i)(?:you must|you will).{0,20}(?:obey|follow|listen|comply)",
        "inject_executive",
    ),
    (
        r"(?i)(?:do not|don't).{0,20}(?:follow|obey|listen|heed)",
        "inject_disobey",
    ),
    # DAN / jailbreak
    (
        r"(?i)(?:DAN|jailbreak|jail.?break|dev.?mode|developer.?mode)",
        "inject_jailbreak",
    ),
    (
        r"\b(?:do|say).{0,10}(?:anything|whatever|everything).{0,20}"
        r"(?:want|ask|tell|command)",
        "inject_do_anything",
    ),
    # ── RAG-specific: instructions hidden in retrieved documents ──
    (
        r"(?i)(?:according to the document|the document says|as stated in the document)"
        r".{0,30}(?:you must|you will|your task|you are to|your new role)",
        "inject_rag_doc_override",
    ),
    (
        r"(?i)(?:disregard|overwrite|override|ignore).{0,30}"
        r"(?:these instructions|your guidelines|this prompt|the rules above)",
        "inject_rag_override",
    ),
    (
        r"(?i)(?:this is a test|for testing purposes only|this is a hypothetical)"
        r".{0,30}(?:ignore|forget|disregard|override)",
        "inject_rag_test_override",
    ),
]


# ── Default output leak patterns ─────────────────────────────────────────────

DEFAULT_OUTPUT_PATTERNS: list[tuple[str, str]] = [
    (
        r"(?i)(?:my system prompt|my instructions are|i was told to|"
        r"i am programmed to|my core directive)",
        "leak_system_prompt",
    ),
    (
        r"(?i)(?:here (?:are|is|were|was) (?:my|the original|the full|the complete))"
        r".{0,30}(?:instruction|prompt|directive|guideline)",
        "leak_full_prompt",
    ),
    # API keys / tokens in output
    (
        r"(?i)(?:sk-[a-zA-Z][a-zA-Z0-9_\-]{2,}[a-zA-Z0-9]{16,}|"
        r"api.?key[\s\":=]+[a-zA-Z0-9_\-]{16,}|"
        r"secret[\s\":=]+[a-zA-Z0-9_\-]{16,})",
        "leak_credentials",
    ),
    (
        r"(?i)(?:Bearer\s+[a-zA-Z0-9_\-.:]{20,}|Authorization\s*:?\s*Bearer)",
        "leak_bearer_token",
    ),
]


@dataclass
class GuardResult:
    """Result of a guard check."""

    blocked: bool = False
    reason: str = ""
    pattern: str = ""


@dataclass
class GuardConfig:
    """Guard configuration."""

    enabled: bool = True
    block_on_match: str = "block"  # "block" | "warn"
    input_patterns: list[tuple[str, str]] = field(
        default_factory=lambda: list(DEFAULT_BLOCK_PATTERNS)
    )
    output_patterns: list[tuple[str, str]] = field(
        default_factory=lambda: list(DEFAULT_OUTPUT_PATTERNS)
    )
    blocked_count: int = 0

    @classmethod
    def from_env(cls) -> GuardConfig:
        """Load config from env vars."""
        config = cls(
            enabled=os.environ.get("GUARDRAIL_ENABLED", "true").lower()
            in ("true", "1", "yes"),
            block_on_match=os.environ.get("GUARDRAIL_BLOCK_ON_MATCH", "block"),
        )
        # Override patterns from env var (JSON)
        override_raw = os.environ.get("GUARDRAIL_BLOCK_PATTERNS", "")
        if override_raw:
            try:
                overrides = json.loads(override_raw)
                if "input" in overrides:
                    config.input_patterns = [
                        (p["pattern"], p["reason"]) for p in overrides["input"]
                    ]
                if "output" in overrides:
                    config.output_patterns = [
                        (p["pattern"], p["reason"]) for p in overrides["output"]
                    ]
            except (json.JSONDecodeError, KeyError, TypeError) as e:
                logger.warning("Failed to parse GUARDRAIL_BLOCK_PATTERNS: %s", e)
        return config


class GuardChecker:
    """Check messages against prompt injection patterns."""

    def __init__(self, config: Optional[GuardConfig] = None):
        self.config = config or GuardConfig.from_env()
        self._input_compiled = [
            (re.compile(p), reason) for p, reason in self.config.input_patterns
        ]
        self._output_compiled = [
            (re.compile(p), reason) for p, reason in self.config.output_patterns
        ]

    def reload(self) -> None:
        """Reload config from env."""
        self.config = GuardConfig.from_env()
        self._input_compiled = [
            (re.compile(p), reason) for p, reason in self.config.input_patterns
        ]
        self._output_compiled = [
            (re.compile(p), reason) for p, reason in self.config.output_patterns
        ]

    def check_input(self, message: str) -> GuardResult:
        """Check user message for prompt injection."""
        if not self.config.enabled:
            return GuardResult()
        if not message:
            return GuardResult()
        for compiled, reason in self._input_compiled:
            if compiled.search(message):
                self.config.blocked_count += 1
                logger.warning(
                    "[GUARD] Blocked input: %s (pattern: %s)",
                    reason,
                    compiled.pattern[:60],
                )
                if self.config.block_on_match == "block":
                    return GuardResult(
                        blocked=True,
                        reason=reason,
                        pattern=compiled.pattern,
                    )
                return GuardResult(
                    blocked=False,
                    reason=f"warn:{reason}",
                    pattern=compiled.pattern,
                )
        return GuardResult()

    def check_output(self, content: str) -> GuardResult:
        """Check LLM response for system prompt leak or credentials."""
        if not self.config.enabled:
            return GuardResult()
        if not content:
            return GuardResult()
        for compiled, reason in self._output_compiled:
            if compiled.search(content):
                self.config.blocked_count += 1
                logger.warning(
                    "[GUARD] Matched output: %s (pattern: %s)",
                    reason,
                    compiled.pattern[:60],
                )
                return GuardResult(
                    blocked=True,
                    reason=reason,
                    pattern=compiled.pattern,
                )
        return GuardResult()


# ── Singleton ────────────────────────────────────────────────────────────────

_guard_checker: Optional[GuardChecker] = None


def get_guard_checker() -> GuardChecker:
    """Get or create singleton guard checker."""
    global _guard_checker
    if _guard_checker is None:
        _guard_checker = GuardChecker()
    return _guard_checker


def reload_guard_checker() -> None:
    """Reload guard checker from env."""
    global _guard_checker
    _guard_checker = GuardChecker()
