"""Tests for agent adapters — the ports the loop and orchestrator depend on."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from prometheus_client import REGISTRY

from api_service.agent.adapters import (
    _AsyncSpendingTracker,
    _BacklogRecorder,
    _PrometheusLlmCallMetrics,
    _TurnRecorder,
    build_turn_recorder,
)


class TestAsyncSpendingTracker:
    """Tests for _AsyncSpendingTracker."""

    @pytest.mark.asyncio
    async def test_record_does_not_raise(self):
        """record() delegates to SpendingChecker without raising."""
        tracker = _AsyncSpendingTracker()
        with patch("api_service.agent.adapters.get_spending_checker") as mock_get:
            checker = MagicMock()
            mock_get.return_value = checker
            await tracker.record("tenant-a", 1.5)
            checker.record_spending.assert_called_once_with("tenant-a", 1.5)

    @pytest.mark.asyncio
    async def test_check_limits_delegates(self):
        """check_limits() returns the tuple from SpendingChecker."""
        tracker = _AsyncSpendingTracker()
        with patch("api_service.agent.adapters.get_spending_checker") as mock_get:
            checker = MagicMock()
            checker.check_limits.return_value = (True, "ok")
            mock_get.return_value = checker
            allowed, reason = await tracker.check_limits("tenant-b")
            assert allowed is True
            assert reason == "ok"
            checker.check_limits.assert_called_once_with("tenant-b")


class TestBacklogRecorder:
    """Tests for _BacklogRecorder (the durable evidence sink)."""

    def test_turn_start_delegates(self):
        """turn_start delegates to the backlog singleton and returns the id."""
        recorder = _BacklogRecorder()
        with patch("api_service.agent.adapters.backlog") as mock_bl:
            mock_bl.turn_start.return_value = "abc123"
            assert recorder.turn_start("s1", "hello") == "abc123"
            mock_bl.turn_start.assert_called_once_with("s1", "hello")

    def test_turn_end_delegates(self):
        """turn_end delegates every aggregate field."""
        recorder = _BacklogRecorder()
        with patch("api_service.agent.adapters.backlog") as mock_bl:
            recorder.turn_end(
                "s1",
                turn_id="t1",
                duration_ms=10.0,
                outcome="answer",
                total_prompt_tokens=1,
                total_completion_tokens=2,
                total_cost=0.01,
                llm_calls=1,
                tool_calls=2,
                tool_errors=0,
            )
            # Every aggregate field must be forwarded: dropping one (e.g.
            # final_text or empty_rounds) would silently break benchmark
            # analysis reading the turn_end record.
            mock_bl.turn_end.assert_called_once_with(
                session_id="s1",
                turn_id="t1",
                duration_ms=10.0,
                outcome="answer",
                total_prompt_tokens=1,
                total_completion_tokens=2,
                total_cost=0.01,
                llm_calls=1,
                tool_calls=2,
                tool_errors=0,
                empty_results=0,
                empty_rounds=0,
                iterations=0,
                final_length_chars=0,
                final_text="",
                error_message="",
            )

    def test_record_llm_call(self):
        """record_llm_call delegates the pinned capacity-evidence field set."""
        writer = _BacklogRecorder()
        with patch("api_service.agent.adapters.backlog") as mock_bl:
            writer.record_llm_call(
                "s1",
                model="gpt-4",
                provider="openai",
                duration_ms=42.0,
                prompt_tokens=11,
                completion_tokens=7,
                total_tokens=18,
                cost=0.002,
                status="success",
                tenant_ids=["t1"],
                turn_id="turn-1",
                iteration=1,
                untrusted_tool_results_in_context=0,
            )
            mock_bl.record_llm_call.assert_called_once_with(
                "s1",
                model="gpt-4",
                provider="openai",
                duration_ms=42.0,
                prompt_tokens=11,
                completion_tokens=7,
                total_tokens=18,
                cost=0.002,
                status="success",
                tenant_ids=["t1"],
                turn_id="turn-1",
                iteration=1,
                untrusted_tool_results_in_context=0,
            )

    def test_record_llm_call_rejects_fields_the_record_does_not_have(self):
        """A misspelled field must fail here, not silently reach the JSONL.

        backlog.record_llm_call spills unknown keywords into the durable record
        via record.update(extra), so without a pinned adapter signature a typo
        (the historical "tokens") would land in the capacity evidence instead
        of raising. The port pins the field set for the loop; this pins the
        sink underneath it.
        """
        writer = _BacklogRecorder()
        with patch("api_service.agent.adapters.backlog") as mock_bl:
            with pytest.raises(TypeError):
                writer.record_llm_call("s1", model="gpt-4", tokens=100)
            mock_bl.record_llm_call.assert_not_called()

    def test_tool_call(self):
        """tool_call delegates to backlog singleton."""
        writer = _BacklogRecorder()
        with patch("api_service.agent.adapters.backlog") as mock_bl:
            writer.tool_call("s1", "t1", 0, "grep_students", {"q": "test"})
            mock_bl.tool_call.assert_called_once_with(
                "s1", "t1", 0, "grep_students", {"q": "test"}
            )

    def test_tool_result(self):
        """tool_result delegates to backlog singleton."""
        writer = _BacklogRecorder()
        with patch("api_service.agent.adapters.backlog") as mock_bl:
            writer.tool_result("s1", "t1", 0, "grep_students", "[]", 42.5)
            mock_bl.tool_result.assert_called_once_with(
                "s1", "t1", 0, "grep_students", "[]", 42.5
            )

    def test_error(self):
        """error delegates to backlog singleton."""
        writer = _BacklogRecorder()
        with patch("api_service.agent.adapters.backlog") as mock_bl:
            writer.error("s1", "t1", 0, "timeout", {"detail": "slow"})
            mock_bl.error.assert_called_once_with(
                "s1", "t1", 0, "timeout", {"detail": "slow"}
            )

    def test_error_without_context(self):
        """error() with context=None doesn't crash."""
        writer = _BacklogRecorder()
        with patch("api_service.agent.adapters.backlog") as mock_bl:
            writer.error("s1", "t1", 0, "boom")
            mock_bl.error.assert_called_once_with("s1", "t1", 0, "boom", None)


