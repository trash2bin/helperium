"""Prompt injection guard layer.

Two directions:
1. Input guard — blocks messages with prompt injection before LLM.
2. Output guard — detects system prompt leaks in LLM response.

Configurable via env vars and admin API.
"""

from __future__ import annotations

import html
import json
import logging
import os
import re
import unicodedata
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# ── Unicode normalization strategy ────────────────────────────────────────
# 1. NFKC normalization handles: fullwidth forms, mathematical alphanumerics
#    (bold, sans-serif, monospace, etc.), compatibility characters, ligatures.
# 2. Small explicit map for scripts NFKC doesn't normalize: Greek, Cyrillic.
# 3. Strip zero-width and invisible separator characters that attackers insert
#    inside keywords (e.g., "ig\u200bnore" to bypass regex).
# 4. Decode URL-encoded sequences (%69 → i) and HTML entities (&#105; → i).
# 5. Collapse whitespace variations so the .{0,20} regex gap matches across
#    tabs, newlines, non-breaking spaces, etc.
HOMOGLYPH_MAP: dict[str, str] = {
    # Greek (Greek and Coptic block)
    "\u03b1": "a",  # α Greek small letter alpha
    "\u03b5": "e",  # ε Greek small letter epsilon
    "\u03b9": "i",  # ι Greek small letter iota
    "\u03bf": "o",  # ο Greek small letter omicron
    "\u03c1": "p",  # ρ Greek small letter rho
    "\u03c3": "c",  # σ Greek small letter sigma
    "\u03c5": "y",  # υ Greek small letter upsilon
    "\u03c7": "x",  # χ Greek small letter chi
    # Cyrillic / Ukrainian
    "\u0430": "a",  # Cyrillic а → Latin a
    "\u0435": "e",  # Cyrillic е → Latin e
    "\u0456": "i",  # Ukrainian і → Latin i
    "\u043e": "o",  # Cyrillic о → Latin o
    "\u0440": "p",  # Cyrillic р → Latin p
    "\u0441": "c",  # Cyrillic с → Latin c
    "\u0443": "y",  # Cyrillic у → Latin y
    "\u0445": "x",  # Cyrillic х → Latin x
}


def _normalize_homoglyphs(text: str) -> str:
    """Normalize homoglyphs to Latin via NFKC + script-specific map.

    1. NFKC: fullwidth, mathematical alphanumerics, compatibility chars → ASCII
    2. HOMOGLYPH_MAP: Greek, Cyrillic (distinct scripts NFKC doesn't touch)
    """
    # Step 1: NFKC handles most compatibility variants automatically
    nfkc = unicodedata.normalize("NFKC", text)
    # Step 2: Apply small map for Greek/Cyrillic (scripts NFKC doesn't touch)
    return "".join(HOMOGLYPH_MAP.get(ch, ch) for ch in nfkc)


# ── Escape sequence decoding ──────────────────────────────────────────────
# Attackers embed escape sequences (\x69, \u0069, \151) to spell out injection
# keywords.  LLMs and many runtimes decode these before processing; regex
# guard patterns see the literal backslash and do NOT match.

_HEX_RUN_ESCAPE = re.compile(r"(?:\\x[0-9a-fA-F]{2})+")
_UNICODE2_ESCAPE = re.compile(r"\\u([0-9a-fA-F]{4})")
_UNICODE4_ESCAPE = re.compile(r"\\U([0-9a-fA-F]{8})")
_OCTAL_ESCAPE = re.compile(r"\\([0-3][0-7]{0,2})")  # \0 to \377


