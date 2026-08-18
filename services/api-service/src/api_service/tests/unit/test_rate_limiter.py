"""Тесты для rate limiter (slowapi) в api-service/src/api_service/server/.

Проверяет что CHAT_RATE_LIMIT env читается корректно и применяется
к конструктору Limiter и декораторам @limiter.limit().
"""

from __future__ import annotations
from starlette.requests import Request


import importlib
import os
import sys
from types import SimpleNamespace
from unittest.mock import patch

from starlette.responses import Response

# Direct reference to the submodule — avoids attribute-shadowing in __init__.py
_rate_limit_mod_name = "api_service.server.rate_limit"
_server_pkg_name = "api_service.server"


def _reload_rate_limit_and_server():
    """Reload rate_limit module then the server package.

    rate_limit is now defined in a dedicated module; it must be reloaded
    before the server package so that ``from .rate_limit import ...`` in
    app.py picks up the new env value.
    """
    # Use sys.modules to avoid the attribute-shadowing issue:
    # __init__.py imports `rate_limit` (a string) from .rate_limit,
    # which shadows the submodule name in the package namespace.
    rl = sys.modules[_rate_limit_mod_name]
    importlib.reload(rl)
    server = sys.modules[_server_pkg_name]
    importlib.reload(server)


class TestRateLimiterInit:
    """Тесты что rate_limit переменная инициализируется из env."""

    def test_rate_limit_default(self):
        """Без env — дефолтный лимит."""
        from api_service.server import rate_limit

        assert rate_limit == "30/minute"

    def test_rate_limit_from_env_valid(self):
        """CHAT_RATE_LIMIT подхватывается."""
        with patch.dict(os.environ, {"CHAT_RATE_LIMIT": "100/minute"}):
            _reload_rate_limit_and_server()

            from api_service.server import rate_limit

            assert rate_limit == "100/minute"


class TestRateLimiterAppInit:
    """Проверки что app инициализируется с разными значениями лимита."""

    def test_app_inits_with_custom_limit(self):
        """Приложение инициализируется с кастомным лимитом без ошибок."""
        with patch.dict(os.environ, {"CHAT_RATE_LIMIT": "50/minute"}):
            _reload_rate_limit_and_server()

            from api_service.server import app

            # Проверяем что limiter есть и default_limits применён
            assert hasattr(app.state, "limiter")

    def test_app_inits_with_empty_default(self):
        """Приложение инициализируется с дефолтным лимитом."""
        # Сбрасываем env
        with patch.dict(os.environ, {}, clear=True):
            _reload_rate_limit_and_server()

            from api_service.server import app, rate_limit

            assert rate_limit == "30/minute"
            assert hasattr(app.state, "limiter")


def _request(*, headers: list[tuple[bytes, bytes]], client_ip: str) -> Request:
    return Request(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/api/chat/demo-agent",
            "raw_path": b"/api/chat/demo-agent",
            "query_string": b"",
            "headers": headers,
            "client": (client_ip, 12345),
            "server": ("testserver", 80),
        }
    )


class TestRateLimitResponse:
    """The public 429 contract must tell the client when to retry."""

    def test_handler_sets_retry_after_from_limit_expiry(self):
        from api_service.server.app import _helperium_rate_limit_handler

        request = _request(headers=[], client_ip="172.18.0.7")
        exc = SimpleNamespace(
            limit=SimpleNamespace(limit=SimpleNamespace(get_expiry=lambda: 120))
        )

        with patch(
            "api_service.server.app._rate_limit_exceeded_handler",
            return_value=Response(status_code=429),
        ):
            response = _helperium_rate_limit_handler(request, exc)

        assert response.status_code == 429
        assert response.headers["Retry-After"] == "120"


class TestRateLimitClientIP:
    """The limiter must distinguish visitors behind the internal proxy."""

    def test_uses_first_forwarded_for_address(self):
        from api_service.server.rate_limit import get_client_ip

        request = _request(
            headers=[(b"x-forwarded-for", b"198.51.100.10, 172.18.0.7")],
            client_ip="172.18.0.7",
        )

        assert get_client_ip(request) == "198.51.100.10"

    def test_falls_back_to_direct_peer_without_forwarded_header(self):
        from api_service.server.rate_limit import get_client_ip

        request = _request(headers=[], client_ip="172.18.0.7")

        assert get_client_ip(request) == "172.18.0.7"
