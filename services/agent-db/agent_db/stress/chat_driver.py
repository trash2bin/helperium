"""L2/L3 driver: one turn of chat against api-service over SSE (§1, §3, §7).

L1 replays the *calls* a turn would make; this driver replays the turn itself.
That is the only way to measure what §1 calls the orchestration layer - SSE
framing, the session store, the per-tenant MCP client - because none of those
exist on the tool path.

The shape follows :class:`agent_db.stress.driver.McpToolDriver` exactly, so the
open-loop scheduler and the ladder drive either layer without knowing which one
they are driving: ``open_session`` / ``close_session`` / ``execute_turn``.

What a turn records, and why:

- ``ttfe_session``/``ttfe``/``ttfe_tool`` come from the offsets of the frames
  themselves. ``ttfe`` deliberately skips the ``session`` handshake event: §1
  calls a TTFE measured on the handshake a measurement of the wrong thing.
- ``t_complete`` is the offset of the terminal frame (``done``), not of the
  connection closing: a stream that lingers after ``done`` is not a slower turn.
- A quality refusal arrives as HTTP 200 + SSE ``error`` (§5), so an error is a
  *stream* fact. The structural class is ``sse_abort``; the terminator is left
  unset because §7 forbids deriving ``limit_*`` from localised message text -
  that join belongs to the backlog, which carries ``outcome`` structurally.

The message text is load, not content, but it is not arbitrary either: it embeds
a stable per-turn marker (``stress:<scenario>:<step>:<n>``), which is what a
stub-side join keys on (§4), and it keeps the per-session turn count in the
record's ``history_turns`` slot so a recycled session is visible in the raw file.
"""

from __future__ import annotations

import threading
import time
from dataclasses import replace
from typing import Any

from .chat_transport import ChatFrame, ChatSession, ChatTransport, ChatTransportError
from .driver import ToolCallTiming, TurnExecution
from .profile import WorkloadStep
from .records import ErrorClass, RawRequestRecord

#: How long a turn may stream before the harness stops waiting. A turn that has
#: not finished by then has failed in a way the platform should have bounded
#: (``AGENT_MAX_ITERATIONS`` caps the rounds; the provider has a timeout).
DEFAULT_TURN_TIMEOUT_S = 120.0


