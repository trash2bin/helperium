"""Tests for the report (§10): fixed columns, honest blanks, stated caveats."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from agent_db.stress.ladder import LadderPlan, predict_tenant_ceiling_rps, run_ladder
from agent_db.stress.manifest import CodeInfo, RunManifest, collect_environment
from agent_db.stress.profile import load_profile
from agent_db.stress.report import (
    COLUMNS,
    HEADERS,
    build_report,
    interpretation_notes,
    render_markdown,
    stage_row,
    unavailable_reasons,
    write_report,
)
from agent_db.stress.runner import WorkloadPlan

from tests.test_stress_ladder import FakeRunner, make_result

STRESS_DIR = Path(__file__).resolve().parents[1] / "agent_db" / "stress"
L1_PROFILE = STRESS_DIR / "profiles" / "mcp-tool-call-l1.json"


def code(dirty: bool = False) -> CodeInfo:
    return CodeInfo(
        commit="abcdef0123456789" * 2 + "abcdef01",
        branch="main",
        dirty=dirty,
        api_workers=1,
        image_digests={},
    )


@pytest.fixture
def profile():
    return load_profile(L1_PROFILE)


@pytest.fixture
def manifest(profile):
    return RunManifest(
        environment=collect_environment(profile, probe_host=False), code=code()
    )


def ladder_result(profile, *, passing_rps: float = 25.0, repeats: int = 1):
    return run_ladder(
        runner=FakeRunner(passing_rps=passing_rps),
        plan=LadderPlan(
            rates=(5.0, 10.0, 20.0, 40.0),
            t_budget_ms=50.0,
            duration_s=2.0,
            warmup_s=0.5,
            repeats=repeats,
        ),
        workload=WorkloadPlan.from_profile(profile),
        profile=profile,
        tenants=["t-1"],
        prediction=predict_tenant_ceiling_rps(
            p95_tool_ms=5.0, calls_per_turn=2.0, tenants=4
        ),
    )


def report(profile, manifest, **kwargs):
    return build_report(
        result=ladder_result(profile, **kwargs),
        manifest=manifest,
        scope="separate",
        profile_path=str(L1_PROFILE),
    )


class TestColumns:
    def test_the_row_carries_every_fixed_column(self, profile, manifest):
        built = report(profile, manifest)
        for row in built["rows"]:
            assert set(row) == set(COLUMNS)

    def test_the_column_order_is_declared_for_readers(self, profile, manifest):
        assert report(profile, manifest)["columns"] == list(COLUMNS)

    def test_facts_land_in_the_right_columns(self, profile, manifest):
        row = report(profile, manifest)["rows"][0]
        assert row["profile"] == "mcp-tool-call-l1"
        assert row["scope"] == "separate"
        assert row["target_rps"] == 5.0
        # 10 ticks over a 2 s stage, 2 of them warm-up, so 8 measured turns in the
        # 1.5 s measured window: integer ticks cannot land exactly on 5.0 rps.
        assert row["achieved_rps"] == pytest.approx(5.0, rel=0.1)
        assert row["p95"] == 5.0
        assert row["verdict"] == "pass"
        assert row["environment_hash"] == manifest.environment_hash(short=True)
        assert row["generator_cpu"] == 1.5
        # Per stage from the stage's own stats: on L1 that is the compensated
        # turn latency, the metric §7 judges.
        assert row["platform_overhead_p95"] == row["p95"]
        assert row["pool_expansions"] == 0
        assert row["dropped_ticks"] == 0

    def test_on_l1_platform_overhead_is_the_turn_latency_itself(
        self, profile, manifest
    ):
        # No LLM on this layer, so subtracting nothing is the whole computation.
        # The layer rule asserts it by name instead of any caller passing it in.
        row = stage_row(
            ladder_result(profile).stages[0],
            profile="p",
            scope="separate",
            environment_hash="hash",
            layer="L1",
        )
        assert row["platform_overhead_p95"] == row["p95"]
        assert row["platform_overhead_p95"] is not None

    def test_on_l2_the_identity_is_allowed_too(self, profile):
        # §7 explicitly lets t_complete judge L2, where the stub is instant.
        stage = ladder_result(profile).stages[0]
        row = stage_row(
            stage, profile="p", scope="separate", environment_hash="hash", layer="L2"
        )
        assert row["platform_overhead_p95"] == row["p95"]

    def test_a_stage_that_supplied_llm_timings_fills_the_column_per_stage(
        self, profile
    ):
        # The blocker this replaces: one run-wide overhead value stamped into
        # every row, including rows whose overhead was larger.
        stage = ladder_result(profile).stages[0]
        with_timings = replace(
            stage, stats=replace(stage.stats, platform_overhead_p95=17.0)
        )
        row = stage_row(
            with_timings,
            profile="p",
            scope="separate",
            environment_hash="hash",
            layer="L3",
        )
        assert row["platform_overhead_p95"] == 17.0

    def test_on_l1_the_report_fills_platform_overhead_from_the_compensation(
        self, profile, manifest
    ):
        # No LLM on this layer, so overhead equals the compensated turn latency;
        # the report asserts the identity instead of trusting a caller to pass it.
        built = build_report(
            result=ladder_result(profile), manifest=manifest, scope="separate"
        )
        row = built["rows"][0]
        assert row["platform_overhead_p95"] == row["p95"]

    def test_on_a_layer_with_an_llm_the_overhead_is_supplied_by_the_driver(
        self, profile
    ):
        stage = ladder_result(profile).stages[0]
        row = stage_row(
            stage,
            profile="p",
            scope="separate",
            environment_hash="hash",
            layer="L3",
        )
        assert row["platform_overhead_p95"] is None
        assert (
            "phase 2" in unavailable_reasons(row, layer="L3")["platform_overhead_p95"]
        )

    def test_generator_cpu_is_measured_not_assumed(self, profile, manifest):
        assert report(profile, manifest)["rows"][0]["generator_cpu"] == 1.5

    def test_a_stage_without_cpu_evidence_says_so(self, profile, manifest):
        built = report(profile, manifest)
        assert built["rows"][0]["generator_cpu"] == 1.5
        reasons = unavailable_reasons(
            {"generator_cpu": None, "sut_cpu": None}, layer="L1"
        )
        assert reasons["sut_cpu"]

    def test_an_error_rate_is_printed_in_percent(self, profile, manifest):
        built = build_report(
            result=ladder_result(profile, passing_rps=25.0),
            manifest=manifest,
            scope="separate",
        )
        assert built["rows"][0]["err_pct"] == 0.0


class TestHonestBlanks:
    def test_l1_has_no_stub_or_lock_columns_and_says_why(self, profile, manifest):
        built = report(profile, manifest)
        reasons = built["unavailable"]["5.0"]
        assert "L1 does not call the LLM" in reasons["platform_overhead_model_p95"]
        assert "no stub" in reasons["stub_queue_wait_p95"]
        assert "api-service is not on the L1 path" in reasons["mcp_lock_wait_p95"]
        assert "no prompt" in reasons["prompt_tokens_p50"]

    def test_every_empty_cell_has_a_reason(self, profile, manifest):
        built = report(profile, manifest)
        for row in built["rows"]:
            row_reasons = unavailable_reasons(row, layer="L1")
            for column, value in row.items():
                if value is None:
                    assert row_reasons[column], column

    def test_a_column_the_layer_can_produce_is_left_empty_without_pretending(
        self, profile, manifest
    ):
        # sut_cpu is not L1-specific: it is simply not collected yet.
        reasons = unavailable_reasons(report(profile, manifest)["rows"][0], layer="L1")
        assert "cgroup slices" in reasons["sut_cpu"]

    def test_prompt_tokens_are_reported_when_the_driver_provides_them(self, profile):
        stage = ladder_result(profile).stages[0]
        with_tokens = replace(
            stage, stats=replace(stage.stats, prompt_tokens_p50=1234.0)
        )
        row = stage_row(
            with_tokens,
            profile="p",
            scope="separate",
            environment_hash="hash",
            layer="L3",
        )
        assert row["prompt_tokens_p50"] == 1234.0


class TestNotes:
    def test_the_l1_interpretation_caveat_is_always_present(self, profile, manifest):
        notes = " ".join(interpretation_notes(ladder_result(profile), manifest))
        assert "call_lock" in notes
        assert "not on this path" in notes

    def test_a_single_run_is_flagged_as_not_a_headline(self, profile, manifest):
        notes = " ".join(interpretation_notes(ladder_result(profile), manifest))
        assert "single run" in notes

    def test_repeats_remove_the_single_run_caveat(self, profile, manifest):
        result = ladder_result(profile, repeats=3)
        notes = " ".join(interpretation_notes(result, manifest))
        assert "single run" not in notes
        assert "predicted" in notes

    def test_an_incomplete_environment_blocks_publication_in_words(
        self, profile, manifest
    ):
        built = report(profile, manifest)
        assert built["blockers"]
        assert any("not publishable as capacity" in note for note in built["notes"])

    def test_a_dirty_tree_is_named_in_the_blockers(self, profile):
        dirty = RunManifest(
            environment=collect_environment(profile, probe_host=False), code=code(True)
        )
        assert any("dirty" in blocker for blocker in report(profile, dirty)["blockers"])

    def test_an_invalid_stage_is_explained_in_the_notes(self, profile, manifest):
        result = run_ladder(
            runner=FakeRunner(always_dropping=True),
            plan=LadderPlan(
                rates=(5.0,), t_budget_ms=50.0, duration_s=2.0, warmup_s=0.5
            ),
            workload=WorkloadPlan.from_profile(profile),
            profile=profile,
            tenants=["t-1"],
        )
        notes = " ".join(interpretation_notes(result, manifest))
        assert "invalid" in notes
        assert "dropped" in notes

    def test_an_invalid_rung_above_the_knee_makes_the_knee_a_lower_bound(
        self, profile, manifest
    ):
        class HalfBrokenRunner(FakeRunner):
            def run(self, spec, plan_, *, tenants, workers=None, recycle=None):
                if spec.target_rps > 5.0:
                    self.calls.append((spec.target_rps, workers))
                    return make_result(spec, dropped=2, cpu_s=0.5)
                return super().run(spec, plan_, tenants=tenants, workers=workers)

        result = run_ladder(
            runner=HalfBrokenRunner(passing_rps=1000.0),
            plan=LadderPlan(
                rates=(5.0, 10.0), t_budget_ms=50.0, duration_s=2.0, warmup_s=0.5
            ),
            workload=WorkloadPlan.from_profile(profile),
            profile=profile,
            tenants=["t-1"],
        )
        notes = " ".join(interpretation_notes(result, manifest))
        assert "LOWER BOUND" in notes
        assert "never shown to fail" in notes

    def test_a_rung_that_needed_a_wider_pool_says_so(self, profile, manifest):
        # §2: an invalid stage is an action, so a rung that passed only after the
        # pool grew is not the same evidence as one that held at the nominal width.
        class StarvedRunner(FakeRunner):
            def run(self, spec, plan_, *, tenants, workers=None, recycle=None):
                if len(self.calls) == 0:
                    self.calls.append((spec.target_rps, workers))
                    return make_result(spec, dropped=4, cpu_s=0.1)
                return super().run(spec, plan_, tenants=tenants, workers=workers)

        result = run_ladder(
            runner=StarvedRunner(passing_rps=1000.0),
            plan=LadderPlan(
                rates=(5.0,),
                t_budget_ms=50.0,
                duration_s=2.0,
                warmup_s=0.5,
                max_pool_expansions=2,
            ),
            workload=WorkloadPlan.from_profile(profile),
            profile=profile,
            tenants=["t-1"],
        )
        assert result.stages[0].verdict == "pass"
        notes = " ".join(interpretation_notes(result, manifest))
        assert "passed only after 1 pool expansion" in notes
        assert "at that width" in notes

    def test_a_ladder_whose_top_passed_is_a_lower_bound_too(self, profile, manifest):
        result = run_ladder(
            runner=FakeRunner(passing_rps=1000.0),
            plan=LadderPlan(
                rates=(5.0, 10.0), t_budget_ms=50.0, duration_s=2.0, warmup_s=0.5
            ),
            workload=WorkloadPlan.from_profile(profile),
            profile=profile,
            tenants=["t-1"],
        )
        notes = " ".join(interpretation_notes(result, manifest))
        assert "LOWER BOUND at the top of the ladder" in notes

    def test_a_lower_bound_is_named_not_reported_as_a_found_knee(
        self, profile, manifest
    ):
        # The da85632e run printed `knee_found` on a ladder whose top rung
        # passed: the status contradicted the lower-bound caveat and read as
        # "the capacity is settled". The knee block must name what the rung
        # actually supports - a lower bound, never a found knee without a
        # bracket.
        result = run_ladder(
            runner=FakeRunner(passing_rps=1000.0),
            plan=LadderPlan(
                rates=(5.0, 10.0), t_budget_ms=50.0, duration_s=2.0, warmup_s=0.5
            ),
            workload=WorkloadPlan.from_profile(profile),
            profile=profile,
            tenants=["t-1"],
        )
        report = build_report(
            result=result, manifest=manifest, scope="separate", profile_path="p.json"
        )
        assert report["ladder"]["status"] == "knee_lower_bound"
        assert report["ladder"]["knee_bracket"] is None
        markdown = render_markdown(report)
        assert "knee_lower_bound" in markdown
        assert "(status `knee_found`" not in markdown

    def test_a_real_failure_still_names_the_knee_found(self, profile, manifest):
        # The other side of the contract: a bracket from a rung that actually
        # failed is a found knee, and renaming it would hide the one case where
        # the ceiling is genuinely measured.
        result = run_ladder(
            runner=FakeRunner(passing_rps=25.0),
            plan=LadderPlan(
                rates=(5.0, 10.0, 20.0, 40.0),
                t_budget_ms=50.0,
                duration_s=2.0,
                warmup_s=0.5,
            ),
            workload=WorkloadPlan.from_profile(profile),
            profile=profile,
            tenants=["t-1"],
        )
        assert result.knee.bracket is not None
        report = build_report(
            result=result, manifest=manifest, scope="separate", profile_path="p.json"
        )
        assert report["ladder"]["status"] == "knee_found"

    def test_discarded_knee_runs_are_reported_not_hidden(self, profile, manifest):
        class HalfBrokenRunner(FakeRunner):
            def run(self, spec, plan_, *, tenants, workers=None, recycle=None):
                if spec.target_rps > 5.0:
                    self.calls.append((spec.target_rps, workers))
                    return make_result(spec, dropped=2, cpu_s=0.5)
                return super().run(spec, plan_, tenants=tenants, workers=workers)

        result = run_ladder(
            runner=HalfBrokenRunner(passing_rps=1000.0),
            plan=LadderPlan(
                rates=(5.0, 10.0),
                t_budget_ms=50.0,
                duration_s=2.0,
                warmup_s=0.5,
                repeats=3,
            ),
            workload=WorkloadPlan.from_profile(profile),
            profile=profile,
            tenants=["t-1"],
        )
        built = build_report(result=result, manifest=manifest, scope="separate")
        assert built["ladder"]["headline"]["runs_discarded"] == 0.0, (
            "the knee rung itself is valid; only the rung above it went invalid"
        )
        assert any("LOWER BOUND" in note for note in built["notes"])

    def test_the_error_mix_is_printed_for_a_valid_stage(self, profile, manifest):
        stage = ladder_result(profile).stages[0]
        noisy = replace(
            stage,
            stats=replace(stage.stats, error_mix={"other": 3}),
        )
        result = ladder_result(profile)
        result.stages[0] = noisy
        notes = " ".join(interpretation_notes(result, manifest))
        assert "error mix" in notes
        assert "other" in notes

    def test_prediction_and_finding_are_compared_not_just_listed(
        self, profile, manifest
    ):
        built = report(profile, manifest)
        assert any("predicted" in note and "found" in note for note in built["notes"])


class TestMarkdown:
    def test_the_table_carries_every_column_the_report_has(self, profile, manifest):
        # §10 wants runs compared by looking at them: a column that exists only in
        # report.json is a column nobody compares.
        text = render_markdown(report(profile, manifest))
        header = next(
            line for line in text.splitlines() if line.startswith("| profile |")
        )
        for column in COLUMNS:
            assert HEADERS[column] in header, column
        assert "| 5 | 5 | 5 | 5 |" in text

    def test_the_generator_and_pool_evidence_reaches_the_table(self, profile, manifest):
        text = render_markdown(report(profile, manifest))
        assert "gen CPU %" in text
        assert "pool x" in text and "dropped" in text and "spin tail ms" in text

    def test_the_knee_and_its_bracket_are_printed(self, profile, manifest):
        text = render_markdown(report(profile, manifest))
        assert "## Knee" in text
        assert "knee:" in text
        assert "bracket:" in text

    def test_the_chart_shows_the_budget_and_the_rungs(self, profile, manifest):
        text = render_markdown(report(profile, manifest))
        assert "Knee chart" in text
        assert "budget 50 ms" in text
        assert "PASS" in text and "FAIL" in text

    def test_a_run_without_a_knee_says_so_instead_of_printing_zero(
        self, profile, manifest
    ):
        result = run_ladder(
            runner=FakeRunner(passing_rps=1.0),
            plan=LadderPlan(
                rates=(5.0,), t_budget_ms=50.0, duration_s=2.0, warmup_s=0.5
            ),
            workload=WorkloadPlan.from_profile(profile),
            profile=profile,
            tenants=["t-1"],
        )
        text = render_markdown(
            build_report(result=result, manifest=manifest, scope="separate")
        )
        assert "not determined" in text
        assert "knee: **0" not in text

    def test_notes_section_carries_the_caveats(self, profile, manifest):
        text = render_markdown(report(profile, manifest))
        assert "## Notes and caveats" in text
        assert "call_lock" in text

    def test_empty_cells_are_explained_in_the_document(self, profile, manifest):
        text = render_markdown(report(profile, manifest))
        assert "## Empty cells" in text
        assert "no prompt" in text


class TestWriteReport:
    def test_both_artefacts_are_written_and_round_trip(
        self, profile, manifest, tmp_path
    ):
        built = report(profile, manifest)
        json_path, md_path = write_report(tmp_path / "run", built)
        assert json_path.name == "report.json"
        assert md_path.name == f"report-{built['run_uuid']}.md"
        assert (
            json.loads(json_path.read_text(encoding="utf-8"))["run_uuid"]
            == built["run_uuid"]
        )
        assert "## Knee" in md_path.read_text(encoding="utf-8")

    def test_the_report_points_at_its_evidence(self, profile, manifest):
        text = render_markdown(report(profile, manifest))
        assert "raw/<rps>-run<k>-attempt<n>.jsonl" in text
