"""Contract tests for the load profile schema (doc/stress/README.md §3).

The profile is a refusal machine: every rule here exists because violating it
would make a run measure something other than what the report claims.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_db.stress import (
    LoadProfile,
    ProfileValidationError,
    canonical_tool_name,
    load_profile,
    validate_tools_against_manifest,
)

PROFILES_DIR = Path(__file__).resolve().parents[1] / "agent_db" / "stress" / "profiles"

BASE = {
    "profile_version": 3,
    "name": "test-profile",
    "target_layer": "L3",
    "fixture": "sqlite-testseed",
    "tenants": {"count": 2, "distribution": "round_robin", "scope": "separate"},
    "sessions": {
        "pool_size": 16,
        "recycle_after_turns": 40,
        "recycle_after_seconds": 1800,
        "min_interval_ms": 1200,
    },
    "arrival": {"model": "constant", "rps": 20, "duration_s": 600, "warmup_s": 120},
    "clients": {"source_ips": 1, "generators": 1},
    "llm": {
        "substrate": "stub",
        "provider_concurrency": 256,
        "p50_ms": 800,
        "p95_ms": 2500,
    },
    "workload": [
        {"weight": 1.0, "name": "one_tool", "tools": ["db_get"], "history_turns": 0}
    ],
    "budget_preset": "off",
}


def _profile(**overrides) -> LoadProfile:
    payload = json.loads(json.dumps(BASE))
    payload.update(overrides)
    return LoadProfile.model_validate(payload)


class TestShippedProfiles:
    @pytest.mark.parametrize("name", ["chat-mixed-v3.json", "mcp-tool-call-l1.json"])
    def test_shipped_profiles_load(self, name: str) -> None:
        profile = load_profile(PROFILES_DIR / name)
        assert profile.profile_version == 3

    def test_l1_profile_declares_no_substrate(self) -> None:
        profile = load_profile(PROFILES_DIR / "mcp-tool-call-l1.json")
        assert profile.llm is None
        # L1 is the layer with no LLM on the path, so the report must not be
        # able to attribute its latency budget to a provider.
        assert profile.target_layer == "L1"

    def test_chat_profile_mix_keeps_the_heavy_step_under_the_terminator(self) -> None:
        profile = load_profile(PROFILES_DIR / "chat-mixed-v3.json")
        heavy = next(step for step in profile.workload if step.name == "heavy")
        assert heavy.planned_calls() == 8
        assert profile.expected_calls_per_turn() == pytest.approx(2.8)


class TestSchemaRefusals:
    def test_weights_must_sum_to_one(self) -> None:
        with pytest.raises(Exception, match="must sum to 1.0"):
            _profile(
                workload=[
                    {
                        "weight": 0.5,
                        "name": "a",
                        "tools": ["db_get"],
                        "history_turns": 0,
                    }
                ]
            )

    def test_workload_budget_at_the_terminator_is_refused(self) -> None:
        # 2 + 8 = 10 calls: the server terminates the turn exactly here, so the
        # declared load would arrive truncated.
        with pytest.raises(Exception, match="terminates a turn at 10"):
            _profile(
                workload=[
                    {
                        "weight": 1.0,
                        "name": "heavy",
                        "tools": ["db_map", "db_search"],
                        "repeat": {"db_get": 8},
                        "history_turns": 0,
                    }
                ]
            )

    def test_repeat_keys_are_tools_and_are_checked_against_the_manifest(self) -> None:
        # §3 repeats db_get in a step that opens with db_map/db_search, so
        # ``repeat`` is not restricted to ``tools``; but it is still a tool the
        # turn calls, so a typo there has to be refused as well.
        profile = _profile(
            workload=[
                {
                    "weight": 1.0,
                    "name": "heavy",
                    "tools": ["db_map"],
                    "repeat": {"db_ge": 2},
                    "history_turns": 0,
                }
            ]
        )
        with pytest.raises(ProfileValidationError) as excinfo:
            validate_tools_against_manifest(profile, ["db_map", "db_get"])
        assert "db_ge" in str(excinfo.value)

    def test_repeat_count_must_be_positive(self) -> None:
        with pytest.raises(Exception, match="repeats"):
            _profile(
                workload=[
                    {
                        "weight": 1.0,
                        "name": "heavy",
                        "tools": ["db_get"],
                        "repeat": {"db_search": 0},
                        "history_turns": 0,
                    }
                ]
            )

    def test_history_longer_than_the_server_keeps_is_refused(self) -> None:
        with pytest.raises(Exception):
            _profile(
                workload=[
                    {
                        "weight": 1.0,
                        "name": "a",
                        "tools": ["db_get"],
                        "history_turns": 9,
                    }
                ]
            )

    def test_session_interval_must_exceed_the_server_abuse_gate(self) -> None:
        payload = json.loads(json.dumps(BASE))
        payload["sessions"]["min_interval_ms"] = 1000
        with pytest.raises(Exception, match="greater than 1000"):
            LoadProfile.model_validate(payload)

    def test_recycling_must_stay_under_the_turn_quota(self) -> None:
        payload = json.loads(json.dumps(BASE))
        payload["sessions"]["recycle_after_turns"] = 50
        with pytest.raises(Exception):
            LoadProfile.model_validate(payload)

    def test_warmup_must_be_shorter_than_the_stage(self) -> None:
        payload = json.loads(json.dumps(BASE))
        payload["arrival"]["warmup_s"] = 600
        with pytest.raises(Exception, match="shorter than duration_s"):
            LoadProfile.model_validate(payload)

    def test_p95_cannot_be_below_p50(self) -> None:
        payload = json.loads(json.dumps(BASE))
        payload["llm"]["p50_ms"] = 3000
        with pytest.raises(Exception, match="p95_ms must be >= p50_ms"):
            LoadProfile.model_validate(payload)

    def test_unknown_field_is_a_typo_not_a_comment(self) -> None:
        payload = json.loads(json.dumps(BASE))
        payload["budgetPreset"] = "off"
        with pytest.raises(Exception):
            LoadProfile.model_validate(payload)

    def test_l1_with_a_substrate_is_refused(self) -> None:
        with pytest.raises(Exception, match="does not call the LLM"):
            _profile(target_layer="L1")

    def test_l0_is_not_expressible_yet(self) -> None:
        # L0 is control-plane traffic (/health, /metrics, tenant-id validation),
        # not MCP tools, so it has no profile shape until the raw_http driver
        # exists. Accepting an L0 profile today would let it silently request
        # tool calls that do not exist on that layer.
        with pytest.raises(Exception):
            _profile(target_layer="L0")

    def test_fixture_name_is_required(self) -> None:
        with pytest.raises(Exception):
            _profile(fixture=None)

    def test_l3_without_a_substrate_is_refused(self) -> None:
        with pytest.raises(Exception, match="requires an llm block"):
            _profile(llm=None)


class TestPoolSizing:
    def test_pool_floor_comes_from_the_criterion_not_from_p95(self) -> None:
        profile = _profile()
        # 20 rps x (3 s budget + 1 s margin) x 2 headroom = 160 slots.
        assert (
            profile.required_pool_size(t_budget_s=3.0, margin_s=1.0, headroom=2.0)
            == 160
        )


class TestToolValidation:
    def test_manifest_mismatch_refuses_and_suggests(self) -> None:
        profile = _profile(
            workload=[
                {"weight": 1.0, "name": "typo", "tools": ["db_ge"], "history_turns": 0}
            ]
        )
        with pytest.raises(ProfileValidationError) as excinfo:
            validate_tools_against_manifest(profile, ["db_get", "db_search"])
        assert "db_ge" in str(excinfo.value)
        assert "db_get" in str(excinfo.value)

    def test_matching_manifest_passes(self) -> None:
        profile = _profile()
        validate_tools_against_manifest(profile, ["db_map", "db_get", "db_search"])

    def test_manifest_missing_the_preload_tool_is_refused(self) -> None:
        # The turn prefetches the schema before the profile's calls, so the
        # preload has to exist in the live manifest even though no profile names
        # it. Otherwise the first turn fails instead of the preflight.
        profile = _profile()
        with pytest.raises(ProfileValidationError, match="db_map"):
            validate_tools_against_manifest(profile, ["db_get", "db_search"])

    def test_composite_prefixes_are_stripped_before_comparison(self) -> None:
        assert canonical_tool_name("tenant-a__db_get") == "db_get"
        assert canonical_tool_name("db_get") == "db_get"

    def test_composite_scope_requires_prefixed_manifest_tools(self) -> None:
        payload = json.loads(json.dumps(BASE))
        payload["tenants"]["scope"] = "composite"
        profile = LoadProfile.model_validate(payload)
        with pytest.raises(ProfileValidationError, match="composite"):
            validate_tools_against_manifest(profile, ["db_get"])

    def test_composite_scoped_manifest_passes(self) -> None:
        payload = json.loads(json.dumps(BASE))
        payload["tenants"]["scope"] = "composite"
        profile = LoadProfile.model_validate(payload)
        validate_tools_against_manifest(
            profile, ["tenant-a__db_get", "tenant-b__db_get", "tenant-a__db_map"]
        )


def test_load_profile_reports_a_path_on_invalid_json(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"profile_version": 9}), encoding="utf-8")
    with pytest.raises(ProfileValidationError, match="bad.json"):
        load_profile(bad)