def _decode_hex_run(match: re.Match[str]) -> str:
    """Decode one run of consecutive \\xNN as a UTF-8 byte sequence.

    A multi-byte pair like \\xD1\\x96 is the UTF-8 encoding of Cyrillic і
    (U+0456); per-byte Latin-1 decoding would yield "Ñ\\x96" and the homoglyph
    map would never see the codepoint. Invalid UTF-8 falls back to per-byte
    Latin-1 so ASCII-only runs keep their meaning.
    """
    pairs = re.findall(r"\\x([0-9a-fA-F]{2})", match.group(0))
    raw = bytes(int(p, 16) for p in pairs)
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def _decode_escapes(text: str) -> str:
    r"""Decode common escape sequences that LLMs/interpreters would expand.

    Handles:
    - \xNN   (hex byte, e.g. \x69 → i; consecutive runs decode as UTF-8)
    - \uNNNN (4-digit unicode, e.g. \u0069 → i)
    - \UNNNNNNNN (8-digit unicode)
    - \NNN   (octal, e.g. \151 → i)

    Iterates until no more changes (handles double-encoding like
    "\x5cx75" → "\u00" → ... → actual chars).
    """
    prev = None
    result = text
    for _ in range(5):  # max nesting depth for double/triple encoding
        if result == prev:
            break
        prev = result
        # Order matters — decode hex first, then unicode, then octal
        result = _HEX_RUN_ESCAPE.sub(_decode_hex_run, result)
        result = _UNICODE2_ESCAPE.sub(lambda m: chr(int(m.group(1), 16)), result)
        result = _UNICODE4_ESCAPE.sub(lambda m: chr(int(m.group(1), 16)), result)
        result = _OCTAL_ESCAPE.sub(lambda m: chr(int(m.group(1), 8)), result)
    return result


# ── URL encoding decoding ───────────────────────────────────────────────
# Attackers use %xx to encode keyword characters (e.g., %69 = 'i').
_URL_RUN_ESCAPE = re.compile(r"(?:%[0-9a-fA-F]{2})+")


def _decode_url_run(match: re.Match[str]) -> str:
    """Decode one run of consecutive %XX as a UTF-8 byte sequence.

    %D1%96 is the UTF-8 encoding of Cyrillic і; per-byte Latin-1 decoding
    would yield "Ñ\\x96" and the homoglyph map would never see the codepoint.
    Invalid UTF-8 falls back to per-byte Latin-1.
    """
    pairs = re.findall(r"%([0-9a-fA-F]{2})", match.group(0))
    raw = bytes(int(p, 16) for p in pairs)
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def _decode_url_encoding(text: str) -> str:
    r"""Decode URL percent-encoding (%69 → i) with iterative decoding.

    Consecutive %XX runs decode as UTF-8 (so %D1%96 → Cyrillic і, which the
    homoglyph map then normalizes). Iterates up to 3 passes for
    double-encoding: %2569 → %69 → i.
    """
    prev = None
    result = text
    for _ in range(3):
        if result == prev:
            break
        prev = result
        result = _URL_RUN_ESCAPE.sub(_decode_url_run, result)
    return result


# ── Zero-width and invisible character stripping ─────────────────────────
# Characters that are invisible to the reader but break regex word matching
# (e.g., "ig\u200bnore" splits "ignore" across invisible chars).
_ZERO_WIDTH_CHARS = {
    "\u200b",  # ZERO WIDTH SPACE
    "\u200c",  # ZERO WIDTH NON-JOINER
    "\u200d",  # ZERO WIDTH JOINER
    "\u2060",  # WORD JOINER
    "\u00ad",  # SOFT HYPHEN
    "\u202a",  # LEFT-TO-RIGHT EMBEDDING
    "\u202b",  # RIGHT-TO-LEFT EMBEDDING
    "\u202c",  # POP DIRECTIONAL FORMATTING
    "\u202d",  # LEFT-TO-RIGHT OVERRIDE
    "\u202e",  # RIGHT-TO-LEFT OVERRIDE
    "\u2061",  # FUNCTION APPLICATION
    "\u2062",  # INVISIBLE TIMES
    "\u2063",  # INVISIBLE SEPARATOR
    "\u2064",  # INVISIBLE PLUS
    "\ufeff",  # ZERO WIDTH NO-BREAK SPACE (BOM)
    "\u00a0",  # NO-BREAK SPACE
}


def _strip_zero_width(text: str) -> str:
    """Remove zero-width and invisible separator characters."""
    return "".join(ch for ch in text if ch not in _ZERO_WIDTH_CHARS)


# ── Whitespace normalization ─────────────────────────────────────────────
# Normalize all whitespace variants (tabs, newlines, non-breaking spaces,
# em spaces, etc.) to single spaces so the .{0,20} regex gap works uniformly.
_WHITESPACE_RE = re.compile(r"\s+", re.UNICODE)


