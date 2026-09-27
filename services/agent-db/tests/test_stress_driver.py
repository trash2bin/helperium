"""L1 driver contract tests (doc/stress/README.md §3, §11).

What a turn costs a tenant is a code fact: one schema preload plus the profile's
calls, serialised on the per-tenant lock. These tests pin that expansion, the
turn-as-record rule (per-call timings are decomposition only), and the refusal to
run a step the fixture cannot feed.
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from agent_db.stress.driver import (
    DriverConfigurationError,
    McpToolDriver,
    TurnExecution,
    plan_calls,
)
from agent_db.stress.profile import WorkloadStep
from agent_db.stress.records import ErrorClass
from agent_db.stress.transport import McpCallOutcome, McpSession

FIXTURE_ARGS = {
    "db_map": {},
    "db_get": {"entity": "group", "id": "g1"},
    "db_search": {"entity": "student", "pattern": "Петров"},
}


class FakeTransport:
    """Scripted MCP transport: outcomes in order, calls recorded.

    With ``delay=True`` the fake actually sleeps for the scripted latency, so a
    test can assert that the turn record's wall clock accounts for its calls
    instead of comparing two synthetic numbers.
    """

    def __init__(self, *outcomes: Any, delay: bool = False) -> None:
        self.outcomes = list(outcomes)
        self.delay = delay
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.opened: list[str] = []
        self.closed = 0

    def factory(self, tenant: str, session_id: str = "sess-1") -> McpSession:
        return McpSession(
            tenant, None, "/mcp", timeout_s=5.0, initialised=True, session_id=session_id
        )  # type: ignore[arg-type]

    def open(self, tenant: str) -> McpSession:
        self.opened.append(tenant)
        return self.factory(tenant)

    def close(self, session: McpSession) -> None:
        self.closed += 1

    def call_tool(
        self, session: McpSession, tool: str, arguments: dict[str, Any]
    ) -> McpCallOutcome:
        self.calls.append((tool, arguments))
        if not self.outcomes:
            raise AssertionError(f"unexpected call: {tool}")
        item = self.outcomes.pop(0)
        outcome = item if isinstance(item, McpCallOutcome) else McpCallOutcome(**item)
        if self.delay:
            time.sleep(outcome.elapsed_ms / 1000.0)
        return outcome


def ok(elapsed_ms: float = 10.0) -> McpCallOutcome:
    return McpCallOutcome(elapsed_ms=elapsed_ms, http_status=200)


def step(**overrides: Any) -> WorkloadStep:
    payload: dict[str, Any] = {
        "weight": 1.0,
        "name": "step",
        "tools": ["db_get"],
        "history_turns": 0,
    }
    payload.update(overrides)
    return WorkloadStep.model_validate(payload)


def driver(transport: FakeTransport, arguments: dict | None = None) -> McpToolDriver:
    return McpToolDriver(transport, arguments=arguments or FIXTURE_ARGS)  # type: ignore[arg-type]


def run_turn(
    drv: McpToolDriver, transport: FakeTransport, the_step: WorkloadStep
) -> TurnExecution:
    session = drv.open_session("t-1")
    return drv.execute_turn(
        session,
        the_step,
        planned_ms=0.0,
        started_ms=0.0,
        tenant="t-1",
        scenario="sqlite-testseed",
    )


class TestCallRecordsCarryAWallClock:
    def test_a_call_is_stamped_with_absolute_time(self):
        # The turn record's clock starts at zero for the stage, so a call cannot
        # be located in the services' logs without an absolute stamp: a report
        # reader otherwise has "call 14 of the 80 rps stage" and 25 MB of
        # wall-clock log lines.
        transport = FakeTransport(ok(1.0), ok(1.0))
        ticks = iter([1_790_000_000.0, 1_790_000_000.25])
        drv = McpToolDriver(
            transport, arguments=FIXTURE_ARGS, wall_clock=lambda: next(ticks)
        )
        execution = run_turn(drv, transport, step(tools=["db_get"]))
        stamps = [call.started_wall_ms for call in execution.calls]
        assert stamps == [1_790_000_000_000.0, 1_790_000_000_250.0]
        assert execution.calls[0].as_dict()["started_wall_ms"] == 1_790_000_000_000.0
        # The stamp is on the call, not on the turn: per-call timings are what a
        # slow tool has to be found by.
        assert [call.tool for call in execution.calls] == ["db_map", "db_get"]


class TestPlanCalls:
    def test_preload_leads_then_tools_then_repeat(self) -> None:
        planned = plan_calls(step(tools=["db_search", "db_get"], repeat={"db_get": 2}))
        assert planned == [
            ("db_map", True),
            ("db_search", False),
            ("db_get", False),
            ("db_get", False),
            ("db_get", False),
        ]

    def test_preload_can_be_suppressed(self) -> None:
        planned = plan_calls(step(), preload_tool=None)
        assert planned == [("db_get", False)]

    def test_expansion_matches_the_step_budget(self) -> None:
        # planned_calls() validates the server-side tool budget; the expansion
        # adds exactly the preload on top of it, and the preload does not count
        # against the loop's budget (orchestrator calls db_map before the loop).
        the_step = step(tools=["db_map", "db_search"], repeat={"db_get": 6})
        assert the_step.planned_calls() == 8
        assert len(plan_calls(the_step)) == 9


class TestFixtureCoverage:
    def test_unfed_calls_lists_missing_tools(self) -> None:
        drv = driver(FakeTransport(), arguments={"db_get": {}})
        assert drv.unfed_calls(step(tools=["db_get", "db_search"])) == [
            "db_map",
            "db_search",
        ]

    def test_unfed_calls_is_empty_when_covered(self) -> None:
        assert driver(FakeTransport()).unfed_calls(step()) == []

    def test_execute_turn_refuses_an_unfed_call(self) -> None:
        drv = driver(FakeTransport(ok()), arguments={"db_map": {}})
        with pytest.raises(DriverConfigurationError, match="db_get"):
            run_turn(drv, drv.transport, step())


class TestTurnExecution:
    def test_records_one_turn_with_per_call_decomposition(self) -> None:
        transport = FakeTransport(ok(4.0), ok(6.0), ok(8.0), delay=True)
        drv = driver(transport)
        execution = run_turn(drv, transport, step(tools=["db_search", "db_get"]))

        assert [call[0] for call in transport.calls] == [
            "db_map",
            "db_search",
            "db_get",
        ]
        assert [c.tool for c in execution.calls] == ["db_map", "db_search", "db_get"]
        assert [c.preload for c in execution.calls] == [True, False, False]
        assert all(c.index == i for i, c in enumerate(execution.calls))
        # One record for the turn, and its wall clock covers every call: the
        # decomposition is a report of the same time, not a parallel truth.
        assert execution.record.status == "ok"
        assert execution.record.actual_ms >= 17.0
        assert execution.record.t_complete == execution.record.actual_ms
        assert execution.record.ttfe_tool == execution.calls[0].elapsed_ms
        assert sum(c.elapsed_ms for c in execution.calls) == pytest.approx(18.0)

    def test_record_carries_plan_and_scope(self) -> None:
        transport = FakeTransport(ok(), ok())
        drv = driver(transport)
        execution = run_turn(drv, transport, step(tools=["db_get"], history_turns=3))
        record = execution.record
        assert record.history_turns == 3
        assert record.session_id == "sess-1"
        assert record.tenant == "t-1"
        assert record.scenario == "sqlite-testseed"
        assert record.prompt_tokens is None
        assert record.sse_terminal_event is None

    def test_arguments_come_from_the_fixture_per_tool(self) -> None:
        transport = FakeTransport(ok(), ok(), ok())
        drv = driver(transport)
        run_turn(drv, transport, step(tools=["db_search", "db_get"]))
        assert transport.calls == [
            ("db_map", {}),
            ("db_search", FIXTURE_ARGS["db_search"]),
            ("db_get", FIXTURE_ARGS["db_get"]),
        ]

    def test_a_failing_call_ends_the_turn(self) -> None:
        transport = FakeTransport(
            ok(),
            {
                "elapsed_ms": 5.0,
                "http_status": 200,
                "is_error": True,
                "error_class": ErrorClass.OTHER,
            },
            ok(),
        )
        drv = driver(transport)
        execution = run_turn(drv, transport, step(tools=["db_search", "db_get"]))

        # The platform aborts the turn on a tool error, so the third call is
        # never made and its outcome stays unconsumed.
        assert [call[0] for call in transport.calls] == ["db_map", "db_search"]
        assert len(transport.outcomes) == 1
        assert execution.record.status == "error"
        assert execution.record.error_class is ErrorClass.OTHER
        assert execution.record.http_status == 200

    def test_transport_failure_keeps_its_class(self) -> None:
        transport = FakeTransport(
            ok(),
            {
                "elapsed_ms": 2.0,
                "error_class": ErrorClass.BUDGET_429,
                "is_error": True,
                "http_status": 429,
            },
        )
        drv = driver(transport)
        execution = run_turn(drv, transport, step(tools=["db_search"]))
        assert execution.record.status == "error"
        assert execution.record.error_class is ErrorClass.BUDGET_429
        assert execution.record.http_status == 429

    def test_reinitialisation_is_surfaced(self) -> None:
        transport = FakeTransport(ok(), ok())
        drv = driver(transport)
        session = drv.open_session("t-1")
        session.reinitialised_last_call = True
        execution = drv.execute_turn(
            session,
            step(),
            planned_ms=0.0,
            started_ms=0.0,
            tenant="t-1",
            scenario="sqlite-testseed",
        )
        assert execution.reinitialisations == 1

    def test_calls_jsonl_is_round_trippable(self) -> None:
        import json

        transport = FakeTransport(ok(7.5), ok(1.0))
        drv = driver(transport)
        execution = run_turn(drv, transport, step())
        lines = execution.calls_jsonl()
        assert len(lines) == 2
        payload = json.loads(lines[0])
        # The wall-clock stamp is what places the call in the services' logs, so
        # it has to survive the round trip; its value is a clock reading, checked
        # for plausibility rather than pinned.
        stamp = payload.pop("started_wall_ms")
        assert stamp > 1_700_000_000_000  # epoch ms, not seconds or a relative
        assert payload == {
            "index": 0,
            "tool": "db_map",
            "elapsed_ms": 7.5,
            "http_status": 200,
            "error_class": None,
            "is_error": False,
            "preload": True,
        }

    def test_session_lifecycle_is_delegated_to_the_transport(self) -> None:
        transport = FakeTransport(ok())
        drv = driver(transport)
        session = drv.open_session("t-9")
        drv.close_session(session)
        assert transport.opened == ["t-9"]
        assert transport.closed == 1
