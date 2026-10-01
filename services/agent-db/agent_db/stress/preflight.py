"""Preflight: what has to be true before the first tick is scheduled (§6, §10).

A load run that fails at rung 3 because a fixture value does not resolve, or
because the tenant was never registered, has already spent the stand's warm-up
and produced a page of numbers nobody may use. The design learned this the
expensive way (§3): the first live run found that ``db_search`` takes ``pattern``
and not ``query``, and that a call without a fixture answers ``isError`` in 5 ms.
Both are checkable in a second, so they are checked in a second.

The rule here is the one §6 states for the manifest: a probe either returns a
measured value or records a gap with the reason. Nothing is estimated, and a
check that could not run is never reported as passed.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .driver import McpToolDriver
from .fixture import ArgumentFixture, validate_fixture_covers
from .manifest import BudgetSpec, collect_budgets
from .profile import LoadProfile, validate_tools_against_manifest
from .runner import WorkloadPlan
from .transport import McpTransport


@dataclass
class PreflightResult:
    """What was checked, what was measured, and what stops the run."""

    checks: list[dict[str, Any]] = field(default_factory=list)
    refusals: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.refusals

    def record(self, name: str, status: str, **detail: Any) -> None:
        self.checks.append({"check": name, "status": status, **detail})

    def refuse(self, name: str, reason: str) -> None:
        self.checks.append({"check": name, "status": "failed", "reason": reason})
        self.refusals.append(f"{name}: {reason}")

    def payload(self) -> dict[str, Any]:
        return {
            "status": "ok" if self.ok else "failed",
            "checks": self.checks,
            "refusals": self.refusals,
        }


def quiet_host_probe() -> dict[str, Any]:
    """Load average against the core count, as §6's quiet-host preflight.

    Only a load average is read: a stand is quiet when nothing else is competing
    for its cores, and that is exactly what the figure says. Nothing here
    *decides* anything - the CLI prints it and the manifest records it - because
    the honest machine to run on is a decision for whoever owns the stand, and a
    hard gate would only teach people to bypass the gate.
    """
    try:
        load1, load5, load15 = os.getloadavg()
    except (OSError, AttributeError) as exc:  # pragma: no cover - platform
        return {"available": False, "reason": f"getloadavg unavailable: {exc}"}
    cores = os.cpu_count() or 1
    return {
        "available": True,
        # The generator itself is a process, so a *quiet* host is not zero: what
        # matters is whether the measured services share these cores with
        # anything else heavy (§6: generator and services on separate hosts for
        # wide pools).
        "load1": round(load1, 2),
        "load5": round(load5, 2),
        "load15": round(load15, 2),
        "cpu_cores": cores,
        "load1_per_core": round(load1 / cores, 3),
    }


def budget_ceiling_check(
    profile: LoadProfile,
    *,
    top_rps: float,
    budgets: Mapping[str, dict[str, Any]],
    tool_calls_per_turn: float,
) -> dict[str, Any]:
    """Flag a rate limiter that will cap the ladder before the platform does.

    §5 calls ``CHAT_RATE_LIMIT`` "главный ложный потолок": 30/minute per path is
    0.5 rps, three orders of magnitude below any machine, and a knee found under
    it is a knee of slowapi. The same reasoning applies to the gateway's tool
    budget on an L1 ladder, where the harness multiplies the rung by the calls a
    turn makes.
    """
    rung_calls = top_rps * max(tool_calls_per_turn, 1.0)
    budget = budgets.get("mcp_rate_limit_rps", {})
    raw = budget.get("value")
    try:
        limit = float(str(raw).split("/")[0])
    except (TypeError, ValueError):
        return {"available": False, "reason": f"unreadable budget value: {raw!r}"}
    return {
        "available": True,
        "top_rung_rps": top_rps,
        "tool_calls_per_turn": tool_calls_per_turn,
        "expected_gateway_calls_per_second": round(rung_calls, 1),
        "mcp_rate_limit_rps": limit,
        "mcp_rate_limit_burst": budgets.get("mcp_rate_limit_burst", {}).get("value"),
        "caps_the_ladder": rung_calls > limit,
        "source": budget.get("origin"),
    }


def mcp_preflight(
    *,
    profile: LoadProfile,
    fixture: ArgumentFixture,
    transport: McpTransport,
    driver: McpToolDriver,
    tenants: Sequence[str],
    top_rps: float,
    budgets: Mapping[str, dict[str, Any]],
    probe_tenants: bool = True,
) -> PreflightResult:
    """Everything that must hold before an L1 ladder may start.

    ``probe_tenants`` runs one real turn per tenant with the fixture's own
    arguments. It is the check that caught both live fixture defects, and it is
    the only way to know that the values resolve against the *seeded* database
    rather than merely against the schema.
    """
    result = PreflightResult()

    try:
        validate_fixture_covers(profile, fixture)
        result.record("fixture_coverage", "ok", tools=len(fixture.arguments))
    except Exception as exc:  # noqa: BLE001 - refusal, not a crash
        result.refuse("fixture_coverage", str(exc))
        return result

    manifest: dict[str, list[str]] = {}
    for tenant in tenants:
        try:
            tools = transport.fetch_manifest(tenant)
        except Exception as exc:  # noqa: BLE001 - the target is unreachable
            result.refuse("manifest", f"tenant {tenant!r}: {exc}")
            continue
        manifest[tenant] = sorted(tools)
        try:
            validate_tools_against_manifest(profile, tools)
        except Exception as exc:  # noqa: BLE001
            result.refuse("profile_tools", str(exc))
    if manifest:
        result.record(
            "manifest",
            "ok",
            tenants={tenant: tools for tenant, tools in manifest.items()},
        )
    if not result.ok:
        return result

    if probe_tenants:
        plan = WorkloadPlan.from_profile(profile)
        for tenant in tenants:
            session = None
            try:
                session = driver.open_session(tenant)
                step = plan.step_for(0)
                execution = driver.execute_turn(
                    session,
                    step,
                    planned_ms=0.0,
                    started_ms=0.0,
                    tenant=tenant,
                    scenario=plan.scenario,
                )
            except Exception as exc:  # noqa: BLE001
                result.refuse("fixture_resolves", f"tenant {tenant!r}: {exc}")
                continue
            finally:
                if session is not None:
                    driver.close_session(session)
            failed = [call for call in execution.calls if call.is_error]
            if failed:
                result.refuse(
                    "fixture_resolves",
                    f"tenant {tenant!r}: tool {failed[0].tool!r} answered "
                    f"isError (http {failed[0].http_status}, "
                    f"class {failed[0].error_class})",
                )
                continue
            result.record(
                "fixture_resolves",
                "ok",
                tenant=tenant,
                step=step.name,
                calls=len(execution.calls),
                elapsed_ms=execution.record.actual_ms,
            )
    else:
        result.record(
            "fixture_resolves",
            "skipped",
            reason="tenant probing disabled by the caller",
        )

    result.record("quiet_host", "measured", **quiet_host_probe())
    result.record(
        "budget_ceiling",
        "measured",
        **budget_ceiling_check(
            profile,
            top_rps=top_rps,
            budgets=budgets,
            tool_calls_per_turn=profile.expected_calls_per_turn(),
        ),
    )
    return result


def chat_preflight(
    *,
    profile: LoadProfile,
    fixture: ArgumentFixture,
    transport: Any,
    driver: Any,
    tenants: Sequence[str],
    top_rps: float,
    budgets: Mapping[str, dict[str, Any]],
    probe_tenants: bool = True,
) -> PreflightResult:
    """Preflight for the chat layers (L2-L4): health, then one real turn.

    The turn is the only honest check that the agent the profile names exists,
    resolves a provider, and answers over SSE - and it is the check that catches
    a stand where the stub was never registered, before that reads as a knee.

    ``fixture`` is accepted so both preflights share one call shape (the CLI
    dispatches between them with the same kwargs, and a layer that refused one
    would crash before measuring anything - the drift that kept L2/L3 from ever
    running live). On the chat path the tool arguments come from the stub
    script, not the fixture, so it is deliberately not consumed here.
    """
    from urllib.request import urlopen

    result = PreflightResult()
    health_url = transport.base_url + "/health"
    try:
        with urlopen(health_url, timeout=10) as response:
            body = json.loads(response.read().decode())
        result.record("health", "ok", url=health_url, body=body)
    except Exception as exc:  # noqa: BLE001 - refusal, not a crash
        result.refuse("health", f"{health_url}: {exc}")
        return result

    plan = WorkloadPlan.from_profile(profile)
    step = plan.step_for(0)
    # Every tenant, like the L1 preflight: the fixture defect the first run
    # caught was tenant-specific, and one green tenant proves nothing about
    # the others - a probe that samples cannot refuse on behalf of the rest.
    for tenant in tenants if probe_tenants else ():
        session = None
        try:
            session = driver.open_session(tenant)
            execution = driver.execute_turn(
                session,
                step,
                planned_ms=0.0,
                started_ms=0.0,
                tenant=tenant,
                scenario=plan.scenario,
            )
        except Exception as exc:  # noqa: BLE001
            result.refuse("chat_turn", f"tenant {tenant!r}: {exc}")
            continue
        finally:
            if session is not None:
                driver.close_session(session)
        record = execution.record
        if record.is_error:
            result.refuse(
                "chat_turn",
                f"tenant {tenant!r}: turn ended in {record.sse_terminal_event} "
                f"({record.error_class})",
            )
            continue
        result.record(
            "chat_turn",
            "ok",
            tenant=tenant,
            step=step.name,
            terminal=record.sse_terminal_event,
            t_complete_ms=record.t_complete,
            ttfe_ms=record.ttfe,
            tool_calls=len(execution.calls),
        )
    if not probe_tenants:
        result.record("chat_turn", "skipped", reason="probe disabled")

    result.record("quiet_host", "measured", **quiet_host_probe())
    result.record(
        "budget_ceiling",
        "measured",
        chat_rps=top_rps,
        chat_rate_limit=budgets.get("chat_rate_limit", {}).get("value"),
        chat_rate_limit_origin=budgets.get("chat_rate_limit", {}).get("origin"),
        caps_the_ladder=_chat_limit_caps(budgets, top_rps),
        note="§5: CHAT_RATE_LIMIT is per (IP, path); 30/minute is 0.5 rps per route",
    )
    return result


def _chat_limit_caps(budgets: Mapping[str, dict[str, Any]], top_rps: float) -> bool:
    """Whether the per-path chat limiter is below the first rung (§5)."""
    raw = budgets.get("chat_rate_limit", {}).get("value")
    if not raw:
        return False
    text = str(raw)
    per_minute = text.endswith("/minute")
    try:
        number = float(text.split("/")[0])
    except ValueError:
        return False
    per_second = number / 60.0 if per_minute else number
    return top_rps > per_second


def budget_overrides_from(
    raw: Sequence[str] | None, specs: Sequence[BudgetSpec] | None = None
) -> dict[str, str]:
    """Parse ``name=value`` overrides for §5's budget table.

    The harness usually runs outside the services' environment, so the values in
    effect on the stand have to be supplied explicitly; ``collect_budgets`` then
    labels them ``origin: stand`` instead of printing a documented default next
    to a limiter that was raised (§5).
    """
    from .manifest import BUDGET_TABLE

    known = {spec.name for spec in (specs or BUDGET_TABLE)}
    overrides: dict[str, str] = {}
    for item in raw or ():
        name, sep, value = item.partition("=")
        if not sep or not name.strip():
            raise ValueError(f"budget override {item!r} is not NAME=VALUE")
        name = name.strip()
        if name not in known:
            raise ValueError(
                f"budget override {name!r} is not in the §5 table; a budget "
                "outside the table cannot be compared between runs"
            )
        overrides[name] = value.strip()
    return overrides


def budgets_for_run(
    overrides: Mapping[str, str] | None, environ: Mapping[str, str] | None = None
) -> dict[str, dict[str, Any]]:
    """§5's table with the values in effect, from the environment and overrides."""
    return collect_budgets(environ or os.environ, overrides=overrides)


__all__ = [
    "PreflightResult",
    "budget_ceiling_check",
    "budget_overrides_from",
    "budgets_for_run",
    "mcp_preflight",
    "quiet_host_probe",
]
