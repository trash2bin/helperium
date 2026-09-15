"""Brute-force throttle for the Django admin login.

Pentest 2026-09-14 (S1): the storefront exposes ``/admin/`` publicly and
accepted credential guesses at full speed. Failed attempts are counted per
client IP in the shared ``admin_throttle`` cache; once the count reaches
``ADMIN_LOGIN_FAILURE_LIMIT`` within the cooloff window, requests to
``/admin/login/`` from that IP answer 429 without touching the auth
machinery. A successful login clears the counter.

The counter cache is ``DatabaseCache`` so the three gunicorn workers share
one budget per IP — a per-process ``LocMemCache`` would multiply the
attacker's budget by the worker count. The client IP is the raw socket peer:
the storefront has no trusted proxy in front (docker-compose terminates TLS
directly in the public deployment), and ``X-Forwarded-For`` is trivially
spoofable, so it is deliberately ignored.

Cache failures fail open (with an error log) so an ops mistake can never
permanently brick the admin — the throttle is a speed bump, not a lock.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.contrib.auth.signals import user_logged_in, user_login_failed
from django.core.cache import caches
from django.dispatch import receiver
from django.http import HttpResponse

logger = logging.getLogger("catalog.admin_login_throttle")

_CACHE_ALIAS = "admin_throttle"
_KEY_PREFIX = "admin-login-failures:"


def _client_ip(request) -> str:
    return request.META.get("REMOTE_ADDR") or "unknown"


def _key(ip: str) -> str:
    return f"{_KEY_PREFIX}{ip}"


def _limit() -> int:
    # The values (and their env defaults) live in config.settings only: a second
    # fallback here would silently diverge from what the deployment configured.
    return int(settings.ADMIN_LOGIN_FAILURE_LIMIT)


def _cooloff() -> int:
    return int(settings.ADMIN_LOGIN_COOLOFF_SECONDS)


def failure_count(ip: str) -> int:
    try:
        return int(caches[_CACHE_ALIAS].get(_key(ip), 0))
    except Exception:
        logger.exception("admin login throttle cache read failed; failing open")
        return 0


def _record_failure(ip: str) -> None:
    cache = caches[_CACHE_ALIAS]
    cooloff = _cooloff()
    try:
        # add() wins only when the key is absent, so the window is fixed at
        # the first failure and incr() does not extend it.
        stored = cache.add(_key(ip), 1, cooloff)
        if not stored:
            cache.incr(_key(ip))
    except Exception:
        logger.exception("admin login throttle cache write failed; failing open")


@receiver(user_login_failed)
def record_login_failure(sender, credentials=None, request=None, **kwargs) -> None:
    if request is None:
        return
    _record_failure(_client_ip(request))


@receiver(user_logged_in)
def clear_login_failures(sender, request=None, user=None, **kwargs) -> None:
    if request is None:
        return
    try:
        caches[_CACHE_ALIAS].delete(_key(_client_ip(request)))
    except Exception:
        logger.exception("admin login throttle cache clear failed")


def throttled_admin_login(request, *args, **kwargs):
    """Stand-in for the admin login view enforcing the per-IP lockout."""
    ip = _client_ip(request)
    if failure_count(ip) >= _limit():
        response = HttpResponse(
            "Слишком много неудачных попыток входа. Повторите попытку позже.",
            status=429,
            content_type="text/plain; charset=utf-8",
        )
        response["Retry-After"] = str(_cooloff())
        # Prevent browsers from caching the lockout page (stale 429 after cooloff).
        response["Cache-Control"] = "no-store"
        return response
    from django.contrib import admin

    return admin.site.login(request, *args, **kwargs)
