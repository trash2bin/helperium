"""Tool call parsing utilities for LLM responses."""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from .types import ParsedToolCall

logger = logging.getLogger("api_service.agent.tool_parser")


class ToolCallParser:
    """Parses tool calls from LLM responses.

    Priority:
    1. Native ``tool_calls`` field (OpenAI-style) — used by `_AsyncBacklogWriter` in orchestrator.py.
       In the new pipeline (LAYER 1), LiteLLM handles this before ToolCallParser
       is reached, but the legacy adapter path still needs it.
    2. JSON in ``content`` — fallback for models that write tool calls as text.
       Supports: NDJSON (line-delimited), JSON array, single dict, OpenAI wrapper.
    """

    def extract_tool_calls(self, message: dict[str, Any]) -> list[ParsedToolCall]:
        """Extract tool calls from a message, handling native and JSON formats."""
        # 1. Native tool_calls (OpenAI-style) — used by _AsyncBacklogWriter in orchestrator.py
        native_calls = message.get("tool_calls")
        if native_calls:
            return self._from_native_tool_calls(native_calls)

        # 2. JSON in content (fallback for models that write tool calls as text)
        text_content = message.get("content") or ""
        if not text_content:
            return []

        return self._extract_json_tool_calls(text_content)

    @staticmethod
    def _from_native_tool_calls(native_calls: list[dict]) -> list[ParsedToolCall]:
        """Convert native OpenAI-style tool_calls to ParsedToolCall list."""
        calls: list[ParsedToolCall] = []
        for item in native_calls:
            function = item.get("function") or {}
            name = function.get("name")
            if not name:
                continue
            raw_args = function.get("arguments", "{}")
            if isinstance(raw_args, str):
                try:
                    parsed_args = json.loads(raw_args)
                except (json.JSONDecodeError, TypeError):
                    parsed_args = {}
            else:
                parsed_args = raw_args
            calls.append(
                ParsedToolCall(
                    id=item.get("id") or f"call_{name}_{uuid.uuid4().hex[:8]}",
                    name=name,
                    arguments=parsed_args,
                )
            )
        return calls

    def _extract_json_tool_calls(self, text_content: str) -> list[ParsedToolCall]:
        """Extract tool calls from JSON blocks in text content."""
        calls: list[ParsedToolCall] = []

        potential_jsons: list[str] = []

        # Try NDJSON (line-delimited JSON) — каждая строка это отдельный тул
        # Некоторые модели (MiniMax) пишут тулы построчно без массива:
        #   {"name": "get_product", "arguments": {"id": 1}}
        #   {"name": "get_product", "arguments": {"id": 2}}
        ndjson_candidates: list[dict] = []
        for line in text_content.split("\n"):
            line = line.strip()
            if not line:
                continue
            if line.startswith("{") and line.endswith("}"):
                try:
                    obj = json.loads(line)
                    if isinstance(obj, dict) and (
                        "name" in obj or "tool_name" in obj or "function" in obj
                    ):
                        ndjson_candidates.append(obj)
                except (json.JSONDecodeError, TypeError):
                    pass
        if len(ndjson_candidates) >= 1:
            # Convert NDJSON items to ParsedToolCall list
            for item in ndjson_candidates:
                name = (
                    item.get("tool_name")
                    or item.get("name")
                    or (item.get("function") or {}).get("name")
                )
                if not name:
                    continue
                args = item.get("arguments") or item.get("args") or {}
                calls.append(
                    ParsedToolCall(
                        id=item.get("id") or f"call_{name}_{uuid.uuid4().hex[:8]}",
                        name=name,
                        arguments=self.parse_tool_arguments(args),
                    )
                )
            if calls:
                logger.info(
                    "[TOOL_PARSER] Extracted %d tool calls from NDJSON (line-delimited JSON)",
                    len(calls),
                )
                return calls

        # Try plain JSON (order matters: array first, then dict)
        if not potential_jsons:
            start_list_idx = text_content.find("[")
            end_list_idx = text_content.rfind("]")
            if (
                start_list_idx != -1
                and end_list_idx != -1
                and end_list_idx > start_list_idx
            ):
                array_str = text_content[start_list_idx : end_list_idx + 1]
                try:
                    parsed_arr = json.loads(array_str)
                    if isinstance(parsed_arr, list) or isinstance(parsed_arr, dict):
                        potential_jsons.append(array_str)
                except json.JSONDecodeError:
                    # Try single dict
                    start_idx = text_content.find("{")
                    end_idx = text_content.rfind("}")
                    if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
                        potential_jsons.append(text_content[start_idx : end_idx + 1])
            else:
                start_idx = text_content.find("{")
                end_idx = text_content.rfind("}")
                if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
                    potential_jsons.append(text_content[start_idx : end_idx + 1])

        for json_str in potential_jsons:
            # Try direct JSON parse FIRST (before parse_tool_arguments which
            # returns {} for arrays). Arrays = multiple tool calls.
            try:
                parsed_direct = json.loads(json_str)
            except (json.JSONDecodeError, TypeError):
                parsed_direct = None

            if isinstance(parsed_direct, list):
                extracted_items = parsed_direct
            else:
                data = self.parse_tool_arguments(json_str)
                if not data:
                    continue
                if isinstance(data, list):
                    extracted_items = data
                elif "tool_calls" in data and isinstance(data["tool_calls"], list):
                    extracted_items = data["tool_calls"]
                elif "params" in data and isinstance(data["params"], dict):
                    extracted_items = [data["params"]]
                elif "tool_name" in data or "name" in data or "function" in data:
                    extracted_items = [data]
                else:
                    continue

            for item in extracted_items:
                if not isinstance(item, dict):
                    continue
                name: str | None = (
                    item.get("tool_name")
                    or item.get("name")
                    or item.get("tool")
                    or (item.get("function") or {}).get("name")
                )
                if not name:
                    continue

                # Arguments can be at top level OR inside function dict
                # (OpenAI format: {"function": {"name": "x", "arguments": "{}"}})
                args = (
                    item.get("arguments")
                    or item.get("args")
                    or (item.get("function") or {}).get("arguments")
                    or {}
                )
                if isinstance(args, str):
                    # If arguments is a string, it might be escaped JSON.
                    # Try direct parse first, then unescape and retry.
                    try:
                        parsed_args = json.loads(args)
                        if isinstance(parsed_args, dict):
                            args = parsed_args
                    except (json.JSONDecodeError, TypeError):
                        # Try unescaping: replace \" with " and \n with newline
                        unescaped = args.replace('\\"', '"').replace("\\n", "\n")
                        try:
                            parsed_args = json.loads(unescaped)
                            if isinstance(parsed_args, dict):
                                args = parsed_args
                        except (json.JSONDecodeError, TypeError):
                            pass

                calls.append(
                    ParsedToolCall(
                        id=item.get("id") or f"call_{name}_{uuid.uuid4().hex[:8]}",
                        name=name,
                        arguments=self.parse_tool_arguments(args),
                    )
                )

        return calls

    @staticmethod
    def parse_tool_arguments(raw_args: Any) -> dict[str, Any]:
        """Parse tool arguments from various formats into a dict."""
        if isinstance(raw_args, dict):
            return raw_args
        if not isinstance(raw_args, str):
            return {}

        text = raw_args.strip()
        if not text:
            return {}

        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            logger.debug("[TOOL_PARSER] Failed to parse tool arguments: %s", text[:100])
            return {}
