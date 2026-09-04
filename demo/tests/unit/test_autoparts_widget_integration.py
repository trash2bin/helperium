"""Regression guards for the native storefront/widget integration."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
COMPOSE = ROOT / "demo" / "autoparts-store" / "docker-compose.yml"
LAUNCHER = ROOT / "infra" / "scripts" / "dev.sh"


def test_native_storefront_enables_widget_and_uses_host_api_url():
    """The browser must load the widget from the host-published API service."""
    compose = COMPOSE.read_text(encoding="utf-8")

    assert "HELPERIUM_WIDGET_ENABLED: ${HELPERIUM_WIDGET_ENABLED:-true}" in compose
    assert (
        "HELPERIUM_API_BASE: ${HELPERIUM_API_BASE:-http://127.0.0.1:8081}"
        in compose
    )
    assert "api:8081" not in compose.split("environment:", 1)[1].split("volumes:", 1)[0]


def test_native_autoparts_launcher_overrides_public_values_and_allows_cors():
    """Explicit native startup must not inherit a public Docker/Caddy URL."""
    launcher = LAUNCHER.read_text(encoding="utf-8")

    assert 'HELPERIUM_API_BASE="http://127.0.0.1:$API_PORT"' in launcher
    assert "HELPERIUM_WIDGET_ENABLED=true" in launcher
    assert (
        "CORS_ALLOW_ORIGINS=http://localhost:8080,http://127.0.0.1:8080,"
        "http://localhost:8000,http://127.0.0.1:8000"
    ) in launcher
