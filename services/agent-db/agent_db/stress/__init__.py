"""Stress/capacity harness for Helperium (design: doc/stress/README.md).

Status: phase 1 in progress. The contract layer (profile schema, argument
fixtures, raw request record, percentile calculator), the L1 driver with its MCP
transport, the open-loop stage scheduler with its lag accounting, the ladder with
its pool expansion and bisection, and the evidence manifest and report are
written. The session pool semantics, the LLM substrate and the CI smoke test are
not, so a ladder can be run end to end but its numbers are only as good as the
environment manifest says they are.
"""

from __future__ import annotations

from .constants import PRELOAD_TOOL
from .driver import (
    DriverConfigurationError,
    McpToolDriver,
    ToolCallTiming,
    TurnExecution,
    plan_calls,
)
from .fixture import (
    ArgumentFixture,
    FixtureValidationError,
    effective_fixture,
    load_fixture,
    validate_fixture_covers,
)
from .ladder import (
    DEFAULT_RATES,
    LAYER_T_BUDGET_MS,
    KneeEstimate,
    LadderError,
    LadderPlan,
    LadderResult,
    Prediction,
    StageOutcome,
    predict_tenant_ceiling_rps,
    run_ladder,
)
from .manifest import (
    BinaryInfo,
    BinarySpec,
    CodeInfo,
    EnvironmentInfo,
    ManifestError,
    RunManifest,
    collect_binaries,
    collect_budgets,
    collect_code,
    collect_environment,
)
from .profile import (
    LoadProfile,
    ProfileValidationError,
    canonical_tool_name,
    load_profile,
    validate_tools_against_manifest,
)
from .report import (
    COLUMNS,
    build_report,
    interpretation_notes,
    render_markdown,
    write_report,
)
from .records import (
    ErrorClass,
    RawRequestRecord,
    StageStats,
    percentile,
    summarise,
    write_raw_records,
)
from .runner import (
    GENERATOR_CPU_LIMIT,
    LAG_INVALID_MS,
    StageResult,
    StageRunner,
    StageSpec,
    WorkloadPlan,
)
from .transport import (
    McpCallOutcome,
    McpSession,
    McpTransport,
    TransportError,
    TransportStats,
    parse_mcp_response,
)

__all__ = [
    "COLUMNS",
    "DEFAULT_RATES",
    "GENERATOR_CPU_LIMIT",
    "LAG_INVALID_MS",
    "LAYER_T_BUDGET_MS",
    "PRELOAD_TOOL",
    "ArgumentFixture",
    "BinaryInfo",
    "BinarySpec",
    "CodeInfo",
    "DriverConfigurationError",
    "EnvironmentInfo",
    "ErrorClass",
    "FixtureValidationError",
    "KneeEstimate",
    "LadderError",
    "LadderPlan",
    "LadderResult",
    "LoadProfile",
    "ManifestError",
    "McpCallOutcome",
    "McpSession",
    "McpToolDriver",
    "McpTransport",
    "Prediction",
    "ProfileValidationError",
    "RawRequestRecord",
    "RunManifest",
    "StageOutcome",
    "StageResult",
    "StageRunner",
    "StageSpec",
    "StageStats",
    "ToolCallTiming",
    "TransportError",
    "TransportStats",
    "TurnExecution",
    "WorkloadPlan",
    "build_report",
    "canonical_tool_name",
    "collect_binaries",
    "collect_budgets",
    "collect_code",
    "collect_environment",
    "effective_fixture",
    "interpretation_notes",
    "load_fixture",
    "load_profile",
    "parse_mcp_response",
    "percentile",
    "plan_calls",
    "predict_tenant_ceiling_rps",
    "render_markdown",
    "run_ladder",
    "summarise",
    "validate_fixture_covers",
    "validate_tools_against_manifest",
    "write_raw_records",
    "write_report",
]
