"""Tool call parsing utilities for LLM responses."""

from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any

from .types import ParsedToolCall, ToolCall

logger = logging.getLogger("api_service.agent.tool_parser")


class ToolCallParser:
    """Parses tool calls from LLM responses in various formats."""

    def extract_tool_calls(self, message: dict[str, Any]) -> list[ParsedToolCall]:
        """Extract tool calls from a message, handling native and JSON formats."""
        # Try native tool_calls format
        native_calls = self._extract_native_tool_calls(message)
        if native_calls:
            return native_calls

        # Try parsing JSON from content
        text_content = message.get("content") or ""
        if not text_content:
            return []

        return self._extract_json_tool_calls(text_content)

    def _extract_native_tool_calls(
        self, message: dict[str, Any]
    ) -> list[ParsedToolCall]:
        """Extract tool calls from native OpenAI-style tool_calls field."""
        calls: list[ParsedToolCall] = []
        native_calls = message.get("tool_calls") or []

        for item in native_calls:
            function = item.get("function") or {}
            name = function.get("name")
            if not name:
                continue

            calls.append(
                ParsedToolCall(
                    id=item.get("id") or f"call_{name}_{uuid.uuid4().hex[:8]}",
                    name=name or "",
                    arguments=self.parse_tool_arguments(function.get("arguments", {})),
                )
            )

        return calls

    def _extract_json_tool_calls(self, text_content: str) -> list[ParsedToolCall]:
        """Extract tool calls from JSON blocks or custom tags in text content."""
        calls: list[ParsedToolCall] = []

        # 1. Handle <tool_call> tags (e.g. <invoke name="foo">...)
        tag_matches = re.findall(
            r"<invoke\s+name=['\"]([^'\"]+)['\"]([^>]*)>", text_content
        )
        for name, extra in tag_matches:
            calls.append(
                ParsedToolCall(
                    id=f"call_{name}_{uuid.uuid4().hex[:8]}",
                    name=name or "",
                    arguments={},
                )
            )
        if calls:
            return calls

        potential_jsons: list[str] = []

        # Try markdown JSON blocks
        md_matches = re.findall(
            r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", text_content, re.DOTALL
        )
        potential_jsons.extend(md_matches)

        # Try plain JSON
        if not potential_jsons:
            start_idx = text_content.find("{")
            end_idx = text_content.rfind("}")
            if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
                potential_jsons.append(text_content[start_idx : end_idx + 1])

            start_list_idx = text_content.find("[")
            end_list_idx = text_content.rfind("]")
            if (
                start_list_idx != -1
                and end_list_idx != -1
                and end_list_idx > start_list_idx
            ):
                potential_jsons.append(text_content[start_list_idx : end_list_idx + 1])

        for json_str in potential_jsons:
            data = self.parse_tool_arguments(json_str)
            if not data:
                continue

            extracted_items: list[dict[str, Any]] = []
            if isinstance(data, list):
                extracted_items = data
            elif "tool_calls" in data and isinstance(data["tool_calls"], list):
                extracted_items = data["tool_calls"]
            elif "params" in data and isinstance(data["params"], dict):
                extracted_items = [data["params"]]
            elif "tool_name" in data or "name" in data or "function" in data:
                extracted_items = [data]

            for item in extracted_items:
                if not isinstance(item, dict):
                    continue
                name: str | None = (
                    item.get("tool_name") or item.get("name") or item.get("tool")
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

    def format_for_model(self, tool_calls: list[ParsedToolCall]) -> list[ToolCall]:
        """Format tool calls for LLM consumption."""
        import json as json_module

        formatted: list[ToolCall] = []
        for tool_call in tool_calls:
            name: str = tool_call["name"]
            if not name:
                continue
            formatted.append(
                ToolCall(
                    id=tool_call["id"] or f"call_{name}_{uuid.uuid4().hex[:8]}",
                    type="function",
                    function={
                        "name": name,
                        "arguments": json_module.dumps(
                            tool_call["arguments"] or {}, ensure_ascii=False
                        ),
                    },
                )
            )
        return formatted