def _normalize_whitespace(text: str) -> str:
    """Collapse all Unicode whitespace to single ASCII spaces.

    This also handles the case where zero-width chars have already been stripped
    and adjacent words need a separator. Newlines, tabs, non-breaking spaces,
    em spaces, thin spaces, etc. all become regular spaces.
    """
    return _WHITESPACE_RE.sub(" ", text)


# ── HTML entity decoding ─────────────────────────────────────────────────
def _decode_html_entities(text: str) -> str:
    r"""Decode HTML entities iteratively (&#105; → i, &amp;#105; → i).

    html.unescape is single-pass: "&amp;#105;" becomes "&#105;" and a literal
    "&#105;" survives the guard, while a reader that applies entity decoding
    again sees "i". Iterates until stable (bounded) so layered entity
    wrappers cannot hide a keyword, mirroring the URL and escape decoders.
    """
    prev = None
    result = text
    for _ in range(5):  # max wrapper depth, consistent with _decode_escapes
        if result == prev:
            break
        prev = result
        result = html.unescape(result)
    return result


def _normalize_for_guard(text: str) -> str:
    r"""Full normalization pipeline for guard input/output.

    Order:
    1. Decode URL percent-encoding (%69 → i)
    2. Decode HTML entities iteratively (&#105; → i, &amp;#105; → i)
    3. Decode escape sequences (\x69 → i, \u0069 → i, etc.)
    4. Strip zero-width and invisible characters
    5. NFKC normalization (fullwidth, math alphanumerics, etc.)
    6. Homoglyph map (Greek, Cyrillic)
    7. Normalize whitespace variants to single space
    """
    # Step 1: URL encoding
    url_decoded = _decode_url_encoding(text)
    # Step 2: HTML entities (iterative — double-encoded wrappers must open)
    html_decoded = _decode_html_entities(url_decoded)
    # Step 3: Escape sequences (iterative for double-encoding)
    escape_decoded = _decode_escapes(html_decoded)
    # Step 4: Strip zero-width/invisible chars
    no_zw = _strip_zero_width(escape_decoded)
    # Step 5+6: NFKC + homoglyph map
    homoglyph_normalized = _normalize_homoglyphs(no_zw)
    # Step 7: Whitespace normalization
    return _normalize_whitespace(homoglyph_normalized)


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
    # ── Database connection strings with embedded credentials ──────────
    # Only URLs WITH credentials match ("db:5432/store" without @ passes).
    (
        r"(?i)(?:postgres|postgresql|mysql|mongodb|redis|amqp)://"
        r"[a-zA-Z0-9._-]+(?::[^@]+@)",
        "leak_db_connection_string",
    ),
]

