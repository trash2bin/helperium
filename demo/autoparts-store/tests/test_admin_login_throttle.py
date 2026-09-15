"""Pentest 2026-09-14 (S1): /admin/login/ has no rate limit or lockout.

Live attack evidence: six rapid credential guesses from one IP were all
accepted instantly (HTTP 200, no delay/captcha/lockout). This pins the
throttle contract:

- after ``ADMIN_LOGIN_FAILURE_LIMIT`` failed logins from one IP, further
  requests to ``/admin/login/`` from that IP answer 429 (Retry-After);
- other client IPs are unaffected;
- a successful login clears the failure counter.

Counters live in the shared ``admin_throttle`` cache (DatabaseCache in
deployment) so all gunicorn workers enforce one budget per client IP.

Run from demo/autoparts-store against the SQLite test settings (no
Postgres required):

    DJANGO_DEBUG=True DJANGO_SETTINGS_MODULE=config.test_settings \
        uv run manage.py test tests.test_admin_login_throttle -v 1
"""

from __future__ import annotations

from django.conf import settings
from django.contrib.auth.models import User
from django.core.cache import caches
from django.core.management import call_command
from django.test import TestCase

FAILURE_LIMIT = settings.ADMIN_LOGIN_FAILURE_LIMIT
LOCKED_IP = "10.1.2.3"
OTHER_IP = "10.9.9.9"


class AdminLoginThrottleTests(TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        # No-op when CACHES uses LocMemCache (config.test_settings); creates
        # the DatabaseCache table when the suite runs against deployment-like
        # settings. Either way the throttle needs a usable "admin_throttle"
        # alias before the first request.
        call_command("createcachetable", verbosity=0)

    def setUp(self) -> None:
        # The throttle cache is process-global and is NOT rolled back with
        # the test transaction (LocMemCache in config.test_settings). Without
        # an explicit reset the counters leak from one test method into the
        # next and the assertions below observe a foreign lockout.
        caches["admin_throttle"].clear()

    def _attempt(self, ip: str = LOCKED_IP, **credentials):
        # The browser always reaches the admin login via ?next=/admin/ — the
        # hidden "next" field drives the post-login redirect.
        payload = {"username": "admin", "password": "wrong-guess", "next": "/admin/"}
        payload.update(credentials)
        return self.client.post("/admin/login/", payload, REMOTE_ADDR=ip)

    def test_lockout_after_failure_limit(self):
        for _ in range(FAILURE_LIMIT):
            response = self._attempt()
            self.assertEqual(response.status_code, 200)
        response = self._attempt()
        self.assertEqual(
            response.status_code,
            429,
            "attempt beyond the failure limit must be throttled",
        )
        self.assertIn("Retry-After", response.headers)
        # Even loading the login form is throttled for the locked IP.
        response = self.client.get("/admin/login/", REMOTE_ADDR=LOCKED_IP)
        self.assertEqual(response.status_code, 429)

    def test_other_ip_unaffected(self):
        for _ in range(FAILURE_LIMIT):
            self._attempt(ip=LOCKED_IP)
        response = self._attempt(ip=OTHER_IP)
        self.assertEqual(response.status_code, 200)

    def test_successful_login_clears_counter(self):
        User.objects.create_superuser("admin", "admin@example.com", "s3cret-pass")
        self._attempt()  # one failure recorded
        response = self.client.post(
            "/admin/login/",
            {"username": "admin", "password": "s3cret-pass", "next": "/admin/"},
            REMOTE_ADDR=LOCKED_IP,
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        # Counter cleared: a fresh run of failures is accepted again until
        # the limit is reached.
        for _ in range(FAILURE_LIMIT):
            self.assertEqual(self._attempt().status_code, 200)
        self.assertEqual(self._attempt().status_code, 429)
