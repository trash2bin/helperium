"""Tests for ErrorContext dataclass."""

from api_service.agent.error_context import ErrorContext


class TestErrorContext:
    """Tests for ErrorContext."""

    def test_default_values(self):
        ctx = ErrorContext()
        assert ctx.session_id == ""
        assert ctx.correlation_id == ""
        assert ctx.error_code == ""
        assert ctx.message == ""
        assert ctx.stage == ""
        assert ctx.iteration == 0
        assert ctx.metadata == {}

    def test_custom_values(self):
        ctx = ErrorContext(
            session_id="s1",
            correlation_id="c1",
            error_code="E001",
            message="boom",
            stage="llm",
            iteration=3,
            metadata={"key": "val"},
        )
        assert ctx.session_id == "s1"
        assert ctx.correlation_id == "c1"
        assert ctx.error_code == "E001"
        assert ctx.message == "boom"
        assert ctx.stage == "llm"
        assert ctx.iteration == 3
        assert ctx.metadata == {"key": "val"}

    def test_with_stage_returns_copy(self):
        ctx = ErrorContext(session_id="s1", error_code="E001")
        ctx2 = ctx.with_stage("tool_execution")
        assert ctx2.session_id == "s1"
        assert ctx2.error_code == "E001"
        assert ctx2.stage == "tool_execution"
        # Original unchanged
        assert ctx.stage == ""

    def test_to_dict_excludes_message(self):
        ctx = ErrorContext(
            session_id="s1",
            correlation_id="c1",
            error_code="E001",
            message="should be excluded",
            stage="llm",
            iteration=2,
            metadata={"extra": "data"},
        )
        d = ctx.to_dict()
        assert "message" not in d, "message must be excluded (conflicts with LogRecord)"
        assert d["session_id"] == "s1"
        assert d["correlation_id"] == "c1"
        assert d["error_code"] == "E001"
        assert d["stage"] == "llm"
        assert d["iteration"] == 2
        assert d["metadata"]["extra"] == "data"

    def test_to_dict_no_metadata(self):
        ctx = ErrorContext(session_id="s1")
        d = ctx.to_dict()
        assert d == {
            "session_id": "s1",
            "correlation_id": "",
            "error_code": "",
            "stage": "",
            "iteration": 0,
            "metadata": {},
        }

    def test_chained_with_stage(self):
        ctx = ErrorContext(session_id="s1")
        ctx2 = ctx.with_stage("stage1").with_stage("stage2")
        assert ctx2.stage == "stage2"
        assert ctx2.session_id == "s1"
