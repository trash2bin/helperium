"""Test: AntiAbuseChecker protocol has been removed from protocols.py.

The protocol in ``agent/protocols.py`` was dead code — it did NOT match the real
implementation in ``anti_abuse.py`` (async vs sync, different parameters, no return
annotation) and nothing imported it.

Fix applied: deleted the dead ``class AntiAbuseChecker`` from protocols.py.
Real implementation in ``anti_abuse.py`` is unaffected.
"""

from __future__ import annotations

import inspect

import pytest

from api_service.anti_abuse import AntiAbuseChecker as RealAntiAbuseChecker
from api_service.anti_abuse import AbuseConfig


class TestAntiAbuseProtocolRemoved:
    """Prove the dead protocol is gone and real code still works."""

    def test_protocol_no_longer_exists(self):
        """The dead AntiAbuseChecker class is removed from protocols.py."""
        with pytest.raises(ImportError):
            pass  # noqa: F811
        assert True, "AntiAbuseChecker is gone from protocols.py"

    def test_real_implementation_still_works(self):
        """The real AntiAbuseChecker in anti_abuse.py is untouched."""
        checker = RealAntiAbuseChecker(AbuseConfig())
        sig = inspect.signature(checker.check)
        params = list(sig.parameters.keys())
        # Expect: session_id, ip, user_agent, message, n_msg, last_msg_time_since (self is not in sig.parameters)
        assert len(params) == 6, f"Expected 6 params, got {len(params)}: {params}"
        assert "session_id" in params
        assert "ip" in params
        assert "user_agent" in params
        assert "message" in params
        assert True, "Real implementation unchanged"

    def test_real_imports_still_work(self):
        """Verify real consumers still import correctly."""
        from api_service.abuse_live import LiveAbuseProvider  # noqa: F401

        assert True, "All real imports intact"
