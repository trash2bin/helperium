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

from .chat_driver import ChatDriver, DEFAULT_TURN_TIMEOUT_S
from .chat_transport import (
    ChatFrame,
    ChatSession,
    ChatTransport,
    ChatTransportError,
    classify_chat_status,
)
from .constants import PRELOAD_TOOL
from .driver import (
    DriverConfigurationError,
    McpToolDriver,
    ToolCallTiming,
    TurnExecution,
    plan_calls,
)
from .evidence import (
    RunLayout,
    StageEvidenceSink,
    read_raw_records,
    stage_summary,
    write_calls,
)
from .guard import (
    TERMINAL_STATUSES,
    StressRunContext,
    StressRunGuard,
    StressRunInProgressError,
    write_json_atomic,
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
    JUDGED_METRIC_READY_LAYERS,
    LAYER_RULE,
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
from .preflight import (
    PreflightResult,
    chat_preflight,
    budget_overrides_from,
    budgets_for_run,
    mcp_preflight,
    quiet_host_probe,
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
    join_stub_timings,
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
    "JUDGED_METRIC_READY_LAYERS",
    "DEFAULT_TURN_TIMEOUT_S",
    "LAG_INVALID_MS",
    "LAYER_RULE",
    "LAYER_T_BUDGET_MS",
    "PRELOAD_TOOL",
    "TERMINAL_STATUSES",
    "ArgumentFixture",
    "ChatDriver",
    "ChatFrame",
    "ChatSession",
    "ChatTransport",
    "ChatTransportError",
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
    "PreflightResult",
    "ProfileValidationError",
    "RawRequestRecord",
    "RunLayout",
    "RunManifest",
    "StageEvidenceSink",
    "StageOutcome",
    "StageResult",
    "StageRunner",
    "StageSpec",
    "StageStats",
    "StressRunContext",
    "StressRunGuard",
    "StressRunInProgressError",
    "ToolCallTiming",
    "TransportError",
    "TransportStats",
    "TurnExecution",
    "WorkloadPlan",
    "build_report",
    "chat_preflight",
    "classify_chat_status",
    "budget_overrides_from",
    "budgets_for_run",
    "canonical_tool_name",
    "collect_binaries",
    "collect_budgets",
    "collect_code",
    "collect_environment",
    "effective_fixture",
    "interpretation_notes",
    "load_fixture",
    "load_profile",
    "mcp_preflight",
    "parse_mcp_response",
    "percentile",
    "plan_calls",
    "predict_tenant_ceiling_rps",
    "quiet_host_probe",
    "read_raw_records",
    "render_markdown",
    "run_ladder",
    "stage_summary",
    "join_stub_timings",
    "summarise",
    "validate_fixture_covers",
    "validate_tools_against_manifest",
    "write_calls",
    "write_json_atomic",
    "write_raw_records",
    "write_report",
]
