"""OpenAI-compatible stub LLM: the instrument for L2/L3 (doc/stress/README.md §4).

Why a stub at all. The provider's own latency is not ours to control, and §7
judges the *platform* overhead - the turn without the model's share. Measuring
that needs a model whose share is known exactly, so the harness runs a model
whose latency it chose, and subtracts the service time the stub reports.

The stub is part of the measuring instrument, not part of the system under test,
and §4 draws three consequences that this module implements literally:

- **It reports its own timings per request** (``arrival_ms``, ``service_start_ms``,
  ``service_ms``, ``queue_wait_ms``, ``prompt_tokens``), so
  ``platform_overhead = t_complete - Σ service_ms`` is exact. When the stub
  saturates, its own queue otherwise hides inside api-service's ``duration_ms``
  and the platform looks better than it is.
- **Latency is a function of the prompt**, not a constant. History is trimmed to
  ``DEMO_HISTORY_TURNS`` and capped at ``AGENT_MAX_TURN_TOKENS``, and a constant
  would make that trim invisible in the very metric it is supposed to move.
- **Tool calls are deterministic and scripted**, one round at a time. The scripted
  provider (§4) can only ever replay its first round because it resets a cursor,
  so a multi-round dialogue cannot be modelled with it.

Non-streaming on purpose: the project's real transport is non-streaming too
(``litellm_provider.complete`` never passes ``stream``), so a streaming stub would
measure a pipeline that does not exist.

Wire contract: ``POST /v1/chat/completions`` with the OpenAI body LiteLLM builds,
answering with ``chat.completion`` JSON - ``choices[0].message.content`` and/or
``choices[0].message.tool_calls[].function.{name,arguments}`` with ``arguments``
as a JSON **string**, which is what ``LiteLLMProvider._tool_call`` parses.
"""

from __future__ import annotations

import json
import logging
import random
import re
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping, Sequence

from .profile import canonical_tool_name, composite_tool_name

logger = logging.getLogger(__name__)

DEFAULT_DONE_TEXT = "Готово. Я всё проверил в базе данных."

# Rough, and deliberately so: the figure exists to make latency a function of the
# prompt, not to reproduce a tokenizer. It is also written to the log, so a
# reader can see what the stub believed the prompt was.
CHARS_PER_TOKEN = 4


@dataclass(frozen=True)
class LatencyModel:
    """Service time as a function of the prompt, expressed in the profile's terms.

    ``draw`` is a quantile in ``[0, 1]``: the sample is ``p50_ms`` up to
    ``1 - tail_probability`` and ``p95_ms`` above it. That way the profile's
    configured quantiles *are* the model, instead of a distribution being fitted
    to them, and a reader can check the stub against the profile by eye.

    ``prompt_ms_per_token`` is added on both branches. §4 demands it: with a
    constant the history trim and the 8000-token turn cap never show up in
    ``platform_overhead``, and the run silently measures a prompt length the
    platform never sends.
    """

    p50_ms: float
    p95_ms: float | None = None
    prompt_ms_per_token: float = 0.0
    tail_probability: float = 0.05

    def __post_init__(self) -> None:
        if self.p50_ms < 0:
            raise ValueError(f"p50_ms must not be negative, got {self.p50_ms}")
        if self.p95_ms is not None and self.p95_ms < self.p50_ms:
            raise ValueError(
                f"p95_ms ({self.p95_ms}) must not be below p50_ms ({self.p50_ms})"
            )
        if not 0.0 < self.tail_probability < 1.0:
            raise ValueError(
                f"tail_probability must be in (0, 1), got {self.tail_probability}"
            )
        if self.prompt_ms_per_token < 0:
            raise ValueError(
                f"prompt_ms_per_token must not be negative, got "
                f"{self.prompt_ms_per_token}"
            )

    @property
    def tail_ms(self) -> float:
        return self.p95_ms if self.p95_ms is not None else self.p50_ms

    @classmethod
    def from_profile(
        cls,
        *,
        p50_ms: float,
        p95_ms: float,
        prompt_ms_per_token: float = 0.0,
        tail_probability: float = 0.05,
    ) -> LatencyModel:
        """Build from the profile's ``llm`` block (§3) without re-deriving it."""
        return cls(
            p50_ms=p50_ms,
            p95_ms=p95_ms,
            prompt_ms_per_token=prompt_ms_per_token,
            tail_probability=tail_probability,
        )

    def sample(self, *, prompt_tokens: int, draw: float) -> float:
        """Service time in ms for one request."""
        if not 0.0 <= draw <= 1.0:
            raise ValueError(f"draw must be a quantile in [0, 1], got {draw}")
        base = self.tail_ms if draw >= 1.0 - self.tail_probability else self.p50_ms
        return base + self.prompt_ms_per_token * max(prompt_tokens, 0)


