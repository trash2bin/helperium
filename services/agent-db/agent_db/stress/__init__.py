"""Stress/capacity harness for Helperium (design: doc/stress/README.md).

Status: phase 1 in progress. The contract layer (profile schema, argument
fixtures, raw request record, percentile calculator) and the L1 driver with its
MCP transport are written. The scheduler, session pool, substrate and report are
not, and no number produced from this package is publishable until they are.
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
from .profile import (
    LoadProfile,
    ProfileValidationError,
    canonical_tool_name,
    load_profile,
    validate_tools_against_manifest,
)
from .records import (
    ErrorClass,
    RawRequestRecord,
    StageStats,
    percentile,
    summarise,
    write_raw_records,
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
    "PRELOAD_TOOL",
    "ArgumentFixture",
    "DriverConfigurationError",
    "ErrorClass",
    "FixtureValidationError",
    "LoadProfile",
    "McpCallOutcome",
    "McpSession",
    "McpToolDriver",
    "McpTransport",
    "ProfileValidationError",
    "RawRequestRecord",
    "StageStats",
    "ToolCallTiming",
    "TransportError",
    "TransportStats",
    "TurnExecution",
    "canonical_tool_name",
    "effective_fixture",
    "load_fixture",
    "load_profile",
    "parse_mcp_response",
    "percentile",
    "plan_calls",
    "summarise",
    "validate_fixture_covers",
    "validate_tools_against_manifest",
    "write_raw_records",
]
