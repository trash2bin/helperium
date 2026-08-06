"""ScriptedLLMProvider — controllable LLM provider for dev/testing.

Вместо вызова реальной LLM читает ответы из JSONL-файла или списка.
Каждая строка/элемент — один раунд ``CompletionResponse``.

Использование:

    # Из Python
    provider = ScriptedLLMProvider.from_file("scripts/llm.jsonl")
    for round in provider.rounds:
        resp = await provider.complete(req)
        print(f"Round {round}: tools={resp.tool_calls}, content={resp.content[:50]!r}")

    # Из env (dev-режим):
    USE_SCRIPTED_LLM=1 SCRIPTED_LLM_PATH=script.jsonl ./scripts/dev.sh restart

    # Запись реальных LLM вызовов:
    provider = ScriptedLLMProvider(record_to="recorded.jsonl")
    # все complete() вызовы пишутся в файл

Формат JSONL — каждый объект может содержать:
    content (str):         текст ответа
    tool_calls (list):     [{name, arguments}, ...]
    reasoning_content (str, optional): reasoning/thinking
    usage (dict, optional): {prompt_tokens, completion_tokens, total_tokens}
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any

from .models import CompletionRequest, CompletionResponse, UsageInfo

logger = logging.getLogger("api_service.agent.scripted_provider")


@dataclass
class _Round:
    """Один скриптованный раунд."""

    content: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    reasoning_content: str | None = None
    usage: dict | None = None
    cost: float = 0.0
    delay_ms: float = 0.0  # имитация задержки LLM


class ScriptedLLMProvider:
    """Controllable LLM provider for dev/testing.

    Вместо вызова реальной LLM возвращает предопределённые ответы.
    Ответы читаются из JSONL-файла (одна строка = один раунд)
    или передаются списком при создании.

    Поддерживает режим записи: ``record_to="path.jsonl"`` — каждый
    ``complete()`` вызов пишется в файл для последующего реплея.
    """

    model: str = "scripted/dev"
    api_base: str | None = None
    enable_thinking: bool = False

    def __init__(
        self,
        rounds: list[_Round] | None = None,
        record_to: str | None = None,
    ) -> None:
        """Initialise with explicit list of rounds.

        Args:
            rounds: List of ``_Round`` — each one is consumed in order.
            record_to: If set, every ``complete()`` call is appended to this JSONL.
        """
        self._rounds: list[_Round] = list(rounds or [])
        self._cursor = 0
        self._record_to = record_to
        self._file = None
        if record_to:
            logger.info("[SCRIPTED] Recording calls to %s", record_to)

    @classmethod
    def from_file(cls, path: str, record_to: str | None = None) -> ScriptedLLMProvider:
        """Load scripted responses from a JSONL file.

        Format — each line is a JSON object with:
            content (str):                  text response
            tool_calls (list, optional):    [{name, arguments}, ...]
            reasoning_content (str, optional): thinking
            usage (dict, optional):         {prompt_tokens, ...}
            cost (float, optional):         cost in USD
            delay_ms (int, optional):       simulated latency

        Lines starting with ``//`` or ``#`` are skipped as comments.
        """
        path = os.path.expanduser(path)
        if not os.path.exists(path):
            logger.warning(
                "[SCRIPTED] File not found: %s — returning empty provider", path
            )
            return cls(record_to=record_to)

        rounds: list[_Round] = []
        with open(path) as f:
            for lineno, line in enumerate(f, 1):
                stripped = line.strip()
                if not stripped or stripped.startswith(("//", "#")):
                    continue
                try:
                    data = json.loads(stripped)
                    rounds.append(
                        _Round(
                            content=data.get("content", ""),
                            tool_calls=data.get("tool_calls", []),
                            reasoning_content=data.get("reasoning_content"),
                            usage=data.get("usage"),
                            cost=data.get("cost", 0.0),
                            delay_ms=data.get("delay_ms", 0.0),
                        )
                    )
                except json.JSONDecodeError as e:
                    logger.warning("[SCRIPTED] Line %d: invalid JSON — %s", lineno, e)

        logger.info("[SCRIPTED] Loaded %d rounds from %s", len(rounds), path)
        return cls(rounds=rounds, record_to=record_to)

    def _next_round(self) -> _Round:
        """Pop the next scripted round, or return empty if exhausted."""
        if self._cursor < len(self._rounds):
            r = self._rounds[self._cursor]
            self._cursor += 1
            return r
        return _Round()

    async def complete(self, req: CompletionRequest) -> CompletionResponse:
        """Return the next scripted response.

        If recording, appends the request + response to the JSONL file.
        """
        r = self._next_round()
        # Simulate latency
        if r.delay_ms > 0:
            import asyncio

            await asyncio.sleep(r.delay_ms / 1000)

        # Build response
        usage: UsageInfo | None = None
        if r.usage:
            usage = UsageInfo(
                prompt_tokens=r.usage.get("prompt_tokens", 0),
                completion_tokens=r.usage.get("completion_tokens", 0),
                total_tokens=r.usage.get("total_tokens", 0),
            )

        # Prepare tool_calls in LLM format
        tool_calls_raw: list[dict[str, Any]] = []
        for tc in r.tool_calls:
            name = tc.get("name", "")
            args = tc.get("arguments", {})
            tool_calls_raw.append(
                {
                    "id": tc.get("id", f"call_scripted_{self._cursor}"),
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": json.dumps(args, ensure_ascii=False)
                        if isinstance(args, dict)
                        else str(args),
                    },
                }
            )

        resp = CompletionResponse(
            content=r.content,
            tool_calls=tool_calls_raw,
            reasoning_content=r.reasoning_content,
            usage=usage,
            cost=r.cost,
            content_tokens=[r.content] if r.content else [],
        )

        # Record request + response
        if self._record_to:
            await self._record(req, resp)

        # Log
        tc_info = [
            {
                "name": tc.get("function", {}).get("name", "?"),
                "args": tc.get("function", {}).get("arguments", "{}"),
            }
            for tc in resp.tool_calls
        ]
        if resp.tool_calls:
            logger.info(
                "[SCRIPTED] Round %d/%d — tool_calls=%s",
                self._cursor,
                len(self._rounds),
                tc_info,
            )
        elif resp.content:
            logger.info(
                "[SCRIPTED] Round %d/%d — content=%s",
                self._cursor,
                len(self._rounds),
                resp.content[:80],
            )
        else:
            logger.info(
                "[SCRIPTED] Round %d/%d — empty (end of script?)",
                self._cursor,
                len(self._rounds),
            )

        return resp

    async def _record(self, req: CompletionRequest, resp: CompletionResponse) -> None:
        """Append request + response to the JSONL log."""
        try:
            record = {
                "request": {
                    "messages": [
                        {"role": m.get("role"), "content": m.get("content", "")[:200]}
                        for m in (req.messages or [])
                    ],
                    "tools_count": len(req.tools or []),
                },
                "response": {
                    "content": resp.content,
                    "tool_calls_count": len(resp.tool_calls),
                    "tool_calls": [
                        {
                            "name": tc.get("function", {}).get("name"),
                            "arguments": tc.get("function", {}).get("arguments"),
                        }
                        for tc in resp.tool_calls
                    ],
                    "usage": resp.usage.model_dump() if resp.usage else None,
                },
            }
            assert self._record_to is not None  # guarded by caller
            with open(self._record_to, "a") as f:
                f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        except Exception as e:
            logger.warning("[SCRIPTED] Failed to record: %s", e)

    @property
    def remaining(self) -> int:
        """Number of remaining scripted rounds."""
        return len(self._rounds) - self._cursor

    @property
    def exhausted(self) -> bool:
        """True if all rounds were consumed."""
        return self._cursor >= len(self._rounds)


def create_scripted_provider() -> ScriptedLLMProvider | None:
    """Factory: create a ScriptedLLMProvider from env vars.

    Reads:
        ``USE_SCRIPTED_LLM`` — если ``1``/``true``, читает ``SCRIPTED_LLM_PATH``.
        ``SCRIPTED_LLM_PATH`` — путь к JSONL-файлу со скриптами.
        ``SCRIPTED_LLM_RECORD`` — если нужна запись реальных вызовов.

    Если ``USE_SCRIPTED_LLM`` не установлен — возвращает ``None``,
    и pipeline использует реальную LLM как обычно.
    """
    use_scripted = os.environ.get("USE_SCRIPTED_LLM", "").lower() in (
        "1",
        "true",
        "yes",
    )
    if not use_scripted:
        import sys as _sys

        _sys.stderr.write("[SCRIPTED] USE_SCRIPTED_LLM not set — using real LLM\n")
        _sys.stderr.flush()
        return None

    path = os.environ.get("SCRIPTED_LLM_PATH", "")
    record_to = os.environ.get("SCRIPTED_LLM_RECORD", None)

    import sys as _sys

    _sys.stderr.write(f"[SCRIPTED] USE_SCRIPTED_LLM=1, path={path!r}\n")
    _sys.stderr.flush()

    if not path:
        logger.info(
            "[SCRIPTED] USE_SCRIPTED_LLM=1 but SCRIPTED_LLM_PATH is empty — using empty rounds"
        )
        return ScriptedLLMProvider(record_to=record_to)

    return ScriptedLLMProvider.from_file(path, record_to=record_to)
