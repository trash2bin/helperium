from __future__ import annotations

import json
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


SYSTEM_PROMPT = """Ты университетский ассистент.
Отвечай по-русски, кратко и по делу. Для данных о студентах, расписании,
оценках, преподавателях и документах используй доступные MCP-инструменты.

Правила работы с документами:
- Если пользователь спрашивает про доступные материалы, сначала найди студента
  и его дисциплины, затем покажи материалы только по этим дисциплинам.
- В списке материалов не пропускай PDF: документы с mime_type application/pdf
  называй "Лекция (PDF)".
- Если пользователь просит пересказать, найти или объяснить что-то внутри
  документа, используй context_search_in_documents или search_documents, а не
  только list_documents.
- Не придумывай содержимое документов. Если найденных фрагментов мало, скажи
  что данных недостаточно.

Если данных не хватает, прямо скажи об этом и предложи уточнить запрос."""


@asynccontextmanager
async def mcp_session() -> AsyncIterator[ClientSession]:
    params = StdioServerParameters(command=sys.executable, args=["server.py"])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


async def list_ollama_tools(session: ClientSession) -> list[dict[str, Any]]:
    result = await session.list_tools()
    tools = []
    for tool in result.tools:
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description or "",
                    "parameters": tool.inputSchema,
                },
            }
        )
    return tools


async def call_tool(session: ClientSession, name: str, arguments: dict[str, Any]) -> str:
    result = await session.call_tool(name, arguments)
    if result.isError:
        return json.dumps({"error": collect_text_content(result)}, ensure_ascii=False)

    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        return json.dumps(structured, ensure_ascii=False)

    return collect_text_content(result)


def collect_text_content(result: Any) -> str:
    parts = []
    for item in getattr(result, "content", []) or []:
        text = getattr(item, "text", None)
        if text:
            parts.append(text)
    return "\n".join(parts)
