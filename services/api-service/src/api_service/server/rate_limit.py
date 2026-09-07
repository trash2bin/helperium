"""Rate limit configuration — shared between app.py and route modules."""

from __future__ import annotations

import os

from fastapi import Request
from slowapi import Limiter


rate_limit = os.environ.get("CHAT_RATE_LIMIT", "30/minute")

# Public problem-report endpoint: reports are rare by nature, so the per-IP
# budget is much tighter than chat and independent of chat's bucket.
reports_rate_limit = os.environ.get("REPORTS_RATE_LIMIT", "5/minute")


def get_client_ip(request: Request) -> str:
    """Return the client IP vouched for by the private ingress chain.

    api-service is not exposed directly in the Compose deployment: requests
    arrive only through Caddy or demo-web.  Those trusted internal proxies
    overwrite or append to ``X-Forwarded-For``, so the rightmost entry is the
    only one the trusted hop vouched for — earlier entries are
    client-controlled and must not become the limiter key.  Without the
    header, SlowAPI would rate-limit every public visitor as the proxy
    container's single bridge-network address.
    """

    forwarded_for = request.headers.get("x-forwarded-for", "")
    if forwarded_for:
        for entry in reversed(forwarded_for.split(",")):
            client_ip = entry.strip()
            if client_ip:
                return client_ip
    client = request.client
    return client.host if client else "127.0.0.1"


limiter = Limiter(key_func=get_client_ip, default_limits=[rate_limit])
