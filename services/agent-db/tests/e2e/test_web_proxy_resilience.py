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


def test_rate_limit_survives_rotated_spoofed_forwarded_for() -> None:
    """Rotating a spoofed X-Forwarded-For must not open a fresh limiter bucket.

    Regression for the spoofable per-IP key: the web proxy overwrites the
    client header with the real peer address and the API limiter trusts only
    that entry, so every request below lands in the same bucket no matter
    which spoofed source each request claims.  Prior tests may already have
    consumed part of the shared per-minute budget, so the loop only requires
    a 429 to eventually arrive, not at an exact index.
    """

    missing_agent = f"missing-spoof-xff-{uuid.uuid4().hex}"

    statuses = []
    for i in range(45):
        spoofed_ip = f"198.18.{i // 250}.{i % 250 + 1}"
        status = _chat_via_web(agent=missing_agent, client_ip=spoofed_ip).status_code
        if status == 429:
            break
        statuses.append(status)
        assert status == 404, statuses

    assert status == 429, statuses

    # A yet-unseen spoofed address must stay inside the exhausted bucket.
    rotated = _chat_via_web(agent=missing_agent, client_ip="198.18.66.66")
    assert rotated.status_code == 429, rotated.text[:500]
    assert rotated.headers.get("retry-after"), rotated.headers
    _assert_single_http_body_framing(rotated)
