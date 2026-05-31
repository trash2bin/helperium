from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from demo.mcp_bridge import SYSTEM_PROMPT, call_tool, list_ollama_tools, mcp_session
from demo.settings import settings


class OllamaAssistant:
    def __init__(self) -> None:
        self.base_url = settings.ollama_url.rstrip("/")
        self.model = settings.ollama_model
        self.timeout = httpx.Timeout(settings.request_timeout)

    async def stream_answer(self, user_message: str) -> AsyncIterator[str]:
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ]

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            await self._ensure_available(client)
            async with mcp_session() as session:
                tools = await list_ollama_tools(session)
                for _ in range(4):
                    message = await self._chat_once(client, messages, tools)
                    tool_calls = self._extract_tool_calls(message)
                    if not tool_calls:
                        if content := message.get("content"):
                            yield content
                        return

                    messages.append(message)
                    for tool_call in tool_calls:
                        name = tool_call["name"]
                        arguments = tool_call["arguments"]
                        yield f"\n\n[tool:{name}]\n"
                        tool_result = await call_tool(session, name, arguments)
                        messages.append(
                            {
                                "role": "tool",
                                "content": tool_result,
                                "tool_name": name,
                            }
                        )

                async for token in self._stream_final(client, messages):
                    yield token

    async def health(self) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
            response = await client.get(f"{self.base_url}/api/tags")
            response.raise_for_status()
            data = response.json()
        return {"status": "ok", "model": self.model, "models": data.get("models", [])}

    async def _ensure_available(self, client: httpx.AsyncClient) -> None:
        try:
            response = await client.get(f"{self.base_url}/api/tags")
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise RuntimeError(
                f"Ollama недоступна по адресу {self.base_url}. "
                "Запустите Ollama и проверьте OLLAMA_URL."
            ) from exc

    async def _chat_once(
        self,
        client: httpx.AsyncClient,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> dict[str, Any]:
        response = await client.post(
            f"{self.base_url}/api/chat",
            json={
                "model": self.model,
                "messages": messages,
                "tools": tools,
                "stream": False,
            },
        )
        response.raise_for_status()
        return response.json().get("message", {})

    async def _stream_final(
        self,
        client: httpx.AsyncClient,
        messages: list[dict[str, Any]],
    ) -> AsyncIterator[str]:
        async with client.stream(
            "POST",
            f"{self.base_url}/api/chat",
            json={"model": self.model, "messages": messages, "stream": True},
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line:
                    continue
                chunk = json.loads(line)
                content = chunk.get("message", {}).get("content", "")
                if content:
                    yield content

    @staticmethod
    def _extract_tool_calls(message: dict[str, Any]) -> list[dict[str, Any]]:
        calls = []
        for item in message.get("tool_calls") or []:
            function = item.get("function") or {}
            name = function.get("name")
            raw_args = function.get("arguments") or function.get("args") or {}
            if not name:
                continue
            if isinstance(raw_args, str):
                try:
                    arguments = json.loads(raw_args)
                except json.JSONDecodeError:
                    arguments = {}
            else:
                arguments = raw_args
            calls.append({"name": name, "arguments": arguments})
        return calls
