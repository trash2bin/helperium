"""Tests for agent adapters — async wrappers around sync singletons."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from api_service.agent.adapters import _AsyncBacklogWriter, _AsyncSpendingTracker


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


class TestAsyncBacklogWriter:
    """Tests for _AsyncBacklogWriter."""

    def test_record_llm_call(self):
        """record_llm_call delegates to backlog singleton."""
        writer = _AsyncBacklogWriter()
        with patch("api_service.agent.adapters.backlog") as mock_bl:
            writer.record_llm_call("s1", model="gpt-4", tokens=100)
            mock_bl.record_llm_call.assert_called_once_with(
                "s1", model="gpt-4", tokens=100
            )

    def test_tool_call(self):
        """tool_call delegates to backlog singleton."""
        writer = _AsyncBacklogWriter()
        with patch("api_service.agent.adapters.backlog") as mock_bl:
            writer.tool_call("s1", "t1", 0, "grep_students", {"q": "test"})
            mock_bl.tool_call.assert_called_once_with(
                "s1", "t1", 0, "grep_students", {"q": "test"}
            )

    def test_tool_result(self):
        """tool_result delegates to backlog singleton."""
        writer = _AsyncBacklogWriter()
        with patch("api_service.agent.adapters.backlog") as mock_bl:
            writer.tool_result("s1", "t1", 0, "grep_students", "[]", 42.5)
            mock_bl.tool_result.assert_called_once_with(
                "s1", "t1", 0, "grep_students", "[]", 42.5
            )

    def test_error(self):
        """error delegates to backlog singleton."""
        writer = _AsyncBacklogWriter()
        with patch("api_service.agent.adapters.backlog") as mock_bl:
            writer.error("s1", "t1", 0, "timeout", {"detail": "slow"})
            mock_bl.error.assert_called_once_with(
                "s1", "t1", 0, "timeout", {"detail": "slow"}
            )

    def test_error_without_context(self):
        """error() with context=None doesn't crash."""
        writer = _AsyncBacklogWriter()
        with patch("api_service.agent.adapters.backlog") as mock_bl:
            writer.error("s1", "t1", 0, "boom")
            mock_bl.error.assert_called_once_with("s1", "t1", 0, "boom", None)
