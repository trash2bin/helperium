"""Pentest H3: X-Forwarded-For must only be trusted from configured proxies.

get_client_ip() previously used the rightmost X-Forwarded-For entry
unconditionally. In deployments without a trusted proxy in front of
api-service (native dev, direct compose port mappings), a client controls the
header entirely, so per-IP rate limits (SlowAPI + the H2 IP bucket) could be
bypassed by changing XFF per request.

The fix: XFF is only used when the direct TCP peer is in TRUSTED_PROXIES;
otherwise the socket peer IP is the limiter key.
"""

from __future__ import annotations

from starlette.requests import Request

from api_service.server.rate_limit import get_client_ip


async def _receive() -> dict:
    return {"type": "http.request", "body": b"{}", "more_body": False}


def _request(
    peer: str = "203.0.113.7",
    xff: str | None = None,
) -> Request:
    headers = []
    if xff is not None:
        headers.append((b"x-forwarded-for", xff.encode()))
    return Request(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/api/chat",
            "raw_path": b"/api/chat",
            "query_string": b"",
            "headers": headers,
            "client": (peer, 4321),
            "server": ("testserver", 80),
        },
        receive=_receive,
    )


def test_no_trusted_proxies_ignores_spoofed_xff(monkeypatch):
    """Without TRUSTED_PROXIES the socket IP wins; spoofed XFF is ignored."""
    monkeypatch.delenv("TRUSTED_PROXIES", raising=False)
    req = _request(peer="203.0.113.7", xff="198.51.100.88, 172.16.0.5")
    assert get_client_ip(req) == "203.0.113.7"


def test_untrusted_peer_ignores_spoofed_xff(monkeypatch):
    """XFF from a peer that is NOT trusted is attacker data."""
    monkeypatch.setenv("TRUSTED_PROXIES", "127.0.0.1,10.0.0.1")
    req = _request(peer="203.0.113.7", xff="1.2.3.4")
    assert get_client_ip(req) == "203.0.113.7"


def test_trusted_proxy_xff_rightmost_is_used(monkeypatch):
    """From a trusted hop the rightmost XFF entry is the vouched client."""
    monkeypatch.setenv("TRUSTED_PROXIES", "127.0.0.1,10.0.0.1")
    req = _request(peer="10.0.0.1", xff="198.51.100.88, 203.0.113.9")
    assert get_client_ip(req) == "203.0.113.9"


def test_trusted_proxy_without_xff_falls_back_to_peer(monkeypatch):
    monkeypatch.setenv("TRUSTED_PROXIES", "127.0.0.1,10.0.0.1")
    req = _request(peer="10.0.0.1", xff=None)
    assert get_client_ip(req) == "10.0.0.1"


def test_trusted_proxy_whitespace_variants(monkeypatch):
    """Comma-separated list trims whitespace and empty entries."""
    monkeypatch.setenv("TRUSTED_PROXIES", " 127.0.0.1 , caddy.internal , , 10.0.0.1 ")
    req = _request(peer="caddy.internal", xff="198.51.100.7")
    assert get_client_ip(req) == "198.51.100.7"


def test_cidr_trusted_proxy_is_honored(monkeypatch):
    """CIDR entries trust every container on the compose network."""
    monkeypatch.setenv("TRUSTED_PROXIES", "172.28.0.0/16")
    req = _request(peer="172.28.0.7", xff="198.51.100.7")
    assert get_client_ip(req) == "198.51.100.7"


def test_cidr_does_not_trust_outside_block(monkeypatch):
    """A peer outside the CIDR is not trusted: its XFF is spoof data."""
    monkeypatch.setenv("TRUSTED_PROXIES", "172.28.0.0/16")
    req = _request(peer="203.0.113.9", xff="198.51.100.7")
    assert get_client_ip(req) == "203.0.113.9"
