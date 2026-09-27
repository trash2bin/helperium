"""L1 driver: replay a turn's tool-call traffic over MCP (§11).

L1 has no LLM, so the harness has to reproduce the *call pattern* a turn would
produce, not a turn itself. What a turn costs a tenant is known from the code
(§3): one schema preload plus the profile's calls, all of them serialised on the
per-tenant ``call_lock``. Replaying exactly that pattern is what makes an L1
number a measurement of the tool/data layer instead of a guess about the model.

The unit of a record here is the **turn**, not the individual call: the open-loop
generator schedules turns, the capacity criterion is about turns, and coordinated
omission compensation only means anything for a unit that has a planned start.
Per-call timings are kept as a decomposition next to the turn record (they are
what the analytic knee prediction in §3 is checked against), with no planned
offset of their own - inventing one per call would double count the turn's slip.

The preload is outside the server's tool-call budget: the orchestrator calls
``db_map`` before the loop starts (``orchestrator.py``, ``mcp.call_tool("db_map",
{})``), so it never increments the loop's ``tool_calls`` counter. A step
declaring ``AGENT_MAX_TOOL_CALLS - 1`` planned calls therefore stays valid even
though the turn makes one more request than that.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from .constants import PRELOAD_TOOL
from .profile import WorkloadStep
from .records import ErrorClass, RawRequestRecord
from .transport import McpSession, McpTransport


class DriverConfigurationError(ValueError):
    """The driver was handed a step the fixture cannot feed."""


@dataclass(frozen=True)
class ToolCallTiming:
    """One call inside a turn: decomposition, not a scheduling unit."""

    index: int
    tool: str
    elapsed_ms: float
    http_status: int | None = None
    error_class: ErrorClass | None = None
    is_error: bool = False
    preload: bool = False
    # Wall-clock epoch milliseconds. The turn record is on a monotonic clock
    # that starts at zero, so without this a call cannot be placed in the
    # services' logs at all: only "14 calls into the 80 rps stage" is known, and
    # the gateway and data-service log in wall-clock time. With it, one record
    # picks the log window to read.
    started_wall_ms: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "tool": self.tool,
            "elapsed_ms": round(self.elapsed_ms, 3),
            "http_status": self.http_status,
            "error_class": self.error_class.value if self.error_class else None,
            "is_error": self.is_error,
            "preload": self.preload,
            "started_wall_ms": round(self.started_wall_ms, 3),
        }

    def as_jsonl(self) -> str:
        return json.dumps(self.as_dict(), ensure_ascii=False, separators=(",", ":"))


@dataclass(frozen=True)
class TurnExecution:
    """What one scheduled turn produced: one record plus its decomposition."""

    record: RawRequestRecord
    calls: tuple[ToolCallTiming, ...]
    reinitialisations: int = 0

    def calls_jsonl(self) -> list[str]:
        return [call.as_jsonl() for call in self.calls]


def plan_calls(
    step: WorkloadStep, *, preload_tool: str | None = PRELOAD_TOOL
) -> list[tuple[str, bool]]:
    """Expand a step into the calls one turn makes, preload first.

    Returns ``(tool, is_preload)`` pairs: ``tools`` in declared order, then each
    ``repeat`` entry the declared number of times. The preload leads because the
    orchestrator fetches the schema before the first model call.
    """
    planned: list[tuple[str, bool]] = []
    if preload_tool:
        planned.append((preload_tool, True))
    planned.extend((tool, False) for tool in step.tools)
    for tool, count in step.repeat.items():
        planned.extend((tool, False) for _ in range(count))
    return planned


class McpToolDriver:
    """Drives L1: one turn is a sequential run of MCP tool calls."""

    layer = "L1"

    def __init__(
        self,
        transport: McpTransport,
        *,
        arguments: Mapping[str, Mapping[str, Any]],
        preload_tool: str | None = PRELOAD_TOOL,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        self.transport = transport
        self.arguments = {name: dict(args) for name, args in arguments.items()}
        self.preload_tool = preload_tool
        # Injectable so a test can pin the wall clock a call records.
        self.wall_clock = wall_clock

    # ── sessions ───────────────────────────────────────────────────────────

    def open_session(self, tenant: str) -> McpSession:
        """Handshake. Cold start, so it belongs to warm-up, not to the stage."""
        return self.transport.open(tenant)

    def close_session(self, session: McpSession) -> None:
        self.transport.close(session)

    # ── preflight ──────────────────────────────────────────────────────────

    def unfed_calls(self, step: WorkloadStep) -> list[str]:
        """Tools this step would call without an argument fixture.

        Checked before a stage runs: a call without arguments turns the stage
        into an error-rate measurement instead of a capacity one.
        """
        missing = {
            tool
            for tool, _is_preload in plan_calls(step, preload_tool=self.preload_tool)
            if tool not in self.arguments
        }
        return sorted(missing)

    # ── the turn ───────────────────────────────────────────────────────────

    def execute_turn(
        self,
        session: McpSession,
        step: WorkloadStep,
        *,
        planned_ms: float,
        started_ms: float,
        tenant: str,
        scenario: str,
    ) -> TurnExecution:
        """Run one turn of ``step`` against ``session``.

        A failing call ends the turn: the platform aborts the turn on a tool
        error, so replaying the remaining calls would measure traffic the real
        system never makes.
        """
        calls: list[ToolCallTiming] = []
        turn_started = time.perf_counter()
        for index, (tool, is_preload) in enumerate(
            plan_calls(step, preload_tool=self.preload_tool)
        ):
            arguments = self.arguments.get(tool)
            if arguments is None:
                raise DriverConfigurationError(
                    f"no fixture arguments for {tool!r} in step {step.name!r}"
                )
            call_started_wall_ms = self.wall_clock() * 1000.0
            outcome = self.transport.call_tool(session, tool, arguments)
            calls.append(
                ToolCallTiming(
                    index=index,
                    tool=tool,
                    elapsed_ms=outcome.elapsed_ms,
                    http_status=outcome.http_status,
                    error_class=outcome.error_class,
                    is_error=outcome.is_error,
                    preload=is_preload,
                    started_wall_ms=call_started_wall_ms,
                )
            )
            if outcome.is_error:
                break
        turn_elapsed = (time.perf_counter() - turn_started) * 1000.0

        failure = next((call for call in calls if call.is_error), None)
        first = calls[0] if calls else None
        last = calls[-1] if calls else None
        record = RawRequestRecord(
            planned_ms=planned_ms,
            started_ms=started_ms,
            actual_ms=turn_elapsed,
            # No LLM stream on L1: no session-level first event and no tokens.
            # ``ttfe_tool`` is the preload's completion - the first thing the
            # turn waits for.
            ttfe_tool=first.elapsed_ms if first else None,
            t_complete=turn_elapsed,
            history_turns=step.history_turns,
            session_id=session.session_id or "",
            tenant=tenant,
            scenario=scenario,
            status="error" if failure else "ok",
            http_status=(failure or last).http_status if (failure or last) else None,
            error_class=failure.error_class if failure else None,
        )
        reinitialisations = 1 if session.reinitialised_last_call else 0
        return TurnExecution(
            record=record, calls=tuple(calls), reinitialisations=reinitialisations
        )