class TestPrometheusLlmCallMetrics:
    """LLM telemetry must not depend on the backlog being enabled.

    Capacity evidence subtracts per-call LLM latency from the turn duration; if
    emitting it required BACKLOG_MODE=full, a discriminating run with the
    backlog off would silently also change what is measured. Every test uses
    its own model label because Prometheus series are process-global.
    """

    @staticmethod
    def _sample(name: str, labels: dict[str, str]) -> float:
        return REGISTRY.get_sample_value(name, labels) or 0.0

    def test_record_increments_all_llm_series(self):
        metrics = _PrometheusLlmCallMetrics()
        model = "metrics-test/one"
        provider = "metrics-test"

        calls = self._sample("llm_calls_total", {"model": model, "provider": provider})
        durations = self._sample("llm_duration_ms_count", {"model": model})
        prompts = self._sample("llm_token_usage_total", {"type": "prompt"})
        totals = self._sample("llm_token_usage_total", {"type": "total"})

        metrics.record(
            model=model,
            provider=provider,
            duration_ms=25.0,
            prompt_tokens=11,
            completion_tokens=7,
            total_tokens=18,
            cost=0.0,
            tenant_ids=["tenant-a"],
        )

        assert (
            self._sample("llm_calls_total", {"model": model, "provider": provider})
            == calls + 1
        )
        assert self._sample("llm_duration_ms_count", {"model": model}) == durations + 1
        assert self._sample("llm_token_usage_total", {"type": "prompt"}) == prompts + 11
        assert self._sample("llm_token_usage_total", {"type": "total"}) == totals + 18

    def test_cost_is_attributed_to_every_tenant_in_scope(self):
        metrics = _PrometheusLlmCallMetrics()
        model = "metrics-test/composite"
        provider = "metrics-test"
        labels = {"model": model, "provider": provider, "tenant_id": "tenant-b"}

        before = self._sample("llm_cost_total", labels)
        metrics.record(
            model=model,
            provider=provider,
            duration_ms=1.0,
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            cost=0.002,
            tenant_ids=["tenant-b", "tenant-c"],
        )

        assert self._sample("llm_cost_total", labels) > before
        assert self._sample("llm_cost_total", {**labels, "tenant_id": "tenant-c"}) > 0


class TestTurnRecorderFanOut:
    """One event, both sinks — and nothing else leaks into the metrics."""

    @staticmethod
    def _recorder() -> tuple[_TurnRecorder, MagicMock, MagicMock]:
        backlog = MagicMock()
        metrics = MagicMock()
        return (
            _TurnRecorder(backlog=backlog, metrics=metrics),  # type: ignore[arg-type]
            backlog,
            metrics,
        )

    def test_record_llm_call_reaches_both_sinks_with_one_field_set(self):
        recorder, backlog, metrics = self._recorder()

        recorder.record_llm_call(
            "s1",
            model="m",
            provider="p",
            duration_ms=42.0,
            prompt_tokens=1,
            completion_tokens=2,
            total_tokens=3,
            cost=0.5,
            status="success",
            tenant_ids=["t1"],
            turn_id="turn-1",
            iteration=1,
            untrusted_tool_results_in_context=0,
        )

        assert metrics.record.call_count == 1
        assert backlog.record_llm_call.call_count == 1
        assert metrics.record.call_args.kwargs["duration_ms"] == 42.0
        assert backlog.record_llm_call.call_args.kwargs["duration_ms"] == 42.0

    def test_non_completion_events_go_only_to_the_backlog(self):
        recorder, backlog, metrics = self._recorder()

        recorder.turn_start("s1", "hi")
        recorder.tool_call("s1", "turn-1", 0, "db_get", {"id": 1})
        recorder.tool_result("s1", "turn-1", 0, "db_get", "{}", 3.0)
        recorder.error("s1", "turn-1", 1, "boom", {"outcome": "tool_error"})
        recorder.turn_end(
            "s1",
            turn_id="turn-1",
            duration_ms=10.0,
            outcome="answer",
            total_prompt_tokens=1,
            total_completion_tokens=1,
            total_cost=0.0,
            llm_calls=1,
            tool_calls=1,
            tool_errors=0,
        )

        assert metrics.record.call_count == 0
        assert backlog.turn_start.call_count == 1
        assert backlog.tool_call.call_count == 1
        assert backlog.tool_result.call_count == 1
        assert backlog.error.call_count == 1
        assert backlog.turn_end.call_count == 1


class TestBuildTurnRecorder:
    def test_turn_id_round_trips_from_the_backlog(self):
        recorder = build_turn_recorder()
        with patch("api_service.agent.adapters.backlog") as mock_bl:
            mock_bl.turn_start.return_value = "deadbeef"
            assert recorder.turn_start("s1", "hi") == "deadbeef"