def estimate_tokens(text: str) -> int:
    """A rough token count for the prompt-length term and the usage block."""
    if not text:
        return 0
    return max(1, len(text) // CHARS_PER_TOKEN)


def extract_marker(messages: list[dict[str, Any]]) -> str | None:
    """The harness marker carried by the LAST user message (§4).

    api-service resends the whole stored history before the new user message,
    so old markers ride along in every round's request. The marker the join
    keys on is the current turn's - taken from the last user-role message, not
    from wherever it appears in the history. Real provider traffic carries no
    marker: ``None``, not a guess (§7).
    """
    for message in reversed(messages):
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = message.get("content")
        text = ""
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            text = " ".join(
                part.get("text", "")
                for part in content
                if isinstance(part, dict) and isinstance(part.get("text"), str)
            )
        match = re.search(r"\[([^\]]+)\]", text)
        if match and match.group(1).startswith("stress:"):
            return match.group(1)
        return None
    return None


@dataclass(frozen=True)
class ChatRequest:
    """The OpenAI-shaped request, reduced to what the stub must honour."""

    model: str
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]] = field(default_factory=list)
    requested_stream: bool = False

    def tool_names(self) -> list[str]:
        names: list[str] = []
        for tool in self.tools:
            function = tool.get("function") if isinstance(tool, dict) else None
            name = function.get("name") if isinstance(function, dict) else None
            if isinstance(name, str) and name:
                names.append(name)
        return names

    def prompt_tokens(self) -> int:
        total = 0
        for message in self.messages:
            content = message.get("content")
            if isinstance(content, str):
                total += estimate_tokens(content)
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and isinstance(part.get("text"), str):
                        total += estimate_tokens(part["text"])
            calls = message.get("tool_calls")
            if isinstance(calls, list):
                for call in calls:
                    function = call.get("function") if isinstance(call, dict) else None
                    if isinstance(function, dict):
                        total += estimate_tokens(str(function.get("arguments", "")))
        return total

    def completed_tool_rounds(self) -> int:
        """How many tool calls of this turn already have a ``role: tool`` answer.

        This is what makes a multi-round dialogue reproducible without server
        state: the agent loop resends the transcript every round, so the round
        index is a property of the request, not of a cursor. §4 names the
        scripted provider's resetting cursor as the reason it cannot do this.

        The count covers only the CURRENT turn: api-service resends prior
        turns' messages as history (orchestrator flattens every stored turn,
        tool answers included), so a whole-transcript count would drift upward
        each turn and silently exhaust the script after two or three turns of
        one session. The current turn starts at the last ``role: user``
        message - the one this request appends.
        """
        last_user = -1
        for index, message in enumerate(self.messages):
            if message.get("role") == "user":
                last_user = index
        return sum(
            1
            for message in self.messages[last_user + 1 :]
            if message.get("role") == "tool"
        )

    def last_user_message(self) -> str:
        for message in reversed(self.messages):
            if message.get("role") == "user":
                content = message.get("content")
                return content if isinstance(content, str) else ""
        return ""


def parse_chat_request(payload: Any) -> ChatRequest:
    """Validate just enough of the OpenAI body to answer it."""
    if not isinstance(payload, dict):
        raise ValueError("request body must be a JSON object")
    messages = payload.get("messages")
    if not isinstance(messages, list):
        raise ValueError("messages must be a list")
    tools = payload.get("tools")
    return ChatRequest(
        model=str(payload.get("model") or "stub"),
        messages=[message for message in messages if isinstance(message, dict)],
        tools=[tool for tool in tools if isinstance(tool, dict)]
        if isinstance(tools, list)
        else [],
        requested_stream=bool(payload.get("stream")),
    )


