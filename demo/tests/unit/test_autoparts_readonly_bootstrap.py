from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "demo" / "autoparts-store" / "helperium_readonly_bootstrap.py"


def load_module(monkeypatch):
    fake_psycopg2 = types.ModuleType("psycopg2")
    fake_psycopg2.Error = Exception
    fake_psycopg2.connect = lambda **_: None
    fake_sql = types.ModuleType("psycopg2.sql")
    fake_sql.Composed = object
    fake_sql.Identifier = lambda value: value
    fake_sql.SQL = lambda value: value
    fake_psycopg2.sql = fake_sql
    monkeypatch.setitem(sys.modules, "psycopg2", fake_psycopg2)
    monkeypatch.setitem(sys.modules, "psycopg2.sql", fake_sql)

    spec = importlib.util.spec_from_file_location("autoparts_readonly_bootstrap", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


def settings(module):
    return module.BootstrapSettings(
        store_host="storefront-db",
        store_port=5432,
        store_database="autoparts",
        store_user="autoparts",
        store_password="writer-secret",
        readonly_user="helperium_autoparts_ro",
        readonly_password="read only/secret",
        register_tenant=True,
        data_service_url="http://data-service:8084",
        data_admin_token="admin-token",
        tenant_id="autoparts",
        attempts=1,
        retry_seconds=0,
    )


def test_readonly_dsn_encodes_separate_credential(monkeypatch):
    module = load_module(monkeypatch)

    dsn = module.readonly_dsn(settings(module))

    assert dsn == (
        "postgres://helperium_autoparts_ro:read%20only%2Fsecret@"
        "storefront-db:5432/autoparts?sslmode=disable"
    )
    assert "writer-secret" not in dsn


def test_existing_tenant_update_and_rewrite_are_tenant_scoped(monkeypatch):
    module = load_module(monkeypatch)
    calls = []

    def fake_request(_settings, method, path, payload=None, tenant_scoped=False):
        calls.append((method, path, payload, tenant_scoped))
        return 200, {"status": "ok"}

    monkeypatch.setattr(module, "request_json", fake_request)
    module.bootstrap_tenant(settings(module))

    assert [(method, path, scoped) for method, path, _, scoped in calls] == [
        ("GET", "/admin/tenants/autoparts", False),
        ("POST", "/admin/config", True),
        ("POST", "/admin/config/rewrite", True),
    ]
    config = calls[1][2]
    assert config["data_source"] == {
        "driver": "postgres",
        "dsn": "postgres://helperium_autoparts_ro:read%20only%2Fsecret@storefront-db:5432/autoparts?sslmode=disable",
        "read_only": True,
    }


def test_role_only_mode_does_not_require_core_admin_credentials(monkeypatch):
    module = load_module(monkeypatch)
    monkeypatch.setenv("STORE_DB_NAME", "autoparts")
    monkeypatch.setenv("STORE_DB_USER", "autoparts")
    monkeypatch.setenv("STORE_DB_PASSWORD", "writer-secret")
    monkeypatch.setenv("HELPERIUM_AUTOPARTS_RO_PASSWORD", "readonly-secret")
    monkeypatch.setenv("HELPERIUM_AUTOPARTS_REGISTER_TENANT", "false")
    monkeypatch.delenv("HELPERIUM_DATA_SERVICE_URL", raising=False)
    monkeypatch.delenv("HELPERIUM_DATA_ADMIN_TOKEN", raising=False)

    loaded = module.load_settings()

    assert loaded.register_tenant is False
    assert loaded.data_service_url == ""
    assert loaded.data_admin_token == ""


def test_development_credentials_are_scoped_to_autoparts_demo_env():
    root_env = (ROOT / ".env.example").read_text(encoding="utf-8")
    demo_env = (ROOT / "demo" / "autoparts-store" / ".env.dev.example").read_text(
        encoding="utf-8"
    )
    local_compose = (ROOT / "demo" / "autoparts-store" / "docker-compose.yml").read_text(
        encoding="utf-8"
    )
    launcher = (ROOT / "infra" / "scripts" / "dev.sh").read_text(encoding="utf-8")

    assert "HELPERIUM_AUTOPARTS_RO_PASSWORD" not in root_env
    assert "HELPERIUM_AUTOPARTS_RO_PASSWORD=" in demo_env
    assert "STORE_DB_PASSWORD=" in demo_env
    assert "autoparts_secret_2024" not in local_compose
    assert "${STORE_DB_PASSWORD:?set STORE_DB_PASSWORD in .env}" in local_compose
    assert 'env_file="$AUTOPARTS_DIR/.env"' in launcher
