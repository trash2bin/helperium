from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from copy import deepcopy
from pathlib import Path
from typing import Any

import litellm
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from demo.settings import PROJECT_ROOT, settings

logger = logging.getLogger("demo.api.agent")

litellm.drop_params = True
setattr(litellm, "set_verbose", os.environ.get("LITELLM_DEBUG", "false").lower() == "true")


# Температура модели (чем выше, тем более креативным)
TEMPERATURE = 0.3

# Максимальное количество итераций модели до ответа
MAX_ITERATIONS = 5

# Максимальное количество пустых ответов до остановки модели
# Например, если модель не вызывает инструменты и не возвращает ответ
# в течение нескольких раундов, остановим её работу.
MAX_EMPTY_ROUNDS = 3

SYSTEM_PROMPT = """
Ты университетский ассистент с доступом к базе данных через MCP-инструменты.

КРИТИЧЕСКИ ВАЖНЫЕ ПРАВИЛА:
1. Ты НЕ знаешь никаких данных о студентах, расписании, оценках, преподавателях или документах без инструментов.
2. При любом вопросе о данных университета сначала используй MCP-инструмент.
3. Не выдумывай ответ из памяти.
4. Если вопрос общий — отвечай кратко и по делу.

ПРАВИЛА ОТВЕТА:
- Отвечай на языке пользователя, по умолчанию используй русский.
- Если данных нет — прямо скажи об этом.
- Если не понял запрос — уточни.
"""

@asynccontextmanager
async def mcp_session() -> AsyncIterator[ClientSession]:
    logger.debug("[AGENT] Creating MCP session...")
    server_path = str(PROJECT_ROOT / "server.py")
    params = StdioServerParameters(
        command=settings.python_executable,
        args=[server_path],
        env={**dict(os.environ), "PYTHONPATH": str(PROJECT_ROOT)},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            logger.debug("[AGENT] MCP session initialized")
            yield session
            logger.debug("[AGENT] MCP session closing")


async def list_mcp_tools(session: ClientSession) -> list[dict[str, Any]]:
    result = await session.list_tools()
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description or "",
                "parameters": tool.inputSchema,
            },
        }
        for tool in result.tools
    ]


def _collect_text_content(result: Any) -> str:
    return "\n".join(
        getattr(item, "text", "")
        for item in getattr(result, "content", []) or []
        if getattr(item, "text", None)
    )


async def call_mcp_tool(session: ClientSession, name: str, arguments: dict[str, Any]) -> str:
    try:
        logger.debug(f"[AGENT] Calling MCP tool: {name} with args: {arguments}")
        result = await session.call_tool(name, arguments)

        if result.isError:
            error_text = _collect_text_content(result)
            return json.dumps(
                {"ok": False, "error": error_text or f"Error calling tool {name}"},
                ensure_ascii=False,
            )

        structured = getattr(result, "structuredContent", None)
        if structured is not None:
            return json.dumps({"ok": True, "data": structured}, ensure_ascii=False)

        content = _collect_text_content(result)
        return json.dumps({"ok": True, "data": content}, ensure_ascii=False)
    except Exception as e:
        logger.exception(f"[AGENT] Exception calling tool {name}")
        return json.dumps({"ok": False, "error": f"Error calling {name}: {e}"}, ensure_ascii=False)