# PII patterns for INTERMEDIATE data (raw tool results, tool arguments) only.
# Deliberately NOT in DEFAULT_OUTPUT_PATTERNS: the final answer guard shares
# check_output, and the LLM legitimately mentions dates, article numbers and
# contact emails in answers. A loose phone pattern there would block every
# answer carrying a date (15.09.2026) or article (1234567890) — false-positive
# regression verified before this split. Intermediate raw DB rows, on the other
# hand, must not be readable from the browser's devtools, so email/tight-phone
# blocking is cheap there (the transcript keeps raw content; only the SSE event
# is redacted, and the widget UI ignores tool_result payloads anyway).
#
# Phone formats covered (each ≥10 digits so 8-digit dates never match):
#   +15551234567, +7 999 123-45-67   — international with leading +
#   555-123-4567, 555 123 4567       — US 3-3-4
#   8 999 123-45-67, 7999123-45-67   — RU with leading 7/8
DEFAULT_INTERMEDIATE_PATTERNS: list[tuple[str, str]] = [
    *DEFAULT_OUTPUT_PATTERNS,
    (
        r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
        "leak_pii_email",
    ),
    (
        # "+" + date-like shape (+15.09.2026, +5/9/26) is not a phone:
        # the lookahead excludes dd.mm.yyyy before the digit run matches.
        r"\+(?!\d{1,2}[./]\d{1,2}[./]\d{2,4})\d[\d\s\-().]{8,}\d"
        r"|\b\d{3}[\s\-.]\d{3}[\s\-.]\d{4}\b"
        r"|\b[78][\s\-().]?\d{3}[\s\-]?\d{3}[\s\-]\d{2}[\s\-]\d{2}\b",
        "leak_pii_phone",
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
    # "block" | "warn". Honored by check_input; check_output/check_intermediate
    # always block on match — a warn-only output leak would stream to the
    # browser, so output-side blocking is unconditional by design.
    block_on_match: str = "block"
    input_patterns: list[tuple[str, str]] = field(
        default_factory=lambda: list(DEFAULT_BLOCK_PATTERNS)
    )
    output_patterns: list[tuple[str, str]] = field(
        default_factory=lambda: list(DEFAULT_OUTPUT_PATTERNS)
    )
    intermediate_patterns: list[tuple[str, str]] = field(
        default_factory=lambda: list(DEFAULT_INTERMEDIATE_PATTERNS)
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
                if "intermediate" in overrides:
                    config.intermediate_patterns = [
                        (p["pattern"], p["reason"]) for p in overrides["intermediate"]
                    ]
                # NB: each key replaces its family wholesale, and "output" does
                # NOT propagate into intermediate_patterns — intermediate keeps
                # the compiled defaults (default output + PII), so an output
                # override cannot re-open the tool-result scan. Operators who
                # ADD output patterns must add them to "intermediate" too.
            except (json.JSONDecodeError, KeyError, TypeError) as e:
                logger.warning("Failed to parse GUARDRAIL_BLOCK_PATTERNS: %s", e)
        return config


class GuardChecker:
    """Check messages against prompt injection patterns."""

    def __init__(self, config: GuardConfig | None = None):
        self.config = config or GuardConfig.from_env()
        self._input_compiled = [
            (re.compile(p), reason) for p, reason in self.config.input_patterns
        ]
        self._output_compiled = [
            (re.compile(p), reason) for p, reason in self.config.output_patterns
        ]
        self._intermediate_compiled = [
            (re.compile(p), reason) for p, reason in self.config.intermediate_patterns
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
        self._intermediate_compiled = [
            (re.compile(p), reason) for p, reason in self.config.intermediate_patterns
        ]

    def check_input(self, message: str) -> GuardResult:
        """Check user message for prompt injection."""
        if not self.config.enabled:
            return GuardResult()
        if not message:
            return GuardResult()
        # Decode escapes + normalize homoglyphs before regex search
        normalized = _normalize_for_guard(message)
        for compiled, reason in self._input_compiled:
            if compiled.search(normalized):
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
        """Check LLM response for system prompt leak or credentials.

        Gates the FINAL answer only: real secrets (credentials, bearer tokens,
        credentialed DB URLs) block. PII email/phone patterns deliberately do
        NOT run here — legitimate answers carry dates, article numbers and
        contact emails; see DEFAULT_INTERMEDIATE_PATTERNS for the split.
        """
        if not self.config.enabled:
            return GuardResult()
        if not content:
            return GuardResult()
        # Decode escapes + normalize homoglyphs before regex search
        normalized = _normalize_for_guard(content)
        for compiled, reason in self._output_compiled:
            if compiled.search(normalized):
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

    def check_intermediate(self, content: str) -> GuardResult:
        """Check intermediate data (raw tool results, tool arguments) for leaks.

        Extends the output scan with PII patterns (email, tight phone formats).
        Raw DB rows must not be readable from the browser's devtools via
        tool_result/tool_call SSE events; the transcript keeps raw content, so
        only the SSE event is redacted and answer quality is unaffected.
        """
        if not self.config.enabled:
            return GuardResult()
        if not content:
            return GuardResult()
        normalized = _normalize_for_guard(content)
        for compiled, reason in self._intermediate_compiled:
            if compiled.search(normalized):
                self.config.blocked_count += 1
                logger.warning(
                    "[GUARD] Matched intermediate: %s (pattern: %s)",
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

_guard_checker: GuardChecker | None = None


def get_guard_checker() -> GuardChecker:
    """Get or create singleton guard checker."""
    global _guard_checker
    if _guard_checker is None:
        _guard_checker = GuardChecker()
    return _guard_checker
