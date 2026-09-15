"""Pentest 2026-09-14 (S2) regression pin: storefront widget embed base URL.

Root cause of the dead widget: ``HELPERIUM_API_BASE=http://localhost`` (no
port) in the local compose environment — a leftover from the public Caddy
deployment where port 80 serves the domain. The browser then asked for
``http://localhost/embed/embed.js`` and nothing answered.

Pinned here:
- the local compose default must be an absolute URL with an explicit port;
- the local ``.env`` override, when present, must also carry a port — this
  is the exact regression that shipped (the file is untracked, so the check
  runs only where the deployment exists, i.e. this machine);
- the public compose default stays empty (same-origin behind Caddy) or,
  if ever set, carries an explicit port.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

DEMO_DIR = Path(__file__).resolve().parent.parent
LOCAL_COMPOSE = DEMO_DIR / "docker-compose.yml"
PUBLIC_COMPOSE = DEMO_DIR / "docker-compose.public.yml"
LOCAL_ENV = DEMO_DIR / ".env"

# A browser-facing base URL must say which port it talks to; a portless
# origin silently targets port 80 where nothing listens in local dev.
_PORTED_URL = re.compile(r"^https?://[^\s/]+:\d+$")
_DEFAULT_RE = re.compile(r"HELPERIUM_API_BASE:\s*\$\{HELPERIUM_API_BASE:-([^}]*)\}")


def _compose_default(path: Path) -> str:
    for line in path.read_text(encoding="utf-8").splitlines():
        match = _DEFAULT_RE.search(line)
        if match:
            return match.group(1).strip()
    raise AssertionError(f"HELPERIUM_API_BASE default not found in {path}")


class WidgetEmbedBaseUrlTest(unittest.TestCase):
    def test_local_compose_default_has_explicit_port(self) -> None:
        default = _compose_default(LOCAL_COMPOSE)
        self.assertTrue(
            _PORTED_URL.match(default),
            f"local HELPERIUM_API_BASE default {default!r} must be an absolute "
            "URL with an explicit port (portless origins hit port 80, where "
            "nothing listens in local dev)",
        )

    def test_local_env_override_has_explicit_port(self) -> None:
        if not LOCAL_ENV.exists():
            self.skipTest("local .env not present")
        value: str | None = None
        for line in LOCAL_ENV.read_text(encoding="utf-8").splitlines():
            if line.startswith("HELPERIUM_API_BASE="):
                value = line.split("=", 1)[1].strip().strip('"').rstrip("/")
        if value is None:
            self.skipTest("HELPERIUM_API_BASE not set in local .env (compose default applies)")
        self.assertTrue(
            _PORTED_URL.match(value),
            f"HELPERIUM_API_BASE={value!r} in .env has no explicit port — this "
            "is the dead-widget regression (was http://localhost)",
        )

    def test_public_compose_default_stays_same_origin_or_ported(self) -> None:
        default = _compose_default(PUBLIC_COMPOSE)
        self.assertTrue(
            default == "" or _PORTED_URL.match(default),
            f"public HELPERIUM_API_BASE default {default!r} must be empty "
            "(same origin behind Caddy) or an explicit-port URL",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
