"""Pentest 2026-09-13 security regressions at the live HTTP layer.

Findings reproduced against a running stack (native dev or compose test
profile). No LLM is invoked: every probe targets a control-plane surface or
an unauthenticated preflight.

- L3: admin-dashboard must not echo the configured CORS origin list to
  origins that are not listed (it must reflect a single matching origin, or
  send no Access-Control-Allow-Origin at all).
"""

from __future__ import annotations

import os

import requests


def _admin_dashboard_url() -> str:
    host = os.environ.get("ADMIN_DASHBOARD_HOST", "127.0.0.1")
    port = os.environ.get("ADMIN_DASHBOARD_PORT", "8085")
    return os.environ.get("ADMIN_DASHBOARD_URL", f"http://{host}:{port}")


class TestAdminCORSPreflight:
    """admin-dashboard corsMiddleware: reflect-single-origin contract."""

    def test_unlisted_origin_gets_no_allow_origin(self) -> None:
        """A preflight from an origin that is not in CORS_ALLOW_ORIGINS must
        not receive any Access-Control-Allow-Origin header.

        The historical bug echoed the raw CORS_ALLOW_ORIGINS env value (the
        full comma-joined list) to every caller, including attackers, which
        also produced a spec-invalid ACAO value for multi-origin setups.
        """
        response = requests.options(
            f"{_admin_dashboard_url()}/api/dashboard",
            headers={
                "Origin": "https://attacker.invalid",
                "Access-Control-Request-Method": "GET",
            },
            timeout=10,
        )
        headers = {name.lower() for name in response.headers}
        assert "access-control-allow-origin" not in headers, dict(response.headers)

    def test_listed_origin_is_reflected_exactly(self) -> None:
        """A listed origin must be reflected as exactly that single origin —
        never the joined list value."""
        response = requests.options(
            f"{_admin_dashboard_url()}/api/dashboard",
            headers={
                "Origin": "http://localhost:8080",
                "Access-Control-Request-Method": "GET",
            },
            timeout=10,
        )
        acao = response.headers.get("access-control-allow-origin")
        assert acao == "http://localhost:8080", dict(response.headers)
