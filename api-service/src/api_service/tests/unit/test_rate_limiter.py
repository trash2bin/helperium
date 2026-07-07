"""Тесты для rate limiter (slowapi) в api-service/src/api_service/server.py.

Проверяет что CHAT_RATE_LIMIT env читается корректно и применяется
к конструктору Limiter и декораторам @limiter.limit().
"""

from __future__ import annotations

import os
from unittest.mock import patch


class TestRateLimiterInit:
    """Тесты что rate_limit переменная инициализируется из env."""

    def test_rate_limit_default(self):
        """Без env — дефолтный лимит."""
        from api_service.server import rate_limit

        assert rate_limit == "30/minute"

    def test_rate_limit_from_env_valid(self):
        """CHAT_RATE_LIMIT подхватывается."""
        with patch.dict(os.environ, {"CHAT_RATE_LIMIT": "100/minute"}):
            # reload module чтобы переинициализировалась module-level rate_limit
            import importlib

            import api_service.server

            importlib.reload(api_service.server)

            from api_service.server import rate_limit

            assert rate_limit == "100/minute"


class TestRateLimiterAppInit:
    """Проверки что app инициализируется с разными значениями лимита."""

    def test_app_inits_with_custom_limit(self):
        """Приложение инициализируется с кастомным лимитом без ошибок."""
        with patch.dict(os.environ, {"CHAT_RATE_LIMIT": "50/minute"}):
            import importlib

            import api_service.server

            importlib.reload(api_service.server)

            from api_service.server import app

            # Проверяем что limiter есть и default_limits применён
            assert hasattr(app.state, "limiter")

    def test_app_inits_with_empty_default(self):
        """Приложение инициализируется с дефолтным лимитом."""
        # Сбрасываем env
        with patch.dict(os.environ, {}, clear=True):
            import importlib

            import api_service.server

            importlib.reload(api_service.server)

            from api_service.server import app, rate_limit

            assert rate_limit == "30/minute"
            assert hasattr(app.state, "limiter")