def _resolve_tool_name(
    name: str, tenant: str | None, advertised: Sequence[str], script_index: int
) -> str:
    """Resolve a script step's logical tool name to the advertised name.

    A named agent spanning several tenants gets composite tools (``{tenant}__
    db_map``: ``tools.go``), while scripts and profiles carry logical names
    (``db_map``). This bridges the two with ``canonical_tool_name``/
    ``composite_tool_name`` - the same rule the manifest validation already
    applies - instead of the exact string equality that refused every composite
    turn (the L2 failure). It never invents a tool: an unadvertised name is
    still refused, and an ambiguous one (several tenants expose the same
    canonical tool and the step names none) is refused too, so a shared script
    cannot silently funnel a multi-tenant load onto a single tenant.
    """
    if not advertised:
        return name
    if name in advertised:
        return name
    if tenant:
        candidate = composite_tool_name(tenant, name)
        if candidate in advertised:
            return candidate
        raise ValueError(
            f"script step {script_index} calls {name!r} for tenant {tenant!r}, "
            f"which is not advertised in this request's tools ({list(advertised)})"
        )
    canonical = canonical_tool_name(name)
    matches = [t for t in advertised if canonical_tool_name(t) == canonical]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise ValueError(
            f"script step {script_index} calls {name!r}, which is ambiguous under "
            f"composite scope ({matches}); add a 'tenant' field to the step or use "
            f"the prefixed tool name"
        )
    raise ValueError(
        f"script step {script_index} calls {name!r}, which is not advertised "
        f"in this request's tools ({list(advertised)})"
    )


