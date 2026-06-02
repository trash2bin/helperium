from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Literal, cast

import litellm
from litellm import CustomStreamWrapper
from litellm.types.utils import ModelResponse, TextCompletionResponse

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from demo.api.backlog import backlog
from demo.api.sessions import session_store
from demo.settings import PROJECT_ROOT, settings

logger = logging.getLogger("demo.api.agent")

# LiteLLM: drop unsupported OpenAI params and enable provider-specific param adaptation.
litellm.drop_params = True
setattr(litellm, "set_verbose", os.environ.get("LITELLM_DEBUG", "false").lower() == "true")
# Needed for some providers/models with thinking blocks / reasoning + tool calling compatibility.
setattr(litellm, "modify_params", True)


# Температура модели (чем выше, тем более креативным реже зацикливаться)
TEMPERATURE = 0.5

# Максимальное количество итераций модели до ответа
MAX_ITERATIONS = 5

# Максимальное количество токенов для модели во время рассуждений
MAX_TOKENS_THINKING = 4096

# Максимальное количество пустых раундов (без ответа) до остановки
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
""".strip()

EventType = Literal[
    "status",
    "token",
    "tool_call",
    "tool_result",
    "final",
    "error",
]


@dataclass(slots=True)
class AgentEvent:
    type: EventType
    data: Any


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
        logger.debug("[AGENT] Calling MCP tool: %s with args: %s", name, arguments)
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
    except Exception as exc:
        logger.exception("[AGENT] Exception calling tool %s", name)
        return json.dumps({"ok": False, "error": f"Error calling {name}: {exc}"}, ensure_ascii=False)


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

        if settings.ollama_url and not model_name.startswith(known_providers):
            self.model = f"ollama_chat/{model_name}"
        else:
            self.model = model_name

        self.api_base = settings.ollama_url.rstrip("/") if settings.ollama_url else None
        self.timeout = settings.request_timeout
        self.enable_thinking = settings.think_mode

        self._session_locks: dict[str, asyncio.Lock] = {}

    async def stream_answer(self, user_message: str, session_id: str = "default") -> AsyncIterator[str]:
        """Backward-compatible token stream for the existing server.py."""
        streamed_text = ""
        async for event in self.stream_events(user_message, session_id=session_id):
            if event.type == "token":
                token = str(event.data)
                streamed_text += token
                yield token
            elif event.type == "final":
                content = event.data.get("content") if isinstance(event.data, dict) else None
                if content:
                    suffix = self._unstreamed_suffix(streamed_text, str(content))
                    if suffix:
                        yield suffix

    async def stream_sse(self, user_message: str, session_id: str = "default") -> AsyncIterator[str]:
        async for event in self.stream_events(user_message, session_id=session_id):
            yield self._format_sse_event(event)

    async def stream_events(self, user_message: str, session_id: str = "default") -> AsyncIterator[AgentEvent]:
        session_id = session_store.normalize_session_id(session_id)
        logger.info("[AGENT] User message for session %s: %s...", session_id, user_message[:100])

        lock = self._session_locks.setdefault(session_id, asyncio.Lock())
        async with lock:
            async for event in self._run_turn(user_message, session_id):
                yield event

    async def _run_turn(self, user_message: str, session_id: str) -> AsyncIterator[AgentEvent]:
        turn_messages: list[dict[str, Any]] = [{"role": "user", "content": user_message}]
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            *self._history_messages(session_id),
            {"role": "user", "content": user_message},
        ]

        turn_id = backlog.turn_start(session_id, user_message)

        try:
            async with mcp_session() as session:
                tools = await list_mcp_tools(session)
                logger.info("[AGENT] Available tools: %s", [t["function"]["name"] for t in tools])

                empty_rounds = 0

                for iteration in range(MAX_ITERATIONS):
                    logger.info("[AGENT] Iteration %s/%s - calling model...", iteration + 1, MAX_ITERATIONS)
                    backlog.model_request(session_id, turn_id, iteration, messages, tools)

                    final_message = None
                    async for event in self._stream_model_once(messages, tools):
                        if event.type == "token":
                            yield event
                        elif event.type == "final":
                            final_message = event.data

                    if final_message is None:
                        final_message = self._last_final_message
                    if final_message is None:
                        empty_rounds += 1
                        backlog.empty_round(
                            session_id,
                            turn_id,
                            iteration,
                            "",
                            messages,
                        )
                        yield AgentEvent(
                            "status",
                            {
                                "phase": "empty_round",
                                "iteration": iteration,
                                "empty_rounds": empty_rounds,
                            },
                        )
                        if empty_rounds >= MAX_EMPTY_ROUNDS:
                            break
                        continue

                    backlog.model_response(
                        session_id,
                        turn_id,
                        iteration,
                        final_message,
                        duration_ms=0,
                        token_usage=final_message.pop("_usage", None),
                    )

                    reasoning = final_message.get("reasoning_content")
                    tool_calls = self._extract_tool_calls(final_message)
                    content = (final_message.get("content") or "").strip()

                    if reasoning:
                        # Internal-only: do not send raw chain-of-thought to frontend.
                        backlog.empty_round(session_id, turn_id, iteration, reasoning, messages)

                    if tool_calls:
                        yield AgentEvent(
                            "status",
                            {
                                "phase": "tool_calls",
                                "iteration": iteration,
                                "count": len(tool_calls),
                            },
                        )

                        final_message["tool_calls"] = self._format_tool_calls_for_model(tool_calls)
                        messages.append(final_message)
                        turn_messages.append(final_message)
                        empty_rounds = 0

                        for tool_call in tool_calls:
                            name = tool_call["name"]
                            arguments = tool_call["arguments"]
                            tool_call_id = tool_call.get("id") or f"call_{name}_{uuid.uuid4().hex[:8]}"

                            backlog.tool_call(session_id, turn_id, iteration, name, arguments)
                            yield AgentEvent(
                                "tool_call",
                                {
                                    "id": tool_call_id,
                                    "name": name,
                                    "arguments": arguments,
                                },
                            )

                            tool_result = await call_mcp_tool(session, name, arguments)
                            backlog.tool_result(session_id, turn_id, iteration, name, tool_result, duration_ms=0)

                            yield AgentEvent(
                                "tool_result",
                                {
                                    "id": tool_call_id,
                                    "name": name,
                                    "result": tool_result,
                                },
                            )

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
                        final_message["content"] = content
                        messages.append(final_message)
                        turn_messages.append(final_message)
                        self._remember_turn(session_id, turn_messages)

                        yield AgentEvent(
                            "final",
                            {
                                "content": content,
                            },
                        )
                        return

                    empty_rounds += 1
                    yield AgentEvent(
                        "status",
                        {
                            "phase": "empty_round",
                            "iteration": iteration,
                            "empty_rounds": empty_rounds,
                        },
                    )

                    # Дополнительно сохраняем reasoning_content в историю чтобы модель не тупила
                    # если вдруг подумала и ничего не сделала
                    if reasoning:
                        messages.append(
                            {
                                "role": "assistant",
                                "content": reasoning,
                            }
                        )

                    messages.append(
                        {
                            "role": "system",
                            "content": (
                                "Верни только tool_calls или финальный ответ. "
                                "Опирайся на предыдущие сообщения и reasoning_content и действуй"
                            ),
                        }
                    )
                    if empty_rounds >= MAX_EMPTY_ROUNDS:
                        break

                async for token in self._fallback_stream(messages, turn_messages, session_id):
                    yield AgentEvent("token", token)

        except Exception as exc:
            backlog.error(session_id, turn_id, MAX_ITERATIONS, str(exc))
            yield AgentEvent("error", {"message": str(exc)})
            raise

    async def _stream_model_once(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> AsyncIterator[AgentEvent]:
        self._last_final_message: dict[str, Any] | None = None
        extra_params: dict[str, Any] = {}
        if self.enable_thinking:
            extra_params["extra_body"] = {"think": True}
        if self.api_base:
            extra_params["api_base"] = self.api_base

        response = await litellm.acompletion(
            model=self.model,
            messages=messages,
            tools=tools,
            stream=True,
            timeout=self.timeout,
            temperature=TEMPERATURE,
            max_tokens=MAX_TOKENS_THINKING,
            **extra_params,
        )

        # Проверка на корректный тип данных от LiteLLM
        if not isinstance(response, CustomStreamWrapper):
            logger.error(
                "Expected CustomStreamWrapper, got %s",
                type(response).__name__,
            )
            raise TypeError(
                f"Expected CustomStreamWrapper, got {type(response).__name__}"
            )

        chunks: list[Any] = []
        async for chunk in response:
            chunks.append(chunk)
            delta = chunk.choices[0].delta

            token = getattr(delta, "content", None)
            if token:
                yield AgentEvent("token", token)

        final = litellm.stream_chunk_builder(chunks, messages=messages)

        # Проверка на корректный тип данных от LiteLLM
        if final is None:
            raise RuntimeError("stream_chunk_builder returned None")
        elif not isinstance(final, ModelResponse):
            logger.error(
                "Expected ModelResponse, got %s",
                type(final).__name__,
            )
            raise TypeError(
                f"Expected ModelResponse, got {type(final).__name__}"
            )

        msg_obj = final.choices[0].message

        if msg_obj is None:
            raise RuntimeError("ModelResponse.choices[0].message is None")

        result: dict[str, Any] = {
            "role": msg_obj.role or "assistant",
            "content": msg_obj.content or "",
        }

        tool_calls = msg_obj.tool_calls or []
        if tool_calls:
            result["tool_calls"] = [
                {
                    "id": getattr(tc, "id", None),
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments or "{}",
                    },
                }
                for tc in tool_calls
            ]

        reasoning = getattr(msg_obj, "reasoning_content", None)
        if reasoning:
            result["reasoning_content"] = reasoning

        # Логируем reasoning_content
        if reasoning:
            logger.info("[AGENT][REASONING]\n%s", reasoning)
        else:
            logger.warning("[AGENT] reasoning_content is empty")

        self._last_final_message = result
        yield AgentEvent("final", result)

    async def _fallback_stream(
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
            final_parts.append(fallback_msg)
            yield fallback_msg

        turn_messages.append({"role": "assistant", "content": "".join(final_parts)})
        self._remember_turn(session_id, turn_messages)

    async def _stream_final(self, messages: list[dict[str, Any]]) -> AsyncIterator[str]:
        extra_params: dict[str, Any] = {}
        if self.enable_thinking:
            extra_params["extra_body"] = {"think": True}
        if self.api_base:
            extra_params["api_base"] = self.api_base

        response = await litellm.acompletion(
            model=self.model,
            messages=messages,
            stream=True,
            timeout=self.timeout,
            **extra_params,
        )
        # Проверка на корректный тип данных от LiteLLM
        if not isinstance(response, CustomStreamWrapper):
            logger.error(
                "Expected CustomStreamWrapper, got %s",
                type(response).__name__,
            )
            raise TypeError(
                f"Expected CustomStreamWrapper, got {type(response).__name__}"
            )

        async for chunk in response:
            token = chunk.choices[0].delta.content
            if not isinstance(token, str):
                continue
            if token:
                yield token

    @staticmethod
    def _extract_tool_calls(message: dict[str, Any]) -> list[dict[str, Any]]:
        calls: list[dict[str, Any]] = []

        native_calls = message.get("tool_calls") or []
        for item in native_calls:
            function = item.get("function") or {}
            name = function.get("name")
            if not name:
                continue

            calls.append(
                {
                    "id": item.get("id") or f"call_{name}_{uuid.uuid4().hex[:8]}",
                    "name": name,
                    "arguments": LLMAgent._parse_tool_arguments(function.get("arguments", {})),
                }
            )

        if calls:
            return calls

        text_content = message.get("content") or ""
        if not text_content:
            return []

        potential_jsons: list[str] = []

        md_matches = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text_content, re.DOTALL)
        potential_jsons.extend(md_matches)

        if not potential_jsons:
            start_idx = text_content.find("{")
            end_idx = text_content.rfind("}")
            if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
                potential_jsons.append(text_content[start_idx : end_idx + 1])

        for json_str in potential_jsons:
            data = LLMAgent._parse_tool_arguments(json_str)
            if not data:
                continue

            extracted_items: list[dict[str, Any]] = []
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

                calls.append(
                    {
                        "id": item.get("id") or f"call_{name}_{uuid.uuid4().hex[:8]}",
                        "name": name,
                        "arguments": LLMAgent._parse_tool_arguments(args),
                    }
                )

        return calls

    @staticmethod
    def _parse_tool_arguments(raw_args: Any) -> dict[str, Any]:
        if isinstance(raw_args, dict):
            return raw_args
        if not isinstance(raw_args, str):
            return {}

        text = raw_args.strip()
        if not text:
            return {}

        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            try:
                parsed, end = json.JSONDecoder().raw_decode(text)
            except json.JSONDecodeError:
                return {}
            if text[end:].strip():
                logger.warning("[AGENT] Ignored extra data after tool arguments JSON: %r", text[end:].strip())

        return parsed if isinstance(parsed, dict) else {}

    @staticmethod
    def _format_tool_calls_for_model(tool_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
        formatted: list[dict[str, Any]] = []
        for tool_call in tool_calls:
            name = tool_call.get("name")
            if not name:
                continue
            formatted.append(
                {
                    "id": tool_call.get("id") or f"call_{name}_{uuid.uuid4().hex[:8]}",
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": json.dumps(tool_call.get("arguments") or {}, ensure_ascii=False),
                    },
                }
            )
        return formatted

    def _remember_turn(self, session_id: str, turn_messages: list[dict[str, Any]]) -> None:
        session_store.append_turn(session_id, turn_messages)
        logger.debug("[AGENT] Stored turn for session %s", session_id)

    def _history_messages(self, session_id: str) -> list[dict[str, Any]]:
        return session_store.history_messages(session_id)

    @staticmethod
    def _format_sse_event(event: AgentEvent) -> str:
        payload = json.dumps(event.data, ensure_ascii=False)
        return f"event: {event.type}\ndata: {payload}\n\n"

    @staticmethod
    def _unstreamed_suffix(streamed_text: str, final_text: str) -> str:
        if not streamed_text:
            return final_text
        if final_text.startswith(streamed_text):
            return final_text[len(streamed_text) :]
        return ""

    async def health(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "model": self.model,
            "api_base": self.api_base,
            "thinking_enabled": self.enable_thinking,
        }


agent = LLMAgent()
