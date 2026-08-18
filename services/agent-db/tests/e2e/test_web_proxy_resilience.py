"""Docker E2E regressions for the public web-to-API chat proxy.

These tests do not invoke an LLM.  They use a missing agent so that an upstream
non-200 response is deterministic, then exercise the real web proxy and API
rate limiter inside the Compose network.
"""

from __future__ import annotations

import uuid

import requests

from tests.e2e.helpers import api_service_url, demo_web_url


def _assert_single_http_body_framing(response: requests.Response) -> None:
    """A proxied response must use exactly one HTTP body-framing mechanism."""

    header_names = {header.lower() for header in response.headers}
    assert not {"content-length", "transfer-encoding"} <= header_names, (
        f"invalid dual body framing: {dict(response.headers)}"
    )


def _chat_via_web(*, agent: str, client_ip: str) -> requests.Response:
    """Send one non-billable chat request through the public web proxy."""

    return requests.post(
        f"{demo_web_url()}/api/chat/{agent}",
        json={"message": "resilience probe", "session_id": uuid.uuid4().hex},
        headers={"X-Forwarded-For": client_ip},
        timeout=10,
    )


def test_api_compose_cors_default_denies_unconfigured_origin() -> None:
    """A missing deployment override must not turn the public API into wildcard CORS."""

    headers = {
        "Origin": "https://attacker.invalid",
        "Access-Control-Request-Method": "POST",
    }
    denied = requests.options(
        f"{api_service_url()}/api/chat", headers=headers, timeout=10
    )
    assert denied.status_code == 400, denied.text[:500]
    assert "access-control-allow-origin" not in {
        name.lower() for name in denied.headers
    }, denied.headers

    allowed = requests.options(
        f"{api_service_url()}/api/chat",
        headers={
            "Origin": "http://localhost:8080",
            "Access-Control-Request-Method": "POST",
        },
        timeout=10,
    )
    assert allowed.status_code == 200, allowed.text[:500]
    assert allowed.headers.get("access-control-allow-origin") == "http://localhost:8080"


def test_web_proxy_preserves_upstream_not_found_status() -> None:
    """A streamed upstream 404 must never be hidden as a proxy-side 500."""

    response = _chat_via_web(
        agent=f"missing-proxy-{uuid.uuid4().hex}",
        client_ip=f"198.18.0.{uuid.uuid4().int % 200 + 1}",
    )

    assert response.status_code == 404, response.text[:500]
    _assert_single_http_body_framing(response)


def test_rate_limit_is_scoped_to_forwarded_visitor_ip() -> None:
    """One abusive visitor must not exhaust another visitor's chat bucket."""

    missing_agent = f"missing-rate-limit-{uuid.uuid4().hex}"
    abusive_ip = f"198.18.1.{uuid.uuid4().int % 200 + 1}"
    independent_ip = f"198.18.2.{uuid.uuid4().int % 200 + 1}"

    statuses = [
        _chat_via_web(agent=missing_agent, client_ip=abusive_ip).status_code
        for _ in range(31)
    ]

    assert statuses[:30] == [404] * 30, statuses
    assert statuses[30] == 429, statuses

    limited = _chat_via_web(agent=missing_agent, client_ip=abusive_ip)
    assert limited.status_code == 429, limited.text[:500]
    assert limited.headers.get("retry-after"), limited.headers
    _assert_single_http_body_framing(limited)

    independent = _chat_via_web(agent=missing_agent, client_ip=independent_ip)
    assert independent.status_code == 404, independent.text[:500]
    _assert_single_http_body_framing(independent)