class ChatDriver:
    """Drive turns of a workload step through the chat endpoint."""

    def __init__(
        self,
        transport: ChatTransport,
        *,
        turn_timeout_s: float = DEFAULT_TURN_TIMEOUT_S,
        wall_clock: Any = None,
    ) -> None:
        self.transport = transport
        self.turn_timeout_s = turn_timeout_s
        self.wall_clock = wall_clock or time.time
        self._counter_lock = threading.Lock()
        self._counters: dict[str, int] = {}

    # ── sessions ───────────────────────────────────────────────────────────

    def open_session(self, tenant: str) -> ChatSession:
        return self.transport.open_session(tenant)

    def close_session(self, session: ChatSession) -> None:
        self.transport.close_session(session)

    def _next_turn_number(self, session: ChatSession) -> int:
        with self._counter_lock:
            current = self._counters.get(session.key, 0) + 1
            self._counters[session.key] = current
            return current

    # ── the turn ───────────────────────────────────────────────────────────

    def message_for(self, session: ChatSession, step: WorkloadStep) -> str:
        """One deterministic message per turn of this session.

        The marker is stable per turn so a stub's timing log can be joined to a
        turn without server-side changes (§4: "маркер в сообщении"), and its
        length is realistic rather than empty: an empty message is refused before
        the abuse gate, and a one-word message would understate the prompt term.
        """
        return self._format_message(step, self._next_turn_number(session))

    @staticmethod
    def _format_message(step: WorkloadStep, number: int) -> str:
        return (
            f"[stress:{step.name}:{number}] Покажи данные по запросу "
            f"{step.name} номер {number} из базы."
        )

    def execute_turn(
        self,
        session: ChatSession,
        step: WorkloadStep,
        *,
        planned_ms: float,
        started_ms: float,
        tenant: str,
        scenario: str,
    ) -> TurnExecution:
        # The turn number and the correlation id must come from the SAME
        # counter: the stub-side join keys the timing log on the marker, and a
        # number that skips after a failed turn would join the wrong rows.
        number = self._next_turn_number(session)
        message = self._format_message(step, number)
        correlation_id = f"stress-{session.session_id}-{number}"
        calls: list[ToolCallTiming] = []
        pending: dict[str, float] = {}  # call key -> position in calls
        ttfe_session: float | None = None
        ttfe: float | None = None
        ttfe_tool: float | None = None
        terminal: str | None = None
        error_text: str | None = None
        # Transport-level failures are measurements too (§5 budgets live in the
        # error mix), so they are recorded in-band like the L1 driver does -
        # an exception here would kill the worker thread and void the rung
        # without leaving a raw record.
        transport_status: int | None = None
        transport_class: ErrorClass | None = None

        stream = self.transport.stream_turn(
            session, message, correlation_id=correlation_id
        )
        deadline = time.monotonic() + self.turn_timeout_s
        last_offset_ms = 0.0
        try:
            for frame in stream:
                last_offset_ms = frame.offset_ms
                # ttfe_session is the handshake (§1: a network sanity, not
                # responsiveness); ttfe is the first event *after* it.
                ttfe_session = _first(ttfe_session, frame, set())
                ttfe = _first(ttfe, frame, {"session"})
                if frame.type == "tool_call":
                    ttfe_tool = ttfe_tool if ttfe_tool is not None else frame.offset_ms
                    call_id = str(frame.event.get("id") or "")
                    name = str(frame.event.get("name") or "")
                    # The wire's tool_result carries the name but NOT the id
                    # (api-service sse.py builds it from name/display_name), so
                    # the join key is the name; a same-name parallel call in one
                    # turn cannot occur - the loop calls one tool at a time.
                    key = name or call_id
                    pending[key] = len(calls)
                    # One row per call: the duration is filled in by the matching
                    # tool_result, and stays the request offset if none arrives.
                    calls.append(
                        ToolCallTiming(
                            index=len(calls),
                            tool=name,
                            elapsed_ms=frame.offset_ms,
                            started_wall_ms=self.wall_clock() * 1000.0,
                        )
                    )
                elif frame.type == "tool_result":
                    name = str(frame.event.get("name") or "")
                    call_id = str(frame.event.get("id") or "")
                    position = pending.pop(name or call_id, None)
                    if position is not None:
                        opened = calls[position]
                        calls[position] = replace(
                            opened,
                            # The call's own duration, not its offset from the turn
                            # start: the offset is already in the turn record.
                            elapsed_ms=max(frame.offset_ms - opened.elapsed_ms, 0.0),
                        )
                elif frame.type == "error":
                    terminal = terminal or "error"
                    text = frame.event.get("text")
                    error_text = text if isinstance(text, str) else "error"
                elif frame.type == "done":
                    # `done` closes the stream; the terminal fact stays `error` when
                    # the turn was refused - the refusal is what a reader needs.
                    terminal = terminal or "done"
                if frame.type == "done" or time.monotonic() > deadline:
                    break
        except ChatTransportError as exc:
            transport_status = exc.status
            transport_class = exc.error_class or ErrorClass.RESET
            error_text = error_text or str(exc)
            terminal = terminal or "error"

        if terminal is None:
            # The stream closed without a terminal event: §7 calls this
            # ``sse_abort``, and it is an error even when the HTTP status was 200.
            terminal = "aborted"
            error_text = error_text or "stream ended without a terminal event"

        failed = error_text is not None
        record = RawRequestRecord(
            planned_ms=planned_ms,
            started_ms=started_ms,
            actual_ms=last_offset_ms,
            ttfe_session=ttfe_session,
            ttfe=ttfe,
            ttfe_tool=ttfe_tool,
            t_complete=last_offset_ms,
            # The honest prompt length is the backlog's, not a guess: this driver
            # never sees the prompt api-service assembled. The join (session_id
            # + turn order) is a separate, explicit step - a fabricated number
            # here would wear the column's name while meaning something else.
            prompt_tokens=None,
            history_turns=step.history_turns,
            session_id=session.session_id,
            tenant=tenant,
            scenario=scenario,
            status="error" if failed else "ok",
            http_status=transport_status,
            sse_terminal_event=terminal,
            # A transport refusal keeps its structural class (429, 401, 5xx...);
            # a stream-level refusal is ``sse_abort`` by §7 - never guessed
            # from message text.
            error_class=(
                transport_class
                if transport_class is not None
                else ErrorClass.SSE_ABORT
                if failed
                else None
            ),
        )
        return TurnExecution(record=record, calls=tuple(calls), reinitialisations=0)


def _first(current: float | None, frame: ChatFrame, skip: set[str]) -> float | None:
    """The first meaningful frame's offset, skipping the handshake event."""
    if current is not None or frame.type in skip:
        return current
    return frame.offset_ms


__all__ = ["ChatDriver", "DEFAULT_TURN_TIMEOUT_S"]
