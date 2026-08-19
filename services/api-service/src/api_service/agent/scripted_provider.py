"""Deterministic provider fixture for the same typed protocol as LiteLLM."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from collections.abc import Sequence
from typing import Any

from .models import CompletionRequest, CompletionResponse, ToolCall, UsageInfo


class ScriptedLLMProvider:
    """Consume one explicit provider response per call and record every request.

    JSONL accepts only the provider contract: ``content``, ``tool_calls`` with
    ``name``/``arguments`` (optional ``id``), ``usage``, ``cost`` and ``delay_ms``.
    Tool-call IDs omitted by a test fixture receive a deterministic test-only ID.
    """

    model = "scripted/test"
    api_base: str | None = None
    enable_thinking = False

    def __init__(
        self,
        rounds: Sequence[dict[str, Any] | CompletionResponse] | None = None,
        *,
        record_to: str | None = None,
    ) -> None:
        self._rounds = [
            self._round(raw, index) for index, raw in enumerate(rounds or [], 1)
        ]
        self._cursor = 0
        self._record_to = record_to
        self.requests: list[CompletionRequest] = []

    @classmethod
    def from_file(
        cls, path: str, record_to: str | None = None
    ) -> "ScriptedLLMProvider":
        file_path = Path(path).expanduser()
        if not file_path.exists():
            return cls(record_to=record_to)
        rounds: list[dict[str, Any]] = []
        for line_number, line in enumerate(
            file_path.read_text(encoding="utf-8").splitlines(), 1
        ):
            stripped = line.strip()
            if not stripped or stripped.startswith(("#", "//")):
                continue
            try:
                raw = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ValueError(f"script line {line_number} is not JSON") from exc
            if not isinstance(raw, dict):
                raise ValueError(f"script line {line_number} must be an object")
            rounds.append(raw)
        return cls(rounds, record_to=record_to)

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        self.requests.append(request)
        if self._cursor < len(self._rounds):
            response, delay_ms = self._rounds[self._cursor]
        else:
            response, delay_ms = CompletionResponse(), 0
        self._cursor += 1
        if delay_ms:
            await asyncio.sleep(delay_ms / 1000)
        if self._record_to:
            self._record(request, response)
        return response

    @staticmethod
    def _round(
        raw: dict[str, Any] | CompletionResponse, index: int
    ) -> tuple[CompletionResponse, float]:
        if isinstance(raw, CompletionResponse):
            return raw, 0
        raw_calls = raw.get("tool_calls", [])
        if not isinstance(raw_calls, list):
            raise ValueError("script tool_calls must be a list")
        calls: list[ToolCall] = []
        for call_index, call in enumerate(raw_calls, 1):
            if not isinstance(call, dict):
                raise ValueError("script tool call must be an object")
            name = call.get("name")
            arguments = call.get("arguments", {})
            if not isinstance(name, str) or not name:
                raise ValueError("script tool call requires a name")
            if not isinstance(arguments, dict):
                raise ValueError("script tool call arguments must be an object")
            calls.append(
                ToolCall(
                    id=call.get("id") or f"script-{index}-{call_index}",
                    name=name,
                    arguments=arguments,
                )
            )
        raw_usage = raw.get("usage")
        usage = UsageInfo.model_validate(raw_usage) if raw_usage is not None else None
        return (
            CompletionResponse(
                content=raw.get("content", ""),
                tool_calls=calls,
                usage=usage,
                cost=raw.get("cost", 0.0),
            ),
            float(raw.get("delay_ms", 0)),
        )

    def _record(self, request: CompletionRequest, response: CompletionResponse) -> None:
        assert self._record_to is not None
        payload = {"request": request.model_dump(), "response": response.model_dump()}
        with open(self._record_to, "a", encoding="utf-8") as file:
            file.write(json.dumps(payload, ensure_ascii=False) + "\n")

    @property
    def remaining(self) -> int:
        return max(0, len(self._rounds) - self._cursor)

    @property
    def exhausted(self) -> bool:
        return self._cursor >= len(self._rounds)


def create_scripted_provider() -> ScriptedLLMProvider | None:
    if os.environ.get("USE_SCRIPTED_LLM", "").lower() not in {"1", "true", "yes"}:
        return None
    return ScriptedLLMProvider.from_file(
        os.environ.get("SCRIPTED_LLM_PATH", ""),
        record_to=os.environ.get("SCRIPTED_LLM_RECORD"),
    )
