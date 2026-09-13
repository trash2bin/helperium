"""Rate limit configuration — shared between app.py and route modules."""

from __future__ import annotations

import os
from ipaddress import ip_address, ip_network

from fastapi import Request
from slowapi import Limiter


rate_limit = os.environ.get("CHAT_RATE_LIMIT", "30/minute")

# Public problem-report endpoint: reports are rare by nature, so the per-IP
# budget is much tighter than chat and independent of chat's bucket.
reports_rate_limit = os.environ.get("REPORTS_RATE_LIMIT", "5/minute")


def _trusted_proxies() -> set[str]:
    """TRUSTED_PROXIES env: comma-separated peers trusted to set X-Forwarded-For.

    Entries may be exact IPs or CIDR blocks (e.g. "127.0.0.1,172.28.0.0/16").
    Hostnames are not resolvable here — a peer is compared as an IP string /
    CIDR member — so only IP-shaped entries are effective. Read per call
    (not cached) so tests and runtime config changes take effect without
    restart.
    """
    raw = os.environ.get("TRUSTED_PROXIES", "")
    return {p.strip() for p in raw.split(",") if p.strip()}


def _is_trusted_peer(peer: str) -> bool:
    """True if the direct TCP peer is a configured trusted proxy (pentest H3)."""
    for entry in _trusted_proxies():
        if entry == peer:
            return True
        if "/" in entry:
            try:
                if ip_address(peer) in ip_network(entry, strict=False):
                    return True
            except ValueError:
                continue
    return False


def get_client_ip(request: Request) -> str:
    """Return the client IP vouched for by the private ingress chain.

    api-service is not exposed directly in the Compose deployment: requests
    arrive only through Caddy or demo-web. Those trusted internal proxies
    overwrite or append to ``X-Forwarded-For``, so the rightmost entry is the
    only one the trusted hop vouched for — earlier entries are
    client-controlled and must not become the limiter key. Without the
    configured proxy, SlowAPI would rate-limit every public visitor as the
    proxy container's single bridge-network address.

    Pentest H3: X-Forwarded-For is attacker-controlled unless the direct TCP
    peer is a configured trusted proxy (TRUSTED_PROXIES). In deployments where
    api-service is reachable directly (native dev, direct port mappings), the
    header is ignored entirely and the socket peer IP is the limiter key, so
    spoofing XFF per request cannot bypass the per-IP limits.
    """

    peer = request.client.host if request.client else "127.0.0.1"

    if _is_trusted_peer(peer):
        forwarded_for = request.headers.get("x-forwarded-for", "")
        if forwarded_for:
            for entry in reversed(forwarded_for.split(",")):
                client_ip = entry.strip()
                if client_ip:
                    return client_ip
    return peer


limiter = Limiter(key_func=get_client_ip, default_limits=[rate_limit])