class LLMAgent:
    def __init__(self) -> None:
        model_name = settings.ollama_model
        known_providers = (
            "ollama/",
            "ollama_chat/",
            "openai/",
            "anthropic/",
            "deepseek/",
            "huggingface/",
            "mistral/",
            "groq/",
            "together_ai/",
        )

        # Если URL Ollama задан и модель не является известным провайдером, используем ollama_chat
        if settings.ollama_url and not model_name.startswith(known_providers):
            self.model = f"ollama_chat/{model_name}"
        else:
            self.model = model_name

        self.api_base = settings.ollama_url.rstrip("/") if settings.ollama_url else None
        self.timeout = settings.request_timeout
        self.enable_thinking = settings.think_mode

        self.max_history_turns = settings.history_turns
        self.max_history_content_chars = settings.history_content_chars

        self._memory_path = PROJECT_ROOT / ".agent_memory.json"
        self._histories: dict[str, list[list[dict[str, Any]]]] = self._load_histories()
        self._session_locks: dict[str, asyncio.Lock] = {}

    def _load_histories(self) -> dict[str, list[list[dict[str, Any]]]]:
        """Load the agent's memory from the persistent storage."""
        if not self._memory_path.exists():
            return {}
        try:
            data = json.loads(self._memory_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            logger.exception("[AGENT] Failed to load memory file")
            return {}

    def _save_histories(self) -> None:
        tmp = self._memory_path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(self._histories, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(self._memory_path)

    async def stream_answer(self, user_message: str, session_id: str = "default") -> AsyncIterator[str]:
        session_id = self._normalize_session_id(session_id)
        logger.info(f"[AGENT] User message for session {session_id}: {user_message[:100]}...")

        lock = self._session_locks.setdefault(session_id, asyncio.Lock())
        async with lock:
            async for token in self._stream_answer_locked(user_message, session_id):
                yield token

    async def _stream_answer_locked(self, user_message: str, session_id: str) -> AsyncIterator[str]:
        turn_messages: list[dict[str, Any]] = [{"role": "user", "content": user_message}]
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            *self._history_messages(session_id),
            {"role": "user", "content": user_message},
        ]

        async with mcp_session() as session:
            tools = await list_mcp_tools(session)
            logger.info(f"[AGENT] Available tools: {[t['function']['name'] for t in tools]}")

            empty_rounds = 0

            for iteration in range(MAX_ITERATIONS):
                logger.info(f"[AGENT] Iteration {iteration + 1}/{MAX_ITERATIONS} - calling model...")
                message = await self._chat_once(messages, tools)
                logger.info(f"[AGENT] Model response: {json.dumps(message, ensure_ascii=False)[:300]}...")

                sanitized = {k: v for k, v in message.items() if k != "reasoning_content"}
                tool_calls = self._extract_tool_calls(sanitized)
                content = (sanitized.get("content") or "").strip()

                if tool_calls:
                    logger.info(f"[AGENT] Processing {len(tool_calls)} tool call(s)")
                    messages.append(sanitized)
                    turn_messages.append(sanitized)
                    empty_rounds = 0

                    for tool_call in tool_calls:
                        name = tool_call["name"]
                        arguments = tool_call["arguments"]
                        tool_call_id = tool_call.get("id") or f"call_{name}_{uuid.uuid4().hex[:8]}"

                        logger.info(f"[AGENT] Executing tool: {name}")
                        yield f"\n\n[tool:{name}]\n"

                        tool_result = await call_mcp_tool(session, name, arguments)
                        logger.info(f"[AGENT] Tool {name} result: {tool_result[:250]}...")

                        tool_message = {
                            "role": "tool",
                            "content": tool_result,
                            "tool_call_id": tool_call_id,
                            "name": name,
                        }
                        messages.append(tool_message)
                        turn_messages.append(tool_message)

                    continue

                if content:
                    logger.info(f"[AGENT] Final content: {content[:100]}...")
                    messages.append(sanitized)
                    turn_messages.append(sanitized)
                    self._remember_turn(session_id, turn_messages)
                    yield content
                    return

                empty_rounds += 1
                logger.warning(
                    "[AGENT] Model returned no content/tool_calls (reasoning only or empty). "
                    f"empty_rounds={empty_rounds}"
                )


                messages.append(
                    {
                        "role": "system",
                        "content": ( # Отдаем модели ее мысли и ожидаем ответа
                            f"Your thought: {message.get('reasoning_content', '')}\n"
                            "Верни только tool_calls или финальный ответ. "
                            "Не пиши reasoning_content и не повторяй внутренние рассуждения."
                        ),
                    }
                )

                if empty_rounds >= MAX_EMPTY_ROUNDS:
                    break

            async for token in self._stream_and_save(messages, turn_messages, session_id):
                yield token

    async def _stream_and_save(
        self,
        messages: list[dict[str, Any]],
        turn_messages: list[dict[str, Any]],
        session_id: str,
    ) -> AsyncIterator[str]:
        final_parts: list[str] = []

        async for token in self._stream_final(messages):
            final_parts.append(token)
            yield token

        if not final_parts:
            fallback_msg = "Извините, модель завершила работу без ответа. Попробуйте уточнить запрос."
            yield fallback_msg
            final_parts.append(fallback_msg)

        turn_messages.append({"role": "assistant", "content": "".join(final_parts)})
        self._remember_turn(session_id, turn_messages)

    async def _chat_once(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Отправляет запрос к модели и возвращает ответ."""

        extra_params: dict[str, Any] = {}
        if self.enable_thinking:
            extra_params["extra_body"] = {"think": True}
        if self.api_base:
            extra_params["api_base"] = self.api_base

        response: Any = await litellm.acompletion(
            model=self.model,
            messages=messages,
            tools=tools,
            stream=False,
            timeout=self.timeout,
            temperature=TEMPERATURE,
            **extra_params,
        )

        msg_obj = response.choices[0].message
        result: dict[str, Any] = {
            "role": getattr(msg_obj, "role", "assistant") or "assistant",
            "content": getattr(msg_obj, "content", "") or "",
        }

        if getattr(msg_obj, "tool_calls", None):
            result["tool_calls"] = [
                {
                    "id": getattr(tc, "id", None),
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments or "{}",
                    },
                }
                for tc in msg_obj.tool_calls
            ]

        # reasonning_content оставляем только для логов, но НЕ для контекста
        reasoning = getattr(msg_obj, "reasoning_content", None)
        if reasoning:
            result["reasoning_content"] = reasoning

        return result

    async def _stream_final(self, messages: list[dict[str, Any]]) -> AsyncIterator[str]:
        """Отправляет финальный запрос к модели и возвращает ответ."""

        extra_params: dict[str, Any] = {}
        if self.enable_thinking:
            extra_params["extra_body"] = {"think": True}
        if self.api_base:
            extra_params["api_base"] = self.api_base

        response: Any = await litellm.acompletion(
            model=self.model,
            messages=messages,
            stream=True,
            timeout=self.timeout,
            **extra_params,
        )

        async for chunk in response:
            if chunk.choices and getattr(chunk.choices[0].delta, "content", None):
                yield chunk.choices[0].delta.content

    @staticmethod
    def _extract_tool_calls(message: dict[str, Any]) -> list[dict[str, Any]]:
        """Извлекает вызовы инструментов из сообщения модели."""

        calls = []

        native_calls = message.get("tool_calls") or []
        for item in native_calls:
            function = item.get("function") or {}
            name = function.get("name")
            if not name:
                continue

            raw_args = function.get("arguments") or "{}"
            try:
                arguments = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
            except json.JSONDecodeError:
                arguments = {}

            calls.append(
                {
                    "id": item.get("id") or f"call_{name}_{uuid.uuid4().hex[:8]}",
                    "name": name,
                    "arguments": arguments,
                }
            )

        if calls:
            return calls

        text_content = message.get("content") or ""
        if not text_content:
            return []

        potential_jsons = []

        md_matches = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text_content, re.DOTALL)
        potential_jsons.extend(md_matches)

        if not potential_jsons:
            start_idx = text_content.find("{")
            end_idx = text_content.rfind("}")
            if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
                potential_jsons.append(text_content[start_idx : end_idx + 1])

        for json_str in potential_jsons:
            try:
                data = json.loads(json_str)
            except json.JSONDecodeError:
                continue

            extracted_items = []
            if "tool_calls" in data and isinstance(data["tool_calls"], list):
                extracted_items = data["tool_calls"]
            elif "tool_name" in data or "name" in data or "function" in data:
                extracted_items = [data]

            for item in extracted_items:
                name = item.get("tool_name") or item.get("name")
                args = item.get("arguments", {})

                if not name and "function" in item and isinstance(item["function"], dict):
                    name = item["function"].get("name")
                    args = item["function"].get("arguments", args)

                if not name:
                    continue

                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {}

                calls.append(
                    {
                        "id": item.get("id") or f"call_{name}_{uuid.uuid4().hex[:8]}",
                        "name": name,
                        "arguments": args,
                    }
                )

        return calls

    def _remember_turn(self, session_id: str, turn_messages: list[dict[str, Any]]) -> None:
        filtered: list[dict[str, Any]] = []
        for message in turn_messages:
            clean = self._compact_history_message(message)

            # Не сохраняем пустые assistant-сообщения без tool_calls и без content
            if clean.get("role") == "assistant":
                has_content = bool((clean.get("content") or "").strip())
                has_tool_calls = bool(clean.get("tool_calls"))
                if not has_content and not has_tool_calls:
                    continue

            filtered.append(clean)

        history = self._histories.setdefault(session_id, [])
        history.append(filtered)

        if len(history) > self.max_history_turns:
            del history[: len(history) - self.max_history_turns]

        self._save_histories()
        logger.debug(f"[AGENT] Stored turn for session {session_id}; total turns={len(history)}")

    def _compact_history_message(self, message: dict[str, Any]) -> dict[str, Any]:
        compact = {k: deepcopy(v) for k, v in message.items() if k != "reasoning_content"}
        content = compact.get("content")
        if isinstance(content, str) and len(content) > self.max_history_content_chars:
            compact["content"] = content[: self.max_history_content_chars] + "\n\n...[обрезано в истории диалога]"
        return compact

    def _history_messages(self, session_id: str) -> list[dict[str, Any]]:
        turns = self._histories.get(session_id, [])
        return [deepcopy(message) for turn in turns for message in turn]

    @staticmethod
    def _normalize_session_id(session_id: str) -> str:
        session_id = str(session_id or "").strip()
        return session_id[:128] if session_id else "default"

    async def health(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "model": self.model,
            "api_base": self.api_base,
            "thinking_enabled": self.enable_thinking,
        }


agent = LLMAgent()
