"""Contract tests for argument fixtures (doc/stress/README.md §3).

The manifest publishes no argument metadata, so the fixture is the only source
of tool arguments. These tests pin the facts that were verified live on
2026-09-27: parameter names come from the tenant config, values must reference
rows that exist in the seeded scenario, and a profile without fixture coverage
must be refused rather than run into an all-error stage.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_db.stress import (
    ArgumentFixture,
    FixtureValidationError,
    LoadProfile,
    effective_fixture,
    load_fixture,
    load_profile,
    validate_fixture_covers,
)

STRESS_DIR = Path(__file__).resolve().parents[1] / "agent_db" / "stress"
PROFILES = STRESS_DIR / "profiles"
FIXTURES = STRESS_DIR / "fixtures"
SCENARIOS = (
    Path(__file__).resolve().parents[2] / "data-service" / "testdata" / "scenarios"
)

BASE_PROFILE = {
    "profile_version": 3,
    "name": "fixture-test",
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
        {"weight": 1.0, "name": "one_tool", "tools": ["db_map"], "history_turns": 0}
    ],
    "budget_preset": "off",
}


def _profile(tools: list[str]) -> LoadProfile:
    payload = json.loads(json.dumps(BASE_PROFILE))
    payload["workload"] = [
        {"weight": 1.0, "name": "step", "tools": tools, "history_turns": 0}
    ]
    return LoadProfile.model_validate(payload)


class TestLoadFixture:
    def test_shipped_fixture_loads_by_name_and_by_path(self) -> None:
        by_name = load_fixture("sqlite-testseed")
        by_path = load_fixture(FIXTURES / "sqlite-testseed.json")
        assert by_name.name == "sqlite-testseed"
        assert by_name.scenario == "sqlite-testseed"
        assert by_name == by_path

    def test_unknown_fixture_lists_what_exists(self) -> None:
        with pytest.raises(FixtureValidationError) as excinfo:
            load_fixture("no-such-fixture")
        assert "sqlite-testseed" in str(excinfo.value)

    def test_unknown_field_is_refused(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.json"
        bad.write_text(
            json.dumps(
                {
                    "fixture_version": 1,
                    "name": "bad",
                    "scenario": "sqlite-testseed",
                    "arguments": {"db_map": {}},
                    "scenarios": "sqlite-testseed",
                }
            ),
            encoding="utf-8",
        )
        with pytest.raises(FixtureValidationError):
            load_fixture(bad)

    def test_wrong_version_is_refused(self, tmp_path: Path) -> None:
        bad = tmp_path / "v9.json"
        bad.write_text(
            json.dumps(
                {
                    "fixture_version": 9,
                    "name": "v9",
                    "scenario": "sqlite-testseed",
                    "arguments": {"db_map": {}},
                }
            ),
            encoding="utf-8",
        )
        with pytest.raises(FixtureValidationError, match="fixture_version"):
            load_fixture(bad)

    def test_empty_argument_map_is_refused(self, tmp_path: Path) -> None:
        bad = tmp_path / "empty.json"
        bad.write_text(
            json.dumps(
                {
                    "fixture_version": 1,
                    "name": "empty",
                    "scenario": "sqlite-testseed",
                    "arguments": {},
                }
            ),
            encoding="utf-8",
        )
        with pytest.raises(FixtureValidationError):
            load_fixture(bad)


class TestCoverage:
    @pytest.mark.parametrize(
        "profile_file", sorted(p.name for p in PROFILES.glob("*.json"))
    )
    def test_every_shipped_profile_is_covered_by_its_fixture(
        self, profile_file: str
    ) -> None:
        profile = load_profile(PROFILES / profile_file)
        validate_fixture_covers(profile, load_fixture(profile.fixture))

    def test_profile_without_fixture_coverage_is_refused(self) -> None:
        # db_related has no arguments in the sqlite-testseed fixture: that
        # scenario exposes no relations (all entities report relations: null),
        # so a profile calling it would run into a permanent isError stream.
        profile = _profile(["db_related"])
        with pytest.raises(FixtureValidationError) as excinfo:
            validate_fixture_covers(profile, load_fixture("sqlite-testseed"))
        assert "db_related" in str(excinfo.value)

    def test_extra_fixture_entries_are_allowed(self) -> None:
        # One fixture is shared by several profiles and may carry arguments for
        # tools a given profile never calls.
        validate_fixture_covers(_profile(["db_map"]), load_fixture("sqlite-testseed"))

    def test_fixture_without_the_preload_tool_is_refused(self) -> None:
        # Every turn prefetches the schema before the profile's calls (§3), so a
        # fixture that feeds only the declared tools still cannot run a stage:
        # the preload call would have no arguments at all.
        fixture = ArgumentFixture.model_validate(
            {
                "fixture_version": 1,
                "name": "no-preload",
                "scenario": "sqlite-testseed",
                "arguments": {"db_get": {"entity": "group", "id": "g1"}},
            }
        )
        with pytest.raises(FixtureValidationError, match="db_map"):
            validate_fixture_covers(_profile(["db_get"]), fixture)

    def test_effective_fixture_starts_with_the_preload(self) -> None:
        # The order matches what a turn does, so a caller can zip the two.
        effective = effective_fixture(
            load_fixture("sqlite-testseed"), _profile(["db_get"])
        )
        assert list(effective) == ["db_map", "db_get"]

    def test_effective_fixture_keeps_the_profile_tool_order(self) -> None:
        profile = _profile(["db_search", "db_get"])
        effective = effective_fixture(load_fixture("sqlite-testseed"), profile)
        assert list(effective) == ["db_map", "db_search", "db_get"]
        assert effective["db_search"]["pattern"] == "Петров"


class TestFixturesMatchReality:
    def test_every_fixture_names_a_scenario_that_exists(self) -> None:
        for path in sorted(FIXTURES.glob("*.json")):
            fixture = load_fixture(path)
            scenario_dir = SCENARIOS / fixture.scenario
            assert (scenario_dir / "config.json").exists(), (
                f"{path.name} references scenario {fixture.scenario!r} which has "
                f"no config.json under {SCENARIOS}"
            )

    def test_arguments_use_parameters_the_tools_actually_declare(self) -> None:
        # Parameter names verified live: db_search takes `pattern` (not
        # `query`) and db_filter takes field names as top-level parameters, not
        # wrapped in a `filters` object. A fixture drifting from that measures
        # the error path instead of the platform.
        fixture = load_fixture("sqlite-testseed")
        assert set(fixture.arguments["db_search"]) == {"entity", "pattern", "limit"}
        assert set(fixture.arguments["db_filter"]) == {"entity", "name", "limit"}
        assert set(fixture.arguments["db_get"]) == {"entity", "id"}