def build_response(
    request: ChatRequest,
    *,
    script: Sequence[Mapping[str, Any]],
    script_index: int,
    content_when_done: str = DEFAULT_DONE_TEXT,
    completion_id: str | None = None,
) -> dict[str, Any]:
    """The OpenAI body for one round of one turn.

    A scripted tool name is first resolved to the advertised name (logical
    ``db_map`` -> composite ``{tenant}__db_map``) via :func:`_resolve_tool_name`,
    so one script drives both separate and composite scopes. A tool the request
    did not advertise is still refused rather than sent: ``LiteLLMProvider``
    would raise a protocol error on it and the agent loop would abort the turn,
    which reads as a platform failure and sends whoever debugs it to the wrong
    service.
    """
    prompt_tokens = request.prompt_tokens()
    tool_calls: list[dict[str, Any]] = []
    if 0 <= script_index < len(script):
        step = script[script_index]
        name = step.get("tool")
        arguments = step.get("arguments", {})
        if not isinstance(name, str) or not name:
            raise ValueError(f"script step {script_index} has no tool name")
        if not isinstance(arguments, Mapping):
            raise ValueError(f"script step {script_index} arguments must be an object")
        if request.tools:
            name = _resolve_tool_name(
                name, step.get("tenant"), request.tool_names(), script_index
            )
        tool_calls.append(
            {
                "id": f"stub-call-{script_index}",
                "type": "function",
                "function": {
                    "name": name,
                    # A JSON string, because that is the shape LiteLLM hands to
                    # ``_tool_call`` and the shape a real provider returns.
                    "arguments": json.dumps(arguments, ensure_ascii=False),
                },
            }
        )

    message: dict[str, Any] = {"role": "assistant"}
    if tool_calls:
        message["tool_calls"] = tool_calls
        message["content"] = ""
    else:
        message["content"] = content_when_done

    completion_tokens = estimate_tokens(json.dumps(message, ensure_ascii=False))
    return {
        "id": completion_id or f"chatcmpl-stub-{int(time.time() * 1000)}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": request.model,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": "tool_calls" if tool_calls else "stop",
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


def _split_bytes(body: bytes, chunks: int) -> list[bytes]:
    """Split a response body into at most ``chunks`` non-empty pieces.

    HTTP chunked framing has no empty chunk before the terminator (a zero-length
    chunk *ends* the message), so a body shorter than the requested chunk count
    yields fewer pieces — declared "at most" for exactly that reason.
    """
    count = min(chunks, len(body))
    if count <= 1:
        return [body]
    size = (len(body) + count - 1) // count  # ceil, so no piece is empty
    return [body[index : index + size] for index in range(0, len(body), size)]


class _Handler(BaseHTTPRequestHandler):
    """One HTTP request. ``server`` is the :class:`StubServer` owner."""

    protocol_version = "HTTP/1.1"
    server_version = "helperium-stress-stub/1.0"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        logger.debug("stub: " + format, *args)

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode()
        owner: StubServer | None = getattr(self.server, "owner", None)
        chunks = owner.response_chunks if owner is not None else 1
        delay_s = (owner.chunk_delay_ms / 1000.0) if owner is not None else 0.0
        if chunks <= 1:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        # Realistic delivery (Трек 0 плана api-service): the body arrives in
        # ``chunks`` pieces with ``chunk_delay_ms`` pauses, so the client pays
        # for reading a response over time instead of one local write. The
        # JSON is unchanged — chunking is transport, not semantics — and the
        # non-streamed contract holds: this is still one HTTP response.
        pieces = _split_bytes(body, chunks)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        for index, piece in enumerate(pieces):
            if index and delay_s > 0:
                time.sleep(delay_s)
            self.wfile.write(f"{len(piece):X}\r\n".encode("ascii"))
            self.wfile.write(piece)
            self.wfile.write(b"\r\n")
            self.wfile.flush()
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()

    def do_GET(self) -> None:  # noqa: N802 - http.server's naming
        if self.path.rstrip("/") == "/health":
            self._send_json(200, {"status": "ok"})
            return
        self._send_json(404, {"error": {"message": f"no route for {self.path}"}})

    def do_POST(self) -> None:  # noqa: N802 - http.server's naming
        owner: StubServer = self.server.owner  # type: ignore[attr-defined]
        if self.path.rstrip("/") != "/v1/chat/completions":
            self._send_json(404, {"error": {"message": f"no route for {self.path}"}})
            return

        arrival_ms = time.time() * 1000.0
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        correlation_id = self.headers.get("X-Correlation-ID") or ""

        try:
            request = parse_chat_request(json.loads(raw.decode("utf-8")))
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._send_json(400, {"error": {"message": f"bad request: {exc}"}})
            # A refused request is still an arrival the instrument saw: the
            # timing log must cover 4xx paths too, or the join undercounts.
            owner.record(
                arrival_ms=arrival_ms,
                service_start_ms=time.time() * 1000.0,
                service_ms=0.0,
                prompt_tokens=0,
                correlation_id=correlation_id,
                outcome="bad_request",
                marker=None,
            )
            return

        # The concurrency limit queues rather than rejects: §4's queue_wait is
        # the signal that says "the instrument saturated", and a 429 here would
        # instead model a provider outage. A wait longer than ``queue_timeout_s``
        # is answered 503 and logged, because an answer that late is a different
        # failure from a slow one.
        with owner.slot(timeout_s=owner.queue_timeout_s) as acquired:
            if not acquired:
                self._send_json(
                    503,
                    {"error": {"message": "stub queue timeout"}},
                )
                owner.record(
                    arrival_ms=arrival_ms,
                    service_start_ms=time.time() * 1000.0,
                    service_ms=0.0,
                    prompt_tokens=request.prompt_tokens(),
                    correlation_id=correlation_id,
                    outcome="queue_timeout",
                    marker=extract_marker(request.messages),
                )
                return
            service_start_ms = time.time() * 1000.0
            latency_ms = owner.latency.sample(
                prompt_tokens=request.prompt_tokens(),
                draw=owner.draw(),
            )
            time.sleep(latency_ms / 1000.0)

            try:
                payload = build_response(
                    request,
                    script=owner.script,
                    script_index=request.completed_tool_rounds(),
                    content_when_done=owner.content_when_done,
                )
            except ValueError as exc:
                self._send_json(400, {"error": {"message": str(exc)}})
                owner.record(
                    arrival_ms=arrival_ms,
                    service_start_ms=service_start_ms,
                    service_ms=latency_ms,
                    prompt_tokens=request.prompt_tokens(),
                    correlation_id=correlation_id,
                    outcome="script_refused",
                    marker=extract_marker(request.messages),
                )
                return

            # §7: the stream time is the stub's own service time, not the
            # platform's overhead. The harness subtracts service_ms from
            # t_complete, so a chunked delivery billed to nobody would appear as
            # platform overhead — the exact lie a zero-latency stub would
            # otherwise make of this profile. Send first, then record: the
            # elapsed send is what must land in service_ms, and ``finally``
            # keeps the row even when the client hangs up mid-chunk (a client
            # abort is a measurement, not a reason to lose the record).
            send_started_ms = time.time() * 1000.0
            try:
                self._send_json(200, payload)
            finally:
                send_ms = (time.time() * 1000.0) - send_started_ms
                owner.record(
                    arrival_ms=arrival_ms,
                    service_start_ms=service_start_ms,
                    service_ms=latency_ms + max(send_ms, 0.0),
                    prompt_tokens=payload["usage"]["prompt_tokens"],
                    correlation_id=correlation_id,
                    outcome="ok",
                    marker=extract_marker(request.messages),
                    tool_calls=[
                        call["function"]["name"]
                        for call in payload["choices"][0]["message"].get(
                            "tool_calls", []
                        )
                    ],
                )

    def do_DELETE(self) -> None:  # noqa: N802 - http.server's naming
        self._send_json(404, {"error": {"message": "no route"}})


class StubServer:
    """The stub model as a process-local HTTP server plus its timing log.

    ``concurrency`` is the stub's own parallelism (§3 ``provider_concurrency``).
    It is deliberately a real limit rather than an unbounded thread pool: a stub
    that never queues cannot show the queue-wait signal §4 asks for, and the
    whole point of that signal is to catch the case where the instrument is what
    saturated.
    """

    def __init__(
        self,
        *,
        latency: LatencyModel,
        script: Sequence[Mapping[str, Any]] = (),
        log_path: str | Path = "stub-timings.jsonl",
        concurrency: int = 64,
        queue_timeout_s: float = 30.0,
        host: str = "127.0.0.1",
        port: int = 0,
        content_when_done: str = DEFAULT_DONE_TEXT,
        response_chunks: int = 1,
        chunk_delay_ms: float = 0.0,
    ) -> None:
        if concurrency < 1:
            raise ValueError(f"concurrency must be positive, got {concurrency}")
        if queue_timeout_s <= 0:
            raise ValueError(f"queue_timeout_s must be positive, got {queue_timeout_s}")
        if response_chunks < 1:
            raise ValueError(
                f"response_chunks must be at least 1, got {response_chunks}"
            )
        if chunk_delay_ms < 0:
            raise ValueError(
                f"chunk_delay_ms must not be negative, got {chunk_delay_ms}"
            )
        self.latency = latency
        self.script = list(script)
        self.log_path = Path(log_path)
        self.concurrency = concurrency
        self.queue_timeout_s = queue_timeout_s
        self.content_when_done = content_when_done
        self.response_chunks = response_chunks
        self.chunk_delay_ms = chunk_delay_ms

        self._sema = threading.BoundedSemaphore(concurrency)
        self._log_lock = threading.Lock()
        self._counter_lock = threading.Lock()
        self._counter = 0
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._host = host
        self._port = port

    # ── lifecycle ──────────────────────────────────────────────────────────

    def start(self) -> StubServer:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._httpd = ThreadingHTTPServer((self._host, self._port), _Handler)
        self._httpd.daemon_threads = True
        self._httpd.owner = self  # type: ignore[attr-defined]
        self._port = self._httpd.server_address[1]
        self._thread = threading.Thread(
            target=self._httpd.serve_forever, name="stress-stub", daemon=True
        )
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        if self._thread is not None:
            self._thread.join(timeout=10)
            self._thread = None

    @property
    def base_url(self) -> str:
        return f"http://{self._host}:{self._port}"

    # ── instrumentation ────────────────────────────────────────────────────

    def next_request_index(self) -> int:
        with self._counter_lock:
            self._counter += 1
            return self._counter

    def draw(self) -> float:
        """The quantile this request samples, from a module-level RNG stream.

        ``random`` is shared between threads on purpose: a per-request seed would
        make the tail deterministic per request index, which is not what a load
        profile promises - it promises a distribution.
        """
        return random.random()

    def saturated(self) -> bool:
        """True when every slot is busy: observable, and not a rejection.

        The point of a concurrency limit on the stub is the *queue* it creates:
        §4 wants ``queue_wait`` to show when the instrument, not the platform,
        is the bottleneck. Rejecting the excess with a 429 would model a provider
        outage instead, so this is a probe, not a gate.
        """
        acquired = self._sema.acquire(blocking=False)
        if acquired:
            self._sema.release()
        return not acquired

    @contextmanager
    def slot(self, *, timeout_s: float):
        """Wait for a concurrency slot; ``False`` when the wait timed out."""
        acquired = self._sema.acquire(timeout=timeout_s)
        try:
            yield acquired
        finally:
            if acquired:
                self._sema.release()

    def record(
        self,
        *,
        arrival_ms: float,
        service_start_ms: float,
        service_ms: float,
        prompt_tokens: int,
        correlation_id: str,
        outcome: str,
        tool_calls: list[str] | None = None,
        marker: str | None = None,
    ) -> None:
        entry = {
            "arrival_ms": round(arrival_ms, 3),
            "service_start_ms": round(service_start_ms, 3),
            "service_ms": round(service_ms, 3),
            "queue_wait_ms": round(max(service_start_ms - arrival_ms, 0.0), 3),
            "prompt_tokens": prompt_tokens,
            "correlation_id": correlation_id,
            "outcome": outcome,
            "tool_calls": tool_calls or [],
            "marker": marker,
        }
        line = json.dumps(entry, ensure_ascii=False)
        with self._log_lock:
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")


__all__ = [
    "DEFAULT_DONE_TEXT",
    "ChatRequest",
    "LatencyModel",
    "StubServer",
    "build_response",
    "estimate_tokens",
    "parse_chat_request",
]
