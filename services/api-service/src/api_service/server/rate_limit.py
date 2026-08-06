"""Rate limit configuration — shared between app.py and route modules."""

from __future__ import annotations

import os

from slowapi import Limiter
from slowapi.util import get_remote_address

rate_limit = os.environ.get("CHAT_RATE_LIMIT", "30/minute")
limiter = Limiter(key_func=get_remote_address, default_limits=[rate_limit])
