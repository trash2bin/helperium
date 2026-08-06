"""Structured error context passed between pipeline stages."""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ErrorContext:
    """Structured error context for pipeline stages.

    Passed between stages to carry error metadata (session, correlation, etc.)
    without relying on logging or global state.
    """

    session_id: str = ""
    correlation_id: str = ""
    error_code: str = ""
    message: str = ""
    stage: str = ""
    iteration: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def with_stage(self, stage: str) -> ErrorContext:
        """Return copy with stage set (immutable-style builder)."""
        return ErrorContext(
            session_id=self.session_id,
            correlation_id=self.correlation_id,
            error_code=self.error_code,
            message=self.message,
            stage=stage,
            iteration=self.iteration,
            metadata=dict(self.metadata),
        )

    def to_dict(self) -> dict[str, Any]:
        # NOTE: 'message' is excluded to avoid conflicting with LogRecord.message
        d: dict[str, Any] = {
            "session_id": self.session_id,
            "correlation_id": self.correlation_id,
            "error_code": self.error_code,
            "stage": self.stage,
            "iteration": self.iteration,
        }
        d["metadata"] = self.metadata
        return d
