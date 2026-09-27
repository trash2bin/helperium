"""Declarative load profile for the stress harness (doc/stress/README.md §3).

The profile is a contract, not a suggestion. It names the tools and the shape of
the load; the harness either runs it as written or refuses to start. There is no
silent adaptation: a tool that is absent from the live MCP manifest is a hard
refusal that names the nearest known tool (§3). An adaptive harness would make
two runs of "the same profile" measure different workloads.

The snippet in §3 is jsonc for readability; on disk profiles are plain JSON,
like ``agent_db/bench/cases/autoparts.json``.
"""

from __future__ import annotations

import difflib
import json
import math
from pathlib import Path
from typing import Any, Iterable, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .constants import (
    AGENT_MAX_TOOL_CALLS,
    DEMO_HISTORY_TURNS,
    MAX_USER_TURNS_PER_SESSION,
    SERVER_MIN_INTERVAL_MS,
)

PROFILE_VERSION = 3

# Composite scope prefixes every tool with its tenant: ``{tenant}__db_get``.
COMPOSITE_SEPARATOR = "__"

# L0 (control-plane: /health, /metrics, tenant-id validation) is driven by
# raw HTTP, not by MCP tools, so it has no profile shape yet; it joins with the
# Driver protocol from §11.
SUPPORTED_LAYERS = ("L1", "L2", "L3", "L4")


class ProfileValidationError(ValueError):
    """A profile cannot be run as written."""


def _suggest(name: str, known: Iterable[str]) -> str:
    close = difflib.get_close_matches(name, list(known), n=1, cutoff=0.6)
    return f" Did you mean {close[0]!r}?" if close else ""


class TenantsSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    count: int = Field(ge=1)
    distribution: Literal["round_robin", "shuffle"] = "round_robin"
    scope: Literal["separate", "composite"] = "separate"


class SessionsSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pool_size: int = Field(ge=1)
    # Below the per-session user-turn quota, otherwise the tail of the run is
    # measured against the abuse gate instead of the platform.
    recycle_after_turns: int = Field(gt=0, lt=MAX_USER_TURNS_PER_SESSION)
    recycle_after_seconds: int = Field(gt=0)
    # Above the server's minimum interval, for the same reason.
    min_interval_ms: int = Field(gt=SERVER_MIN_INTERVAL_MS)


class ArrivalSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: Literal["constant"] = "constant"
    rps: float = Field(gt=0)
    duration_s: int = Field(gt=0)
    warmup_s: int = Field(ge=0)

    @model_validator(mode="after")
    def _warmup_fits_inside_the_stage(self) -> ArrivalSpec:
        if self.warmup_s >= self.duration_s:
            raise ValueError("warmup_s must be shorter than duration_s")
        return self


class ClientsSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_ips: int = Field(ge=1)  # >1 needs macvlan/IP aliases (§5)
    generators: int = Field(ge=1)


class LlmSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    substrate: Literal["stub", "scripted", "local"]
    provider_concurrency: int = Field(ge=1)
    p50_ms: int = Field(gt=0)
    p95_ms: int = Field(gt=0)

    @model_validator(mode="after")
    def _p95_is_not_below_p50(self) -> LlmSpec:
        if self.p95_ms < self.p50_ms:
            raise ValueError("p95_ms must be >= p50_ms")
        return self


class WorkloadStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    weight: float = Field(gt=0, le=1)
    name: str = Field(min_length=1)
    tools: list[str] = Field(min_length=1)
    history_turns: int = Field(ge=0, le=DEMO_HISTORY_TURNS)
    repeat: dict[str, int] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _tool_call_budget_stays_under_the_terminator(self) -> WorkloadStep:
        total = len(self.tools) + sum(self.repeat.values())
        if total >= AGENT_MAX_TOOL_CALLS:
            raise ValueError(
                f"workload {self.name!r} budgets {total} tool calls; the server "
                f"terminates a turn at {AGENT_MAX_TOOL_CALLS}, so the declared "
                "load would be truncated"
            )
        # ``repeat`` adds calls on top of ``tools`` (it is not restricted to
        # them: §3 repeats db_get in a step that opens with db_map/db_search),
        # but every repeated name is still a tool the turn will call, so it is
        # checked against the manifest together with the rest.
        for name, count in self.repeat.items():
            if count < 1:
                raise ValueError(
                    f"workload {self.name!r} repeats {name!r} {count} times"
                )
        return self

    def planned_calls(self) -> int:
        """Tool calls one turn of this step is expected to make."""
        return len(self.tools) + sum(self.repeat.values())


class LoadProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_version: Literal[3]
    name: str = Field(min_length=1)
    target_layer: Literal["L1", "L2", "L3", "L4"]
    # Argument values live in a fixture, not here and not in the manifest: this
    # endpoint publishes no argument metadata (verified live), and one fixture
    # is shared by several profiles. See ``stress.fixture``.
    fixture: str = Field(min_length=1)
    tenants: TenantsSpec
    sessions: SessionsSpec
    arrival: ArrivalSpec
    clients: ClientsSpec
    # L0/L1 never touch the LLM (a tool call goes gateway -> data-service -> DB),
    # so a substrate there would advertise a dependency the stage does not have.
    llm: LlmSpec | None = None
    workload: list[WorkloadStep] = Field(min_length=1)
    budget_preset: Literal["off", "on"]

    @model_validator(mode="after")
    def _substrate_matches_the_layer(self) -> LoadProfile:
        needs_llm = self.target_layer in {"L2", "L3", "L4"}
        if needs_llm and self.llm is None:
            raise ValueError(f"target_layer {self.target_layer} requires an llm block")
        if not needs_llm and self.llm is not None:
            raise ValueError(
                f"target_layer {self.target_layer} does not call the LLM; remove "
                "the llm block so the stage cannot be mistaken for an LLM one"
            )
        return self

    @model_validator(mode="after")
    def _weights_form_a_distribution(self) -> LoadProfile:
        total = sum(step.weight for step in self.workload)
        if abs(total - 1.0) > 1e-6:
            raise ValueError(
                f"workload weights must sum to 1.0, got {total:.6f}: the mix is "
                "what makes two runs comparable"
            )
        return self

    def tool_names(self) -> list[str]:
        """Every tool the profile calls, in declaration order, deduplicated."""
        seen: dict[str, None] = {}
        for step in self.workload:
            for name in (*step.tools, *step.repeat):
                seen.setdefault(name, None)
        return list(seen)

    def expected_calls_per_turn(self) -> float:
        """Weighted mean of planned tool calls per turn."""
        return sum(step.weight * step.planned_calls() for step in self.workload)

    def required_pool_size(
        self, t_budget_s: float, margin_s: float, headroom: float
    ) -> int:
        """Pool floor from the criterion, not from observed p95 (§3).

        Sizing the pool from the previous stage's observed p95 makes the pool
        grow with the very degradation the ladder is looking for.
        """
        return math.ceil(self.arrival.rps * (t_budget_s + margin_s) * headroom)


def load_profile(path: str | Path) -> LoadProfile:
    """Read a profile from disk, reporting validation errors as refusals."""
    raw: Any = json.loads(Path(path).read_text(encoding="utf-8"))
    try:
        return LoadProfile.model_validate(raw)
    except ValidationError as exc:
        raise ProfileValidationError(f"{path}: {exc}") from exc


def canonical_tool_name(name: str) -> str:
    """Strip the composite tenant prefix, if any (``{tenant}__db_get``)."""
    head, sep, tail = name.partition(COMPOSITE_SEPARATOR)
    return tail if sep and head and tail else name


def validate_tools_against_manifest(
    profile: LoadProfile, available_tools: Iterable[str]
) -> None:
    """Refuse a profile that names a tool the live manifest does not expose.

    A missing tool is a profile error, not something to work around (§3): the
    harness runs the declared workload or it does not run at all.
    """
    known = {canonical_tool_name(name) for name in available_tools}
    if profile.tenants.scope == "composite":
        prefixed = {name for name in available_tools if COMPOSITE_SEPARATOR in name}
        if not prefixed:
            raise ProfileValidationError(
                "profile scope is composite but the manifest exposes no "
                f"{COMPOSITE_SEPARATOR!r}-prefixed tools"
            )
    missing = [
        name for name in profile.tool_names() if canonical_tool_name(name) not in known
    ]
    if missing:
        details = "; ".join(f"{name}:{_suggest(name, known)}" for name in missing)
        raise ProfileValidationError(
            f"profile {profile.name!r} names {len(missing)} tool(s) absent from "
            f"the live manifest ({details})"
        )
