"""Server-side limits the load profile has to respect.

These are duplicated on purpose: ``agent-db`` does not depend on
``api-service`` or on ``helperium-sdk``, and the harness must refuse to start on
a profile that the server would silently truncate (doc/stress/README.md §3, §5).

Single source of truth per value is named next to it. If a server default moves,
this file has to move with it; phase 7 adds a preflight that reads the live
effective limits instead of trusting the copy.
"""

from __future__ import annotations

# helperium_sdk/settings.py: AGENT_MAX_TOOL_CALLS default.
# A turn is terminated once tool_calls reaches this, so a workload step that
# budgets more calls measures a truncated turn rather than the declared load.
AGENT_MAX_TOOL_CALLS = 10

# helperium_sdk/settings.py: DEMO_HISTORY_TURNS default. History is trimmed to
# this many turns, so a profile cannot ask for a longer context than exists.
DEMO_HISTORY_TURNS = 8

# api-service anti-abuse defaults (.env.example: ABUSE_MAX_USER_TURNS).
# Exceeding it is a quality refusal (HTTP 200 + SSE error), not a 429.
MAX_USER_TURNS_PER_SESSION = 50

# api-service anti-abuse defaults (.env.example: ABUSE_MIN_INTERVAL_MS).
# A generator faster than this per session meets the abuse gate, not the
# platform: the run would measure our own limiter instead of the SUT.
SERVER_MIN_INTERVAL_MS = 1000

# The schema preload every turn makes before the first model call. Fixed by the
# platform, not by a profile: orchestrator.py calls ``mcp.call_tool("db_map", {})``
# and pastes the result into the transcript as an authoritative system message.
# It sits outside the loop's tool-call budget (the call happens before the loop
# starts), so it adds a call to a turn without consuming AGENT_MAX_TOOL_CALLS - and
# it is part of the fixed per-turn prefix in the analytic knee model (§3).
PRELOAD_TOOL = "db_map"
