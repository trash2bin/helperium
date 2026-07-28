"""Parse backlog records into typed TurnResult dataclasses."""

from pathlib import Path
from typing import Any

from .models import ToolCallEvent, TurnResult
from .reader import read_backlog_file


def _is_same_tool(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Check if two tool-call records refer to the same tool with the same arguments."""
    name_a = a.get("name") or (a.get("data") or {}).get("name", "")
    name_b = b.get("name") or (b.get("data") or {}).get("name", "")
    if name_a != name_b:
        return False
    args_a = a.get("arguments") or (a.get("data") or {}).get("arguments", {})
    args_b = b.get("arguments") or (b.get("data") or {}).get("arguments", {})
    return _canonical_args(args_a) == _canonical_args(args_b)


def _canonical_args(args: Any) -> tuple:
    """Normalize arguments for comparison — sort keys, ignore None values."""
    if not isinstance(args, dict):
        return (str(args),)
    return tuple(sorted((k, v) for k, v in args.items() if v is not None))


def _extract_tool_name(record: dict[str, Any]) -> str:
    """Extract tool name from either backlog (event: tool_call, data.name) or SSE format."""
    if "data" in record and isinstance(record["data"], dict):
        name = record["data"].get("name", "")
        if name:
            return name
    return record.get("name", "")


def _extract_tool_args(record: dict[str, Any]) -> dict[str, Any]:
    """Extract tool arguments from either backlog or SSE format."""
    if "data" in record and isinstance(record["data"], dict):
        args = record["data"].get("arguments")
        if args is not None:
            return args if isinstance(args, dict) else {}
    return record.get("arguments", {})


def _detect_tool_loops(tool_events: list[dict[str, Any]]) -> list[str]:
    """Detect repeated tool calls (same name + args 3+ times in a row).

    Returns a list of warning strings.
    """
    warnings: list[str] = []
    if len(tool_events) < 3:
        return warnings

    # Use sequence window to find 3+ consecutive identical calls
    i = 0
    while i <= len(tool_events) - 3:
        if _is_same_tool(tool_events[i], tool_events[i + 1]) and _is_same_tool(
            tool_events[i], tool_events[i + 2]
        ):
            name = _extract_tool_name(tool_events[i])
            args = _extract_tool_args(tool_events[i])
            # Count how far the streak goes
            streak = 3
            while i + streak < len(tool_events) and _is_same_tool(
                tool_events[i], tool_events[i + streak]
            ):
                streak += 1
            warnings.append(
                f"Loop detected: tool '{name}' called {streak}x with same args "
                f"({_canonical_args(args)})"
            )
            i += streak
        else:
            i += 1

    return warnings


def parse_turns(files: list[Path]) -> list[TurnResult]:
    """Parse backlog files into ``TurnResult`` list.

    Groups records by ``(session_id, turn_id)``.
    Detects tool call loops (same tool + args in 3+ sequential calls).

    Handles both backlog format (``event: "tool_call"``, ``event: "tool_result"``
    with ``data: {name, arguments}``) and SSE format (``type: "tool_call"`` with
    top-level ``name`` / ``arguments``).
    """
    # Read all records grouped by (session_id, turn_id)
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    seen_turns: dict[tuple[str, str], dict[str, Any]] = {}

    for path in files:
        records = read_backlog_file(path)
        for rec in records:
            sid = rec.get("session_id", "")
            tid = rec.get("turn_id", "")

            if not sid or not tid:
                continue

            key = (sid, tid)
            groups.setdefault(key, []).append(rec)

            if rec.get("type") == "turn_end":
                seen_turns[key] = rec

    results: list[TurnResult] = []

    for (sid, tid), end_rec in seen_turns.items():
        events = groups.get((sid, tid), [])

        # Collect tool-call events in order (by iteration)
        tool_events: list[dict[str, Any]] = [
            e
            for e in sorted(events, key=lambda x: x.get("iteration", 0))
            if e.get("event") == "tool_call" or e.get("type") == "tool_call"
        ]

        # Build ToolCallEvent list
        tool_evts: list[ToolCallEvent] = []
        for te in tool_events:
            name = _extract_tool_name(te)
            args = _extract_tool_args(te)

            # Find matching tool_result (by name, same iteration)
            it = te.get("iteration", 0)
            result_text: str | None = None
            result_chars = 0
            dur_ms = 0.0
            for ev in events:
                if (
                    ev.get("event") == "tool_result"
                    and ev.get("iteration") == it
                    and (
                        _extract_tool_name(ev) == name
                        or (ev.get("data") or {}).get("name") == name
                    )
                ):
                    result_data = ev.get("data", {}) if isinstance(ev.get("data"), dict) else {}
                    raw = result_data.get("result", "")
                    result_text = str(raw) if raw is not None else None
                    result_chars = result_data.get("result_chars", len(str(raw or "")))
                    dur_ms = ev.get("duration_ms", 0.0) or 0.0
                    break

            tool_evts.append(
                ToolCallEvent(
                    name=name,
                    arguments=args,
                    result=result_text,
                    result_chars=result_chars,
                    duration_ms=dur_ms,
                )
            )

        # Detect loops
        loop_warnings = _detect_tool_loops(tool_events)

        # Collect errors
        errors: list[str] = []
        for ev in events:
            if ev.get("event") == "error":
                err_data = ev.get("data", {}) if isinstance(ev.get("data"), dict) else {}
                err_text = err_data.get("error", str(ev))
                if err_text:
                    errors.append(err_text)

        turn_res = TurnResult(
            session_id=sid,
            turn_id=tid,
            question=_extract_question(events),
            outcome=end_rec.get("outcome", "unknown"),
            duration_ms=end_rec.get("duration_ms", 0.0) or 0.0,
            total_prompt_tokens=end_rec.get("total_prompt_tokens", 0) or 0,
            total_completion_tokens=end_rec.get("total_completion_tokens", 0) or 0,
            total_tokens=end_rec.get("total_tokens", 0) or 0,
            total_cost=end_rec.get("total_cost", 0.0) or 0.0,
            llm_calls=end_rec.get("llm_calls", 0) or 0,
            tool_calls=tool_evts,
            tool_errors=end_rec.get("tool_errors", 0) or 0,
            empty_results=end_rec.get("empty_results", 0) or 0,
            empty_rounds=end_rec.get("empty_rounds", 0) or 0,
            iterations=end_rec.get("iterations", 0) or 0,
            final_text="",
            errors=errors,
            loop_warnings=loop_warnings,
        )
        results.append(turn_res)

    return results


def _extract_question(events: list[dict[str, Any]]) -> str:
    """Try to extract the user question from turn_start event."""
    for ev in events:
        if ev.get("event") == "turn_start":
            data = ev.get("data", {})
            if isinstance(data, dict):
                return data.get("user_message", "")
    return ""
