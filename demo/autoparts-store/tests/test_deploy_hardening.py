"""Deployment hardening regression for the autoparts demo storefront.

Authorized pentest 2026-09-13 finding: the storefront container runs
``manage.py runserver`` with ``DJANGO_DEBUG: "true"``. In that mode any
malformed request (e.g. ``Host: evil.example`` → DisallowedHost) or any
unhandled exception renders Django's technical page with the full settings
dump, URLconf, traceback and filesystem paths. Secret values are masked by
Django's filter, but topology/config/paths leak.

This test pins the deployment contract: the compose stack must not enable
DEBUG and must not serve traffic through the dev WSGI server. Expected to
FAIL against the current docker-compose.yml; fix the compose file (not this
test). Run: ``python3 -m unittest tests/test_deploy_hardening.py -v``
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

COMPOSE = Path(__file__).resolve().parent.parent / "docker-compose.yml"


class StorefrontDeployHardeningTest(unittest.TestCase):
    def setUp(self) -> None:
        self.text = COMPOSE.read_text(encoding="utf-8")

    def test_no_django_debug_true(self) -> None:
        offenders = [
            line.strip()
            for line in self.text.splitlines()
            if re.search(r'DJANGO_DEBUG:\s*["\']?true["\']?\s*$', line)
        ]
        self.assertEqual(
            offenders,
            [],
            "DJANGO_DEBUG=true in compose leaks Django technical pages "
            f"(settings dump/traceback) on bad requests; offenders: {offenders}",
        )

    def test_no_runserver_as_serving_command(self) -> None:
        offenders = [
            line.strip()
            for line in self.text.splitlines()
            if re.search(r'\brunserver\b', line)
        ]
        self.assertEqual(
            offenders,
            [],
            "runserver is a dev WSGI server (verbose errors, not for traffic); "
            f"serve via gunicorn instead; offenders: {offenders}",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
