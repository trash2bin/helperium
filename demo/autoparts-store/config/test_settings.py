"""Test settings for demo/autoparts-store.

Overrides the production PostgreSQL DATABASES with an in-memory
SQLite database so tests can run locally without a running Postgres.

Usage:
    DJANGO_DEBUG=True DJANGO_SETTINGS_MODULE=config.test_settings \
        uv run manage.py test tests.test_admin_login_throttle -v 1
"""

from __future__ import annotations

import os

# Force DEBUG=True so the DB_PASSWORD check in settings.py
# is skipped during test runs.
os.environ.setdefault("DJANGO_DEBUG", "true")

from config.settings import *  # noqa: F401,F403 — inherit all production settings

# ── Database: SQLite in-memory for local test runs ──────────
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

# Cache uses local memory — no PostgreSQL needed for tests.
# The throttle still works: per-IP counters are enforced within
# the single test process via LocMemCache.
CACHES = {
    "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"},
    "admin_throttle": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
    },
}
