"""Contract tests for the raw JSONL record and the percentile calculator (§10).

These pin the two things every layer's number depends on: one record shape per
request, and one place where percentiles are computed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_db.stress import (
    ErrorClass,
    RawRequestRecord,
    percentile,
    summarise,
    write_raw_records,
)


def _record(**overrides) -> RawRequestRecord:
    payload = {
        "planned_ms": 0.0,
        "started_ms": 0.0,
        "actual_ms": 100.0,
        "session_id": "s-1",
        "tenant": "tenant-a",
        "scenario": "one_tool",
    }
    payload.update(overrides)
    return RawRequestRecord(**payload)


class TestRecordRoundTrip:
    def test_jsonl_survives_a_round_trip_with_an_error_class(self) -> None:
        record = _record(
            status="error",
            http_status=429,
            error_class=ErrorClass.BUDGET_429,
            terminator="limit_tool_calls",
            ttfe_session=12.0,
            ttfe=40.0,
            ttfe_tool=55.0,
            t_complete=980.0,
            prompt_tokens=812,
            history_turns=3,
        )
        restored = RawRequestRecord.from_jsonl(record.as_jsonl())
        assert restored == record
        assert restored.error_class is ErrorClass.BUDGET_429

    def test_jsonl_line_is_single_line_compact_json(self) -> None:
        line = _record().as_jsonl()
        assert "\n" not in line
        assert json.loads(line)["planned_ms"] == 0.0

    def test_write_raw_records_appends_and_counts(self, tmp_path: Path) -> None:
        target = tmp_path / "raw" / "stage-1.jsonl"
        assert write_raw_records(target, [_record(), _record(actual_ms=120.0)]) == 2
        assert write_raw_records(target, [_record()]) == 1
        lines = target.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 3
        assert json.loads(lines[2])["session_id"] == "s-1"


class TestCoordinatedOmission:
    def test_on_schedule_request_reports_its_measured_latency(self) -> None:
        assert _record(
            planned_ms=1000.0, started_ms=1000.0, actual_ms=250.0
        ).compensated_ms() == pytest.approx(250.0)

    def test_a_late_start_carries_the_slip(self) -> None:
        # The generator fell 500 ms behind: the request took 250 ms of service,
        # but a client that kept the schedule would have waited 750 ms.
        record = _record(planned_ms=1000.0, started_ms=1500.0, actual_ms=250.0)
        assert record.compensated_ms() == pytest.approx(750.0)

    def test_an_early_start_cannot_invent_an_improvement(self) -> None:
        record = _record(planned_ms=1000.0, started_ms=200.0, actual_ms=250.0)
        assert record.compensated_ms() == pytest.approx(250.0)

    def test_finished_offset_is_start_plus_latency(self) -> None:
        assert _record(started_ms=400.0, actual_ms=100.0).finished_ms == pytest.approx(
            500.0
        )


class TestPercentile:
    def test_median_interpolates_between_neighbours(self) -> None:
        assert percentile([4.0, 1.0, 3.0, 2.0], 0.5) == pytest.approx(2.5)

    def test_single_sample_is_itself(self) -> None:
        assert percentile([7.0], 0.99) == pytest.approx(7.0)

    def test_extremes_are_min_and_max(self) -> None:
        assert percentile([5.0, 1.0, 3.0], 0.0) == pytest.approx(1.0)
        assert percentile([5.0, 1.0, 3.0], 1.0) == pytest.approx(5.0)

    def test_empty_sample_is_refused(self) -> None:
        with pytest.raises(ValueError):
            percentile([], 0.95)

    def test_q_outside_unit_interval_is_refused(self) -> None:
        with pytest.raises(ValueError):
            percentile([1.0], 1.5)


class TestSummarise:
    def test_stage_aggregates_match_the_report_contract(self) -> None:
        records = [
            _record(
                planned_ms=i * 1000.0,
                started_ms=i * 1000.0,
                actual_ms=lat,
                prompt_tokens=800,
                t_complete=lat + 5,
            )
            for i, lat in enumerate(
                [100.0, 110.0, 120.0, 130.0, 140.0, 150.0, 160.0, 170.0, 180.0, 900.0]
            )
        ]
        records[9] = _record(
            planned_ms=9000.0,
            started_ms=9000.0,
            actual_ms=900.0,
            status="error",
            http_status=429,
            error_class=ErrorClass.BUDGET_429,
            terminator="limit_tool_calls",
            prompt_tokens=812,
            t_complete=1900.0,
        )

        stats = summarise(records, duration_s=10.0)

        assert stats.count == 10
        assert stats.errors == 1
        assert stats.error_rate == pytest.approx(0.1)
        assert stats.achieved_rps == pytest.approx(1.0)
        assert stats.p50 == pytest.approx(145.0)
        assert stats.p99 == pytest.approx(835.2)
        assert stats.prompt_tokens_p50 == pytest.approx(800.0)
        assert stats.terminator_mix == {"limit_tool_calls": 1}
        assert stats.error_mix == {ErrorClass.BUDGET_429.value: 1}

    def test_compensated_p95_exposes_generator_slip(self) -> None:
        on_schedule = [
            _record(planned_ms=i * 100.0, started_ms=i * 100.0, actual_ms=50.0)
            for i in range(20)
        ]
        slipping = [
            _record(planned_ms=i * 100.0, started_ms=i * 100.0 + 300.0, actual_ms=50.0)
            for i in range(20)
        ]
        assert summarise(on_schedule, 2.0).compensated_p95 == pytest.approx(50.0)
        assert summarise(slipping, 2.0).compensated_p95 == pytest.approx(350.0)
        # The uncorrected percentile looks identical in both cases, which is
        # exactly why the correction exists.
        assert summarise(slipping, 2.0).p95 == pytest.approx(50.0)

    def test_capacity_criterion_is_p95_plus_error_budget(self) -> None:
        healthy = summarise(
            [_record(actual_ms=40.0) for _ in range(10)], duration_s=1.0
        )
        assert healthy.meets(t_budget_ms=50.0) is True

        slow = summarise([_record(actual_ms=80.0) for _ in range(10)], duration_s=1.0)
        assert slow.meets(t_budget_ms=50.0) is False

        erroring = summarise(
            [
                _record(
                    actual_ms=40.0, status="error", error_class=ErrorClass.SERVER_5XX
                )
                for _ in range(10)
            ],
            duration_s=1.0,
        )
        assert erroring.meets(t_budget_ms=50.0) is False

    def test_the_criterion_uses_the_compensated_percentile(self) -> None:
        # Twenty turns that started 300 ms late: their measured latency is 40 ms,
        # but the platform saw them 340 ms after they were due. Judging on the
        # uncorrected p95 would call this rung healthy (open-loop CO, §2).
        slipping = [
            _record(
                planned_ms=float(i * 100),
                started_ms=float(i * 100 + 300),
                actual_ms=40.0,
            )
            for i in range(20)
        ]
        stats = summarise(slipping, duration_s=2.0)
        assert stats.p95 == pytest.approx(40.0)
        assert stats.meets(t_budget_ms=50.0) is False

    def test_a_rung_that_ran_fewer_turns_than_asked_does_not_pass(self) -> None:
        # §7: achieved >= 0.98 x target is part of the criterion, not a footnote.
        half = summarise([_record(actual_ms=10.0) for _ in range(50)], duration_s=1.0)
        assert half.achieved_rps == pytest.approx(50.0)
        assert half.meets(t_budget_ms=50.0, target_rps=50.0) is True
        assert half.meets(t_budget_ms=50.0, target_rps=100.0) is False

    def test_missing_llm_metrics_do_not_fabricate_zero(self) -> None:
        stats = summarise([_record()], duration_s=1.0)
        assert stats.t_complete_p95 is None
        assert stats.prompt_tokens_p50 is None

    def test_empty_stage_and_zero_duration_are_refused(self) -> None:
        with pytest.raises(ValueError):
            summarise([], duration_s=1.0)
        with pytest.raises(ValueError):
            summarise([_record()], duration_s=0.0)
