"""Runner — send questions to the API and collect SSE responses."""

from __future__ import annotations

import json
import time
import uuid
from typing import Any


def _sse_parse_events(response: Any) -> dict[str, Any]:
    """Parse an SSE stream into structured result dict.

    Handles ``data: {...}`` lines (api-service format: type is inside the JSON payload,
    not an SSE event type).  Returns keys: ``events``, ``tool_calls``, ``tool_results``,
    ``final_text``, ``errors``, ``status_messages``, ``duration_ms``.
    """
    result: dict[str, Any] = {
        "events": [],
        "tool_calls": [],
        "tool_results": [],
        "final_text": "",
        "errors": [],
        "status_messages": [],
        "duration_ms": 0.0,
    }

    t_start = time.monotonic()

    try:
        for line_bytes in response.iter_lines():
            if not line_bytes:
                continue
            line = line_bytes.decode("utf-8", errors="replace") if isinstance(line_bytes, bytes) else line_bytes
            if not line.startswith("data: "):
                continue
            try:
                payload = json.loads(line[6:])
            except json.JSONDecodeError:
                continue

            result["events"].append(payload)
            ev_type = payload.get("type", "")

            if ev_type == "status":
                result["status_messages"].append(
                    payload.get("message") or payload.get("phase", "")
                )
            elif ev_type == "tool_call":
                result["tool_calls"].append(payload)
            elif ev_type == "tool_result":
                result["tool_results"].append(payload)
            elif ev_type == "token":
                result["final_text"] += payload.get("text", "")
            elif ev_type == "error":
                result["errors"].append(payload.get("text", str(payload)))
            elif ev_type == "final":
                result["final_text"] += payload.get("text", "")
            elif ev_type == "done":
                break
    except (OSError, TimeoutError):
        if not result["events"]:
            result["errors"].append("SSE stream ended unexpectedly")

    result["duration_ms"] = round((time.monotonic() - t_start) * 1000, 1)
    return result


def run_bench(
    api_url: str,
    agent_name: str,
    questions: list[str],
    tenant_id: str = "default",
    admin_token: str = "",
    backlog_dir: str | None = None,
) -> list[dict[str, Any]]:
    """Run benchmark questions against the api-service and return raw results.

    Uses ``POST /api/chat/{agent_name}`` with SSE streaming.
    Each result dict contains ``events``, ``tool_calls``, ``tool_results``,
    ``final_text``, ``errors``, and ``duration_ms``.

    Args:
        api_url: Base URL of the api-service (e.g. ``http://127.0.0.1:8081``).
        agent_name: Name of the agent to query.
        questions: List of question strings.
        tenant_id: Tenant ID passed as ``X-Tenant-ID`` header.
        admin_token: Bearer token for Authorization header.
        backlog_dir: Optional path to backlog dir (not used here, returned for caller).

    Returns:
        List of result dicts, one per question.
    """
    if not questions:
        return []

    # Build headers
    headers: dict[str, str] = {
        "X-Tenant-ID": tenant_id,
        "Content-Type": "application/json",
        "User-Agent": "BenchRunner/1.0",
    }
    if admin_token:
        headers["Authorization"] = f"Bearer {admin_token}"

    results: list[dict[str, Any]] = []

    # Try httpx first (cleaner API), fall back to requests
    try:
        import httpx as _httpx_module

        _run_httpx(results, _httpx_module, api_url, agent_name, questions, headers)
    except ImportError:
        import requests as _requests_module

        _run_requests(results, _requests_module, api_url, agent_name, questions, headers)

    return results


def _run_httpx(
    results: list[dict[str, Any]],
    httpx_module: Any,
    api_url: str,
    agent_name: str,
    questions: list[str],
    headers: dict[str, str],
) -> None:
    """Run questions using httpx (streaming)."""
    with httpx_module.Client(timeout=httpx_module.Timeout(120.0)) as client:
        for question in questions:
            session_id = f"bench-{uuid.uuid4().hex[:8]}"
            payload = {"message": question, "session_id": session_id}

            try:
                with client.stream(
                    "POST",
                    f"{api_url}/api/chat/{agent_name}",
                    json=payload,
                    headers=headers,
                ) as resp:
                    if resp.status_code != 200:
                        results.append(
                            {
                                "error": f"HTTP {resp.status_code}",
                                "events": [],
                                "tool_calls": [],
                                "tool_results": [],
                                "final_text": "",
                                "errors": [f"HTTP {resp.status_code}: {resp.text[:200]}"],
                                "status_messages": [],
                                "duration_ms": 0.0,
                                "question": question,
                                "session_id": session_id,
                            }
                        )
                        continue

                    result = _sse_parse_events(resp)
                    result["question"] = question
                    result["session_id"] = session_id
                    results.append(result)

            except (httpx_module.RequestError, OSError) as e:
                results.append(
                    {
                        "error": str(e),
                        "events": [],
                        "tool_calls": [],
                        "tool_results": [],
                        "final_text": "",
                        "errors": [f"Request failed: {e}"],
                        "status_messages": [],
                        "duration_ms": 0.0,
                        "question": question,
                        "session_id": session_id,
                    }
                )


def _run_requests(
    results: list[dict[str, Any]],
    requests_module: Any,
    api_url: str,
    agent_name: str,
    questions: list[str],
    headers: dict[str, str],
) -> None:
    """Run questions using requests (streaming fallback)."""
    for question in questions:
        session_id = f"bench-{uuid.uuid4().hex[:8]}"
        payload = {"message": question, "session_id": session_id}

        try:
            resp = requests_module.post(
                f"{api_url}/api/chat/{agent_name}",
                json=payload,
                headers=headers,
                timeout=120,
                stream=True,
            )
        except requests_module.RequestException as e:
            results.append(
                {
                    "error": str(e),
                    "events": [],
                    "tool_calls": [],
                    "tool_results": [],
                    "final_text": "",
                    "errors": [f"Request failed: {e}"],
                    "status_messages": [],
                    "duration_ms": 0.0,
                    "question": question,
                    "session_id": session_id,
                }
            )
            continue

        if resp.status_code != 200:
            results.append(
                {
                    "error": f"HTTP {resp.status_code}",
                    "events": [],
                    "tool_calls": [],
                    "tool_results": [],
                    "final_text": "",
                    "errors": [f"HTTP {resp.status_code}: {resp.text[:200]}"],
                    "status_messages": [],
                    "duration_ms": 0.0,
                    "question": question,
                    "session_id": session_id,
                }
            )
            continue

        result = _sse_parse_events(resp)
        result["question"] = question
        result["session_id"] = session_id
        results.append(result)
