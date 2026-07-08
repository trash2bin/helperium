"""Тесты для конфигурации сервисов (demo/settings.py).

Проверяет:
1. Дефолтные значения когда env vars не заданы
2. Парсинг env vars всех типов (int, float, bool, str, list через comma-separated)
3. Edge cases: пустые строки, невалидные числа, boolean вариации
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

# Тесты идут без установки env vars — проверяем что дефолты корректные
# после import'а settings мы меняем env и пересоздаём DemoSettings


class TestDemoSettingsDefaults:
    """Без env vars — все дефолты."""

    def test_core_defaults(self):
        from demo.settings import DemoSettings

        s = DemoSettings()
        assert s.api_host == "127.0.0.1"
        assert s.api_port == 8081
        assert s.web_host == "127.0.0.1"
        assert s.web_port == 8080
        assert s.web_origin == "http://localhost:8080"
        assert s.api_bearer_token is None

    def test_tenant_defaults(self):
        """Новые поля tenant-конфигурации — дефолты."""
        from demo.settings import DemoSettings

        s = DemoSettings()
        assert s.default_tenant_id == "default"
        assert s.demo_tenants == ""  # пустая строка = авто-дискавери

    def test_service_url_defaults(self):
        """Новые поля URL сервисов — дефолты."""
        from demo.settings import DemoSettings

        s = DemoSettings()
        assert s.data_service_url == "http://127.0.0.1:8084"
        assert s.rag_service_url == "http://127.0.0.1:8082"
        assert s.web_proxy_timeout == 30.0

    def test_mcp_service_url_default(self):
        from demo.settings import DemoSettings

        s = DemoSettings()
        assert s.mcp_service_url == "http://127.0.0.1:8083/mcp"


class TestDemoSettingsFromEnv:
    """С env vars — проверяем что парсинг корректный."""

    def test_data_service_url_override(self):
        from demo.settings import DemoSettings

        with patch.dict(
            os.environ, {"DATA_SERVICE_URL": "http://data-svc.custom:9090"}
        ):
            s = DemoSettings()
            assert s.data_service_url == "http://data-svc.custom:9090"

    def test_rag_service_url_override(self):
        from demo.settings import DemoSettings

        with patch.dict(os.environ, {"RAG_SERVICE_URL": "http://rag.internal:8082"}):
            s = DemoSettings()
            assert s.rag_service_url == "http://rag.internal:8082"

    def test_web_proxy_timeout_float(self):
        """Float парсится корректно."""
        from demo.settings import DemoSettings

        with patch.dict(os.environ, {"WEB_PROXY_TIMEOUT": "60.5"}):
            s = DemoSettings()
            assert s.web_proxy_timeout == 60.5

    def test_web_proxy_timeout_invalid_falls_back(self):
        """Невалидный float не падает — падает int()/float().
        Но мы пишем осмысленно: если значение кривое — пусть падает,
        чтобы админ увидел ошибку при старте.
        """
        from demo.settings import DemoSettings

        with patch.dict(os.environ, {"WEB_PROXY_TIMEOUT": "not-a-number"}):
            with pytest.raises(ValueError):
                DemoSettings()

    def test_default_tenant_id_override(self):
        from demo.settings import DemoSettings

        with patch.dict(os.environ, {"DEFAULT_TENANT_ID": "tenant-shop"}):
            s = DemoSettings()
            assert s.default_tenant_id == "tenant-shop"

    def test_demo_tenants_comma_separated(self):
        """Comma-separated список tenant'ов хранится как строка (парсинг в web/server.py)."""
        from demo.settings import DemoSettings

        with patch.dict(os.environ, {"DEMO_TENANTS": "tenant-a,tenant-b,tenant-c"}):
            s = DemoSettings()
            # Сохраняется как есть — парсинг в get_tenants эндпоинте
            assert s.demo_tenants == "tenant-a,tenant-b,tenant-c"

    def test_demo_tenants_empty(self):
        from demo.settings import DemoSettings

        with patch.dict(os.environ, {"DEMO_TENANTS": ""}):
            s = DemoSettings()
            assert s.demo_tenants == ""

    def test_cors_origin_comma_separated(self):
        """WEB_ORIGIN может быть comma-separated списком."""
        from demo.settings import DemoSettings

        with patch.dict(os.environ, {"WEB_ORIGIN": "http://app1.com,http://app2.com"}):
            s = DemoSettings()
            assert s.web_origin == "http://app1.com,http://app2.com"


class TestDemoSettingsEdgeCases:
    """Граничные случаи."""

    def test_api_port_override(self):
        from demo.settings import DemoSettings

        with patch.dict(os.environ, {"DEMO_API_PORT": "9090"}):
            s = DemoSettings()
            assert s.api_port == 9090

    def test_api_port_invalid_falls_back(self):
        """Невалидный int падает — это правильно (админ ошибку увидит)."""
        from demo.settings import DemoSettings

        with patch.dict(os.environ, {"DEMO_API_PORT": "not-a-port"}):
            with pytest.raises(ValueError):
                DemoSettings()

    def test_bool_think_mode_variants(self):
        from demo.settings import DemoSettings

        for truthy in ("1", "true", "yes", "True", "TRUE", "Yes"):
            with patch.dict(os.environ, {"ENABLE_THINK": truthy}):
                s = DemoSettings()
                assert s.think_mode is True, f"ENABLE_THINK={truthy!r} should be True"

        for falsy in ("0", "false", "no", "False", "FALSE", ""):
            with patch.dict(os.environ, {"ENABLE_THINK": falsy}):
                s = DemoSettings()
                assert s.think_mode is False, f"ENABLE_THINK={falsy!r} should be False"

    def test_bearer_token_none_by_default(self):
        from demo.settings import DemoSettings

        s = DemoSettings()
        assert s.api_bearer_token is None

    def test_bearer_token_set(self):
        from demo.settings import DemoSettings

        with patch.dict(os.environ, {"API_BEARER_TOKEN": "super-secret-123"}):
            s = DemoSettings()
            assert s.api_bearer_token == "super-secret-123"


class TestDemoSettingsQuoting:
    """Настройки с пробелами и спецсимволами — всё сырым из env."""

    def test_url_with_auth(self):
        """URL с паролем не урл-энкодится."""
        from demo.settings import DemoSettings

        with patch.dict(
            os.environ, {"DATA_SERVICE_URL": "http://user:pass@custom:8084"}
        ):
            s = DemoSettings()
            assert s.data_service_url == "http://user:pass@custom:8084"
