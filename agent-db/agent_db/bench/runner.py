"""Runner — send questions to the API and collect SSE responses."""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from .backlog_parser import find_backlog_file, parse_backlog_data, read_all_records
from .models import BacklogData, RunResult


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


class BenchmarkRunner:
    """Run benchmark cases against the api-service, collecting SSE + backlog.

    Usage:
        runner = BenchmarkRunner(api_url, agent_name, tenant_id)
        run = runner.run_case("Сколько стоит артикул X?")
        run.final_text, run.tool_calls, run.backlog
    """

    def __init__(
        self,
        api_url: str,
        agent_name: str,
        tenant_id: str,
        admin_token: str = "",
        backlog_dir: str | Path | None = None,
        timeout: float = 120.0,
        bench_log_dir: str | Path | None = None,
    ) -> None:
        self.api_url = api_url.rstrip("/")
        self.agent_name = agent_name
        self.tenant_id = tenant_id
        self.admin_token = admin_token
        self.timeout = timeout
        # Default backlog dir: reader.find_backlog_dir() walks project root
        if backlog_dir is None:
            from .reader import find_backlog_dir

            backlog_dir = find_backlog_dir()
        self.backlog_dir = Path(backlog_dir)
        # Отдельное логгирование бенча: свои файлы, изолированно от общего backlog.
        # По умолчанию — "bench-backlog/" в cwd; можно переопределить.
        if bench_log_dir is None:
            bench_log_dir = Path.cwd() / "bench-backlog"
        self.bench_log_dir = Path(bench_log_dir)
        self.bench_log_dir.mkdir(parents=True, exist_ok=True)

    def _write_bench_log(self, run: RunResult) -> None:
        """Записать трассу одного прогона в отдельный bench-log (JSONL).

        Полный trace: вопрос, SSE-события, tool_calls/results, final_text,
        метрики из backlog — в ОТДЕЛЬНОМ каталоге (bench_log_dir), чтобы не
        смешивать с общим backlog api-service.
        """
        import json as _json

        # Копируем исходный backlog-файл сессии (полный, как записал api-service)
        src = find_backlog_file(self.backlog_dir, run.session_id)
        if src is not None:
            dst = self.bench_log_dir / src.name
            try:
                dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
            except OSError:
                pass

        # Свой bench-файл: одна строка = один прогон (все данные)
        record = {
            "session_id": run.session_id,
            "question": run.question,
            "final_text": run.final_text,
            "events": run.events,
            "tool_calls": run.tool_calls,
            "tool_results": run.tool_results,
            "errors": run.errors,
            "backlog": {
                "outcome": run.backlog.outcome if run.backlog else None,
                "duration_ms": run.backlog.duration_ms if run.backlog else None,
                "total_tokens": run.backlog.total_tokens if run.backlog else None,
                "total_cost": run.backlog.total_cost if run.backlog else None,
                "llm_calls": run.backlog.llm_calls if run.backlog else None,
                "tool_calls": run.backlog.tool_calls_count if run.backlog else None,
                "tool_errors": run.backlog.tool_errors if run.backlog else None,
            },
        }
        path = self.bench_log_dir / f"{run.session_id}.bench.jsonl"
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(_json.dumps(record, ensure_ascii=False, default=str) + "\n")
        except OSError:
            pass

    def _headers(self) -> dict[str, str]:
        h = {
            "X-Tenant-ID": self.tenant_id,
            "Content-Type": "application/json",
            "User-Agent": "BenchRunner/1.0",
            "Accept": "text/event-stream",
        }
        if self.admin_token:
            h["Authorization"] = f"Bearer {self.admin_token}"
        return h

    def run_case(self, question: str, session_id: str | None = None) -> RunResult:
        """Run one question against the agent and return a RunResult.

        Sends ``POST /api/chat/{agent_name}`` with SSE streaming, parses
        the stream (via :func:`_sse_parse_events`), then reads the backlog
        ``turn_end`` record for the session.

        Args:
            question: The question text.
            session_id: Optional client session id; defaults to ``bench-<hex>``.

        Returns:
            RunResult with SSE events, tool calls/results, final_text and
            backlog metrics (``backlog`` is None if no turn_end found).
        """
        sid = session_id or f"bench-{uuid.uuid4().hex[:8]}"
        url = f"{self.api_url}/api/chat/{self.agent_name}"
        payload = {"message": question, "session_id": sid}
        headers = self._headers()

        # Retry up to 3 times on 429 (rate limit)
        last_parsed: dict[str, Any] | None = None
        for attempt in range(3):
            try:
                import httpx as httpx_module

                with httpx_module.Client(
                    timeout=httpx_module.Timeout(self.timeout)
                ) as client:
                    with client.stream("POST", url, json=payload, headers=headers) as resp:
                        if resp.status_code == 429:
                            time.sleep(2 * (attempt + 1))
                            continue
                        if resp.status_code != 200:
                            return RunResult(
                                session_id=sid,
                                question=question,
                                errors=[f"HTTP {resp.status_code}: {resp.text[:200]}"],
                            )
                        last_parsed = _sse_parse_events(resp)
                        break
            except ImportError:
                # Fallback to requests (streaming)
                import requests as requests_module

                resp = requests_module.post(
                    url,
                    json=payload,
                    headers=headers,
                    timeout=self.timeout,
                    stream=True,
                )
                if resp.status_code == 429:
                    time.sleep(2 * (attempt + 1))
                    continue
                if resp.status_code != 200:
                    return RunResult(
                        session_id=sid,
                        question=question,
                        errors=[f"HTTP {resp.status_code}: {resp.text[:200]}"],
                    )
                last_parsed = _sse_parse_events(resp)
                break
            except Exception as e:  # httpx.RequestError, OSError, ...
                return RunResult(
                    session_id=sid,
                    question=question,
                    errors=[f"Request failed: {e}"],
                )

        if last_parsed is None:
            return RunResult(
                session_id=sid,
                question=question,
                errors=["Rate-limited (429) after retries"],
            )
        parsed = last_parsed

        # Read backlog metrics for this session
        backlog_data = parse_backlog_data(self.backlog_dir, sid)

        run = RunResult(
            session_id=sid,
            question=question,
            final_text=parsed.get("final_text", ""),
            events=parsed.get("events", []),
            tool_calls=parsed.get("tool_calls", []),
            tool_results=parsed.get("tool_results", []),
            errors=parsed.get("errors", []),
            status_messages=parsed.get("status_messages", []),
            backlog=backlog_data,
        )
        # Отдельное логгирование бенча (изолированный каталог)
        self._write_bench_log(run)
        return run

    def run_cases(self, questions: list[str]) -> list[RunResult]:
        """Run multiple questions (one-shot each)."""
        return [self.run_case(q) for q in questions]
