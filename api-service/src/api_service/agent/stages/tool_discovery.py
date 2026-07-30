"""Stage 2 — Tool Discovery.

Открыть MCP session, получить список инструментов и схему БД.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

from ..pipeline import PipelineContext
from ..types import AgentEvent

logger = logging.getLogger("api_service.agent.stages")


class ToolDiscoveryStage:
    """Открыть MCP session, получить список инструментов и схему БД.

    Выполняется один раз (gate через _done_flags).
    Результат сохраняется в ctx.turn.tools.
    """

    def __init__(self) -> None:
        self._schema_cache: dict[str, str] = {}

    async def run(self, ctx: PipelineContext) -> AsyncIterator[AgentEvent]:
        if False:
            yield  # pragma: no cover — make Python treat this as async generator

        if ctx._stage_ran("tool_discovery"):
            return
        ctx._mark_done("tool_discovery")

        # 1. List tools
        tools = await ctx.mcp_session.list_tools()
        ctx.turn.tools = tools
        logger.info(
            "[TOOL_DISCOVERY] Available tools: %s",
            [t.get("function", {}).get("name") for t in tools],
        )

        # 2. Get schema (LLM-friendly)
        try:
            schema = await ctx.mcp_session.get_schema()
            if schema and schema.get("entities"):
                cache_key = "-".join(ctx.turn.tenant_ids or ["default"])
                if cache_key not in self._schema_cache:
                    self._schema_cache[cache_key] = _build_schema_message(schema, tools)
                schema_note = self._schema_cache[cache_key]
                ctx.turn.messages.append({"role": "system", "content": schema_note})
                logger.info(
                    "[TOOL_DISCOVERY] Injected schema with %d entities and %d hints (%d chars)",
                    len(schema["entities"]),
                    len(schema.get("workflow_hints", [])),
                    len(schema_note),
                )
        except Exception:
            logger.warning("[TOOL_DISCOVERY] Failed to get schema", exc_info=True)

        return


def _entity_tool_name(ent_name: str) -> str:
    """Extract the short tool-prefix from an entity display name.

    'Auto_parts (auto_parts)' -> 'auto_parts'
    'Brands (brands)' -> 'brands'
    """
    if "(" in ent_name and ent_name.endswith(")"):
        return ent_name[ent_name.index("(") + 1 : -1].strip()
    return ent_name.lower().replace(" ", "_")


def _build_schema_message(schema: dict, tools: list[dict] | None = None) -> str:
    """Build a system-prompt block from the LLM-friendly schema and tool list.

    Generates a compact but complete description per entity listing:
    - All tool names with required parameters
    - Search/filter fields with operators (__gt, __like, __in)
    - Foreign key relationships for navigation
    - Entity-level workflow hints

    Args:
        schema: LLM-friendly schema from /mcp/schema endpoint
        tools: OpenAI-format tool definitions from list_tools
    """
    # Index tools by entity prefix
    # Tool name format: "grep_auto_parts" -> prefix "auto_parts", kind "grep"
    tool_map: dict[str, dict[str, dict]] = {}
    if tools:
        for t in tools:
            func = t.get("function", {})
            fname = func.get("name", "")
            if "_" not in fname:
                continue
            kind, _, prefix = fname.partition("_")
            if prefix not in tool_map:
                tool_map[prefix] = {}
            tool_map[prefix][kind] = func

    lines = [
        "# Database Schema (auto-loaded for your session)",
        "",
        "Each entity below lists the tools you can use to query it and which fields are available.",
        "",
    ]

    for ent in schema.get("entities", []):
        ent_raw_name = ent.get("name", "?")
        prefix = _entity_tool_name(ent_raw_name)
        display_name = (
            ent_raw_name.split(" (")[0] if "(" in ent_raw_name else ent_raw_name
        )

        lines.append(f"## {display_name}")

        # ── Tools section ──
        entity_tools = tool_map.get(prefix, {})
        if entity_tools:
            tool_lines = []
            for kind in ("schema", "grep", "filter", "get", "count", "distinct"):
                func = entity_tools.get(kind)
                if not func:
                    continue
                params = func.get("parameters", {}).get("properties", {})
                required = func.get("parameters", {}).get("required", [])

                # Signature: required params only
                sig_parts = []
                for pname in required:
                    ptype = params.get(pname, {}).get("type", "str")
                    sig_parts.append(f"{pname}:{ptype}")

                signature = f"{func['name']}({', '.join(sig_parts)})"
                desc = func.get("description", "")[:120]
                if desc:
                    tool_lines.append(f"- `{signature}` — {desc}")
                else:
                    tool_lines.append(f"- `{signature}`")

            if tool_lines:
                lines.append("")
                lines.append("   Tools:")
                lines.extend(tool_lines)

        # ── Search field ──
        sf = ent.get("search_fields", "")
        if sf and entity_tools.get("grep"):
            lines.append("")
            lines.append(
                f"   Text search via `grep_{prefix}(pattern=...)` — searches across `{sf}` and other text fields."
            )
            lines.append(
                "   Supports: multi-word AND, regex (`regex=true`), case-insensitive, invert (`invert=true`)."
            )

        # ── Filter fields ──
        filter_fields = []
        for fg in ent.get("filter_fields", []):
            for f in fg.get("fields", []):
                filter_fields.append(f)

        if filter_fields and entity_tools.get("filter"):
            lines.append("")
            lines.append(f"   Field filters via `filter_{prefix}(...)`:")
            for f in filter_fields:
                col = f.get("column", f.get("name", "?"))
                ftype = f.get("type", "str")
                fdesc = f.get("description", "")

                # Build operator hints based on type
                if ftype in ("int", "float"):
                    ops = ", ".join(
                        [
                            f"`{col}__gt`",
                            f"`{col}__gte`",
                            f"`{col}__lt`",
                            f"`{col}__lte`",
                        ]
                    )
                    extra = f"[range: {ops}]"
                elif ftype == "string":
                    ops = ", ".join(
                        [f"`{col}` (exact)", f"`{col}__like`", f"`{col}__in`"]
                    )
                    extra = f"[{ops}]"
                else:
                    extra = f"[type: {ftype}]"

                label = fdesc if fdesc else col
                if f.get("is_fk"):
                    fk_entity = f.get("fk_entity", "?")
                    lines.append(f"    - {label} {extra} — FK → {fk_entity}")
                else:
                    lines.append(f"    - {label} {extra}")

        # ── Relations ──
        rels = ent.get("relations", [])
        if rels:
            lines.append("")
            lines.append("   Relations:")
            for rel in rels:
                field = rel.get("field", "?")
                ref = rel.get("referenced_entity", "?")
                ref_prefix = (
                    _entity_tool_name(ref)
                    if "(" not in ref
                    else _entity_tool_name(f"name ({ref.lower().replace(' ', '_')})")
                )
                lines.append(
                    f"    - `{field}` → **{ref}** (use `grep_{ref_prefix}()` or `get_{ref_prefix}()`)"
                )

        # ── Entity-specific workflow hint ──
        if entity_tools.get("schema"):
            lines.append("")
            lines.append(
                f"   🎯 Best practice: call `{prefix}_schema()` first to discover distinct values, then use `grep_{prefix}()` or `filter_{prefix}()`."
            )

        lines.append("")

    # ── Global workflow hints ──
    hints = schema.get("workflow_hints", [])
    if hints:
        lines.append("---")
        lines.append("## Workflow Guidelines")
        for h in hints:
            lines.append(f"- {h}")

    return "\n".join(lines)
